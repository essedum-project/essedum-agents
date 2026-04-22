# Goose Agent — Setup & Connection Guide

A self-hosted AI agent platform built on [goose-vibe-studio-agent](https://github.com/essedum-project/essedum-agents), with a Essedum UI, MinIO-backed app preview, and a dynamic deploy service (vibe-code-builder).

---

## Architecture

```
┌─────────────────┐     chat/SSE      ┌──────────────────┐
│  ESSEDUM UI     │ ────────────────► │  goosed (server) │
│                 │ ◄──────────────── │  (port 3005)     │
└─────────────────┘                   └──────────────────┘
                                               │
                                    writes app files
                                               ▼
                                      ┌──────────────┐
                                      │    MinIO     │
                                      │  (port 9000) │
                                      └──────────────┘
                                               │
                                      pulls on /deploy
                                               ▼
                                   ┌────────────────────┐
                                   │  vibe-code-builder │
                                   │  (port 8080)       │
                                   │  serves preview    │
                                   └────────────────────┘
```

---

## Services & Dockerfiles

| Service | Dockerfile | Port | Purpose |
|---------|-----------|------|---------|
| `goosed` | `Dockerfile.goosed` | `3005` | Core goose agent server |
| vibe-code-builder | `services/vibe-code-builder/Dockerfile` | `8080` | Preview/deploy service (reads from MinIO) |

---

## Quick Start

### 1. Build & run goosed

```bash
# Build the goosed binary first
cargo build --release -p goose-server
cp target/release/goosed goosed-bin

# Build image
docker build -f Dockerfile.goosed -t goosed:latest .

# Run
docker run -d \
  --name goosed \
  -p 3005:3005 \
  -e GOOSE_SERVER__SECRET_KEY=<goose_secret_key> \
  goosed:latest
```

### 2. Run the ESSEDUM UI

```bash
docker build -f Dockerfile.streamlit -t goose-ui:latest .

docker run -d \
  --name goose-ui \
  -p 8501:8501 \
  -e GOOSE_URL=<goose_url> \
  -e GOOSE_SERVER__SECRET_KEY=<secret> \
  goose-ui:latest
```

### 3. Run vibe-code-builder (preview service)

```bash
cd services/vibe-code-builder
docker build -t vibe-code-builder:latest .

docker run -d \
  --name vibe-code-builder \
  -p 8080:8080 \
  -e MINIO_ENDPOINT=<minio_endpoint> \
  -e MINIO_ACCESS_KEY=<minio_access_key> \
  -e MINIO_SECRET_KEY=<minio_secret_key> \
  -e MINIO_BUCKET=apps \
  -e MINIO_PREFIX=goose-apps \
  -e PUBLIC_BASE_URL=http://localhost:8080 \
  vibe-code-builder:latest
```

---

## Environment Variables

### goosed

| Variable | Default | Description |
|----------|---------|-------------|
| `GOOSE_HOST` | `0.0.0.0` | Listen address |
| `GOOSE_PORT` | `3005` | Listen port |
| `GOOSE_TLS` | `false` | Enable TLS |
| `GOOSE_SERVER__SECRET_KEY` | — | API auth key (set this!) |

### ESSEDUM UI

| Variable | Default | Description |
|----------|---------|-------------|
| `GOOSE_URL` | `http://127.0.0.1:3005` | goosed server URL |
| `GOOSE_SERVER__SECRET_KEY` | `<goose_secret_key>` | Must match goosed secret key |
| `GOOSE_WORKING_DIR` | `$PWD` | Working directory for agent sessions |
| `LITELLM_HOST` | `<lite_llm_url>` | LiteLLM proxy URL (pre-filled in UI) |
| `LITELLM_API_KEY` | `<litellm_api_key>` | LiteLLM master key (pre-filled in UI) |

### vibe-code-builder

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `MINIO_ENDPOINT` | ✓ | — | MinIO S3-compatible endpoint |
| `MINIO_ACCESS_KEY` | ✓ | — | MinIO access key |
| `MINIO_SECRET_KEY` | ✓ | — | MinIO secret key |
| `MINIO_BUCKET` | — | `apps` | Bucket name |
| `MINIO_PREFIX` | — | `goose-apps` | Object prefix inside bucket |
| `MINIO_REGION` | — | `us-east-1` | Region (cosmetic for MinIO) |
| `MINIO_TLS_SKIP_VERIFY` | — | — | Set `true` to skip TLS verification |
| `PUBLIC_BASE_URL` | — | `http://localhost:8080` | Base URL used in deploy response |
| `PORT` | — | `8080` | Service listen port |

---

## Connecting to the Agent

### From the ESSEDUM UI

1. Open `http://localhost:8501`
2. In the sidebar, set:
   - **Goose Server URL**: `http://<goosed-host>:3005`
   - **Secret Key**: your `GOOSE_SERVER__SECRET_KEY`
3. Click **Connect** — a green success message confirms the connection
4. Select your **Provider** and **Model**, then click **Configure Provider**
5. Start chatting

### Direct API (no UI)

All requests require the header:
```
X-Secret-Key: <your-secret-key>
```

**Start a session:**
```bash
curl -X POST http://localhost:3005/agent/start \
  -H "X-Secret-Key: <goose_secret_key>" \
  -H "Content-Type: application/json" \
  -d '{"working_dir": "/tmp"}'
```

**Send a message (streaming):**
```bash
curl -N -X POST http://localhost:3005/reply \
  -H "X-Secret-Key: <goose_secret_key>" \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -d '{
    "session_id": "<session-id>",
    "user_message": {
      "role": "user",
      "created": 1713600000,
      "content": [{"type": "text", "text": "Hello!"}],
      "metadata": {"userVisible": true, "agentVisible": true}
    }
  }'
```

**Configure a provider:**
```bash
# 1. Upsert config keys
curl -X POST http://localhost:3005/config/upsert \
  -H "X-Secret-Key: <goose_secret_key>" \
  -H "Content-Type: application/json" \
  -d '{"key": "LITELLM_HOST", "value": "http://litellm:4000", "is_secret": false}'

# 2. Set active provider
curl -X POST http://localhost:3005/agent/update_provider \
  -H "X-Secret-Key: <goose_secret_key>" \
  -H "Content-Type: application/json" \
  -d '{"provider": "litellm", "model": "Llama-3.2-3B-Instruct", "session_id": "<id>"}'
```

---

## Supported Model Providers

| Provider ID | Display Name | Required Config | Default Model |
|-------------|-------------|-----------------|---------------|
| `litellm` | LiteLLM | `LITELLM_API_KEY`, `LITELLM_HOST` | *(any model behind proxy)* |
| `openai` | OpenAI | `OPENAI_API_KEY` | `gpt-4o` |
| `anthropic` | Anthropic | `ANTHROPIC_API_KEY` | `claude-sonnet-4-5` |
| `ollama` | Ollama | `OLLAMA_HOST` (default: `localhost`) | `llama3.2` |
| `google` | Google Gemini | `GOOGLE_API_KEY` | `gemini-2.0-flash` |
| `azure` | Azure OpenAI | `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT_NAME` | `gpt-4o` |
| `bedrock` | AWS Bedrock | `AWS_REGION` + AWS credential chain | `anthropic.claude-3-5-sonnet` |
| `databricks` | Databricks | `DATABRICKS_HOST`, `DATABRICKS_TOKEN` | *(workspace model)* |
| `openrouter` | OpenRouter | `OPENROUTER_API_KEY` | `anthropic/claude-3.5-sonnet` |
| `xai` | xAI / Grok | `XAI_API_KEY` | `grok-2-latest` |
| `gcp_vertex_ai` | GCP Vertex AI | `GCP_PROJECT_ID`, `GCP_LOCATION` | `gemini-2.5-flash` |
| `snowflake` | Snowflake | `SNOWFLAKE_HOST`, `SNOWFLAKE_TOKEN` | *(Cortex model)* |
| `github_copilot` | GitHub Copilot | `GITHUB_COPILOT_TOKEN` (OAuth) | `gpt-4o` |
| `sagemaker_tgi` | AWS SageMaker TGI | `SAGEMAKER_ENDPOINT_NAME` | — |
| `venice` | Venice | `VENICE_API_KEY` | `llama-3.3-70b` |
| `nano-gpt` | NanoGPT | `NANOGPT_API_KEY` | `anthropic/claude-sonnet-4.6` |
| `tetrate` | Tetrate | `TETRATE_API_KEY` | — |
| `local` | Local Inference | *(none — uses llama.cpp)* | *(GGUF model)* |

> **In this deployment, LiteLLM (`litellm`) is the recommended provider** since it proxies to the in-cluster models at `http://litellm.aipns.svc.cluster.local:4000`.

---

## Preview Flow

1. The agent builds app files and uploads them to MinIO under `goose-apps/<session_id>/`
2. Clicking **🚀 Preview App** in the UI calls `POST /sessions/<id>/preview` on goosed
3. goosed calls vibe-code-builder's `POST /deploy` with the session ID
4. vibe-code-builder pulls all files from MinIO and serves them at `http://vibe-code-builder:8080/apps/<session_id>/`
5. The URL is returned to the UI and embedded as an iframe

---

## Key API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/status` | Health check |
| `POST` | `/agent/start` | Create a new agent session |
| `POST` | `/reply` | Send message, stream SSE response |
| `POST` | `/agent/update_provider` | Change provider/model for a session |
| `POST` | `/config/upsert` | Set a config key or secret |
| `POST` | `/config/read` | Read a config value |
| `GET` | `/config/providers` | List all providers + configured status |
| `GET` | `/config/providers/{name}/models` | List live models for a provider |
| `POST` | `/sessions/{id}/preview` | Trigger app preview/deploy |
| `POST` | `/config/custom-providers` | Register a custom provider |

---
