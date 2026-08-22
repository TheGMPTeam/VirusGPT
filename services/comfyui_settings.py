"""ComfyUI service settings + tools.

Exposes ComfyUI configuration (URL, default model, timeout), live status with
available checkpoints, and a registry of runnable tools (render image, list
models). Generation is an external compute side effect — render requires confirm.
"""
from __future__ import annotations

from typing import Any, List

from services import settings_base as base
from services import comfyui as _client

SERVICE = "comfyui"


async def _status() -> dict:
    enabled = base.cfg.service_cfg(SERVICE).get("enabled", False)
    healthy = await _client.comfyui_health() if enabled else False
    models = await _client.comfyui_models() if healthy else []
    return {
        "enabled": enabled,
        "healthy": healthy,
        "base_url": _client._base(),
        "default_model": _client._default_model(),
        "models": models,
    }


base.register_status(SERVICE, _status)

TOOLS: List[base.ToolDef] = [
    base.ToolDef(
        name="list_models",
        description="List checkpoint filenames available on the ComfyUI server.",
        params={},
        run=_client.comfyui_models,
    ),
    base.ToolDef(
        name="render_image",
        description="Generate an image from a prompt. External compute / output "
                    "written to data/generated — requires confirm.",
        params={
            "prompt": {"type": "str", "required": True},
            "negative_prompt": {"type": "str", "default": ""},
            "model": {"type": "str", "default": ""},
            "steps": {"type": "int", "default": 25},
            "cfg_scale": {"type": "float", "default": 7.0},
            "width": {"type": "int", "default": 1024},
            "height": {"type": "int", "default": 1024},
        },
        confirm=True,
        run=_client.render_image,
    ),
]


def read_settings() -> dict:
    return base.read_settings(SERVICE)


def write_settings(patch: dict) -> dict:
    return base.write_settings(SERVICE, patch)


def list_tools() -> dict:
    return base.list_tools(TOOLS)


async def run_tool(name: str, **kwargs: Any) -> dict:
    return await base.run_tool(TOOLS, name, **kwargs)
