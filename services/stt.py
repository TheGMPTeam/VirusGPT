"""STT client — talks to a local Whisper (whisper-fastapi style) server.

Endpoint: POST {base_url}/v1/audio/transcriptions  (multipart file)
Returns {text}. Optional; degrades gracefully when down. Uses the shared
pooled httpx client.
"""
from __future__ import annotations

import logging
from typing import Optional

from services import get_client

logger = logging.getLogger("vg.stt")


async def stt_health(base_url: str) -> bool:
    try:
        r = await get_client().get(f"{base_url.rstrip('/')}/health", timeout=3.0)
        return r.status_code in (200, 404)
    except Exception:
        return False


async def transcribe(audio_bytes: bytes, mime_type: str, base_url: str,
                     timeout: float = 30.0) -> Optional[dict]:
    """Return {text} or None."""
    if not audio_bytes:
        return None
    ext = "webm"
    if "wav" in mime_type:
        ext = "wav"
    elif "mp4" in mime_type or "m4a" in mime_type:
        ext = "m4a"
    elif "ogg" in mime_type:
        ext = "ogg"
    files = {"file": (f"audio.{ext}", audio_bytes, mime_type or "audio/webm")}
    try:
        r = await get_client().post(
            f"{base_url.rstrip('/')}/v1/audio/transcriptions",
            files=files, data={"response_format": "json"},
            timeout=timeout,
        )
        if r.status_code == 200:
            return {"text": r.json().get("text", "")}
        logger.warning("STT %d: %s", r.status_code, r.text[:200])
    except Exception as exc:  # noqa: BLE001
        logger.warning("STT failed: %s", exc)
    return None
