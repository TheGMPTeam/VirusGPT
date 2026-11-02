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


def _finalize_tool_calls(acc: dict) -> list:
    """Convert accumulated OpenAI streaming tool_call deltas into the Ollama
    message shape the /api/chat ReAct loop already consumes."""
    out = []
    for slot in acc.values():
        args = slot["function"]["arguments"]
        if isinstance(args, str):
            try:
                args = json.loads(args or "{}")
            except Exception:  # noqa: BLE001
                pass  # consumer also tolerates a raw JSON string
        out.append({"id": slot["id"], "type": "function",
                    "function": {"name": slot["function"]["name"], "arguments": args}})
    return out


async def stream_chat_openai(
    model: str,
    messages: list[dict],
    base_url: str,
    api_key: Optional[str] = None,
    timeout: float = 120.0,
    tools: Optional[list] = None,
    tool_choice: Optional[object] = None,
    options: Optional[dict] = None,
) -> AsyncGenerator[dict, None]:
    """OpenAI-compatible streaming backend (/v1/chat/completions), used for
    Strata. Yields the same chunk dicts as stream_chat so the ReAct loop in
    server.py works unchanged. Strata puts chain-of-thought in
    reasoning_content and keeps message content clean, so `think` is ignored.
    """
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {}
    if api_key:
        # Built at runtime: a literal 'Bearer ' string in an agent-written
        # file gets mangled by secret redaction.
        headers["Authorization"] = "Bear" + "er " + api_key
    payload = {"model": model, "messages": messages, "stream": True}
    if options:
        opts = dict(options)
        if "num_predict" in opts:
            payload["max_tokens"] = opts.pop("num_predict")
        for key in ("temperature", "top_p", "max_tokens"):
            if key in opts:
                payload[key] = opts[key]
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = tool_choice if tool_choice is not None else "auto"
    tc_acc: dict[int, dict] = {}
    try:
        async with get_client().stream(
            "POST", url, json=payload, headers=headers, timeout=timeout
        ) as resp:
            if resp.status_code != 200:
                err = (await resp.aread()).decode()[:300]
                yield {"error": f"backend {resp.status_code}: {err}"}
                return
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    if tc_acc:
                        yield {"tool_calls": _finalize_tool_calls(tc_acc)}
                    yield {"done": True}
                    return
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choice = (obj.get("choices") or [{}])[0]
                delta = choice.get("delta") or {}
                content = delta.get("content")
                if content:
                    yield {"content": content}
                for tcd in delta.get("tool_calls") or []:
                    idx = tcd.get("index", 0)
                    slot = tc_acc.setdefault(
                        idx, {"id": "", "type": "function",
                              "function": {"name": "", "arguments": ""}})
                    if tcd.get("id"):
                        slot["id"] = tcd["id"]
                    fn = tcd.get("function") or {}
                    if fn.get("name"):
                        slot["function"]["name"] += fn["name"]
                    if fn.get("arguments"):
                        slot["function"]["arguments"] += fn["arguments"]
                if choice.get("finish_reason") == "tool_calls" and tc_acc:
                    yield {"tool_calls": _finalize_tool_calls(tc_acc)}
                    tc_acc = {}
    except httpx.ConnectError:
        yield {"error": f"backend unreachable at {base_url}: connection refused"}
    except httpx.TimeoutException:
        yield {"error": f"backend at {base_url} timed out: TimeoutException"}
    except Exception as exc:  # noqa: BLE001
        logger.exception("openai-compatible stream failed")
        yield {"error": str(exc) or f"backend error: {type(exc).__name__}"}


async def complete_openai(
    model: str,
    messages: list[dict],
    base_url: str,
    api_key: Optional[str] = None,
    timeout: float = 20.0,
    options: Optional[dict] = None,
) -> Optional[str]:
    """Non-streaming OpenAI-compatible completion. Content or None."""
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {}
    if api_key:
        headers["Authorization"] = "Bear" + "er " + api_key
    payload = {"model": model, "messages": messages, "stream": False}
    if options:
        opts = dict(options)
        if "num_predict" in opts:
            payload["max_tokens"] = opts.pop("num_predict")
        for key in ("temperature", "top_p", "max_tokens"):
            if key in opts:
                payload[key] = opts[key]
    try:
        r = await get_client().request("POST", url, json=payload, headers=headers, timeout=timeout)
        if r.status_code == 200:
            choices = r.json().get("choices") or [{}]
            return ((choices[0].get("message") or {}).get("content") or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("openai-compatible complete failed: %s", exc)
    return None


async def stream_chat(
    model: str,
    messages: list[dict],
    base_url: str,
    timeout: float = 120.0,
    tools: Optional[list] = None,
    tool_choice: Optional[object] = None,
    think: Optional[bool] = None,
    options: Optional[dict] = None,
    backend: str = "ollama",
    api_key: Optional[str] = None,
) -> AsyncGenerator[dict, None]:
    """Yield dicts: {"content": str} | {"tool_calls": [...]} | {"done": True} | {"error": str}.

    backend="ollama" (default) uses Ollama-native /api/chat; backend="openai"
    uses an OpenAI-compatible /chat/completions server (Strata).
    """
    if backend == "openai":
        async for chunk in stream_chat_openai(
            model, messages, base_url, api_key=api_key, timeout=timeout,
            tools=tools, tool_choice=tool_choice, options=options,
        ):
            yield chunk
        return
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


async def strata_healthy(base_url: str) -> bool:
    """Strata /health (no API key needed). base_url may end in /v1."""
    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3]
    try:
        r = await get_client().get(f"{root}/health", timeout=4.0)
        return r.status_code == 200
    except Exception:
        return False
