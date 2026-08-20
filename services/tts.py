"""TTS client — talks to a local PocketTTS OpenAI-compatible server.

Endpoint: POST {base_url}/v1/audio/speech  body {model,input,voice,response_format}
Returns audio bytes. Voice cloning is done by passing a reference-audio FILE PATH
as `voice` (PocketTTS encodes it against the active model).
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger("vg.tts")


async def tts_health(base_url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{base_url.rstrip('/')}/health")
            return r.status_code in (200, 404)  # 404 still means server up
    except Exception:
        return False


async def list_voices(base_url: str) -> list[str]:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{base_url.rstrip('/')}/v1/voices")
            if r.status_code == 200:
                return [v["id"] for v in r.json().get("data", [])]
    except Exception as exc:  # noqa: BLE001
        logger.warning("tts voices list failed: %s", exc)
    return []


async def synthesize(
    text: str,
    voice: str,
    base_url: str,
    response_format: str = "mp3",
    timeout: float = 120.0,
) -> Optional[bytes]:
    """Return audio bytes, or None on failure."""
    url = f"{base_url.rstrip('/')}/v1/audio/speech"
    payload = {
        "model": "tts-1",
        "input": text,
        "voice": voice,
        "response_format": response_format,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(url, json=payload)
            if r.status_code == 200:
                return r.content
            logger.warning("TTS %d: %s", r.status_code, r.text[:200])
    except Exception as exc:  # noqa: BLE001
        logger.warning("TTS failed: %s", exc)
    return None
