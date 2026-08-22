"""Maton API Gateway client for VirusGPT.

Connects VirusGPT to third-party apps (YouTube, Gmail, Snapchat, ...) through
the Maton gateway at https://api.maton.ai. Maton injects the OAuth credential
for each app the user has *connected* in their Maton account, so VirusGPT never
holds Google/Snapchat tokens itself.

How the gateway works (from the Maton api-gateway skill):
  * One Maton API key (VG_MARTON_KEY env or services.marton.api_key) is sent as
    `Authorization: Bearer <key>`.
  * Each app is reached by its gateway path prefix:
        youtube     -> /youtube/youtube/v3/...    (proxies www.googleapis.com)
        gmail       -> /google-mail/gmail/v1/...   (proxies gmail.googleapis.com)
        snapchat    -> /snapchat/v1/...            (proxies adsapi.snapchat.com)
  * To target a specific connection when several exist for one app, set
    services.marton.connection_id (or VG_MARTON_CONN) -> `Maton-Connection` header.

CRITICAL: the gateway only routes if the matching connection exists in the
Maton account (`maton connection create youtube`, etc.). We probe a cheap read
endpoint per app to report connection status so /api/services/status shows
exactly which apps are wired (vs. merely "key present").

All write operations are GUARDED: callers must pass confirm=True. This mirrors
Maton's own "explicit user confirmation before any POST/PUT/PATCH/DELETE" policy
and the VirusGPT safety stance for external-side-effect operations.

The key is NEVER hardcoded in source. It comes from the env var VG_MARTON_KEY
or the (untracked) services.marton.api_key config field.
"""
from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

from services import config as cfg, get_client

# Maton gateway root. If services.marton.base_url is empty we fall back to this.
_GATEWAY = "https://api.maton.ai"

# Per-app gateway path prefixes (see Maton reference READMEs).
_APP_PREFIX = {
    "youtube": "/youtube",   # -> www.googleapis.com
    "gmail": "/google-mail",   # -> gmail.googleapis.com
    "snapchat": "/snapchat",   # -> adsapi.snapchat.com
}

# Cheap read probes used to detect whether a connection exists for an app.
# A 200 => connected; 400 (Missing connection) => not connected; 401 => bad key.
_PROBE = {
    "youtube": ("GET", "/youtube/youtube/v3/channels?part=snippet&mine=true"),
    "gmail": ("GET", "/google-mail/gmail/v1/users/me/profile"),
    "snapchat": ("GET", "/snapchat/v1/me"),
}


# --------------------------------------------------------------------------
# Low-level gateway plumbing
# --------------------------------------------------------------------------
def _base() -> str:
    return (cfg.service_cfg("marton").get("base_url") or _GATEWAY).rstrip("/")


def _api_key() -> str:
    """Maton key: env VG_MARTON_KEY wins, then config services.marton.api_key."""
    return (os.environ.get("VG_MARTON_KEY")
            or cfg.service_cfg("marton").get("api_key") or "").strip()


def _conn_id() -> str:
    """Optional specific connection pin (env VG_MARTON_CONN wins)."""
    return (os.environ.get("VG_MARTON_CONN")
            or cfg.service_cfg("marton").get("connection_id") or "").strip()


def _auth_headers(extra: dict | None = None) -> dict:
    h = {"Authorization": f"Bearer {_api_key()}"}
    cid = _conn_id()
    if cid:
        h["Maton-Connection"] = cid
    if extra:
        h.update(extra)
    return h


async def _request(method: str, path: str, *,
                   params: dict | None = None,
                   json_body: dict | None = None,
                   data: bytes | None = None,
                   headers: dict | None = None,
                   timeout: float = 60.0) -> tuple[int, object]:
    """Raw gateway call. Returns (status_code, parsed_json_or_text).

    `path` is the FULL gateway path including the app prefix (e.g.
    /youtube/youtube/v3/channels). Callers build app-prefixed paths via
    _app_path() so the prefix logic lives in one place.
    """
    url = f"{_base()}{path}"
    hdrs = _auth_headers(headers)
    try:
        r = await get_client().request(
            method, url, params=params, json=json_body,
            content=data, headers=hdrs, timeout=timeout,
        )
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, r.text
    except Exception as exc:
        return -1, f"marton gateway error: {exc}"


def _app_path(app: str, upstream: str) -> str:
    """Join an app prefix with its upstream path."""
    return f"{_APP_PREFIX[app]}{upstream}"


# --------------------------------------------------------------------------
# Health & status
# --------------------------------------------------------------------------
async def marton_health() -> bool:
    """True if the gateway is reachable (any HTTP response, even 401/400)."""
    try:
        r = await get_client().get(_base(), timeout=6.0)
        return r.status_code > 0  # any response => reachable
    except Exception:
        return False


async def _app_connected(app: str) -> bool:
    """Probe whether a connection exists for `app` in the Maton account."""
    if not _api_key():
        return False
    method, path = _PROBE[app]
    code, _ = await _request(method, path, timeout=8.0)
    return code == 200


async def marton_status() -> dict:
    """Status dict consumed by /api/services/status (mirrors n8n block).

    Reports: enabled, configured (key present), healthy (gateway reachable),
    and per-app connection state (youtube/gmail/snapchat). Connection probes
    only run when a key is configured, so an unconfigured client stays cheap.
    """
    enabled = cfg.service_cfg("marton").get("enabled", False)
    configured = bool(_api_key())
    healthy = await marton_health() if configured else False
    youtube = gmail = snapchat = False
    if configured and healthy:
        youtube, gmail, snapchat = await asyncio.gather(
            _app_connected("youtube"),
            _app_connected("gmail"),
            _app_connected("snapchat"),
        )
    return {
        "enabled": enabled,
        "configured": configured,
        "healthy": healthy,
        "base_url": _base(),
        "connection_id": _conn_id() or None,
        "connections": {
            "youtube": youtube,
            "gmail": gmail,
            "snapchat": snapchat,
        },
    }


# --------------------------------------------------------------------------
# YouTube (full control: read + upload + playlists)
# --------------------------------------------------------------------------
async def youtube_channel() -> dict:
    code, body = await _request(
        "GET", _app_path("youtube", "/youtube/v3/channels?part=snippet,statistics,contentDetails&mine=true"))
    if code != 200:
        return {"status": "failed", "error": f"youtube {code}: {body}"}
    return {"status": "ok", "channel": body}


async def youtube_list_playlists(max_results: int = 25) -> dict:
    code, body = await _request(
        "GET", _app_path("youtube", "/youtube/v3/playlists"),
        params={"part": "snippet,contentDetails", "mine": "true", "maxResults": max_results})
    if code != 200:
        return {"status": "failed", "error": f"youtube {code}: {body}"}
    items = body.get("items", []) if isinstance(body, dict) else []
    return {
        "status": "ok",
        "count": len(items),
        "playlists": [{"id": p.get("id"), "title": p.get("snippet", {}).get("title"),
                       "privacy": p.get("status", {}).get("privacyStatus")} for p in items],
    }


async def youtube_upload_video(file_path: str, title: str, description: str = "",
                               privacy: str = "private", category_id: str = "22",
                               confirm: bool = False) -> dict:
    """Upload a video to the authenticated channel (multipart single request).

    Uses YouTube's multipart/related uploadType=multipart so the whole call is
    ONE request through the gateway (no two-step resumable handshake, which
    would return a googleapis.com URL the gateway can't auth). Large files are
    still accepted but streamed in one body; for very large uploads prefer the
    Maton CLI's resumable path. Requires confirm=True (external side effect).
    """
    if not confirm:
        return {"status": "confirm_required",
                "error": "youtube upload requires confirm=true (external publish)"}
    path = Path(file_path)
    if not path.exists():
        return {"status": "failed", "error": f"file not found: {file_path}"}
    metadata = {
        "snippet": {"title": title, "description": description, "categoryId": category_id},
        "status": {"privacyStatus": privacy},
        "contentDetails": {},
    }
    mime = mimetypes.guess_type(str(path))[0] or "video/mp4"
    boundary = "vg-marton-yt-upload"
    meta_bytes = json.dumps(metadata).encode("utf-8")
    media_bytes = path.read_bytes()
    body = (
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode()
        + meta_bytes + b"\r\n"
        + f"--{boundary}\r\nContent-Type: {mime}\r\n\r\n".encode()
        + media_bytes + b"\r\n"
        + f"--{boundary}--\r\n".encode()
    )
    headers = {"Content-Type": f"multipart/related; boundary={boundary}"}
    code, resp = await _request(
        "POST", _app_path("youtube", "/youtube/v3/videos?uploadType=multipart&part=snippet,status,contentDetails"),
        data=body, headers=headers, timeout=300.0)
    if code not in (200, 201):
        return {"status": "failed", "error": f"youtube upload {code}: {resp}"}
    vid = resp.get("id") if isinstance(resp, dict) else None
    return {"status": "ok", "video_id": vid, "response": resp}


# --------------------------------------------------------------------------
# Gmail (send + read)
# --------------------------------------------------------------------------
def _gmail_raw(to: str, subject: str, body: str, from_addr: str = "me") -> str:
    """Build an RFC2822 message and return base64url-encoded raw (no padding)."""
    msg = MIMEMultipart() if False else MIMEText(body, "plain", "utf-8")
    msg["to"] = to
    msg["subject"] = subject
    if from_addr and from_addr != "me":
        msg["from"] = from_addr
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    return raw.rstrip("=")


async def gmail_profile() -> dict:
    code, body = await _request("GET", _app_path("gmail", "/gmail/v1/users/me/profile"))
    if code != 200:
        return {"status": "failed", "error": f"gmail {code}: {body}"}
    return {"status": "ok", "profile": body}


async def gmail_list_messages(query: str = "", max_results: int = 10) -> dict:
    params: dict[str, Any] = {"maxResults": max_results}
    if query:
        params["q"] = query
    code, body = await _request("GET", _app_path("gmail", "/gmail/v1/users/me/messages"), params=params)
    if code != 200:
        return {"status": "failed", "error": f"gmail {code}: {body}"}
    msgs = body.get("messages", []) if isinstance(body, dict) else []
    npt = body.get("nextPageToken") if isinstance(body, dict) else None
    return {"status": "ok", "count": len(msgs), "messages": msgs, "nextPageToken": npt}


async def gmail_send(to: str, subject: str, body: str, confirm: bool = False) -> dict:
    """Send a Gmail message. Requires confirm=True (external send side effect)."""
    if not confirm:
        return {"status": "confirm_required",
                "error": "gmail send requires confirm=true (external send)"}
    raw = _gmail_raw(to, subject, body)
    code, resp = await _request(
        "POST", _app_path("gmail", "/gmail/v1/users/me/messages/send"),
        json_body={"raw": raw}, timeout=30.0)
    if code not in (200, 201):
        return {"status": "failed", "error": f"gmail send {code}: {resp}"}
    return {"status": "ok", "id": resp.get("id") if isinstance(resp, dict) else None}


# --------------------------------------------------------------------------
# Snapchat (Ads API: orgs, ad accounts, campaigns, media upload)
# --------------------------------------------------------------------------
async def snapchat_me() -> dict:
    code, body = await _request("GET", _app_path("snapchat", "/v1/me"))
    if code != 200:
        return {"status": "failed", "error": f"snapchat {code}: {body}"}
    return {"status": "ok", "me": body}


async def snapchat_organizations() -> dict:
    code, body = await _request("GET", _app_path("snapchat", "/v1/me/organizations"))
    if code != 200:
        return {"status": "failed", "error": f"snapchat {code}: {body}"}
    orgs = (body.get("organizations") or []) if isinstance(body, dict) else []
    return {"status": "ok", "count": len(orgs),
            "organizations": [{"id": o.get("id"), "name": o.get("name")} for o in orgs]}


async def snapchat_ad_accounts(organization_id: str) -> dict:
    code, body = await _request(
        "GET", _app_path("snapchat", f"/v1/organizations/{organization_id}/adaccounts"))
    if code != 200:
        return {"status": "failed", "error": f"snapchat {code}: {body}"}
    accts = (body.get("adaccounts") or []) if isinstance(body, dict) else []
    return {"status": "ok", "count": len(accts),
            "ad_accounts": [{"id": a.get("id"), "name": a.get("name")} for a in accts]}


async def snapchat_list_campaigns(ad_account_id: str) -> dict:
    code, body = await _request(
        "GET", _app_path("snapchat", f"/v1/adaccounts/{ad_account_id}/campaigns"))
    if code != 200:
        return {"status": "failed", "error": f"snapchat {code}: {body}"}
    camps = (body.get("campaigns") or []) if isinstance(body, dict) else []
    return {"status": "ok", "count": len(camps),
            "campaigns": [{"id": c.get("id"), "name": c.get("name"), "status": c.get("status")}
                          for c in camps]}


async def snapchat_upload_media(ad_account_id: str, file_path: str, name: str = "",
                                confirm: bool = False) -> dict:
    """Upload a creative media file to a Snapchat ad account.

    Snapchat expects multipart/form-data with the binary under the `file`
    field. Requires confirm=True (external upload side effect).
    """
    if not confirm:
        return {"status": "confirm_required",
                "error": "snapchat media upload requires confirm=true (external upload)"}
    path = Path(file_path)
    if not path.exists():
        return {"status": "failed", "error": f"file not found: {file_path}"}
    mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    media = path.read_bytes()
    files = {"file": (path.name, media, mime)}
    # Use httpx files via a direct request (form-data, not the related body).
    url = f"{_base()}{_app_path('snapchat', f'/v1/adaccounts/{ad_account_id}/media')}"
    try:
        r = await get_client().post(
            url, files=files, headers=_auth_headers(), timeout=120.0)
        try:
            resp = r.json()
        except Exception:
            resp = r.text
        if r.status_code not in (200, 201):
            return {"status": "failed", "error": f"snapchat media {r.status_code}: {resp}"}
        return {"status": "ok", "response": resp}
    except Exception as exc:
        return {"status": "failed", "error": f"snapchat media error: {exc}"}

