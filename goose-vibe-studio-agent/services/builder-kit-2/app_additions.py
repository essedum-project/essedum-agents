# builder-kit-2 additions: session-scoped MinIO file serving
# These additions are appended to/injected into the existing app.py

import mimetypes

# In-memory store: session_id -> {relative_path: bytes}
_session_file_cache = {}

MINIO_BUCKET  = os.getenv("MINIO_BUCKET", "aiptest")
MINIO_PREFIX  = os.getenv("MINIO_PREFIX", "goose-apps")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://builderk-service-2.aipns.svc.cluster.local")


def _s3_client():
    return boto3.client(
        "s3",
        endpoint_url=os.getenv("MINIO_ENDPOINT", "http://minio-service:9000"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "minioadmin"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "minioadmin123"),
        region_name=os.getenv("AWS_REGION", "us-east-1"),
    )


def _list_session_keys(s3, session_id: str):
    prefix = f"{MINIO_PREFIX}/{session_id}/"
    keys = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=MINIO_BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []):
            keys.append(obj["Key"])
    return keys, prefix


def _resolve_entry(file_map: dict) -> str:
    names = list(file_map.keys())
    if "index.html" in names:
        return "index.html"
    html_files = [n for n in names if n.endswith(".html")]
    if html_files:
        return html_files[0]
    return names[0] if names else "index.html"


@app.route("/deploy", methods=["POST"])
def deploy_session():
    """
    Fetch all files for a session from MinIO and register them for serving.
    Body: { "session_id": "<id>" }
    Returns: { "url": "http://..." }
    """
    import re as _re

    data = request.get_json(force=True, silent=True) or {}
    session_id = data.get("session_id", "")

    if not session_id or not _re.match(r'^[\w-]+$', session_id):
        return jsonify({"error": "Invalid session_id"}), 400

    # Idempotent: already deployed
    if session_id in _session_file_cache:
        url = f"{PUBLIC_BASE_URL.rstrip('/')}/apps/{session_id}/"
        return jsonify({"url": url}), 200

    s3 = _s3_client()
    try:
        keys, prefix = _list_session_keys(s3, session_id)
    except Exception as exc:
        return jsonify({"error": f"MinIO list failed: {exc}"}), 502

    if not keys:
        return jsonify({"error": f"No files found for session {session_id}"}), 404

    file_map = {}
    for key in keys:
        relative = key[len(prefix):]
        if not relative:
            continue
        try:
            obj = s3.get_object(Bucket=MINIO_BUCKET, Key=key)
            file_map[relative] = obj["Body"].read()
        except Exception as exc:
            print(f"[WARN] Failed to fetch {key}: {exc}")

    if not file_map:
        return jsonify({"error": "All file downloads failed"}), 502

    _session_file_cache[session_id] = {
        "files": file_map,
        "entry": _resolve_entry(file_map),
    }

    print(f"[deploy] session={session_id} files={len(file_map)} entry={_session_file_cache[session_id]['entry']}")
    url = f"{PUBLIC_BASE_URL.rstrip('/')}/apps/{session_id}/"
    return jsonify({"url": url}), 200


@app.route("/apps/<session_id>/", defaults={"filepath": ""})
@app.route("/apps/<session_id>/<path:filepath>")
def serve_session_app(session_id, filepath):
    """Serve a file from an already-deployed session."""
    session = _session_file_cache.get(session_id)
    if not session:
        return f"Session '{session_id}' not deployed. Trigger preview first.", 404

    # Default to entry file
    resolved = filepath if filepath else session["entry"]
    buf = session["files"].get(resolved)

    # SPA fallback: serve entry for unmatched routes
    if buf is None:
        buf = session["files"].get(session["entry"])
        resolved = session["entry"]

    if buf is None:
        return "File not found", 404

    mime, _ = mimetypes.guess_type(resolved)
    mime = mime or "application/octet-stream"
    from flask import Response
    return Response(buf, mimetype=mime)
