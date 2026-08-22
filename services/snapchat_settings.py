"""Snapchat service settings + tools (via Maton API Gateway).

Per-service module for the Snapchat connection. Exposes gateway/connection
settings, live connection status, and a registry of runnable tools: read
me/organizations/ad-accounts/campaigns, and upload creative media (external
upload — requires confirm).
"""
from __future__ import annotations

from typing import Any, List

from services import settings_base as base
from services import marton as _client

SERVICE = "snapchat"


async def _status() -> dict:
    st = await _client.marton_status()
    return {
        "enabled": st["enabled"],
        "connected": st["connections"]["snapchat"],
        "gateway": st["base_url"],
        "connection_id": st["connection_id"],
    }


base.register_status(SERVICE, _status)

TOOLS: List[base.ToolDef] = [
    base.ToolDef(
        name="me",
        description="Get the Snapchat authenticated user / org root.",
        params={},
        run=_client.snapchat_me,
    ),
    base.ToolDef(
        name="organizations",
        description="List Snapchat organizations for the connected user.",
        params={},
        run=_client.snapchat_organizations,
    ),
    base.ToolDef(
        name="ad_accounts",
        description="List ad accounts under a Snapchat organization.",
        params={"organization_id": {"type": "str", "required": True}},
        run=_client.snapchat_ad_accounts,
    ),
    base.ToolDef(
        name="campaigns",
        description="List campaigns under a Snapchat ad account.",
        params={"ad_account_id": {"type": "str", "required": True}},
        run=_client.snapchat_list_campaigns,
    ),
    base.ToolDef(
        name="upload_media",
        description="Upload a creative media file to a Snapchat ad account. "
                    "External upload — requires confirm.",
        params={
            "ad_account_id": {"type": "str", "required": True},
            "file_path": {"type": "str", "required": True},
            "name": {"type": "str", "default": ""},
            "confirm": {"type": "bool", "default": False},
        },
        confirm=True,
        run=_client.snapchat_upload_media,
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
