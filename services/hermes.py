"""Hermes A2A service client for VirusGPT.

Sends a user message to the Hermes Agent peer over Agent-to-Agent (A2A)
JSON-RPC and returns the agent's reply. This is the bridge behind the
"Hermes" persona: talking to that persona relays the user's words to the
Hermes agent and surfaces its answer verbatim.

Verified against the hermes-VirusPC peer @ http://10.0.0.120:9900:
  * The peer speaks the A2A ``message/send`` JSON-RPC method and returns the
    FINAL task result synchronously (no separate poll needed). ``message/get``
    and ``tasks/*`` are NOT implemented on this peer, so we send ``message/send``
    and, if a peer reports ``method not found`` (-32601), fall back to the
    documented ``tasks/send`` + ``tasks/get`` poll pattern for other peers.
  * Roles are the A2A enum (``ROLE_USER`` / ``ROLE_AGENT``).
  * The reply text lives in ``result.status.message.parts[].text`` (or
    ``result.artifacts[].parts[].text``).

Everything degrades gracefully to an error dict so callers (and /api/health)
never hard-fail. The send-token is NEVER hardcoded in tracked files: it is read
from the local env file at runtime, overrideable via VG_HERMES_TOKEN.

Auth note: a ``-32050 unauthorized`` JSON-RPC error means the token was rejected
by the peer — callers should surface that and stop retrying.
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid

from services import config as cfg, get_client

# Local env file that holds the A2A send-token for the VirusPC peer. Read at
# runtime only; do not commit the token into tracked source/config.
_TOKEN_FILE = "/Users/Master/.hermes/cache/a2a_tokens_win.env"
_TOKEN_KEY = "A2A_PEER_TOKENS"
_DEFAULT_URL = "http://10.0.0.120:9900"

# Terminal task states (both the plain and the TASK_STATE_* enum forms).
_TERMINAL = {"completed", "failed", "canceled", "rejected",
             "TASK_STATE_COMPLETED", "TASK_STATE_FAILED",
             "TASK_STATE_CANCELED", "TASK_STATE_REJECTED"}


def _base() -> str:
    return (os.environ.get("VG_HERMES_URL")
            or cfg.service_cfg("hermes").get("base_url")
            or _DEFAULT_URL).rstrip("/")


def _timeout(default: float = 60.0) -> float:
    return cfg.service_timeout("hermes", default)


def _token() -> str:
    """Hermes A2A send-token: env VG_HERMES_TOKEN wins, then config, then the
    local peer-token env file. Empty string if none found."""
    tok = (os.environ.get("VG_HERMES_TOKEN")
           or cfg.service_cfg("hermes").get("token")
           or "").strip()
    if tok:
        return tok
    try:
        for line in open(_TOKEN_FILE, encoding="utf-8").read().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == _TOKEN_KEY:
                return v.strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


def _auth_headers() -> dict:
    tok = _token()
    return {"Authorization": f"Bearer {tok}"} if tok else {}


def _is_code(payload: dict, code: int) -> bool:
    return isinstance(payload, dict) and (payload.get("error") or {}).get("code") == code


def _extract_reply(result: dict) -> str:
    """Pull the agent's reply text out of an A2A task result."""
    try:
        result = result or {}
        # 1) status.message.parts[].text
        msg = (result.get("status", {}) or {}).get("message") or {}
        for part in msg.get("parts", []) or []:
            t = (part or {}).get("text")
            if t:
                return t
        # 2) artifacts[].parts[].text
        for art in result.get("artifacts", []) or []:
            for part in (art or {}).get("parts", []) or []:
                t = (part or {}).get("text")
                if t:
                    return t
    except Exception:
        pass
    return ""


async def _rpc(method: str, params: dict) -> dict:
    """POST a single JSON-RPC request to the peer and return the parsed body."""
    body = {"jsonrpc": "2.0", "id": "1", "method": method, "params": params}
    client = get_client()
    r = await client.post(_base() + "/", headers=_auth_headers(), json=body,
                          timeout=_timeout())
    try:
        return r.json() if r.content else {}
    except Exception:
        return {}


async def _ask_via_tasks(text: str) -> dict:
    """Fallback for peers that implement the documented tasks/send + tasks/get
    polling pattern (the hermes-VirusPC peer does NOT — it uses message/send)."""
    task_id = str(uuid.uuid4())
    send_params = {
        "id": task_id,
        "message": {"role": "ROLE_USER", "parts": [{"text": text}]},
    }
    data = await _rpc("tasks/send", send_params)
    if _is_code(data, -32050):
        return {"status": "failed",
                "error": "hermes unauthorized (-32050): token rejected by peer"}
    if isinstance(data, dict) and data.get("error"):
        return {"status": "failed", "error": f"hermes tasks/send error: {data['error']}"}

    deadline = time.time() + _timeout()
    last_state = ""
    while time.time() < deadline:
        last = await _rpc("tasks/get", {"id": task_id})
        if _is_code(last, -32050):
            return {"status": "failed",
                    "error": "hermes unauthorized (-32050): token rejected by peer"}
        result = (last.get("result") or {}) if isinstance(last, dict) else {}
        last_state = (result.get("status", {}) or {}).get("state", "")
        if last_state in _TERMINAL:
            reply = _extract_reply(result)
            if not reply:
                return {"status": "failed",
                        "error": f"hermes task {last_state} returned no reply text"}
            return {"status": "ok", "reply": reply, "state": last_state}
        await asyncio.sleep(2.0)
    return {"status": "failed",
            "error": "hermes timeout: no terminal response within deadline",
            "last_state": last_state}


async def ask_hermes(text: str) -> dict:
    """Relay `text` to the Hermes Agent peer and return its reply.

    Returns {"status": "ok", "reply": <str>, "state": <str>} on success, or
    {"status": "failed", "error": <str>} on any failure. Never raises.
    """
    text = (text or "").strip()
    if not text:
        return {"status": "failed", "error": "empty message"}
    if not _token():
        return {"status": "failed",
                "error": "hermes not authenticated (no token; set VG_HERMES_TOKEN or the hermes.service.token)"}

    # Primary method: message/send (synchronous final result on this peer).
    res = await _rpc("message/send",
                     {"message": {"role": "ROLE_USER", "parts": [{"text": text}]}})
    if _is_code(res, -32601):
        # Peer doesn't implement message/send — try the documented tasks/* pattern.
        return await _ask_via_tasks(text)
    if _is_code(res, -32050):
        return {"status": "failed",
                "error": "hermes unauthorized (-32050): token rejected by peer"}
    if isinstance(res, dict) and res.get("error"):
        return {"status": "failed", "error": f"hermes message/send error: {res['error']}"}

    result = (res.get("result") or {}) if isinstance(res, dict) else {}
    state = (result.get("status", {}) or {}).get("state", "")
    if state and state not in _TERMINAL:
        # Non-terminal (rare for this synchronous peer) — poll a touch, but the
        # documented method here is message/send which already returned final.
        pass
    reply = _extract_reply(result)
    if not reply:
        return {"status": "failed",
                "error": f"hermes task {state or 'unknown'} returned no reply text"}
    return {"status": "ok", "reply": reply, "state": state or "completed"}


async def hermes_status() -> dict:
    """Graceful status flag for /api/health (never raises, never hard-fails)."""
    enabled = bool(cfg.service_cfg("hermes").get("enabled", False))
    base = _base()
    info = {"enabled": enabled, "base_url": base, "has_token": bool(_token())}
    if enabled:
        try:
            r = await get_client().get(base + "/.well-known/agent-card.json",
                                       timeout=6.0)
            info["reachable"] = (r.status_code == 200)
            if r.status_code == 200:
                try:
                    info["peer_name"] = r.json().get("name")
                except Exception:
                    pass
        except Exception:
            info["reachable"] = False
    else:
        info["reachable"] = False
    return info
