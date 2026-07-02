import json
import os
import uuid
from datetime import datetime

import httpx
import streamlit as st

# ── Configuration ────────────────────────────────────────────────────────────
_DEFAULT_URL = os.environ.get("GOOSE_URL", "http://127.0.0.1:3005")
_DEFAULT_KEY = os.environ.get("GOOSE_SERVER__SECRET_KEY", "sk-1234")

# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(page_title="Goose Agent", page_icon="🪿", layout="wide")
st.title("🪿 Goose Agent")

# ── Session state init ───────────────────────────────────────────────────────
if "session_id" not in st.session_state:
    st.session_state.session_id = None
if "messages" not in st.session_state:
    st.session_state.messages = []
if "agent_started" not in st.session_state:
    st.session_state.agent_started = False
if "goose_url" not in st.session_state:
    st.session_state.goose_url = _DEFAULT_URL
if "secret_key" not in st.session_state:
    st.session_state.secret_key = _DEFAULT_KEY


def get_headers():
    return {
        "X-Secret-Key": st.session_state.secret_key,
        "Content-Type": "application/json",
    }


GOOSE_URL = st.session_state.goose_url


# ── Helper: start agent and create session ───────────────────────────────────
_PROJECT_STRUCTURE_INSTRUCTIONS = """
You build web applications, backends, data apps, and MCP servers.
Read the user's request carefully and pick the correct project type below.
Do NOT default to React — match the type to what the user asks for.

## CRITICAL: What files to create

ALWAYS create MULTIPLE files — a source file, a requirements.txt or package.json, AND a Dockerfile.
NEVER create a single HTML file as the output for a backend, MCP server, or Streamlit app.
A single .html file CANNOT be deployed. It will always fail.

The only time you output HTML is for React frontend source files (e.g. frontend/src/App.js which contain JSX, NOT a standalone .html page).

## How to choose the project type
- User asks for a **website, dashboard, UI, web app** → use React (see below)
- User asks for a **data science, ML, analytics** app → use Streamlit
- User asks for an **MCP server, tool server, AI tool provider, agent integration** → use MCP server structure
- User asks for an **API, backend, microservice** with no frontend → use Python FastAPI or Node.js Express

When in doubt between React and another type, ask yourself: does this need a browser UI? If not, do NOT use React and do NOT create HTML files.

---

## React web application (only for UI/dashboard/website requests)

Use this structure **only** when the user explicitly asks for a website, web app, dashboard, or UI.
The preview system handles bundling, JSX transpilation, and React mounting — you just write the source files.

```
project-name/
├── frontend/
│   ├── src/
│   │   ├── App.js              ← root React component (REQUIRED)
│   │   ├── App.css             ← styles for App (optional)
│   │   └── components/
│   │       ├── MyComponent.js  ← one component per file
│   │       └── MyComponent.css ← styles for that component (optional)
│   └── Dockerfile
├── backend/
│   ├── server.js
│   ├── package.json
│   └── Dockerfile
└── docker-compose.yml
```

## Frontend rules (CRITICAL)

### App.js
- Export a **default function named `App`**:
  ```js
  export default function App() {
    const [count, setCount] = React.useState(0);
    return (
      <div className="app">
        <h1>Hello</h1>
        <button onClick={() => setCount(c => c + 1)}>{count}</button>
      </div>
    );
  }
  ```
- Always use `React.useState`, `React.useEffect`, `React.useCallback`, etc.
  (prefix every hook with `React.` — do NOT destructure from an import).
- Return JSX — standard HTML tags in lowercase, React components in PascalCase.

### Component files (`frontend/src/components/MyComponent.js`)
- Each file defines **exactly one component** as a named function (not exported):
  ```js
  function MyComponent({ title, onClick }) {
    return <div className="my-component"><h2>{title}</h2></div>;
  }
  ```
- Do **NOT** use `export`, `import`, or `require` in any frontend file.
  The preview system concatenates all files automatically.

### CSS files
- Put styles in a `.css` file next to the component (e.g. `App.css`, `MyComponent.css`).
- Reference class names with `className="..."` in JSX.
- Do NOT use inline style objects or CSS-in-JS libraries.

### API calls
- Use `fetch('/api/...')` — never hardcode `localhost`, a port, or a full URL.
- Example:
  ```js
  React.useEffect(() => {
    fetch('/api/items')
      .then(r => r.json())
      .then(data => setItems(data))
      .catch(() => setItems([]));
  }, []);
  ```

### What NOT to do (will break the preview)
- Do NOT write any `import` or `require` statement in frontend files.
- Do NOT call `ReactDOM.createRoot` or `ReactDOM.render` — the preview handles mounting.
- Do NOT generate `node_modules/`, `package-lock.json`, `build/`, or `dist/`.
- Do NOT generate `public/index.html` or `src/index.js` — the preview provides its own shell.
- Do NOT use TypeScript (`.ts`, `.tsx`).
- Do NOT reference any CDN URL or external script.
- Do NOT use CSS-in-JS (styled-components, emotion, etc.).

## Backend rules

The backend can be written in **Node.js (Express)** or **Python (FastAPI / Flask)**.
Choose based on what the user asks for. If not specified, default to Node.js.

### Node.js backend (`backend/server.js`)
- Express server listening on `process.env.PORT || 5000`.
- Include CORS middleware (`cors` npm package).
- All API routes prefixed with `/api/`.
- Respond with JSON.

### Python backend (`backend/app.py` or `backend/main.py`)
- Use **FastAPI** (preferred) or **Flask**.
- FastAPI example:
  ```python
  from fastapi import FastAPI
  from fastapi.middleware.cors import CORSMiddleware
  import uvicorn, os

  app = FastAPI()
  app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

  @app.get("/api/items")
  def get_items():
      return [{"id": 1, "name": "Item 1"}]

  if __name__ == "__main__":
      uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
  ```
- Flask example:
  ```python
  from flask import Flask, jsonify
  from flask_cors import CORS
  import os

  app = Flask(__name__)
  CORS(app)

  @app.route("/api/items")
  def get_items():
      return jsonify([{"id": 1, "name": "Item 1"}])

  if __name__ == "__main__":
      app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
  ```

## Streamlit frontend (for data science / ML / analytics projects)

When the user asks for a **data science, ML, analytics, or Python-first** application,
use **Streamlit** as the frontend instead of React.

### Project structure for Streamlit projects
```
project-name/
├── app.py                  ← Streamlit frontend (REQUIRED)
├── requirements.txt        ← ALL Python dependencies
├── Dockerfile
├── docker-compose.yml
├── README.md
└── src/                    ← helper modules (optional)
    ├── data_loader.py
    ├── model.py
    └── utils.py
```

### `app.py` rules
- The Streamlit app is always in `app.py` at the project root.
- Use `st.` prefix for all Streamlit components.
- Load data and models from `src/` modules.
- Example:
  ```python
  import streamlit as st
  import pandas as pd

  st.set_page_config(page_title="My App", layout="wide")
  st.title("My App")

  uploaded = st.file_uploader("Upload CSV", type="csv")
  if uploaded:
      df = pd.read_csv(uploaded)
      st.dataframe(df)
      st.line_chart(df)
  ```
- Always include `st.set_page_config(page_title="...", layout="wide")` as the first Streamlit call.
- Use `st.sidebar` for controls/filters.
- Use `st.columns()` for multi-column layouts.
- Handle errors with `st.error()` / `st.warning()`.
- Show loading state with `st.spinner()`.

### `Dockerfile` for Streamlit projects
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]
```

### `requirements.txt` for Streamlit projects
Always include ALL dependencies. Example:
```
streamlit>=1.32.0
pandas>=2.0.0
numpy>=1.26.0
scikit-learn>=1.4.0
plotly>=5.20.0
# add any other packages used
```

### `docker-compose.yml` for Streamlit projects
```yaml
version: "3.8"
services:
  app:
    build: .
    ports:
      - "8501:8501"
    restart: unless-stopped
```

## MCP server (for AI tool / agent integration projects)

When the user asks for an **MCP server**, **AI tool server**, **agent tool provider**,
or any project that exposes tools/resources for AI agents via the Model Context Protocol,
use **FastMCP** (Python) or **@modelcontextprotocol/sdk** (Node.js).

**CRITICAL: ALWAYS use HTTP transport (SSE or streamable-http). NEVER use stdio transport.**
stdio servers cannot be deployed — they read from stdin and cannot run as a Kubernetes pod.

### Project structure for Python MCP servers (FastMCP)
```
project-name/
├── server.py               ← MCP server entry point (REQUIRED)
├── requirements.txt        ← ALL Python dependencies (REQUIRED)
├── Dockerfile              ← (REQUIRED)
├── docker-compose.yml
└── README.md
```

### `server.py` — Python FastMCP example
```python
from fastmcp import FastMCP
import os

mcp = FastMCP("my-server")

@mcp.tool()
def add(a: int, b: int) -> int:
    '''Add two numbers together.'''
    return a + b

@mcp.resource("data://items")
def get_items() -> list:
    '''Return a list of items.'''
    return [{"id": 1, "name": "Item 1"}, {"id": 2, "name": "Item 2"}]

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    mcp.run(transport="sse", host="0.0.0.0", port=port)
```

### `requirements.txt` for Python MCP servers
```
fastmcp>=2.0.0
```
Add any additional packages your tools need.

### `Dockerfile` for Python MCP servers
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8080
CMD ["python", "server.py"]
```

### `docker-compose.yml` for Python MCP servers
```yaml
version: "3.8"
services:
  mcp:
    build: .
    ports:
      - "8080:8080"
    restart: unless-stopped
```

### Node.js MCP server (`@modelcontextprotocol/sdk`)

### Project structure for Node.js MCP servers
```
project-name/
├── index.js                ← MCP server entry point (REQUIRED)
├── package.json            ← dependencies (REQUIRED)
├── Dockerfile              ← (REQUIRED)
└── README.md
```

### `index.js` — Node.js MCP example
```js
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { SSEServerTransport } from "@modelcontextprotocol/sdk/server/sse.js";
import express from "express";
import { z } from "zod";

const server = new McpServer({ name: "my-server", version: "1.0.0" });

server.tool("add", { a: z.number(), b: z.number() }, async ({ a, b }) => ({
  content: [{ type: "text", text: String(a + b) }],
}));

const app = express();
const port = process.env.PORT || 3000;
const transports = {};

app.get("/sse", async (req, res) => {
  const transport = new SSEServerTransport("/messages", res);
  transports[transport.sessionId] = transport;
  await server.connect(transport);
});

app.post("/messages", async (req, res) => {
  const transport = transports[req.query.sessionId];
  if (transport) await transport.handlePostMessage(req, res);
  else res.status(404).send("No session");
});

app.listen(port, "0.0.0.0", () => console.log(`MCP server on port ${port}`));
```

### `package.json` for Node.js MCP servers
```json
{
  "name": "my-mcp-server",
  "version": "1.0.0",
  "type": "module",
  "scripts": { "start": "node index.js" },
  "dependencies": {
    "@modelcontextprotocol/sdk": "^1.0.0",
    "express": "^4.18.0",
    "zod": "^3.22.0"
  }
}
```

### `Dockerfile` for Node.js MCP servers
```dockerfile
FROM node:18-slim
WORKDIR /app
COPY package*.json ./
RUN npm install --omit=dev
COPY . .
EXPOSE 3000
CMD ["node", "index.js"]
```

### MCP server rules (CRITICAL)
- **ALWAYS** use HTTP transport: `mcp.run(transport="sse", ...)` in Python or `SSEServerTransport` in Node.js.
- **NEVER** use `mcp.run()` with no args (defaults to stdio) or `transport="stdio"`.
- **ALWAYS** include `EXPOSE <port>` in the Dockerfile matching the port in the server code.
- **ALWAYS** bind to `0.0.0.0` (not `127.0.0.1` or `localhost`) — required for container networking.
- The SSE endpoint path must be `/sse` (FastMCP default) for the proxy to work correctly.
- Use `@mcp.tool()` decorator for callable functions, `@mcp.resource()` for data endpoints.
- Port: default **8080** for Python (FastMCP), **3000** for Node.js.
- **NEVER create a single HTML file** for an MCP server. Always create server.py + requirements.txt + Dockerfile.

### Complete example: File System MCP Server

When asked for a **file system MCP server** (list_files, read_file, write_file), create exactly these files:

**`server.py`**
```python
from fastmcp import FastMCP
import os, pathlib

mcp = FastMCP("filesystem-server")
SAFE_ROOT = pathlib.Path(os.environ.get("SAFE_ROOT", "/tmp/workspace")).resolve()
SAFE_ROOT.mkdir(parents=True, exist_ok=True)

def _safe_path(rel: str) -> pathlib.Path:
    p = (SAFE_ROOT / rel).resolve()
    if not str(p).startswith(str(SAFE_ROOT)):
        raise ValueError("Path escapes safe root")
    return p

@mcp.tool()
def list_files(subdir: str = "") -> list:
    '''List files in the safe workspace directory.'''
    target = _safe_path(subdir)
    if not target.exists():
        return []
    return [str(f.relative_to(SAFE_ROOT)) for f in target.rglob("*") if f.is_file()]

@mcp.tool()
def read_file(path: str) -> str:
    '''Read a file from the safe workspace.'''
    return _safe_path(path).read_text(encoding="utf-8")

@mcp.tool()
def write_file(path: str, content: str) -> str:
    '''Write content to a file in the safe workspace.'''
    p = _safe_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Written {len(content)} bytes to {path}"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    mcp.run(transport="sse", host="0.0.0.0", port=port)
```

**`requirements.txt`**
```
fastmcp>=2.0.0
```

**`Dockerfile`**
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /tmp/workspace
EXPOSE 8080
CMD ["python", "server.py"]
```

## Docker deployment (REQUIRED for every project)

Every project MUST include complete Docker configuration so it can be deployed anywhere.

### `backend/Dockerfile` — Node.js
```dockerfile
FROM node:18-alpine
WORKDIR /app
COPY package*.json ./
RUN npm install --production
COPY . .
EXPOSE 5000
CMD ["node", "server.js"]
```

### `backend/Dockerfile` — Python (FastAPI)
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 5000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5000"]
```

### `backend/Dockerfile` — Python (Flask)
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 5000
CMD ["python", "app.py"]
```

### `backend/requirements.txt` — Python dependencies (include ALL used packages)
```
fastapi==0.110.0
uvicorn==0.29.0
# or for Flask:
flask==3.0.0
flask-cors==4.0.0
```

### `frontend/Dockerfile`
```dockerfile
FROM node:18-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/build /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
```

### `docker-compose.yml` (REQUIRED)
```yaml
version: "3.8"
services:
  backend:
    build: ./backend
    ports:
      - "5000:5000"
    environment:
      - PORT=5000
    restart: unless-stopped

  frontend:
    build: ./frontend
    ports:
      - "3000:80"
    depends_on:
      - backend
    restart: unless-stopped
```

### Deployment instructions (`README.md`)
Always generate a `README.md` with:
```markdown
## Running with Docker

### Prerequisites
- Docker and Docker Compose installed

### Start all services
```bash
docker-compose up --build
```

### Access the app
- Frontend: http://localhost:3000
- Backend API: http://localhost:5000/api/

### Stop services
```bash
docker-compose down
```
```

### Rules for Docker files
- Always pin base image versions (e.g. `node:18-alpine`, `python:3.11-slim`).
- Always include `requirements.txt` for Python backends with ALL dependencies.
- Always include `package.json` with all npm dependencies listed.
- Never bake secrets or API keys into Dockerfiles — use environment variables.
- The `docker-compose.yml` must wire `frontend` → `backend` via service name DNS
  (e.g. `REACT_APP_API_URL=http://backend:5000` as an environment variable).
""".strip()


def start_agent_session():
    """Call POST /agent/start to create a session. Returns session_id or None."""
    working_dir = os.environ.get("GOOSE_WORKING_DIR", os.getcwd())
    recipe = {
        "version": "1.0.0",
        "title": "App Dev Session",
        "description": "Builds web apps, backends, Streamlit apps, and MCP servers. Detects the correct project type from the user request.",
        "instructions": _PROJECT_STRUCTURE_INSTRUCTIONS,
    }
    with httpx.Client(timeout=30.0, verify=False) as client:
        resp = client.post(
            f"{st.session_state.goose_url}/agent/start",
            json={"working_dir": os.getcwd()},
            headers=get_headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("id")


# ── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Connection")

    new_url = st.text_input("Goose Server URL", value=st.session_state.goose_url)
    new_key = st.text_input("Secret Key", value=st.session_state.secret_key, type="password")

    if st.button("Connect"):
        st.session_state.goose_url = new_url.rstrip("/")
        st.session_state.secret_key = new_key
        st.session_state.session_id = None
        st.session_state.messages = []
        st.session_state.agent_started = False
        try:
            with httpx.Client(timeout=10.0, verify=False) as client:
                r = client.get(
                    f"{st.session_state.goose_url}/status",
                    headers=get_headers(),
                )
            if r.status_code == 200:
                st.success(f"Connected to {st.session_state.goose_url}")
            else:
                st.error(f"Server returned {r.status_code}")
        except Exception as e:
            st.error(f"Cannot reach server: {e}")

    st.divider()
    st.header("Settings")

    provider = st.selectbox("Provider", ["litellm", "ollama", "openai", "anthropic", "azure_openai"], index=0)
    model = st.text_input("Model", value="Llama-3.2-3B-Instruct")

    # LiteLLM-specific fields (shown only when litellm is selected)
    if provider == "litellm":
        litellm_host = st.text_input(
            "LiteLLM Host",
            value=os.environ.get("LITELLM_HOST", "http://litellm.aipns.svc.cluster.local:4000"),
            help="Base URL only — no path. e.g. http://litellm.aipns.svc.cluster.local:4000",
        )
        # Strip any accidental path the user may have included (e.g. /chat/completions)
        if litellm_host:
            from urllib.parse import urlparse as _up
            _p = _up(litellm_host)
            litellm_host = f"{_p.scheme}://{_p.netloc}"
        litellm_api_key = st.text_input(
            "LiteLLM API Key",
            value=os.environ.get("LITELLM_API_KEY", "sk-1234"),
            type="password",
            help="Master key for LiteLLM",
        )
        azure_endpoint = None
        azure_deployment = None
        azure_api_key = None
        azure_api_version = None
    elif provider == "azure_openai":
        litellm_host = None
        litellm_api_key = None
        azure_endpoint = st.text_input(
            "Azure OpenAI Endpoint",
            value=os.environ.get("AZURE_OPENAI_ENDPOINT", "https://aiplatform-openai.openai.azure.com"),
            help="Your Azure OpenAI resource endpoint URL",
        )
        azure_deployment = st.text_input(
            "Deployment Name",
            value=os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4o-mini"),
            help="The deployment name (model) in your Azure OpenAI resource",
        )
        azure_api_key = st.text_input(
            "Azure OpenAI API Key",
            value=os.environ.get("AZURE_OPENAI_API_KEY", ""),
            type="password",
            help="API key for your Azure OpenAI resource",
        )
        azure_api_version = st.text_input(
            "API Version",
            value=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21"),
            help="Azure OpenAI API version, e.g. 2024-10-21",
        )
        # When azure_openai is selected, the model follows the deployment name
        model = azure_deployment
    else:
        litellm_host = None
        litellm_api_key = None
        azure_endpoint = None
        azure_deployment = None
        azure_api_key = None
        azure_api_version = None

    if st.button("Configure Provider"):
        try:
            # Ensure we have a session first
            if not st.session_state.session_id:
                st.session_state.session_id = start_agent_session()
                st.session_state.agent_started = True

            # Push LiteLLM connection config into goose config store before
            # creating the provider so from_env() picks them up
            if provider == "litellm" and litellm_host:
                for key, value, is_secret in [
                    ("LITELLM_HOST", litellm_host, False),
                    ("LITELLM_API_KEY", litellm_api_key or "", True),
                ]:
                    httpx.post(
                        f"{st.session_state.goose_url}/config/upsert",
                        json={"key": key, "value": value, "is_secret": is_secret},
                        headers=get_headers(),
                        verify=False,
                        timeout=10.0,
                    ).raise_for_status()

            # Push Azure OpenAI config into goose config store
            if provider == "azure_openai" and azure_endpoint:
                for key, value, is_secret in [
                    ("AZURE_OPENAI_ENDPOINT", azure_endpoint, False),
                    ("AZURE_OPENAI_DEPLOYMENT_NAME", azure_deployment or "", False),
                    ("AZURE_OPENAI_API_VERSION", azure_api_version or "2024-10-21", False),
                    ("AZURE_OPENAI_API_KEY", azure_api_key or "", True),
                ]:
                    httpx.post(
                        f"{st.session_state.goose_url}/config/upsert",
                        json={"key": key, "value": value, "is_secret": is_secret},
                        headers=get_headers(),
                        verify=False,
                        timeout=10.0,
                    ).raise_for_status()

            resp = httpx.post(
                f"{st.session_state.goose_url}/agent/update_provider",
                json={
                    "provider": provider,
                    "model": model,
                    "session_id": st.session_state.session_id,
                },
                headers=get_headers(),
                verify=False,
            )
            resp.raise_for_status()
            st.success(f"Provider set to {provider}/{model}")
        except Exception as e:
            st.error(f"Failed to configure provider: {e}")

    st.divider()

    if st.session_state.session_id:
        st.caption(f"Session: `{st.session_state.session_id}`")

    if st.button("New Session"):
        try:
            st.session_state.session_id = start_agent_session()
            st.session_state.agent_started = True
            st.session_state.messages = []
            st.rerun()
        except Exception as e:
            st.error(f"Failed to create session: {e}")


# ── Render existing messages ─────────────────────────────────────────────────
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])


# ── Helper: stream goose response ───────────────────────────────────────────
def stream_goose_response(user_text: str):
    """Send a message to goose /reply and yield assistant text chunks."""
    # Ensure agent session exists
    if not st.session_state.session_id:
        st.session_state.session_id = start_agent_session()
        st.session_state.agent_started = True

    message = {
        "role": "user",
        "created": int(datetime.now().timestamp()),
        "content": [{"type": "text", "text": user_text}],
        "metadata": {"userVisible": True, "agentVisible": True},
    }

    payload = {
        "user_message": message,
        "session_id": st.session_state.session_id,
    }

    with httpx.Client(timeout=120.0, verify=False) as client:
        with client.stream(
            "POST",
            f"{st.session_state.goose_url}/reply",
            json=payload,
            headers={**get_headers(), "Accept": "text/event-stream"},
        ) as stream:
            for line in stream.iter_lines():
                if not line:
                    continue

                if line.startswith("data: "):
                    line = line[6:]

                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                event_type = event.get("type")

                if event_type == "Finish":
                    return

                if event_type == "Ping":
                    continue

                if event_type == "Error":
                    yield f"\n\n**Error:** {event.get('error', 'Unknown error')}"
                    return

                if event_type == "Message":
                    msg = event.get("message", {})
                    for content in msg.get("content", []):
                        ctype = content.get("type")
                        if ctype == "text":
                            yield content["text"]
                        elif ctype == "thinking":
                            # skip internal model thinking
                            pass
                        elif ctype == "toolRequest":
                            tool_call = content.get("toolCall", {})
                            value = tool_call.get("value", {})
                            tool_name = value.get("name", content.get("id", "unknown"))
                            yield f"\n\n`🔧 Tool: {tool_name}`\n\n"
                        elif ctype == "toolResponse":
                            tool_result = content.get("toolResult", {})
                            if tool_result.get("isError"):
                                yield f"\n\n`❌ Tool error`\n\n"


# ── Chat input ───────────────────────────────────────────────────────────────
if prompt := st.chat_input("Ask Goose anything..."):
    # Show user message
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Stream assistant response
    with st.chat_message("assistant"):
        try:
            full_response = st.write_stream(stream_goose_response(prompt))
        except httpx.ConnectError:
            full_response = "**Error:** Cannot connect to goose server. Make sure `goosed agent` is running on " + st.session_state.goose_url
            st.error(full_response)
        except Exception as e:
            full_response = f"**Error:** {e}"
            st.error(full_response)

    st.session_state.messages.append({"role": "assistant", "content": full_response})

