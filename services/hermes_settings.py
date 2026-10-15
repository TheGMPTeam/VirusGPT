"""Hermes (A2A bridge) service settings + tools.

Exposes the Hermes peer configuration (base_url / enabled / timeout / token)
through the standard settings plumbing. The A2A send-token is a secret: it is
masked on read, never persisted to config.json, and saved to the durable
data-dir .env as VG_HERMES_TOKEN (see settings_base._SECRET_ENV_MAP).
"""
from __future__ import annotations

from typing import Any, List

from services import settings_base as base
from services import hermes as _client

SERVICE = "hermes"


async def _status() -> dict:
    return await _client.hermes_status()


base.register_status(SERVICE, _status)


TOOLS: List[base.ToolDef] = [
    base.ToolDef(
        name="ask_hermes",
        description="Send a message to the Hermes agent peer over A2A and "
                    "return its reply. External side effect — requires confirm.",
        params={"text": {"type": "str", "required": True}},
        confirm=True,
        run=_client.ask_hermes,
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
