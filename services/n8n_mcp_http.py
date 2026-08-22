"""Minimal MCP Streamable-HTTP client for n8n-mcp.

The Python `mcp` SDK's stdio/SSE clients deadlock against n8n-mcp 2.x on this
Node build (anyio cancel-scope crash on the initialize handshake), so we speak
the MCP Streamable HTTP protocol directly with httpx: JSON-RPC-over-POST with
the `Mcp-Session-Id` response header. This is the reliable path and uses the
n8n API token (N8N_API_KEY / VG_N8N_TOKEN) that n8n-mcp forwards to n8n.

Protocol:
  1. POST initialize  -> 200, capture Mcp-Session-Id header
  2. POST notifications/initialized (no id) -> 202
  3. POST tools/list / tools/call (include Mcp-Session-Id + Authorization)
Responses may be application/json or text/event-stream (SSE) frames.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import httpx


class N8nMcpHttpClient:
    """Tiny MCP Streamable-HTTP client for a single n8n-mcp server."""

    def __init__(self, endpoint: str, token: str, timeout: float = 30.0):
        # endpoint is .../mcp; strip it for the httpx base url.
        self.endpoint = endpoint.rstrip("/")
        root = self.endpoint.rsplit("/mcp", 1)[0].rstrip("/") or "http://127.0.0.1"
        self.token = token
        self._client = httpx.Client(
            base_url=root,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
        self._session_id: Optional[str] = None
        self._tools: List[Dict[str, Any]] = []
        self._initialized = False

    def _post(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        headers = {"Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream"}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        r = self._client.post("/mcp", headers=headers, json=payload)
        if "mcp-session-id" in r.headers:
            self._session_id = r.headers["mcp-session-id"]
        if r.status_code >= 400:
            raise RuntimeError(f"n8n-mcp {r.status_code}: {r.text[:200]}")
        text = r.text.strip()
        if text.startswith("event:") or "data:" in text:
            for line in text.splitlines():
                if line.startswith("data:"):
                    return json.loads(line[len("data:"):].strip())
            raise RuntimeError("n8n-mcp: no data frame in SSE response")
        return r.json()

    def initialize(self) -> None:
        if self._initialized:
            return
        self._post({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "VirusGPT", "version": "1.0"},
            },
        })
        self._client.post(
            "/mcp",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                **({"Mcp-Session-Id": self._session_id} if self._session_id else {}),
                "Authorization": f"Bearer {self.token}",
            },
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        self._initialized = True

    def list_tools(self) -> List[Dict[str, Any]]:
        self.initialize()
        res = self._post({"jsonrpc": "2.0", "id": 2,
                          "method": "tools/list", "params": {}})
        self._tools = res.get("result", {}).get("tools", [])
        return self._tools

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        self.initialize()
        res = self._post({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })
        result = res.get("result", {})
        blocks = result.get("content", []) or []
        text = " ".join(b.get("text", "") for b in blocks if isinstance(b, dict))
        return {"status": "ok" if not result.get("isError") else "failed",
                "result": text or json.dumps(result)}

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass
