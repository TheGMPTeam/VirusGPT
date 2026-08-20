"""Shared HTTP infrastructure for VirusGPT service clients.

A single module-level AsyncClient with connection pooling is reused by the
llm / tts / stt / memory clients so we don't pay a TCP+TLS handshake on every
call. httpx keeps the pool alive across requests to the same host.
"""
from __future__ import annotations

import httpx

# One client for the whole process. limits raised so concurrent TTS/STT/chat
# streams don't queue behind a tiny default pool.
_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0),
            limits=httpx.Limits(
                max_connections=32,
                max_keepalive_connections=16,
                keepalive_expiry=30.0,
            ),
        )
    return _client


async def close_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None
