"""YouTube service settings + tools (via Maton API Gateway).

Per-service module for the YouTube connection (TheGMPTeam Studios). Exposes
gateway/connection settings, live connection status, and a registry of
runnable tools: read channel/playlists, and upload a video (external publish
— requires confirm).
"""
from __future__ import annotations

from typing import Any, List

from services import settings_base as base
from services import marton as _client

SERVICE = "youtube"


async def _status() -> dict:
    st = await _client.marton_status()
    return {
        "enabled": st["enabled"],
        "connected": st["connections"]["youtube"],
        "gateway": st["base_url"],
        "connection_id": st["connection_id"],
    }


base.register_status(SERVICE, _status)

TOOLS: List[base.ToolDef] = [
    base.ToolDef(
        name="channel",
        description="Get the authenticated YouTube channel (TheGMPTeam Studios).",
        params={},
        run=_client.youtube_channel,
    ),
    base.ToolDef(
        name="playlists",
        description="List the channel's playlists.",
        params={"max_results": {"type": "int", "default": 25}},
        run=_client.youtube_list_playlists,
    ),
    base.ToolDef(
        name="upload_video",
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
]


def read_settings() -> dict:
    return base.read_settings(SERVICE)


def write_settings(patch: dict) -> dict:
    return base.write_settings(SERVICE, patch)


def list_tools() -> dict:
    return base.list_tools(TOOLS)


async def run_tool(name: str, **kwargs: Any) -> dict:
    return await base.run_tool(TOOLS, name, **kwargs)
