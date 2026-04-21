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
if "preview_url" not in st.session_state:
    st.session_state.preview_url = None
if "preview_loading" not in st.session_state:
    st.session_state.preview_loading = False


def get_headers():
    return {
        "X-Secret-Key": st.session_state.secret_key,
        "Content-Type": "application/json",
    }


GOOSE_URL = st.session_state.goose_url


# ── Helper: start agent and create session ───────────────────────────────────
_PROJECT_STRUCTURE_INSTRUCTIONS = """
When building React or Node.js web applications, always structure the project as follows:

```
project-name/
├── server/          # Express backend
│   ├── package.json
│   ├── index.js
│   ├── Dockerfile
│   └── .gitignore
├── client/          # React frontend
│   ├── package.json
│   ├── src/
│   │   ├── App.js
│   │   └── index.js
│   ├── Dockerfile
│   └── .gitignore
├── docker-compose.yml
└── README.md
```

Rules:
- `server/` contains the Express/Node backend with its own `package.json` and `Dockerfile`
- `client/` contains the React frontend with its own `package.json` and `Dockerfile`
- Each has a separate `Dockerfile` (server on port 5000, client/nginx on port 80)
- Always include a `docker-compose.yml` at the project root
- Never mix frontend and backend code in the same directory
""".strip()


def start_agent_session():
    """Call POST /agent/start to create a session. Returns session_id or None."""
    working_dir = os.environ.get("GOOSE_WORKING_DIR", os.getcwd())
    recipe = {
        "version": "1.0.0",
        "title": "App Dev Session",
        "description": "Enforces standard project structure for React/Node.js apps.",
        "instructions": _PROJECT_STRUCTURE_INSTRUCTIONS,
    }
    with httpx.Client(timeout=30.0, verify=False) as client:
        resp = client.post(
            f"{st.session_state.goose_url}/agent/start",
            json={"working_dir": working_dir, "recipe": recipe},
            headers=get_headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("id")


def call_preview(session_id: str) -> str:
    """Call POST /sessions/{id}/preview and return the deploy URL."""
    with httpx.Client(timeout=300.0, verify=False) as client:
        resp = client.post(
            f"{st.session_state.goose_url}/sessions/{session_id}/preview",
            headers=get_headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("deployUrl") or data.get("deploy_url") or data.get("url", "")


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

    provider = st.selectbox("Provider", ["litellm", "ollama", "openai", "anthropic"], index=0)
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
    else:
        litellm_host = None
        litellm_api_key = None

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

            resp = httpx.post(
                f"{st.session_state.goose_url}/agent/update_provider",
                json={
                    "provider": provider,
                    "model": model,
                    "session_id": st.session_state.session_id,
                },
                headers=get_headers(),
                verify=False,
                timeout=30.0,
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
            st.session_state.preview_url = None
            st.rerun()
        except Exception as e:
            st.error(f"Failed to create session: {e}")

    st.divider()

    # ── Preview button ────────────────────────────────────────────────────
    st.header("Preview")

    if not st.session_state.session_id:
        st.caption("Start a session to enable preview.")
    else:
        if st.button("🚀 Preview App", use_container_width=True, type="primary"):
            st.session_state.preview_loading = True
            st.session_state.preview_url = None
            with st.spinner("Building & deploying app…"):
                try:
                    url = call_preview(st.session_state.session_id)
                    if url:
                        st.session_state.preview_url = url
                        st.success("App deployed!")
                    else:
                        st.error("No URL returned from builder.")
                except httpx.HTTPStatusError as e:
                    st.error(f"Deploy failed ({e.response.status_code}): {e.response.text}")
                except Exception as e:
                    st.error(f"Preview error: {e}")
                finally:
                    st.session_state.preview_loading = False

        if st.session_state.preview_url:
            st.success("✅ App is live")
            st.code(st.session_state.preview_url, language=None)
            if st.button("🔄 Refresh Preview"):
                st.session_state.preview_url = None
                st.rerun()


# ── Layout: chat on left, preview on right when URL is set ──────────────────
if st.session_state.preview_url:
    chat_col, preview_col = st.columns([1, 1], gap="medium")
else:
    chat_col = st.container()
    preview_col = None


# ── Preview panel ─────────────────────────────────────────────────────────────
if preview_col and st.session_state.preview_url:
    with preview_col:
        st.subheader("🖥️ App Preview")
        close_col, link_col = st.columns([1, 3])
        with close_col:
            if st.button("✕ Close"):
                st.session_state.preview_url = None
                st.rerun()
        with link_col:
            st.markdown(
                f'<a href="{st.session_state.preview_url}" target="_blank">'
                f'🔗 Open in new tab</a>',
                unsafe_allow_html=True,
            )
        st.components.v1.iframe(
            src=st.session_state.preview_url,
            height=700,
            scrolling=True,
        )


# ── Render existing messages ─────────────────────────────────────────────────
with chat_col:
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
with chat_col:
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

