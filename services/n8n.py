"""n8n service client for VirusGPT.

Thin async wrapper around a LAN n8n instance (the `n8n` Docker container on
the Windows box, default http://10.0.0.120:5678). Provides:
  * n8n_health()  -> bool   (n8n answers /healthz with 200 when running)
  * n8n_status()  -> dict   (enabled / healthy / base_url / workflows)

Only the STATUS/HEALTH surface is wired here (no workflow triggering yet).
Everything degrades gracefully: if n8n is unreachable the client returns a
clear dict (never raises), so /api/services/status stays green.
"""
from __future__ import annotations

from services import config as cfg, get_client


def _base() -> str:
    return (cfg.service_cfg("n8n").get("base_url") or "http://10.0.0.120:5678").rstrip("/")


def _timeout(default: float = 10.0) -> float:
    return cfg.service_timeout("n8n", default)


async def n8n_health() -> bool:
    """True if n8n is reachable and reports healthy."""
    try:
        # n8n exposes /healthz returning 200 {"status":"ok"} when up.
        r = await get_client().get(f"{_base()}/healthz", timeout=6.0)
        return r.status_code == 200
    except Exception:
        return False


async def n8n_status() -> dict:
    """Status dict consumed by /api/services/status (mirrors comfyui block)."""
    enabled = cfg.service_cfg("n8n").get("enabled", False)
    healthy = await n8n_health() if enabled else False
    return {
        "enabled": enabled,
        "healthy": healthy,
        "base_url": _base(),
    }
