"""VirusGPT MCP CLIENT (Direction B: VirusGPT calls external MCP servers).

VirusGPT can connect to external MCP servers (config `mcp.clients`) and use
their tools. For n8n — which on this box has NO native MCP server — we also
ship an **n8n-REST adapter**: an in-process MCP *server-side* shim is not
needed here; instead this client exposes n8n's REST API AS MCP-callable tools
via a local adapter session, so "call n8n through MCP" works from VirusGPT
even though n8n can't speak MCP itself.

Two kinds of connections:
  1. External MCP servers (real MCP): listed in config mcp.clients as
     {name, transport: "stdio"|"sse"|"streamable-http", command?, args?,
      url?, headers?}. We open a ClientSession, list tools, and can call them.
  2. n8n adapter (virtual): a built-in provider backed by services/n8n.py that
     surfaces list/trigger/create/get workflow as MCP tools.

Discovered tools are stored in TOOL_REGISTRY and exposed via:
  * list_mcp_tools()      -> [{server, name, description, schema}]
  * call_mcp_tool(server, name, arguments) -> result

All failures degrade gracefully (connection errors are reported, never raise
at import/startup). The client is started lazily from server.py.

NOTE: this uses the `mcp` 1.x async ClientSession API. Each external server
gets its own async context managed inside an event loop helper.
"""
import asyncio
import json
import os
from typing import Any, Dict, List, Optional

from services import config as cfg

# server_name -> list of tool dicts discovered at connect time
TOOL_REGISTRY: Dict[str, List[Dict[str, Any]]] = {}

# Active sessions keyed by server name (populated on connect)
_SESSIONS: Dict[str, Any] = {}

# Open context managers keyed by server name (kept alive for the session's life)
_CTX: Dict[str, Any] = {}

_N8N_PROVIDER_TOOLS = [
    {"name": "n8n_list_workflows", "description": "List n8n workflows (virtual MCP tool via n8n REST).",
     "input_schema": {"type": "object", "properties": {"active_only": {"type": "boolean"}}}},
    {"name": "n8n_trigger_workflow", "description": "Trigger an n8n workflow by id.",
     "input_schema": {"type": "object", "properties": {
         "workflow_id": {"type": "string"}, "data": {"type": "object"}}}},
    {"name": "n8n_create_workflow", "description": "Create (build) an n8n workflow.",
     "input_schema": {"type": "object", "properties": {
         "name": {"type": "string"}, "nodes": {"type": "array"},
         "connections": {"type": "object"}, "active": {"type": "boolean"}}}},
]


async def _connect_stdio(server: Dict[str, Any]) -> Optional[Any]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    params = StdioServerParameters(command=server["command"], args=server.get("args", []))
    cm = stdio_client(params)
    read, write = await cm.__aenter__()
    session = ClientSession(read, write)
    await session.initialize()
    # Keep the context manager open for the session's lifetime.
    _CTX[server["name"]] = cm
    _SESSIONS[server["name"]] = session
    return session


async def _connect_sse(server: Dict[str, Any]) -> Optional[Any]:
    from mcp import ClientSession
    from mcp.client.sse import sse_client
    url = server["url"]
    headers = server.get("headers") or (
        {"X-N8N-API-KEY": os.environ.get("VG_N8N_TOKEN", "")}
        if "n8n" in server.get("name", "").lower() else None)
    cm = sse_client(url, headers=headers)
    read, write = await cm.__aenter__()
    session = ClientSession(read, write)
    await session.initialize()
    _CTX[server["name"]] = cm
    _SESSIONS[server["name"]] = session
    return session


async def _connect_external(server: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    transport = (server.get("transport") or "stdio").lower()
    try:
        if transport == "stdio":
            session = await _connect_stdio(server)
        elif transport in ("sse", "streamable-http"):
            session = await _connect_sse(server)
        else:
            return {"error": f"unknown transport {transport}"}
        tools = await session.list_tools()
        TOOL_REGISTRY[server["name"]] = [
            {"server": server["name"], "name": t.name,
             "description": t.description or "", "schema": t.inputSchema}
            for t in tools.tools
        ]
        _SESSIONS[server["name"]] = session
        return {"status": "ok", "server": server["name"],
                "tool_count": len(TOOL_REGISTRY[server["name"]])}
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "error": str(exc), "server": server.get("name")}


def _register_n8n_adapter():
    """Register the virtual n8n MCP tool provider (no network session needed)."""
    TOOL_REGISTRY["n8n-adapter"] = _N8N_PROVIDER_TOOLS


async def connect_all():
    """Connect to every configured external MCP server + register the n8n adapter.

    Automatically appends an `n8n-mcp` stdio client when n8n is enabled and
    `npx` is available — this gives VirusGPT the maintained n8n-MCP toolset
    (workflow build/trigger, credentials/auth, spreadsheets, executions).
    n8n-mcp reads N8N_API_URL / N8N_API_KEY from the environment, which we set
    from our own n8n config (so a single source of truth).

    RESILIENCE: each external connect runs in a watchdog thread with a hard
    wall-clock timeout. The `mcp` Python SDK's stdio_client can deadlock on
    certain Node/n8n-mcp builds (the initialize() handshake never returns and
    asyncio.wait_for cannot cancel the anyio cancel-scope). A hung connect must
    NOT block the rest of the bridge or app startup, so we kill the orphaned
    subprocess and mark the server failed (non-fatal) instead of hanging.
    """
    _register_n8n_adapter()
    clients = list(cfg.CONFIG.get("mcp", {}).get("clients") or [])

    # Auto-wire n8n-mcp (Direction B's real n8n tooling) when n8n is configured.
    if cfg.CONFIG.get("services", {}).get("n8n", {}).get("enabled"):
        import shutil
        n8n_cfg = cfg.CONFIG["services"]["n8n"]
        if shutil.which("npx") and not any(c.get("name") == "n8n-mcp" for c in clients):
            clients.append({
                "name": "n8n-mcp",
                "transport": "stdio",
                "command": "npx",
                "args": ["-y", "n8n-mcp"],
                "env": {
                    "MCP_MODE": "stdio",
                    "LOG_LEVEL": "error",
                    "DISABLE_CONSOLE_OUTPUT": "true",
                    "N8N_API_URL": n8n_cfg.get("base_url", ""),
                    "N8N_API_KEY": os.environ.get("VG_N8N_TOKEN", n8n_cfg.get("api_key", "")),
                },
            })

    CONNECT_TIMEOUT = 30  # seconds; a connect that exceeds this is killed

    def _connect_watchdog(srv):
        """Run _connect_external in a thread; kill any orphaned subprocess on hang."""
        import threading
        result = {}
        def _run():
            try:
                result["value"] = asyncio.run(_connect_external(srv))
            except Exception as exc:  # noqa: BLE001
                result["value"] = {"status": "failed", "error": str(exc),
                                    "server": srv.get("name")}
        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join(CONNECT_TIMEOUT)
        if t.is_alive():
            # Deadlock detected (e.g. mcp stdio_client hang). Kill orphaned
            # n8n-mcp / node subprocesses so they don't leak or block startup.
            _kill_server_subprocesses(srv.get("name"))
            return {"status": "failed",
                    "error": f"connect timed out after {CONNECT_TIMEOUT}s (deadlock guard)",
                    "server": srv.get("name")}
        return result.get("value", {"status": "failed", "error": "no result"})

    # Run all connects concurrently via threads (so one hang doesn't block others).
    import concurrent.futures
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(clients))) as ex:
        futs = {ex.submit(_connect_watchdog, srv): srv for srv in clients if srv.get("name")}
        for fut in concurrent.futures.as_completed(futs):
            res = fut.result()
            srv = futs[fut]
            if not srv.get("name"):
                continue
            env = srv.pop("env", None)
            if env:
                os.environ.update({k: str(v) for k, v in env.items() if v})
            results.append(res)
    return results


def _kill_server_subprocesses(name: str) -> None:
    """Best-effort kill of orphaned subprocesses spawned for an MCP server.

    The `mcp` SDK's stdio_client can leave a hung `node`/`npx n8n-mcp` process
    when the handshake deadlocks; reap them so they don't accumulate.
    """
    if not name:
        return
    try:
        import subprocess
        # Kill node processes whose command line references this server name.
        out = subprocess.run(
            ["pgrep", "-f", name], capture_output=True, text=True, timeout=10,
        ).stdout.split()
        for pid in out:
            try:
                subprocess.run(["kill", "-9", pid.strip()], timeout=5)
            except Exception:
                pass
    except Exception:
        pass


def list_mcp_tools() -> List[Dict[str, Any]]:
    """All discovered MCP tools across servers (including the n8n adapter)."""
    out: List[Dict[str, Any]] = []
    for tools in TOOL_REGISTRY.values():
        out.extend(tools)
    return out


async def call_mcp_tool(server: str, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Call a discovered MCP tool. Handles both real sessions and the n8n adapter."""
    if server == "n8n-adapter":
        return await _call_n8n_adapter(name, arguments)
    session = _SESSIONS.get(server)
    if not session:
        return {"status": "failed", "error": f"no active session for {server}"}
    try:
        res = await session.call_tool(name, arguments)
        # res.content is a list of content blocks; join text blocks.
        text = " ".join(getattr(c, "text", "") for c in (res.content or []))
        return {"status": "ok", "result": text}
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "error": str(exc)}


async def _call_n8n_adapter(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Route n8n-adapter tool calls to services/n8n.py."""
    from services import n8n as _n8n
    if name == "n8n_list_workflows":
        return await _n8n.n8n_list_workflows(active_only=arguments.get("active_only", False))
    if name == "n8n_trigger_workflow":
        return await _n8n.n8n_trigger_workflow(arguments.get("workflow_id", ""), arguments.get("data"))
    if name == "n8n_create_workflow":
        return await _n8n.n8n_create_workflow(
            arguments.get("name", "untitled"), arguments.get("nodes", []),
            arguments.get("connections"), arguments.get("active", False))
    return {"status": "failed", "error": f"unknown n8n-adapter tool {name}"}


def mcp_client_enabled() -> bool:
    return bool(cfg.CONFIG.get("mcp", {}).get("client_enabled", True))
