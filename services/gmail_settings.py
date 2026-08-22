"""Gmail service settings + tools (via Maton API Gateway).

Per-service module for the Gmail connection (thegmpteam@gmail.com). Exposes
gateway/connection settings, live connection status, and a registry of
runnable tools: read profile/messages, and send a message (external send —
requires confirm).
"""
from __future__ import annotations

from typing import Any, List

from services import settings_base as base
from services import marton as _client

SERVICE = "gmail"


async def _status() -> dict:
    st = await _client.marton_status()
    return {
        "enabled": st["enabled"],
        "connected": st["connections"]["gmail"],
        "gateway": st["base_url"],
        "connection_id": st["connection_id"],
    }


base.register_status(SERVICE, _status)

TOOLS: List[base.ToolDef] = [
    base.ToolDef(
        name="profile",
        description="Get the connected Gmail profile (address + message counts).",
        params={},
        run=_client.gmail_profile,
    ),
    base.ToolDef(
        name="list_messages",
        description="List Gmail messages (optional query filter).",
        params={
            "query": {"type": "str", "default": ""},
            "max_results": {"type": "int", "default": 10},
        },
        run=_client.gmail_list_messages,
    ),
    base.ToolDef(
        name="send",
        description="Send a Gmail message. External send — requires confirm.",
        params={
            "to": {"type": "str", "required": True},
            "subject": {"type": "str", "required": True},
            "body": {"type": "str", "default": ""},
            "confirm": {"type": "bool", "default": False},
        },
        confirm=True,
        run=_client.gmail_send,
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
