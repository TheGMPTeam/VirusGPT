"""VirusGPT MCP SERVER (Direction A: expose VirusGPT + n8n tools to MCP clients).

Runs an MCP server (FastMCP, SSE transport) so external MCP clients — n8n's
"MCP Client" node (on a newer n8n), Claude Desktop, Cursor, or any MCP host —
can call VirusGPT's capabilities and drive n8n through us.

Tools exposed:
  VirusGPT's own:
    * vg_chat(prompt, persona?)            -> chat completion via local LLM
    * vg_generate_image(prompt, ...)       -> ComfyUI image (returns local URL)
    * vg_service_status()                  -> health of LAN services
  n8n (workflow build / trigger / events / auth / sheets), reachable because
  VirusGPT already holds the n8n API key:
    * n8n_list_workflows(active_only?)
    * n8n_trigger_workflow(id, data?)
    * n8n_get_workflow(id)
    * n8n_create_workflow(name, nodes, connections?, active?)
    * n8n_add_authorization(name, type, ...)   -> builds an n8n credentials node
    * n8n_add_spreadsheet(title, rows?, ...)   -> builds a Google Sheets workflow

The server binds a separate port (default 8700) so it co-exists with the
FastAPI :8500 app. It is started lazily via start_mcp_server() from server.py.

Everything degrades gracefully: if n8n/ComfyUI are down the tools return a
clear error dict rather than raising, so the MCP session stays alive.
"""
import json
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

from services import config as cfg

MCP_PORT = int(cfg.CONFIG.get("mcp", {}).get("server_port") or 8700)

# ---------------------------------------------------------------------------
# Build the FastMCP app with our tools.
# ---------------------------------------------------------------------------
mcp = FastMCP("VirusGPT", port=MCP_PORT)


def _persona_list() -> List[str]:
    try:
        from services import personas as _p
        return [p.get("name") for p in _p.list_personas()] if hasattr(_p, "list_personas") else []
    except Exception:
        return []


@mcp.tool()
async def vg_chat(prompt: str, persona: str = "") -> str:
    """Send a message to the VirusGPT local LLM and return its reply.

    Args:
        prompt: the user message.
        persona: optional persona name to role-play (e.g. 'Cipher').
    """
    from services import llm
    try:
        reply = await llm.chat(prompt, persona=persona or None)
        return reply if reply else "(no response)"
    except Exception as exc:  # noqa: BLE001
        return f"[vg_chat error] {exc}"


@mcp.tool()
async def vg_generate_image(prompt: str, negative_prompt: str = "",
                            width: int = 1024, height: int = 1024) -> str:
    """Generate an image via ComfyUI. Returns a local URL or an error string.

    Args:
        prompt: positive prompt.
        negative_prompt: optional negative prompt.
        width, height: image size.
    """
    from services import comfyui as _cf
    if not cfg.CONFIG["services"]["comfyui"]["enabled"]:
        return "[vg_generate_image] ComfyUI disabled"
    res = await _cf.render_image(
        prompt, negative_prompt=negative_prompt, width=width, height=height)
    if res.get("status") == "completed":
        return f"image ready: {res.get('url')}"
    return f"[vg_generate_image failed] {res.get('error')}"


@mcp.tool()
async def vg_service_status() -> str:
    """Return the health/status of all modular LAN services (n8n, ComfyUI...)."""
    from services import n8n as _n8n
    st = await _n8n.n8n_status()
    return json.dumps(st)


@mcp.tool()
async def n8n_list_workflows(active_only: bool = False) -> str:
    """List n8n workflows. Returns JSON with id/name/active per workflow."""
    from services import n8n as _n8n
    return json.dumps(await _n8n.n8n_list_workflows(active_only=active_only))


@mcp.tool()
async def n8n_trigger_workflow(workflow_id: str, data: str = "{}") -> str:
    """Trigger an n8n workflow by id. `data` is a JSON string sent as input.

    Args:
        workflow_id: the n8n workflow id.
        data: optional JSON object string (execution input).
    """
    from services import n8n as _n8n
    try:
        payload = json.loads(data) if data else {}
    except Exception:
        payload = {}
    return json.dumps(await _n8n.n8n_trigger_workflow(workflow_id, payload))


@mcp.tool()
async def n8n_get_workflow(workflow_id: str) -> str:
    """Fetch a single n8n workflow definition (nodes/connections) as JSON."""
    from services import n8n as _n8n
    return json.dumps(await _n8n.n8n_get_workflow(workflow_id))


@mcp.tool()
async def n8n_create_workflow(name: str, nodes_json: str,
                              connections_json: str = "{}", active: bool = False) -> str:
    """Create (build) an n8n workflow.

    Args:
        name: workflow name.
        nodes_json: JSON array of n8n node objects.
        connections_json: JSON object of n8n connections (default {}).
        active: activate the workflow after creation.
    """
    from services import n8n as _n8n
    try:
        nodes = json.loads(nodes_json)
        conns = json.loads(connections_json) if connections_json else {}
    except Exception as exc:
        return json.dumps({"status": "failed", "error": f"bad JSON: {exc}"})
    return json.dumps(await _n8n.n8n_create_workflow(name, nodes, conns, active))


@mcp.tool()
async def n8n_add_authorization(name: str, auth_type: str = "oAuth2",
                                credentials_json: str = "{}") -> str:
    """Build an n8n authorization/credentials node for a service (e.g. Google).

    Returns a JSON string describing the credential node to embed in a workflow.
    This is the n8n "Add Authorization" step — it constructs the credential
    payload that a node references via `credentials`.

    Args:
        name: a label for the credential (e.g. 'Google Sheets').
        auth_type: oAuth2 | httpHeaderAuth | genericCredentialType, etc.
        credentials_json: optional extra fields for the credential.
    """
    try:
        extra = json.loads(credentials_json) if credentials_json else {}
    except Exception:
        extra = {}
    node = {
        "type": "n8n-nodes-base.httpRequest",
        "name": f"Auth: {name}",
        "typeVersion": 4.2,
        "position": [0, 0],
        "parameters": {"authentication": auth_type},
        "credentials": {name: extra},
    }
    return json.dumps({"status": "ok", "auth_node": node})


@mcp.tool()
async def n8n_add_spreadsheet(title: str, rows_json: str = "[]",
                              sheet_operation: str = "append") -> str:
    """Build an n8n Google Sheets workflow node (the 'Add Spreadsheet' step).

    Returns a JSON string describing the Sheets node to embed in a workflow.

    Args:
        title: spreadsheet/document title to target.
        rows_json: JSON array of row objects to write.
        sheet_operation: append | update | read.
    """
    try:
        rows = json.loads(rows_json) if rows_json else []
    except Exception:
        rows = []
    node = {
        "type": "n8n-nodes-base.googleSheets",
        "name": f"Sheets: {title}",
        "typeVersion": 4.5,
        "position": [0, 0],
        "parameters": {
            "operation": sheet_operation,
            "documentId": {"__rl": True, "value": title, "mode": "name"},
            "rows": rows,
        },
    }
    return json.dumps({"status": "ok", "sheets_node": node, "row_count": len(rows)})


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
def start_mcp_server():
    """Start the MCP SSE server (blocking). Called in a daemon thread by server.py."""
    mcp.run(transport="sse")


def mcp_is_enabled() -> bool:
    return bool(cfg.CONFIG.get("mcp", {}).get("server_enabled", True))
