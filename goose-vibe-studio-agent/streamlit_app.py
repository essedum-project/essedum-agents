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
def start_agent_session():
    """Call POST /agent/start to create a session. Returns session_id or None."""
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

    provider = st.text_input("Provider", value="ollama")
    model = st.text_input("Model", value="gpt-oss:latest")

    if st.button("Configure Provider"):
        try:
            # Ensure we have a session first
            if not st.session_state.session_id:
                st.session_state.session_id = start_agent_session()
                st.session_state.agent_started = True

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

