"""Memory MCP client — talks to the shared understory (OKF) memory server.

The memory pool is shared across machines (this Mac + Windows). Uses the MCP
JSON-RPC protocol over HTTP (SSE transport: must Accept both application/json
and text/event-stream). All calls go through `tools/call`. Uses the shared
pooled httpx client.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from services import get_client

logger = logging.getLogger("vg.memory")


async def mcp_call(method: str, params: Optional[dict] = None, base_url: str = "",
                   timeout: float = 12.0) -> Optional[dict]:
    """Call an MCP method (tools/call or tools/list). Returns the `result` dict."""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    try:
        r = await get_client().post(base_url, json=payload, headers=headers, timeout=timeout)
        if r.status_code != 200:
            logger.warning("MCP %d: %s", r.status_code, r.text[:200])
            return None
        body = r.json()
        if "error" in body:
            logger.warning("MCP error: %s", body["error"])
            return None
        return body.get("result")
    except Exception as exc:  # noqa: BLE001
        logger.warning("MCP call failed: %s", exc)
        return None


def _text_of(result: Optional[dict]) -> str:
    """Extract the text payload from an MCP tool result."""
    if not result:
        return ""
    content = result.get("content", [])
    if content and isinstance(content, list):
        return content[0].get("text", "") if isinstance(content[0], dict) else str(content[0])
    return ""


async def memory_status(base_url: str) -> Optional[dict]:
    """Deterministic OKF stats (no LLM). Returns parsed JSON dict or None."""
    res = await mcp_call("tools/call", {"name": "memory_status", "arguments": {}}, base_url)
    if not res:
        return None
    txt = _text_of(res)
    try:
        return json.loads(txt)
    except (json.JSONDecodeError, TypeError):
        return {"raw": txt}


async def memory_query(question: str, base_url: str) -> str:
    res = await mcp_call("tools/call", {"name": "memory_query", "arguments": {"question": question}}, base_url)
    if not res:
        return ""
    return _text_of(res)


async def memory_health(base_url: str) -> bool:
    return await memory_status(base_url) is not None
