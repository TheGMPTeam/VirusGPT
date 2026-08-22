"""n8n service settings + tools.

Exposes n8n configuration (URL/token), live status, and a registry of
runnable tools (list/trigger/create/get workflows). Token is runtime-only —
never persisted to config.json (mirrors the VG_N8N_TOKEN rule).
"""
from __future__ import annotations

from typing import Any, List

from services import settings_base as base
from services import n8n as _client

SERVICE = "n8n"


async def _status() -> dict:
    return await _client.n8n_status()


base.register_status(SERVICE, _status)

TOOLS: List[base.ToolDef] = [
    base.ToolDef(
        name="list_workflows",
        description="List n8n workflows (optionally active only).",
        params={"active_only": {"type": "bool", "default": False}},
        run=_client.n8n_list_workflows,
    ),
    base.ToolDef(
        name="get_workflow",
        description="Fetch a single workflow definition by id.",
        params={"workflow_id": {"type": "str", "required": True}},
        run=_client.n8n_get_workflow,
    ),
    base.ToolDef(
        name="trigger_workflow",
        description="Trigger (execute) a workflow by id with optional input data. "
                    "External side effect — requires confirm.",
        params={
            "workflow_id": {"type": "str", "required": True},
            "data": {"type": "dict", "default": {}},
        },
        confirm=True,
        run=_client.n8n_trigger_workflow,
    ),
    base.ToolDef(
        name="create_workflow",
        description="Create (build) a new n8n workflow from nodes/connections. "
                    "External side effect — requires confirm.",
        params={
            "name": {"type": "str", "required": True},
            "nodes": {"type": "list", "required": True},
            "connections": {"type": "dict", "default": {}},
            "active": {"type": "bool", "default": False},
        },
        confirm=True,
        run=_client.n8n_create_workflow,
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
