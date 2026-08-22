"""Shared base for per-service Settings + Tools modules.

Each connected service (n8n, comfyui, marton, ...) exposes its own
`settings` module that, through this base, provides:
  * read_settings(name)   -> current config (secrets masked)
  * write_settings(name, patch) -> update runtime + persist non-secret fields
  * register_status / get_status -> delegate to the service's client status fn
  * ToolDef + a TOOLS registry pattern (see n8n_settings / comfyui_settings /
    marton_settings) with list_tools() (serializable) and run_tool().

Secrets (api_key, token, ...) are kept runtime-only and never written back to
the tracked config.json — exactly the rule used for VG_* env vars.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from services import config as cfg

# Keys that must NEVER be persisted to config.json (kept in env/runtime only).
SECRET_KEYS = {"api_key", "api_token", "token", "secret", "password", "connection_token"}


@dataclass
class ToolDef:
    """A single discoverable tool/action exposed by a service."""
    name: str
    description: str
    params: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    confirm: bool = False           # True => external side effect, needs confirm
    run: Optional[Callable] = None  # async callable(name, **kwargs) -> dict

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "params": self.params,
            "confirm": self.confirm,
        }


# --------------------------------------------------------------------------
# Settings (config get/set)
# --------------------------------------------------------------------------
def read_settings(name: str) -> Dict[str, Any]:
    """Return the service config block with secret values masked."""
    blk = dict(cfg.service_cfg(name))
    for k in list(blk.keys()):
        if k.lower() in SECRET_KEYS and blk[k]:
            blk[k] = "***"
    blk["_service"] = name
    return blk


def write_settings(name: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    """Update a service's config.

    Non-secret fields are persisted to config.json (via cfg.save_service_config)
    and applied to the live runtime config. Secret fields are applied to the
    runtime config ONLY (not written to disk) so credentials stay in env.
    Returns the refreshed (masked) settings.
    """
    patch = patch or {}
    runtime = cfg.CONFIG.setdefault("services", {}).setdefault(name, {})
    persisted: Dict[str, Any] = {}
    for k, v in patch.items():
        runtime[k] = v
        if k.lower() in SECRET_KEYS:
            continue
        persisted[k] = v
    if persisted:
        cfg.save_service_config(name, persisted)
    return read_settings(name)


# --------------------------------------------------------------------------
# Status dispatch (each service registers its client's status fn)
# --------------------------------------------------------------------------
_STATUS_FUNCS: Dict[str, Callable] = {}


def register_status(name: str, fn: Callable) -> None:
    _STATUS_FUNCS[name] = fn


async def get_status(name: str) -> Dict[str, Any]:
    fn = _STATUS_FUNCS.get(name)
    if fn is not None:
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001
            return {"enabled": cfg.service_cfg(name).get("enabled", False),
                    "error": f"status probe failed: {exc}"}
    return {"enabled": cfg.service_cfg(name).get("enabled", False)}


# --------------------------------------------------------------------------
# Tool registry helpers
# --------------------------------------------------------------------------
def list_tools(tools: list[ToolDef]) -> Dict[str, Any]:
    return {"tools": [t.to_dict() for t in tools]}


async def run_tool(tools: list[ToolDef], name: str, **kwargs: Any) -> Dict[str, Any]:
    """Find a tool by name and invoke its async handler with kwargs."""
    for t in tools:
        if t.name == name:
            if t.run is None:
                return {"status": "failed", "error": f"tool '{name}' has no handler"}
            try:
                return await t.run(**kwargs)
            except TypeError as exc:
                return {"status": "failed", "error": f"bad arguments for '{name}': {exc}"}
            except Exception as exc:  # noqa: BLE001
                return {"status": "failed", "error": f"tool '{name}' error: {exc}"}
    return {"status": "failed", "error": f"unknown tool '{name}'"}
