"""Ollama chat client (streaming, OpenAI-compatible /api/chat).

Forwards to a remote Ollama (default 10.0.0.120:11434) and yields SSE-style
chunks {content} / {done} / {error} — the exact contract the VirusGPT client
expects.
"""
from __future__ import annotations

import json
import logging
from typing import AsyncGenerator, Optional

import httpx

logger = logging.getLogger("vg.llm")


async def stream_chat(
    model: str,
    messages: list[dict],
    base_url: str,
    timeout: float = 120.0,
) -> AsyncGenerator[dict, None]:
    """Yield dicts: {"content": str} | {"done": true} | {"error": str}."""
    url = f"{base_url.rstrip('/')}/api/chat"
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", url, json=payload) as resp:
                if resp.status_code != 200:
                    err = (await resp.aread()).decode()[:300]
                    yield {"error": f"Ollama {resp.status_code}: {err}"}
                    return
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    # Ollama native stream shape: {"message":{"content":...},"done":bool}
                    msg = obj.get("message") or {}
                    content = msg.get("content")
                    if content:
                        yield {"content": content}
                    if obj.get("done"):
                        yield {"done": True}
    except httpx.ConnectError as exc:
        yield {"error": f"Ollama unreachable at {base_url}: connection refused"}
    except httpx.TimeoutException as exc:
        yield {"error": f"Ollama at {base_url} timed out / unreachable: {type(exc).__name__}"}
    except Exception as exc:  # noqa: BLE001
        logger.exception("llm stream failed")
        yield {"error": str(exc) or f"Ollama error: {type(exc).__name__}"}


async def list_models(base_url: str) -> list[str]:
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(f"{base_url.rstrip('/')}/api/tags")
            if r.status_code == 200:
                return [m["name"] for m in r.json().get("models", [])]
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not list ollama models: %s", exc)
    return []


async def ollama_healthy(base_url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            r = await client.get(f"{base_url.rstrip('/')}/api/tags")
            return r.status_code == 200
    except Exception:
        return False
