"""Marton API Gateway service settings + tools.

Per-service module for the Maton Gateway, which proxies third-party apps
(YouTube, Gmail, Snapchat, ...) through a single authenticated key. Exposes
the gateway key + connection settings, live connection status (per app), and a
registry of runnable tools (read channel/profile/account, upload, send).

The API key is runtime-only — never persisted to the tracked config.json
(mirrors the VG_MARTON_KEY rule). Updating it via write_settings applies it to
the live process env immediately (see settings_base._SECRET_ENV_MAP).
"""
from __future__ import annotations

from typing import Any, List

from services import settings_base as base
from services import marton as _client

SERVICE = "marton"


async def _status() -> dict:
    st = await _client.marton_status()
    return {
        "enabled": st.get("enabled", False),
        "configured": st.get("configured", False),
        "healthy": st.get("healthy", False),
        "base_url": st.get("base_url", ""),
        "connection_id": st.get("connection_id"),
        "connections": st.get("connections", {}),
    }


base.register_status(SERVICE, _status)

TOOLS: List[base.ToolDef] = [
    base.ToolDef(
        name="youtube_channel",
        description="Get the authenticated YouTube channel (TheGMPTeam Studios).",
        params={},
        run=_client.youtube_channel,
    ),
    base.ToolDef(
        name="youtube_playlists",
        description="List the channel's YouTube playlists.",
        params={"max_results": {"type": "int", "default": 25}},
        run=_client.youtube_list_playlists,
    ),
    base.ToolDef(
        name="youtube_upload",
        description="Upload a video file to YouTube. External publish — requires confirm.",
        params={
            "file_path": {"type": "str", "required": True},
            "title": {"type": "str", "required": True},
            "description": {"type": "str", "default": ""},
            "privacy": {"type": "str", "default": "private"},
            "confirm": {"type": "bool", "default": False},
        },
        confirm=True,
        run=_client.youtube_upload_video,
    ),
    base.ToolDef(
        name="gmail_profile",
        description="Get the authenticated Gmail account profile.",
        params={},
        run=_client.gmail_profile,
    ),
    base.ToolDef(
        name="gmail_messages",
        description="List recent Gmail messages (optionally filtered by query).",
        params={
            "query": {"type": "str", "default": ""},
            "max_results": {"type": "int", "default": 10},
        },
        run=_client.gmail_list_messages,
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
