"""n8n service client for VirusGPT.

Thin async wrapper around a LAN n8n instance (the `n8n` Docker container on
the Windows box, default http://10.0.0.120:5678). Provides:
  * n8n_health()      -> bool   (n8n answers /healthz with 200 when running)
  * n8n_authenticated()-> bool   (can we call the authenticated REST API?)
  * n8n_status()      -> dict   (enabled / healthy / authenticated / base_url)

Auth: this n8n build uses the **API-key** scheme — the credential is sent in
the `X-N8N-API-KEY` header (a JWT issued by n8n works as the key). The token
is read from the `services.n8n.api_key` config OR the `VG_N8N_TOKEN` env var.
It is NEVER hardcoded in source. If absent, the client degrades gracefully
(healthy may still be True via /healthz, but authenticated will be False).

Only the STATUS/HEALTH + AUTH surface is wired here (no workflow triggering
yet). Everything degrades gracefully so /api/services/status stays green.

Note: config.json is committed to git, so the token MUST come from the
VG_N8N_TOKEN env var (or a local, untracked override) — never written into
the tracked config.json.
"""
from __future__ import annotations

import os

from services import config as cfg, get_client

# This n8n build exposes the v1 REST API under /api/v1 and wants the credential
# in X-N8N-API-KEY (a JWT issued by n8n is accepted as the key).
_API_PREFIX = "/api/v1"


def _base() -> str:
    return (cfg.service_cfg("n8n").get("base_url") or "http://10.0.0.120:5678").rstrip("/")


def _timeout(default: float = 10.0) -> float:
    return cfg.service_timeout("n8n", default)


def _api_key() -> str:
    """n8n credential: env VG_N8N_TOKEN wins, then config services.n8n.api_key."""
    return (os.environ.get("VG_N8N_TOKEN")
            or cfg.service_cfg("n8n").get("api_key") or "").strip()


def _auth_headers() -> dict:
    key = _api_key()
    return {"X-N8N-API-KEY": key} if key else {}


async def n8n_health() -> bool:
    """True if n8n is reachable and reports healthy (unauthenticated /healthz)."""
    try:
        r = await get_client().get(f"{_base()}/healthz", timeout=6.0)
        return r.status_code == 200
    except Exception:
        return False


async def n8n_authenticated() -> bool:
    """True if the configured API key is accepted by the authenticated API.

    Probes /api/v1/workflows — returns 200 with a valid key, 401 without/with a
    bad one. A 200 here means login succeeded.
    """
    if not _api_key():
        return False
    try:
        r = await get_client().get(
            f"{_base()}{_API_PREFIX}/workflows",
            headers=_auth_headers(),
            timeout=_timeout(),
        )
        return r.status_code == 200
    except Exception:
        return False


async def n8n_status() -> dict:
    """Status dict consumed by /api/services/status (mirrors comfyui block)."""
    enabled = cfg.service_cfg("n8n").get("enabled", False)
    healthy = await n8n_health() if enabled else False
    authenticated = await n8n_authenticated() if healthy else False
    return {
        "enabled": enabled,
        "healthy": healthy,
        "authenticated": authenticated,
        "base_url": _base(),
        "has_token": bool(_api_key()),
    }
