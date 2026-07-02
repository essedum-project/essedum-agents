# builder-kit-2 additions: session preview + CDN served from MinIO
# React/Babel/Axios bundles stored at cdn/ prefix in MinIO bucket.

import mimetypes
import re as _re

_session_file_cache = {}
_cdn_cache          = {}
_deployed_apps      = {}  # session_id -> {job_id, deploy_name, status, url}

DEPLOYER_URL = os.getenv("DEPLOYER_URL", "http://builder-service")

MINIO_BUCKET    = os.getenv("MINIO_BUCKET", "aiptest")
MINIO_PREFIX    = os.getenv("MINIO_PREFIX", "goose-apps")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://essedum.az.ad.idemo-ppc.com")

_CDN_FILES = {"react.js", "react-dom.js", "babel.js", "axios.js"}


def _s3_client():
    return boto3.client(
        "s3",
        endpoint_url=os.getenv("MINIO_ENDPOINT", "http://minio-service:9000"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "minioadmin"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "minioadmin123"),
        region_name=os.getenv("AWS_REGION", "us-east-1"),
    )


def _list_session_keys(s3, session_id):
    prefix = f"{MINIO_PREFIX}/{session_id}/"
    keys = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=MINIO_BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []):
            keys.append(obj["Key"])
    return keys, prefix


# ── CDN route: serve React/Babel/Axios from MinIO ────────────────────────────

@app.route("/cdn/<filename>")
def serve_cdn(filename):
    from flask import Response
    if filename not in _CDN_FILES:
        return "Not found", 404
    if filename in _cdn_cache:
        return Response(_cdn_cache[filename], mimetype="application/javascript",
                        headers={"Cache-Control": "public, max-age=86400"})
    try:
        s3 = _s3_client()
        obj = s3.get_object(Bucket=MINIO_BUCKET, Key=f"cdn/{filename}")
        data = obj["Body"].read()
        _cdn_cache[filename] = data
        return Response(data, mimetype="application/javascript",
                        headers={"Cache-Control": "public, max-age=86400"})
    except Exception as exc:
        print(f"[cdn] MinIO fetch failed for {filename}: {exc}")
        return f"CDN file unavailable: {filename}", 502


# ── React/JSX preview generator ──────────────────────────────────────────────

_SKIP_DIRS  = {"server", "backend", "node_modules", "build", "dist"}
_SKIP_FILES = {"index.js", "index.jsx", "reportwebvitals.js", "setupTests.js",
               "reportwebvitals.js", "setuptests.js"}

_CDN_BASE = "/apps/vibe-code-builder-service/cdn"


def _unescape(raw_bytes):
    """Files are stored with literal \\n instead of real newlines — fix that."""
    text = raw_bytes.decode("utf-8", errors="replace")
    # Only unescape if the file has no real newlines but has literal \n sequences
    if "\n" not in text and "\\n" in text:
        text = text.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"').replace("\\'", "'")
    return text


def _is_skip_file(path):
    parts = path.lower().split("/")
    return bool(_SKIP_DIRS & set(parts)) or parts[-1] in _SKIP_FILES


def _find_app_file(js_files):
    # Prefer the deepest match on the standard path patterns
    for suffix in ["frontend/src/App.js", "frontend/src/App.jsx",
                   "client/src/App.js",   "client/src/App.jsx",
                   "src/App.js",          "src/App.jsx"]:
        match = next((k for k in js_files if k.endswith("/" + suffix) or k == suffix), None)
        if match:
            return match
    # Fallback: any App.js not in a skip directory
    return next((k for k in js_files if k.split("/")[-1].lower() in ("app.js", "app.jsx")), None)


def _strip_module_syntax(src):
    """Remove ES module import/export so code runs inside a plain <script>."""
    src = _re.sub(r'^[ \t]*import\b[^\n]*\n?', '', src, flags=_re.MULTILINE)
    src = _re.sub(r'\bexport\s+default\s+(function|class)(\s)', r'\1\2', src)
    src = _re.sub(r'^\s*export\s+default\s+\w[\w.]*\s*;?\s*$', '', src, flags=_re.MULTILINE)
    src = _re.sub(r'^([ \t]*)export\s+(const|let|var|function|class)\s+', r'\1\2 ', src, flags=_re.MULTILINE)
    src = _re.sub(r'^\s*export\s+\{[^}]*\}(?:\s+from\s+[\'"][^\'"]+[\'"])?\s*;?\s*$', '', src, flags=_re.MULTILINE)
    return src.strip()


def _collect_component_files(js_files, app_file):
    visited, ordered = set(), []

    def _walk(fp):
        if fp in visited:
            return
        visited.add(fp)
        src = _unescape(js_files[fp])
        base = "/".join(fp.split("/")[:-1]) + "/"
        # Match: import X from './path'  |  import './path'  |  require('./path')
        for rel in _re.findall(r"""(?:from|import)\s+['\"](\.{1,2}/[^'\"]+)['\"]|require\s*\(\s*['\"](\.{1,2}/[^'\"]+)['\"]""", src):
            rel = rel[0] or rel[1]  # one of the two groups matched
            parts = (base + rel).split("/")
            resolved = []
            for p in parts:
                if p == ".." and resolved:
                    resolved.pop()
                elif p not in (".", ""):
                    resolved.append(p)
            candidate = "/".join(resolved)
            for ext in ("", ".js", ".jsx"):
                if candidate + ext in js_files:
                    _walk(candidate + ext)
                    break
        ordered.append(_strip_module_syntax(src))

    _walk(app_file)
    return ordered


def _generate_preview(file_map):
    js_files = {k: v for k, v in file_map.items()
                if k.endswith((".js", ".jsx"))
                and not k.endswith((".test.js", ".test.jsx", ".spec.js"))
                and not _is_skip_file(k)}

    app_file = _find_app_file(js_files)
    if not app_file:
        return None

    snippets = _collect_component_files(js_files, app_file)

    css_parts = []
    for k, v in file_map.items():
        if k.endswith(".css") and not _is_skip_file(k):
            css_parts.append(_unescape(v))
    css = "\n".join(css_parts)
    css_tag = f"<style>\n{css}\n</style>" if css.strip() else ""

    app_code = "\n\n".join(snippets)
    # Minimal shim for react-router-dom: renders children of the default route only
    router_shim = """
// react-router-dom shim (renders default / route children only)
var ReactRouterDOM = (function(){
  function Route(props){ return props.path==='/'||props.exact ? (props.component ? React.createElement(props.component) : (props.children||null)) : null; }
  function Switch(props){ var ch=React.Children.toArray(props.children); var m=ch.find(function(c){return c.props&&(c.props.path==='/'||c.props.exact);}); return m||ch[0]||null; }
  function Router(props){ return props.children; }
  function BrowserRouter(props){ return props.children; }
  function HashRouter(props){ return props.children; }
  function Link(props){ return React.createElement('a',{href:props.to||'#'},props.children); }
  function NavLink(props){ return React.createElement('a',{href:props.to||'#',style:{fontWeight:'bold'}},props.children); }
  function Redirect(){ return null; }
  function useHistory(){ return {push:function(){},replace:function(){},goBack:function(){}}; }
  function useLocation(){ return {pathname:'/',search:'',hash:''}; }
  function useParams(){ return {}; }
  return {Route,Switch,Router,BrowserRouter,HashRouter,Link,NavLink,Redirect,useHistory,useLocation,useParams};
})();
var { Route, Switch, Router, BrowserRouter, HashRouter, Link, NavLink, Redirect, useHistory, useLocation, useParams } = ReactRouterDOM;
"""
    hooks_shim = "const { useState, useEffect, useCallback, useRef, useMemo, useContext, createContext, Fragment } = React;"

    script_body = f"""{hooks_shim}
{router_shim}
{app_code}

ReactDOM.createRoot(document.getElementById('root')).render(React.createElement(App));"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>App Preview</title>
  <script src="{_CDN_BASE}/react.js"></script>
  <script src="{_CDN_BASE}/react-dom.js"></script>
  <script src="{_CDN_BASE}/axios.js"></script>
  <script src="{_CDN_BASE}/babel.js"></script>
  <style>*,*::before,*::after{{box-sizing:border-box}}body{{margin:0;font-family:system-ui,sans-serif}}#root{{min-height:100vh;padding:16px}}</style>
  {css_tag}
</head>
<body>
  <div id="root"></div>
  <script type="text/babel" data-presets="react">
{script_body}
  </script>
</body>
</html>""".encode("utf-8")


def _find_streamlit_app(file_map):
    """Return the key of the Streamlit app.py if this is a Streamlit project."""
    # Must import streamlit and use st. calls
    for k, v in file_map.items():
        if not k.endswith(".py"):
            continue
        text = _unescape(v)
        if "import streamlit" in text and "st." in text:
            return k
    return None


def _find_mcp_server(file_map):
    """Return (key, transport_type) if this is an MCP server project, else None.

    Detects:
      - Python: fastmcp import + mcp.run() / @mcp.tool / FastMCP()
      - Python: from mcp.server import + http transport usage
      - Node.js: @modelcontextprotocol/sdk in package.json
    transport_type is "sse", "streamable-http", or "http" (generic).
    """
    # --- Python detection ---
    for k, v in file_map.items():
        if not k.endswith(".py"):
            continue
        text = _unescape(v)
        is_fastmcp = ("fastmcp" in text or "FastMCP" in text) and (
            "mcp.run(" in text or "@mcp.tool" in text or "FastMCP(" in text
        )
        is_mcp_sdk = "from mcp" in text and "server" in text
        if not (is_fastmcp or is_mcp_sdk):
            continue
        # Determine transport type
        transport = "sse"
        if "streamable-http" in text or "streamable_http" in text:
            transport = "streamable-http"
        elif "sse" in text.lower():
            transport = "sse"
        return (k, transport)

    # --- Node.js detection ---
    for k, v in file_map.items():
        if k.split("/")[-1] != "package.json":
            continue
        text = _unescape(v)
        if "@modelcontextprotocol/sdk" in text:
            transport = "streamable-http" if "streamableHttp" in text else "sse"
            # Find the main JS entry point
            entry = None
            for ek in file_map.keys():
                if ek.endswith((".js", ".ts")) and not ek.endswith(".config.js"):
                    entry = ek
                    break
            return (entry or k, transport)

    return None


def _generate_mcp_info_page(file_map, mcp_info, session_id=None):
    """Render a preview page for MCP server projects."""
    import html as _html, json as _json

    mcp_key, transport_type = mcp_info

    def _get(path):
        for k, v in file_map.items():
            if k.endswith("/" + path) or k == path:
                return _unescape(v)
        return None

    server_src = _unescape(file_map[mcp_key]) if mcp_key and mcp_key in file_map else ""
    readme     = _get("README.md")
    reqs       = _get("requirements.txt")
    pkg_json   = _get("package.json")
    dockerf    = _get("Dockerfile")

    def _section(title, content, lang=""):
        if not content:
            return ""
        escaped = _html.escape(content[:5000])
        trunc = "<p><em>(truncated)</em></p>" if len(content) > 5000 else ""
        return f'<h2>{title}</h2><pre><code class="language-{lang}">{escaped}</code></pre>{trunc}'

    # Detect tools from @mcp.tool() / server.tool() decorators
    tools = []
    for line in server_src.splitlines():
        s = line.strip()
        if "@mcp.tool" in s or "server.tool(" in s:
            # next non-decorator line is usually "def name(...)" or already inline
            tools.append(s)
    tools_html = ""
    if tools:
        items = "".join(
            f'<li><code>{_html.escape(t)}</code></li>' for t in tools[:20]
        )
        tools_html = f'<h2>Detected tools</h2><ul style="line-height:2">{items}</ul>'

    # Detect port
    port = 8080
    for line in server_src.splitlines():
        m = _re.search(r'port\s*[=:,]\s*(\d{2,5})', line)
        if m:
            port = int(m.group(1))
            break
    if reqs and "fastmcp" in reqs:
        lang = "python"
    elif pkg_json:
        lang = "javascript"
        port = port if port != 8080 else 3000
    else:
        lang = "python"

    server_name = mcp_key.split("/")[-1] if mcp_key else "mcp-server"

    # Example client connection snippet
    if lang == "python":
        client_example = f"""from fastmcp.client import FastMCPClient
import asyncio

async def main():
    async with FastMCPClient("http://<DEPLOYED_URL>/sse") as client:
        tools = await client.list_tools()
        print(tools)
        result = await client.call_tool("your_tool", {{"param": "value"}})
        print(result)

asyncio.run(main())"""
    else:
        client_example = f"""import {{ Client }} from "@modelcontextprotocol/sdk/client/index.js";
import {{ SSEClientTransport }} from "@modelcontextprotocol/sdk/client/sse.js";

const transport = new SSEClientTransport(new URL("http://<DEPLOYED_URL>/sse"));
const client = new Client({{ name: "my-client", version: "1.0.0" }});
await client.connect(transport);
const tools = await client.listTools();
console.log(tools);"""

    transport_badge_color = "#0288d1" if transport_type == "sse" else "#7b1fa2"
    transport_label = transport_type.upper()

    file_list = "\n".join(
        f'<li><code>{_html.escape(k)}</code></li>'
        for k in sorted(file_map.keys())
        if not k.split("/")[-1].startswith(".")
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>MCP Server Preview</title>
  <style>
    *{{box-sizing:border-box}}
    body{{font-family:system-ui,sans-serif;margin:0;background:#f5f5f5;color:#1a1a2e}}
    .header{{background:#1a1a2e;color:#fff;padding:20px 32px;display:flex;align-items:center;gap:16px}}
    .header h1{{margin:0;font-size:1.6rem}}
    .badge{{background:rgba(255,255,255,.2);padding:4px 12px;border-radius:20px;font-size:13px;font-weight:600}}
    .transport-badge{{background:{transport_badge_color};padding:4px 12px;border-radius:20px;font-size:13px;font-weight:600}}
    .content{{max-width:960px;margin:0 auto;padding:24px 32px}}
    .info-box{{background:#e3f2fd;border:1px solid #90caf9;border-radius:10px;padding:16px 20px;margin-bottom:28px}}
    .info-box h2{{margin:0 0 8px;color:#0277bd;font-size:1rem}}
    .info-box p{{margin:4px 0;font-size:14px}}
    h2{{color:#1a1a2e;margin-top:32px;border-bottom:1px solid #d1d1d1;padding-bottom:6px}}
    pre{{background:#1e1e1e;color:#d4d4d4;padding:16px;border-radius:8px;overflow-x:auto;font-size:13px;line-height:1.6;margin:0}}
    ul{{background:#fff;border:1px solid #e0e0e0;border-radius:8px;padding:10px 10px 10px 24px;line-height:2;margin:0}}
    .warn{{background:#fff3e0;border:1px solid #ffcc80;border-radius:8px;padding:12px 16px;margin-bottom:20px;font-size:14px}}
  </style>
</head>
<body>
  <div class="header">
    <svg width="32" height="32" viewBox="0 0 32 32" fill="none"><rect width="32" height="32" rx="6" fill="#0288d1"/><path d="M8 16h16M16 8v16" stroke="white" stroke-width="3" stroke-linecap="round"/></svg>
    <h1>MCP Server — {_html.escape(server_name)}</h1>
    <span class="badge">Model Context Protocol</span>
    <span class="transport-badge">{transport_label} transport</span>
  </div>
  <div class="content">
    <div class="info-box">
      <h2>&#128268; MCP Server Details</h2>
      <p><strong>Entry point:</strong> <code>{_html.escape(mcp_key or "unknown")}</code></p>
      <p><strong>Transport:</strong> {transport_label}</p>
      <p><strong>Port:</strong> {port}</p>
      <p><strong>SSE endpoint:</strong> <code>/sse</code></p>
    </div>
    <div class="warn">
      &#9888; After deploying, replace <strong>&lt;DEPLOYED_URL&gt;</strong> in the client snippet with the URL shown above.
    </div>
    {_launch_button_html(session_id) if session_id else ''}
    {tools_html}
    <h2>Client connection example</h2>
    <pre><code class="language-{lang}">{_html.escape(client_example)}</code></pre>
    {_section(server_name + " — server source", server_src, lang)}
    {_section("requirements.txt", reqs, "text")}
    {_section("package.json", pkg_json, "json")}
    {_section("Dockerfile", dockerf, "dockerfile")}
    {('<h2>README</h2><pre style="white-space:pre-wrap;background:#fff;border:1px solid #e0e0e0;color:#1a1a2e">' + _html.escape((readme or "")[:3000]) + '</pre>') if readme else ""}
    <h2>Project files</h2>
    <ul>{file_list}</ul>
  </div>
</body>
</html>""".encode("utf-8")


def _generate_streamlit_info_page(file_map, app_key, session_id=None):
    """Render a preview page for Streamlit projects showing code + run instructions."""
    import html as _html

    def _get(path):
        for k, v in file_map.items():
            if k.endswith("/" + path) or k == path:
                return _unescape(v)
        return None

    app_src   = _unescape(file_map[app_key])
    readme    = _get("README.md")
    reqs      = _get("requirements.txt")
    dockerf   = _get("Dockerfile")
    compose   = _get("docker-compose.yml")

    def _section(title, content, lang=""):
        if not content:
            return ""
        escaped = _html.escape(content[:5000])
        trunc = "<p><em>(truncated)</em></p>" if len(content) > 5000 else ""
        return f'<h2>{title}</h2><pre><code class="language-{lang}">{escaped}</code></pre>{trunc}'

    file_list = "\n".join(
        f'<li><code>{_html.escape(k)}</code></li>'
        for k in sorted(file_map.keys())
        if not k.split("/")[-1].startswith(".")
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>Streamlit App Preview</title>
  <style>
    *{{box-sizing:border-box}}
    body{{font-family:system-ui,sans-serif;margin:0;background:#f0f2f6;color:#262730}}
    .header{{background:#ff4b4b;color:#fff;padding:20px 32px;display:flex;align-items:center;gap:16px}}
    .header h1{{margin:0;font-size:1.6rem}}
    .badge{{background:rgba(255,255,255,.25);padding:4px 12px;border-radius:20px;font-size:13px;font-weight:600}}
    .content{{max-width:960px;margin:0 auto;padding:24px 32px}}
    .run-box{{background:#e8f5e9;border:1px solid #a5d6a7;border-radius:10px;padding:16px 20px;margin-bottom:28px}}
    .run-box h2{{margin:0 0 10px;color:#2e7d32;font-size:1rem}}
    .run-box code{{background:#fff;border:1px solid #c8e6c9;padding:6px 12px;border-radius:6px;display:block;margin:4px 0;font-size:14px}}
    h2{{color:#262730;margin-top:32px;border-bottom:1px solid #d1d1d1;padding-bottom:6px}}
    pre{{background:#1e1e1e;color:#d4d4d4;padding:16px;border-radius:8px;overflow-x:auto;font-size:13px;line-height:1.6;margin:0}}
    ul{{background:#fff;border:1px solid #e0e0e0;border-radius:8px;padding:10px 10px 10px 24px;line-height:2;margin:0}}
    .tabs{{display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap}}
    .tab{{background:#fff;border:1px solid #d1d1d1;border-radius:6px;padding:6px 16px;cursor:pointer;font-size:14px}}
    .tab.active{{background:#ff4b4b;color:#fff;border-color:#ff4b4b}}
  </style>
</head>
<body>
  <div class="header">
    <svg width="32" height="32" viewBox="0 0 32 32" fill="white"><path d="M4 24l12-16 12 16H4z"/></svg>
    <h1>Streamlit App</h1>
    <span class="badge">Python Frontend</span>
  </div>
  <div class="content">
    <div class="run-box">
      <h2>&#9654; How to run this app</h2>
      <code>pip install -r requirements.txt</code>
      <code>streamlit run {_html.escape(app_key.split("/")[-1])}</code>
      <br/>
      <strong>Or with Docker:</strong>
      <code>docker-compose up --build</code>
      <p style="margin:8px 0 0;font-size:13px;color:#388e3c">Then open <strong>http://localhost:8501</strong> in your browser.</p>
    </div>
    {_launch_button_html(session_id) if session_id else ''}
    {_section("app.py — Streamlit frontend", app_src, "python")}
    {_section("requirements.txt", reqs, "text")}
    {_section("Dockerfile", dockerf, "dockerfile")}
    {_section("docker-compose.yml", compose, "yaml")}
    {('<h2>README</h2><pre style="white-space:pre-wrap;background:#fff;border:1px solid #e0e0e0;color:#262730">' + _html.escape((readme or "")[:3000]) + '</pre>') if readme else ""}
    <h2>Project files</h2>
    <ul>{file_list}</ul>
  </div>
</body>
</html>""".encode("utf-8")


# ── Session zip helper ────────────────────────────────────────────────────────

def _make_session_zip(file_map):
    """Bundle all session source files into a zip (bytes) for the deployer."""
    import io, zipfile
    _TEXT_EXTS = {
        '.py', '.js', '.ts', '.jsx', '.tsx', '.json', '.md', '.txt',
        '.yaml', '.yml', '.toml', '.cfg', '.ini', '.env', '.sh',
        '.html', '.css', '.dockerfile', '.gitignore', '.dockerignore',
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for path, raw in file_map.items():
            if path.startswith('_'):        # skip _preview.html etc.
                continue
            fname = path.split('/')[-1]
            ext   = ('.' + fname.rsplit('.', 1)[-1].lower()) if '.' in fname else ''
            if ext in _TEXT_EXTS or not ext:
                content = _unescape(raw).encode('utf-8', errors='replace')
            else:
                content = raw if isinstance(raw, bytes) else raw.encode('utf-8', errors='replace')
            zf.writestr(path, content)
    buf.seek(0)
    return buf.read()


# ── Launch / proxy routes ───────────────────────────────────────────────────

@app.route("/launch", methods=["POST"])
def launch_session():
    """Package session source into a zip and trigger the deployer pipeline."""
    import threading as _threading, json as _j, urllib.request as _ureq
    data       = request.get_json(force=True, silent=True) or {}
    session_id = data.get("session_id", "")
    if not session_id or not _re.match(r'^[\w-]+$', session_id):
        return jsonify({"error": "Invalid session_id"}), 400

    # Derive K8s-safe deployment name immediately so we can return fast
    deploy_name = "session-" + session_id.lower().replace("_", "-")
    deploy_name = _re.sub(r'[^a-z0-9-]', '-', deploy_name)
    deploy_name = _re.sub(r'-+', '-', deploy_name).strip('-')

    _deployed_apps[session_id] = {
        "job_id":      session_id,
        "deploy_name": deploy_name,
        "status":      "building",
        "url":         None,
        "logs":        [],
    }

    def _do_launch():
        def _err(msg):
            _deployed_apps[session_id]["status"]  = "error"
            _deployed_apps[session_id]["message"] = msg

        # Get files from in-memory cache or re-fetch from MinIO
        cached = _session_file_cache.get(session_id)
        if cached:
            file_map = cached["files"]
        else:
            s3 = _s3_client()
            try:
                keys, prefix = _list_session_keys(s3, session_id)
            except Exception as exc:
                return _err(f"MinIO list failed: {exc}")
            file_map = {}
            for key in keys:
                rel = key[len(prefix):]
                if rel:
                    try:
                        obj = s3.get_object(Bucket=MINIO_BUCKET, Key=key)
                        file_map[rel] = obj["Body"].read()
                    except Exception:
                        pass
            if not file_map:
                return _err("No files found for session")

        # Build and upload zip
        zip_bytes = _make_session_zip(file_map)
        zip_key   = f"goose-deploys/{session_id}/source.zip"
        try:
            s3 = _s3_client()
            s3.put_object(Bucket=MINIO_BUCKET, Key=zip_key, Body=zip_bytes,
                          ContentType="application/zip")
        except Exception as exc:
            return _err(f"Zip upload failed: {exc}")

        deployer_payload = _j.dumps({
            "session_id":        session_id,
            "bucket_name":       MINIO_BUCKET,
            "file_path":         zip_key,
            "target_image_tag":  f"localhost:5000/{deploy_name}:latest",
            "deployment_name":   deploy_name,
            "namespace":         "aipns",
        }).encode()

        try:
            req = _ureq.Request(
                f"{DEPLOYER_URL}/api/start-pipeline",
                data=deployer_payload,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with _ureq.urlopen(req, timeout=30) as r:
                _j.loads(r.read())
        except Exception as exc:
            return _err(f"Deployer unreachable: {exc}")

    _threading.Thread(target=_do_launch, daemon=True).start()
    return jsonify({"job_id": session_id, "status": "building"}), 202


@app.route("/launch-status/<session_id>", methods=["GET"])
def launch_status(session_id):
    """Poll deployer for job status and return it to the browser."""
    import json as _j, urllib.request as _ureq
    if not _re.match(r'^[\w-]+$', session_id):
        return jsonify({"error": "Invalid session_id"}), 400

    app_info = _deployed_apps.get(session_id)
    if not app_info:
        return jsonify({"status": "not_launched"}), 404

    # Surface errors set by the background _do_launch thread (pre-deployer failures)
    if app_info.get("status") == "error" and not app_info.get("job_id_queued"):
        return jsonify({"status": "error", "message": app_info.get("message", "Launch failed"), "logs": []})

    job_id = app_info.get("job_id", session_id)
    try:
        req = _ureq.Request(f"{DEPLOYER_URL}/api/job-status/{job_id}", method="GET")
        with _ureq.urlopen(req, timeout=5) as r:
            job = _j.loads(r.read())
    except Exception as exc:
        return jsonify({"status": "building", "error": str(exc), "logs": []}), 200

    proxy_url = f"{PUBLIC_BASE_URL.rstrip('/')}/apps/vibe-code-builder-service/proxy/{session_id}/"

    if job.get("status") == "success":
        _deployed_apps[session_id]["url"]    = job["url"]
        _deployed_apps[session_id]["status"] = "success"
        return jsonify({
            "status":       "success",
            "internal_url": job["url"],
            "proxy_url":    proxy_url,
            "logs":         job.get("logs", []),
        })
    elif job.get("status") == "error":
        _deployed_apps[session_id]["status"] = "error"
        return jsonify({
            "status":  "error",
            "message": job.get("message", "Build failed"),
            "logs":    job.get("logs", []),
        })
    else:
        return jsonify({
            "status": job.get("status", "building"),
            "logs":   job.get("logs", []),
        })


@app.route("/proxy/<session_id>/",  defaults={"subpath": ""},
           methods=["GET","POST","PUT","DELETE","PATCH","OPTIONS"])
@app.route("/proxy/<session_id>/<path:subpath>",
           methods=["GET","POST","PUT","DELETE","PATCH","OPTIONS"])
def proxy_to_deployed_app(session_id, subpath):
    """Reverse-proxy requests to the deployed session app in K8s.
    Supports both regular HTTP and SSE streaming (required for MCP HTTP transport).
    """
    import urllib.request as _ureq, urllib.error as _uerr
    from flask import Response, stream_with_context

    if not _re.match(r'^[\w-]+$', session_id):
        return Response("Invalid session_id", status=400)

    app_info = _deployed_apps.get(session_id)
    if not app_info or not app_info.get("url"):
        return Response(
            "App not yet deployed. Click 'Launch App' first.",
            status=503, mimetype="text/plain"
        )

    base_url   = app_info["url"].rstrip("/")
    target_url = f"{base_url}/{subpath}" if subpath else base_url + "/"
    if request.query_string:
        target_url += "?" + request.query_string.decode("utf-8", errors="replace")

    _HOP = {'connection','keep-alive','proxy-authenticate','proxy-authorization',
            'te','trailers','transfer-encoding','upgrade','host','content-length'}
    fwd_headers = {k: v for k, v in request.headers if k.lower() not in _HOP}
    body = request.get_data() or None

    try:
        req = _ureq.Request(target_url, data=body, headers=fwd_headers,
                            method=request.method)
        # Use a long timeout for SSE connections; short timeout for regular requests
        is_sse = "text/event-stream" in request.headers.get("Accept", "")
        timeout = None if is_sse else 30

        r = _ureq.urlopen(req, timeout=timeout)
        content_type = r.headers.get("Content-Type", "")
        resp_headers = {k: v for k, v in r.headers.items() if k.lower() not in _HOP}

        # SSE / streaming response — stream chunks back without buffering
        if "text/event-stream" in content_type:
            def _sse_generator():
                try:
                    while True:
                        chunk = r.read(4096)
                        if not chunk:
                            break
                        yield chunk
                finally:
                    r.close()
            resp_headers.update({
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",  # disable nginx buffering
            })
            return Response(
                stream_with_context(_sse_generator()),
                status=r.status,
                headers=resp_headers,
                mimetype="text/event-stream",
                direct_passthrough=True,
            )

        # Regular response — buffer and return
        return Response(r.read(), status=r.status, headers=resp_headers)

    except _uerr.HTTPError as e:
        return Response(e.read(), status=e.code, mimetype="text/plain")
    except Exception as exc:
        return Response(f"Proxy error: {exc}", status=502, mimetype="text/plain")


def _needs_preview(file_map):
    for c in ["index.html", "build/index.html", "dist/index.html",
              "client/build/index.html", "frontend/build/index.html"]:
        if c in file_map:
            content = _unescape(file_map[c])
            if "<script" in content and any(x in content for x in ("main.", "bundle", "static/js")):
                return False
    return True


def _extract_api_endpoints(file_map):
    """Parse Python / JS / TS source files and return (endpoints, pydantic_models)."""
    import re as _re2, json as _json2

    endpoints = []
    models = {}

    for key, raw in file_map.items():
        ext = key.rsplit('.', 1)[-1].lower() if '.' in key else ''
        if ext not in ('py', 'js', 'ts'):
            continue
        try:
            content = _unescape(raw)   # returns str already
        except Exception:
            continue
        filename = key.split('/')[-1]

        if ext == 'py':
            # ── Pydantic BaseModel field extraction ─────────────────────────
            for cls_m in _re2.finditer(
                r'class\s+(\w+)\s*\(\s*BaseModel\s*\)\s*:(.*?)(?=\nclass |\Z)',
                content, _re2.DOTALL
            ):
                fields = {}
                for f in _re2.finditer(
                    r'^\s{4}(\w+)\s*:\s*([\w\[\], |]+)',
                    cls_m.group(2), _re2.MULTILINE
                ):
                    fields[f.group(1)] = f.group(2).strip()
                if fields:
                    models[cls_m.group(1)] = fields

            # ── FastAPI: @app.post("/path") / @router.get("/path") ───────────
            for m in _re2.finditer(
                r'@\w+\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)["\']',
                content, _re2.IGNORECASE
            ):
                method = m.group(1).upper()
                path   = m.group(2)
                after  = content[m.end():]
                fn_m   = _re2.search(r'(?:async\s+)?def\s+(\w+)\s*\(([^)]*)\)', after[:600])
                fn_name = (fn_m.group(1) if fn_m else '') or ''
                req_model = None
                if fn_m:
                    for p in _re2.finditer(r':\s*(\w+)', fn_m.group(2)):
                        if p.group(1) in models:
                            req_model = p.group(1)
                            break
                doc = ''
                if fn_m:
                    fn_end = m.end() + fn_m.end()
                    doc_m = _re2.search(r'"""(.*?)"""', content[fn_end:fn_end+500], _re2.DOTALL)
                    if doc_m:
                        doc = ' '.join(doc_m.group(1).strip().split())[:160]
                endpoints.append({'method': method, 'path': path,
                                   'fn': fn_name, 'doc': doc,
                                   'model': req_model, 'file': filename})

            # ── Flask: @app.route("/path", methods=["POST", "GET"]) ──────────
            for m in _re2.finditer(
                r'@\w+\.route\s*\(\s*["\']([^"\']+)["\'][^)]*methods\s*=\s*\[([^\]]+)\]',
                content, _re2.IGNORECASE | _re2.DOTALL
            ):
                path        = m.group(1)
                methods_raw = [x.strip().strip("\"'").upper()
                               for x in m.group(2).split(',')]
                after   = content[m.end():]
                fn_m    = _re2.search(r'def\s+(\w+)', after[:200])
                fn_name = (fn_m.group(1) if fn_m else '') or ''
                for meth in methods_raw:
                    if meth:
                        endpoints.append({'method': meth, 'path': path,
                                           'fn': fn_name, 'doc': '',
                                           'model': None, 'file': filename})

        elif ext in ('js', 'ts'):
            # ── Express / Fastify ────────────────────────────────────────────
            for m in _re2.finditer(
                r'(?:app|router)\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)["\']',
                content, _re2.IGNORECASE
            ):
                endpoints.append({'method': m.group(1).upper(), 'path': m.group(2),
                                   'fn': '', 'doc': '', 'model': None, 'file': filename})

    # Deduplicate by (method, path)
    seen  = set()
    dedup = []
    for ep in endpoints:
        key2 = (ep['method'], ep['path'])
        if key2 not in seen:
            seen.add(key2)
            dedup.append(ep)
    return dedup, models


def _render_swagger_section(endpoints, models):
    """Render a Swagger-UI-style HTML block with Try-it-out execute functionality."""
    import html as _html, json as _json2

    if not endpoints:
        return ''

    _METHOD_COLOR = {
        'GET':    ('#61affe', '#f0f7ff'),
        'POST':   ('#49cc90', '#edfaf3'),
        'PUT':    ('#fca130', '#fff8ed'),
        'DELETE': ('#f93e3e', '#fff0f0'),
        'PATCH':  ('#50e3c2', '#f0fbf9'),
    }

    def _model_example(name):
        if name not in models:
            return '{}'
        ex = {}
        for field, ftype in models[name].items():
            fl = ftype.lower()
            if 'int' in fl:         ex[field] = 0
            elif 'float' in fl:     ex[field] = 0.0
            elif 'bool' in fl:      ex[field] = True
            elif 'list' in fl:      ex[field] = []
            elif 'dict' in fl:      ex[field] = {}
            else:                   ex[field] = 'string'
        return _json2.dumps(ex, indent=2)

    cards = []
    for i, ep in enumerate(endpoints):
        method    = ep['method']
        path_raw  = ep['path']
        path_esc  = _html.escape(path_raw)
        desc_esc  = _html.escape(ep.get('doc') or ep.get('fn') or '')
        color, bg = _METHOD_COLOR.get(method, ('#888', '#f9f9f9'))
        has_body  = method in ('POST', 'PUT', 'PATCH')

        # Request body example
        if ep.get('model') and ep['model'] in models:
            body_example = _model_example(ep['model'])
            model_badge  = f'<span class="schema-badge">{_html.escape(ep["model"])}</span>'
        elif has_body:
            body_example = '{{\n  "key": "value"\n}}'
            model_badge  = ''
        else:
            body_example = ''
            model_badge  = ''

        body_section = ''
        if has_body:
            body_esc = _html.escape(body_example)
            body_section = f'''
        <div class="ep-sec">
          <div class="ep-lbl">Request Body {model_badge}
            <button class="try-edit-btn" onclick="toggleEdit({i})">&#9998; Edit</button>
          </div>
          <textarea class="body-edit" id="body-{i}" style="display:none">{body_esc}</textarea>
          <pre class="cb" id="body-pre-{i}">{body_esc}</pre>
        </div>'''

        # cURL (dynamic — JS will update it based on base URL + edited body)
        if has_body:
            curl_body_js = f"var bd=document.getElementById('body-pre-{i}').innerText;cc+=' -d \\''+bd.replace(/\\n/g,' ')+'\\''"
        else:
            curl_body_js = ''

        cards.append(f"""
    <div class="ep-card" id="card-{i}">
      <div class="ep-hdr" onclick="toggleEp({i})" style="background:{bg};border-left:4px solid {color}">
        <span class="mbadge" style="background:{color}">{method}</span>
        <span class="ep-path">{path_esc}</span>
        <span class="ep-desc">{desc_esc}</span>
        <span class="chev" id="chev-{i}">&#9654;</span>
      </div>
      <div class="ep-body" id="ebody-{i}" style="display:none">
        {body_section}
        <div class="ep-sec">
          <div class="ep-lbl" style="display:flex;align-items:center;gap:8px">
            cURL
            <button class="exec-btn" id="exec-{i}" onclick="execEp({i},'{method}','{path_raw}',{str(has_body).lower()})">&#9654; Execute</button>
            <button class="copybtn" onclick="copyCurl({i},'{method}','{path_raw}',{str(has_body).lower()})">&#128203; Copy cURL</button>
          </div>
          <pre class="cb" id="curl-{i}"></pre>
        </div>
        <div class="ep-sec" id="resp-sec-{i}" style="display:none">
          <div class="ep-lbl" style="display:flex;align-items:center;gap:8px">
            Response
            <span class="status-badge" id="status-{i}"></span>
          </div>
          <pre class="cb resp-pre" id="resp-{i}"></pre>
        </div>
      </div>
    </div>""")

    cards_html = '\n'.join(cards)
    count = len(endpoints)

    # Build the JS endpoint data outside the f-string (backslashes not allowed in f-string exprs)
    _default_body_placeholder = '{}\n  "key": "value"\n}'  # will be replaced below
    _eps_data = []
    for ep in endpoints:
        if ep.get('model') and ep['model'] in models:
            body_js = _model_example(ep['model'])
        elif ep['method'] in ('POST', 'PUT', 'PATCH'):
            body_js = '{\n  "key": "value"\n}'
        else:
            body_js = ''
        _eps_data.append({
            'method':   ep['method'],
            'path':     ep['path'],
            'has_body': ep['method'] in ('POST', 'PUT', 'PATCH'),
            'body':     body_js,
        })
    sw_eps_json = _json2.dumps(_eps_data)

    return f"""<h2 id="api-sec-title">API Endpoints <span class="ep-cnt">{count}</span></h2>
<div class="sw-wrap">
  <div class="sw-bar">
    <span style="font-weight:700;font-size:13px;color:#fff">OAS 3.0</span>
    <span style="color:#aaa;font-size:12px;margin-left:10px">Auto-detected from source</span>
    <label style="color:#ccc;font-size:12px;margin-left:auto;display:flex;align-items:center;gap:6px">
      Base URL
      <input id="sw-base-url" type="text" value="http://localhost:8000"
        style="background:#2d2d2d;border:1px solid #555;color:#fff;padding:3px 8px;border-radius:4px;font-size:12px;width:220px"
        oninput="refreshCurls()" />
    </label>
    <button class="expand-btn" onclick="swExpandAll()">Expand All</button>
  </div>
  <div class="ep-list">
{cards_html}
  </div>
</div>
<style>
  .sw-wrap{{border:1px solid #d0d5dd;border-radius:8px;overflow:hidden;margin-bottom:28px;font-family:system-ui,sans-serif}}
  .sw-bar{{background:#1b1b1b;padding:10px 16px;display:flex;align-items:center;gap:8px;flex-wrap:wrap}}
  .expand-btn{{background:transparent;border:1px solid #777;color:#ccc;padding:4px 12px;border-radius:4px;cursor:pointer;font-size:12px}}
  .expand-btn:hover{{border-color:#fff;color:#fff}}
  .ep-card{{border-bottom:1px solid #e8eaf0}}
  .ep-card:last-child{{border-bottom:none}}
  .ep-hdr{{display:flex;align-items:center;gap:12px;padding:10px 16px;cursor:pointer;transition:filter .15s}}
  .ep-hdr:hover{{filter:brightness(.95)}}
  .mbadge{{color:#fff;font-weight:700;font-size:11px;padding:4px 9px;border-radius:4px;min-width:60px;text-align:center;letter-spacing:.4px}}
  .ep-path{{font-family:monospace;font-size:14px;font-weight:600;color:#1f1f1f;word-break:break-all}}
  .ep-desc{{color:#555;font-size:12px;flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
  .chev{{color:#888;font-size:11px;transition:transform .2s;flex-shrink:0}}
  .ep-body{{background:#fafafa;padding:16px 20px;border-top:1px solid #e8eaf0}}
  .ep-sec{{margin-bottom:14px}}
  .ep-sec:last-child{{margin-bottom:0}}
  .ep-lbl{{font-size:11px;font-weight:700;color:#3b4151;text-transform:uppercase;letter-spacing:.6px;margin-bottom:6px}}
  .schema-badge{{font-size:10px;background:#e8eaf0;color:#3b4151;padding:2px 6px;border-radius:4px;margin-left:4px;text-transform:none;letter-spacing:0;font-weight:600}}
  .cb{{background:#1e1e1e;color:#d4d4d4;padding:12px 16px;border-radius:6px;font-size:12px;line-height:1.6;overflow-x:auto;margin:0;white-space:pre-wrap;word-break:break-all}}
  .resp-pre{{min-height:40px}}
  .body-edit{{width:100%;min-height:120px;background:#1e1e1e;color:#d4d4d4;border:1px solid #555;border-radius:6px;padding:12px;font-size:12px;line-height:1.6;font-family:monospace;resize:vertical;margin-bottom:6px;box-sizing:border-box}}
  .exec-btn{{background:#49cc90;color:#fff;border:none;padding:4px 14px;border-radius:4px;cursor:pointer;font-size:12px;font-weight:600}}
  .exec-btn:hover{{background:#3aaa77}}
  .exec-btn.loading{{background:#aaa;cursor:wait}}
  .copybtn{{background:#4990e2;color:#fff;border:none;padding:4px 12px;border-radius:4px;cursor:pointer;font-size:12px}}
  .copybtn:hover{{background:#357abd}}
  .try-edit-btn{{background:transparent;border:1px solid #aaa;color:#555;padding:2px 8px;border-radius:4px;cursor:pointer;font-size:11px;margin-left:6px}}
  .try-edit-btn:hover{{border-color:#333;color:#333}}
  .ep-cnt{{background:#4990e2;color:#fff;font-size:12px;padding:2px 8px;border-radius:10px;margin-left:6px;vertical-align:middle}}
  .status-badge{{font-size:11px;font-weight:700;padding:2px 8px;border-radius:10px;color:#fff}}
  .status-ok{{background:#49cc90}}.status-err{{background:#f93e3e}}.status-warn{{background:#fca130}}
</style>
<script>
var _SW_EPS = {sw_eps_json};

function _baseUrl(){{return(document.getElementById('sw-base-url')||{{value:'http://localhost:8000'}}).value.replace(/\\/+$/,'');}}

function _buildCurl(i){{
  var ep=_SW_EPS[i]; var base=_baseUrl(); var cc='curl -X '+ep.method+' '+base+ep.path;
  if(ep.has_body){{var bd=document.getElementById('body-pre-'+i)?document.getElementById('body-pre-'+i).innerText:(ep.body||'{{}}');cc+=' \\\n  -H "Content-Type: application/json" \\\n  -d \\''+bd.replace(/\\n/g,' ')+'\\'';}}
  return cc;
}}

function refreshCurls(){{
  _SW_EPS.forEach(function(_,i){{var el=document.getElementById('curl-'+i);if(el&&el.closest('.ep-body')&&el.closest('.ep-body').style.display!=='none')el.textContent=_buildCurl(i);}});
}}

function toggleEp(i){{
  var b=document.getElementById('ebody-'+i);var c=document.getElementById('chev-'+i);
  var open=b.style.display!=='none';
  b.style.display=open?'none':'block';
  c.style.transform=open?'':'rotate(90deg)';
  if(!open){{var el=document.getElementById('curl-'+i);if(el)el.textContent=_buildCurl(i);}}
}}

function swExpandAll(){{
  _SW_EPS.forEach(function(_,i){{
    var b=document.getElementById('ebody-'+i);var c=document.getElementById('chev-'+i);
    if(b){{b.style.display='block';}} if(c)c.style.transform='rotate(90deg)';
    var el=document.getElementById('curl-'+i);if(el)el.textContent=_buildCurl(i);
  }});
}}

function toggleEdit(i){{
  var ta=document.getElementById('body-'+i);var pre=document.getElementById('body-pre-'+i);
  if(!ta||!pre)return;
  var editing=ta.style.display!=='none';
  if(editing){{pre.textContent=ta.value;ta.style.display='none';pre.style.display='';var el=document.getElementById('curl-'+i);if(el)el.textContent=_buildCurl(i);}}
  else{{ta.value=pre.textContent;ta.style.display='';pre.style.display='none';}}
}}

function execEp(i,method,path,hasBody){{
  var btn=document.getElementById('exec-'+i);
  var rsec=document.getElementById('resp-sec-'+i);
  var rpre=document.getElementById('resp-'+i);
  var sbadge=document.getElementById('status-'+i);
  if(!rpre)return;
  btn.classList.add('loading');btn.textContent='Loading\u2026';
  var url=_baseUrl()+path;
  var opts={{method:method,headers:{{}}}};
  if(hasBody){{
    var bodyEl=document.getElementById('body-pre-'+i);
    var bodyTa=document.getElementById('body-'+i);
    var bodyStr=(bodyTa&&bodyTa.style.display!=='none')?bodyTa.value:(bodyEl?bodyEl.innerText:'{{}}');
    opts.headers['Content-Type']='application/json';
    opts.body=bodyStr;
  }}
  rsec.style.display='block';
  rpre.textContent='Sending request to '+url+'\u2026';
  sbadge.textContent='';sbadge.className='status-badge';
  fetch(url,opts).then(function(r){{
    var st=r.status;
    sbadge.textContent=st+' '+r.statusText;
    sbadge.className='status-badge '+(st<300?'status-ok':st<500?'status-warn':'status-err');
    return r.text().then(function(t){{
      try{{rpre.textContent=JSON.stringify(JSON.parse(t),null,2);}}catch(e){{rpre.textContent=t;}}
      btn.classList.remove('loading');btn.textContent='\u25b6 Execute';
    }});
  }}).catch(function(e){{
    rpre.textContent='Error: '+e.message+'\\n\\nMake sure your service is running and CORS is enabled.\\nIf the service is on a different host, add:\\n  from fastapi.middleware.cors import CORSMiddleware\\n  app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])';    
    sbadge.textContent='Network Error';sbadge.className='status-badge status-err';
    btn.classList.remove('loading');btn.textContent='\u25b6 Execute';
  }});
}}

function copyCurl(i){{
  navigator.clipboard.writeText(_buildCurl(i)).then(function(){{
    var btns=document.getElementById('card-'+i).querySelectorAll('.copybtn');
    if(btns[0]){{btns[0].textContent='Copied!';setTimeout(function(){{btns[0].textContent='Copy cURL';}},1500);}}
  }});
}}
</script>"""


def _launch_button_html(session_id):
    """Return the HTML+JS block for the 'Launch App in Kubernetes' button."""
    import html as _html
    sid = _html.escape(session_id or '')
    svc = '/apps/vibe-code-builder-service'
    return f"""<style>
  .launch-section{{background:#f0f7ff;border:1px solid #b6d4fe;border-radius:10px;padding:18px 22px;margin-bottom:24px}}
  .launch-section h2{{margin:0 0 14px;color:#0d6efd;border:none;padding:0}}
  .launch-row{{display:flex;align-items:center;gap:12px;flex-wrap:wrap}}
  .launch-btn{{background:#0d6efd;color:#fff;border:none;padding:9px 22px;border-radius:6px;cursor:pointer;font-size:14px;font-weight:600}}
  .launch-btn:hover{{background:#0b5ed7}} .launch-btn:disabled{{background:#6c757d;cursor:wait}}
  .launch-badge{{font-size:12px;font-weight:700;padding:4px 12px;border-radius:12px;color:#fff}}
  .launch-url-link{{font-family:monospace;font-size:13px;color:#0d6efd;word-break:break-all}}
</style>
<script>
var _SID = '{sid}';
var _SVC = '{svc}';
var _lastLog = 0;
function launchApp() {{
  var btn=document.getElementById('launch-btn');
  btn.disabled=true; btn.textContent='\u23f3 Launching\u2026';
  document.getElementById('launch-log-wrap').style.display='block';
  fetch(_SVC+'/launch',{{method:'POST',headers:{{'Content-Type':'application/json'}},
    body:JSON.stringify({{session_id:_SID}})}})
  .then(function(r){{return r.json();}})
  .then(function(d){{
    if(d.error){{appendLLog('Error: '+d.error,'ERR');btn.disabled=false;btn.textContent='\u128640 Launch App';return;}}
    appendLLog('Build queued (job: '+d.job_id+')');
    showBadge('\u23f3 Building\u2026','#fca130');
    setTimeout(pollLaunch,3000);
  }}).catch(function(e){{appendLLog('Error: '+e.message,'ERR');btn.disabled=false;btn.textContent='\u128640 Launch App';}});
}}
function pollLaunch() {{
  fetch(_SVC+'/launch-status/'+_SID).then(function(r){{return r.json();}}).then(function(d){{
    var logs=d.logs||[];
    for(var i=_lastLog;i<logs.length;i++) appendLLog('['+logs[i].step+'] '+logs[i].message);
    _lastLog=logs.length;
    if(d.status==='success') {{
      showBadge('\u2705 Running','#49cc90');
      var btn=document.getElementById('launch-btn');
      btn.textContent='\u2705 Running'; btn.style.background='#49cc90';
      var wrap=document.getElementById('launch-url-wrap');
      var link=document.getElementById('launch-url-link');
      wrap.style.display='block'; link.href=d.proxy_url; link.textContent=d.proxy_url;
      var sw=document.getElementById('sw-base-url');
      if(sw){{sw.value=d.proxy_url;if(typeof refreshCurls==='function')refreshCurls();}}
    }} else if(d.status==='error') {{
      showBadge('\u274c Build Failed','#f93e3e');
      appendLLog('Build failed: '+(d.message||'Unknown error'),'ERR');
      var btn=document.getElementById('launch-btn');
      btn.disabled=false; btn.textContent='\u128640 Retry Launch';
    }} else {{
      setTimeout(pollLaunch,3000);
    }}
  }}).catch(function(){{setTimeout(pollLaunch,4000);}});
}}
function appendLLog(msg,lvl) {{
  var pre=document.getElementById('launch-log');
  var line=document.createTextNode(msg+'\\n');
  pre.appendChild(line); pre.scrollTop=pre.scrollHeight;
}}
function showBadge(text,bg) {{
  var b=document.getElementById('launch-badge');
  b.textContent=text; b.style.background=bg; b.style.display='inline-block';
}}
function copyLaunchUrl() {{
  navigator.clipboard.writeText(document.getElementById('launch-url-link').href);
}}
function openLaunchUrl() {{
  window.open(document.getElementById('launch-url-link').href,'_blank');
}}
</script>
<div class="launch-section">
  <h2>&#128640; Deploy &amp; Run in Kubernetes</h2>
  <div class="launch-row">
    <button class="launch-btn" id="launch-btn" onclick="launchApp()">&#128640; Launch App</button>
    <span class="launch-badge" id="launch-badge" style="display:none"></span>
  </div>
  <div id="launch-log-wrap" style="display:none;margin-top:12px">
    <div class="ep-lbl">Build &amp; Deploy Log</div>
    <pre class="cb" id="launch-log" style="max-height:280px;overflow-y:auto;font-size:11px"></pre>
  </div>
  <div id="launch-url-wrap" style="display:none;margin-top:12px">
    <div class="ep-lbl">Live App URL (via proxy)</div>
    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
      <a class="launch-url-link" id="launch-url-link" href="#" target="_blank"></a>
      <button class="copybtn" onclick="copyLaunchUrl()">Copy URL</button>
      <button class="copybtn" style="background:#49cc90" onclick="openLaunchUrl()">Open &#8599;</button>
    </div>
  </div>
</div>"""


def _generate_backend_info_page(file_map, session_id=None):
    """Render a readable info page for pure backend / Python projects."""
    import html as _html

    def _get(path):
        for k, v in file_map.items():
            if k.endswith("/" + path) or k == path:
                return _unescape(v)
        return None

    # Collect key files to display
    readme   = _get("README.md")
    dockerf  = _get("Dockerfile")
    reqs     = _get("requirements.txt") or _get("package.json")
    main_py  = _get("main.py") or _get("app.py") or _get("api/main.py") or _get("server.js")

    def _section(title, content, lang=""):
        if not content:
            return ""
        escaped = _html.escape(content[:4000])
        return f'<h2>{title}</h2><pre><code class="language-{lang}">{escaped}</code></pre>'

    # Extract API endpoints for Swagger-like display
    endpoints, ep_models = _extract_api_endpoints(file_map)
    swagger_html  = _render_swagger_section(endpoints, ep_models)
    launch_html   = _launch_button_html(session_id) if session_id else ''

    sections = ""
    if readme:
        # Render README as preformatted text
        sections += f'<h2>README</h2><pre style="white-space:pre-wrap">{_html.escape(readme[:3000])}</pre>'
    sections += _section("Dockerfile", dockerf, "dockerfile")
    sections += _section("requirements.txt / package.json", reqs, "text")
    sections += _section("Entry point", main_py, "python")

    # List all files
    file_list = "\n".join(
        f'<li><code>{_html.escape(k)}</code></li>'
        for k in sorted(file_map.keys())
        if not k.startswith(".")
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>Project Info</title>
  <style>
    body{{font-family:system-ui,sans-serif;margin:0;padding:24px;background:#f8f9fa;color:#212529}}
    h1{{color:#0d6efd;border-bottom:2px solid #0d6efd;padding-bottom:8px}}
    h2{{color:#495057;margin-top:28px}}
    pre{{background:#1e1e1e;color:#d4d4d4;padding:16px;border-radius:8px;overflow-x:auto;font-size:13px;line-height:1.5}}
    ul{{background:#fff;border:1px solid #dee2e6;border-radius:8px;padding:12px 12px 12px 28px;line-height:2}}
    .badge{{background:#198754;color:#fff;padding:3px 10px;border-radius:12px;font-size:12px;margin-left:8px}}
    .info{{background:#cff4fc;border:1px solid #b6effb;border-radius:8px;padding:12px 16px;margin-bottom:20px}}
  </style>
</head>
<body>
  <h1>Backend Project <span class="badge">No Frontend</span></h1>
  <div class="info">
    This project has no React frontend. It is a backend/API or Python service.<br/>
    To run it locally: <code>docker-compose up --build</code> or see the Dockerfile below.
  </div>
  {launch_html}
  {swagger_html}
  {sections}
  <h2>All project files</h2>
  <ul>{file_list}</ul>
</body>
</html>""".encode("utf-8")


@app.route("/deploy", methods=["POST"])
def deploy_session():
    data = request.get_json(force=True, silent=True) or {}
    session_id = data.get("session_id", "")
    if not session_id or not _re.match(r'^[\w-]+$', session_id):
        return jsonify({"error": "Invalid session_id"}), 400
    # Invalidate stale cache so re-deploys pick up new files
    _session_file_cache.pop(session_id, None)

    s3 = _s3_client()
    try:
        keys, prefix = _list_session_keys(s3, session_id)
    except Exception as exc:
        return jsonify({"error": f"MinIO list failed: {exc}"}), 502
    if not keys:
        return jsonify({"error": f"No files found for session {session_id}"}), 404

    file_map = {}
    for key in keys:
        rel = key[len(prefix):]
        if not rel:
            continue
        try:
            obj = s3.get_object(Bucket=MINIO_BUCKET, Key=key)
            file_map[rel] = obj["Body"].read()
        except Exception as exc:
            print(f"[WARN] fetch failed {key}: {exc}")

    if not file_map:
        return jsonify({"error": "All file downloads failed"}), 502

    if _needs_preview(file_map):
        preview = _generate_preview(file_map)
        if preview:
            file_map["_preview.html"] = preview
            entry = "_preview.html"
        else:
            # Check for Streamlit → MCP → generic backend (in that order)
            streamlit_key = _find_streamlit_app(file_map)
            if streamlit_key:
                file_map["_preview.html"] = _generate_streamlit_info_page(file_map, streamlit_key, session_id)
            else:
                mcp_info = _find_mcp_server(file_map)
                if mcp_info:
                    file_map["_preview.html"] = _generate_mcp_info_page(file_map, mcp_info, session_id)
                else:
                    file_map["_preview.html"] = _generate_backend_info_page(file_map, session_id)
            entry = "_preview.html"
    else:
        for c in ["index.html", "build/index.html", "dist/index.html",
                  "client/build/index.html", "frontend/build/index.html"]:
            if c in file_map:
                entry = c
                break
        else:
            html_files = [k for k in file_map if k.endswith(".html")]
            if html_files:
                entry = html_files[0]
            else:
                file_map["_preview.html"] = _generate_backend_info_page(file_map, session_id)
                entry = "_preview.html"

    _session_file_cache[session_id] = {"files": file_map, "entry": entry}
    print(f"[deploy] session={session_id} files={len(file_map)} entry={entry}")
    return jsonify({"url": f"{PUBLIC_BASE_URL.rstrip('/')}/apps/vibe-code-builder-service/session/{session_id}/"}), 200


@app.route("/session/<session_id>/", defaults={"filepath": ""})
@app.route("/session/<session_id>/<path:filepath>")
def serve_session_app(session_id, filepath):
    session = _session_file_cache.get(session_id)
    if not session:
        return f"Session '{session_id}' not deployed.", 404
    resolved = filepath if filepath else session["entry"]
    buf = session["files"].get(resolved)
    if buf is None:
        buf = session["files"].get(session["entry"])
        resolved = session["entry"]
    if buf is None:
        return "File not found", 404
    _MIME = {
        ".html": "text/html; charset=utf-8", ".css": "text/css",
        ".js":   "application/javascript",   ".mjs": "application/javascript",
        ".json": "application/json",          ".svg": "image/svg+xml",
        ".png":  "image/png",                 ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",                ".gif": "image/gif",
        ".ico":  "image/x-icon",              ".woff": "font/woff",
        ".woff2":"font/woff2",                ".ttf": "font/ttf",
        ".txt":  "text/plain; charset=utf-8",
    }
    ext = os.path.splitext(resolved)[1].lower()
    mime = _MIME.get(ext) or mimetypes.guess_type(resolved)[0] or "text/html; charset=utf-8"
    from flask import Response
    return Response(buf, mimetype=mime)
