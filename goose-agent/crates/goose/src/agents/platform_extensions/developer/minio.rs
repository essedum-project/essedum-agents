use chrono::Utc;
use hmac::{Hmac, Mac};
use sha2::{Digest, Sha256};

type HmacSha256 = Hmac<Sha256>;

fn hmac_sha256(key: &[u8], data: &[u8]) -> Vec<u8> {
    let mut mac = HmacSha256::new_from_slice(key).expect("HMAC accepts any key length");
    mac.update(data);
    mac.finalize().into_bytes().to_vec()
}

fn to_hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{:02x}", b)).collect()
}

fn sha256_hex(data: &[u8]) -> String {
    to_hex(&Sha256::digest(data))
}

async fn upload(
    endpoint: &str,
    access_key: &str,
    secret_key: &str,
    bucket: &str,
    region: &str,
    object_key: &str,
    body: &[u8],
) -> anyhow::Result<()> {
    let now = Utc::now();
    let date_str = now.format("%Y%m%d").to_string();
    let datetime_str = now.format("%Y%m%dT%H%M%SZ").to_string();
    let service = "s3";

    let parsed = url::Url::parse(endpoint)?;
    let host = parsed
        .host_str()
        .ok_or_else(|| anyhow::anyhow!("invalid MinIO endpoint: missing host"))?;
    let host_header = match parsed.port() {
        Some(port) => format!("{}:{}", host, port),
        None => host.to_string(),
    };

    let content_type = "application/octet-stream";
    let payload_hash = sha256_hex(body);

    // URL-encode each path component of the object key
    let encoded_key: String = object_key
        .split('/')
        .map(|s| urlencoding::encode(s).into_owned())
        .collect::<Vec<_>>()
        .join("/");

    let canonical_uri = format!("/{}/{}", bucket, encoded_key);
    let canonical_headers = format!(
        "content-type:{}\nhost:{}\nx-amz-content-sha256:{}\nx-amz-date:{}\n",
        content_type, host_header, payload_hash, datetime_str
    );
    let signed_headers = "content-type;host;x-amz-content-sha256;x-amz-date";

    let canonical_request = format!(
        "PUT\n{}\n\n{}\n{}\n{}",
        canonical_uri, canonical_headers, signed_headers, payload_hash
    );

    let credential_scope = format!("{}/{}/{}/aws4_request", date_str, region, service);
    let string_to_sign = format!(
        "AWS4-HMAC-SHA256\n{}\n{}\n{}",
        datetime_str,
        credential_scope,
        sha256_hex(canonical_request.as_bytes())
    );

    let signing_key = {
        let k_date = hmac_sha256(format!("AWS4{}", secret_key).as_bytes(), date_str.as_bytes());
        let k_region = hmac_sha256(&k_date, region.as_bytes());
        let k_service = hmac_sha256(&k_region, service.as_bytes());
        hmac_sha256(&k_service, b"aws4_request")
    };

    let signature = to_hex(&hmac_sha256(&signing_key, string_to_sign.as_bytes()));

    let authorization = format!(
        "AWS4-HMAC-SHA256 Credential={}/{},SignedHeaders={},Signature={}",
        access_key, credential_scope, signed_headers, signature
    );

    let url = format!(
        "{}/{}/{}",
        endpoint.trim_end_matches('/'),
        bucket,
        encoded_key
    );

    let skip_tls_verify =
        std::env::var("GOOSE_MINIO_TLS_SKIP_VERIFY").as_deref() == Ok("true");
    let client = reqwest::Client::builder()
        .danger_accept_invalid_certs(skip_tls_verify)
        .build()?;
    let response = client
        .put(&url)
        .header("Content-Type", content_type)
        .header("x-amz-date", &datetime_str)
        .header("x-amz-content-sha256", &payload_hash)
        .header("Authorization", &authorization)
        .body(body.to_vec())
        .send()
        .await?;

    if response.status().is_success() {
        Ok(())
    } else {
        let status = response.status();
        let body_txt = response.text().await.unwrap_or_default();
        Err(anyhow::anyhow!(
            "MinIO upload failed: HTTP {} - {}",
            status,
            body_txt
        ))
    }
}

/// Upload `content` to MinIO as `path` if `GOOSE_MINIO_UPLOAD_ENABLED=true`.
///
/// Required env vars when enabled:
///   GOOSE_MINIO_ENDPOINT   – e.g. http://localhost:9000
///   GOOSE_MINIO_ACCESS_KEY
///   GOOSE_MINIO_SECRET_KEY
///   GOOSE_MINIO_BUCKET
///
/// Optional:
///   GOOSE_MINIO_REGION     – defaults to "us-east-1"
pub async fn maybe_upload(session_id: &str, path: String, content: String) {
    if std::env::var("GOOSE_MINIO_UPLOAD_ENABLED").as_deref() != Ok("true") {
        return;
    }

    macro_rules! require_env {
        ($var:literal) => {
            match std::env::var($var) {
                Ok(v) => v,
                Err(_) => {
                    tracing::warn!(
                        "GOOSE_MINIO_UPLOAD_ENABLED=true but {} is not set",
                        $var
                    );
                    return;
                }
            }
        };
    }

    let endpoint = require_env!("GOOSE_MINIO_ENDPOINT");
    let access_key = require_env!("GOOSE_MINIO_ACCESS_KEY");
    let secret_key = require_env!("GOOSE_MINIO_SECRET_KEY");
    let bucket = require_env!("GOOSE_MINIO_BUCKET");
    let region =
        std::env::var("GOOSE_MINIO_REGION").unwrap_or_else(|_| "us-east-1".to_string());

    let prefix = std::env::var("GOOSE_MINIO_PREFIX").unwrap_or_else(|_| "goose-apps".to_string());
    let relative = path.trim_start_matches('/');
    let object_key = format!("{}/{}/{}", prefix.trim_end_matches('/'), session_id, relative);

    match upload(
        &endpoint,
        &access_key,
        &secret_key,
        &bucket,
        &region,
        &object_key,
        content.as_bytes(),
    )
    .await
    {
        Ok(()) => tracing::info!("Uploaded {} to MinIO bucket {}", path, bucket),
        Err(e) => tracing::warn!("MinIO upload failed for {}: {}", path, e),
    }
}
