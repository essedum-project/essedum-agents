use dotenvy::dotenv;
use goose::agents::platform_extensions::developer::minio::maybe_upload;

#[tokio::main]
async fn main() {
    let _ = dotenv();

    // Set up tracing so upload success/failure is visible
    tracing_subscriber::fmt()
        .with_max_level(tracing::Level::INFO)
        .init();

    let session_id = "minio-upload-test".to_string();
    let path = "goose-test/hello.txt".to_string();
    let content = "Hello from goose! MinIO upload test.\n".to_string();

    println!("Uploading '{}' to MinIO ...", path);
    maybe_upload(&session_id, path.clone(), content).await;
    println!("Done. Check MinIO bucket for '{}'.", path);
}
