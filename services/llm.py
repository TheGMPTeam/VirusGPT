"""Ollama chat client (streaming, OpenAI-compatible /api/chat).

Forwards to a remote Ollama and yields SSE-style chunks. When `tools` is passed,
the model may respond with native `tool_calls` (Ollama function-calling); those
are surfaced as {"tool_calls": [...]} chunks so the caller can execute them.
Uses the shared pooled httpx client from services/__init__.py.
"""
from __future__ import annotations

import json
import logging
from typing import AsyncGenerator, Optional

import httpx
from services import get_client

logger = logging.getLogger("vg.llm")


async def stream_chat(
    model: str,
    messages: list[dict],
    base_url: str,
    timeout: float = 120.0,
    tools: Optional[list] = None,
    tool_choice: Optional[object] = None,
    think: Optional[bool] = None,
    options: Optional[dict] = None,
) -> AsyncGenerator[dict, None]:
    """Yield dicts: {"content": str} | {"tool_calls": [...]} | {"done": True} | {"error": str}."""
    url = f"{base_url.rstrip('/')}/api/chat"
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
    }
    if think is not None:
        payload["think"] = bool(think)
    if options:
        payload["options"] = options
    if tools:
        payload["tools"] = tools
        # tool_choice: "auto" (default), "none", or a forced function dict.
        payload["tool_choice"] = tool_choice if tool_choice is not None else "auto"
    try:
        async with get_client().stream(
            "POST", url, json=payload, timeout=timeout
        ) as resp:
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
                msg = obj.get("message") or {}
                content = msg.get("content")
                if content:
                    yield {"content": content}
                tc = msg.get("tool_calls")
                if tc:
                    yield {"tool_calls": tc}
                if obj.get("done"):
                    yield {"done": True}
    except httpx.ConnectError:
        yield {"error": f"Ollama unreachable at {base_url}: connection refused"}
    except httpx.TimeoutException:
        yield {"error": f"Ollama at {base_url} timed out / unreachable: TimeoutException"}
    except Exception as exc:  # noqa: BLE001
        logger.exception("llm stream failed")
        yield {"error": str(exc) or f"Ollama error: {type(exc).__name__}"}


async def complete(
    model: str,
    messages: list[dict],
    base_url: str,
    timeout: float = 20.0,
    options: Optional[dict] = None,
    think: Optional[bool] = None,
) -> Optional[str]:
    """Non-streaming chat completion. Returns the message content, or None.

    `think=False` disables reasoning tokens on thinking models (gemma4,
    ornith) — otherwise the num_predict budget is consumed by invisible
    chain-of-thought and `content` comes back empty.
    """
    url = f"{base_url.rstrip('/')}/api/chat"
    payload = {"model": model, "messages": messages, "stream": False}
    if think is not None:
        payload["think"] = bool(think)
    if options:
        payload["options"] = options
    try:
        r = await get_client().request("POST", url, json=payload, timeout=timeout)
        if r.status_code == 200:
            return ((r.json().get("message") or {}).get("content") or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("llm complete failed: %s", exc)
    return None


async def list_models(base_url: str) -> list[str]:
    try:
        r = await get_client().get(f"{base_url.rstrip('/')}/api/tags", timeout=8.0)
        if r.status_code == 200:
            return [m["name"] for m in r.json().get("models", [])]
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not list ollama models: %s", exc)
    return []


async def ollama_healthy(base_url: str) -> bool:
    try:
        r = await get_client().get(f"{base_url.rstrip('/')}/api/tags", timeout=4.0)
        return r.status_code == 200
    except Exception:
        return False
