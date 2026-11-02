"""Unit tests for the OpenAI-compatible (Strata) streaming backend in llm.py.

Offline: a fake httpx client replays canned SSE lines, so no Strata server
is needed. Verifies content streaming, tool_call delta accumulation into the
Ollama-shaped chunks the /api/chat ReAct loop consumes, and the router.
"""
from __future__ import annotations

import asyncio
import json

from services import llm


class _FakeStream:
    def __init__(self, lines, status=200):
        self._lines = lines
        self.status_code = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aread(self):
        return b"upstream error"

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeClient:
    def __init__(self, lines, status=200):
        self.lines = lines
        self.status = status
        self.last_kwargs = None

    def stream(self, method, url, **kwargs):
        self.last_kwargs = {"method": method, "url": url, **kwargs}
        return _FakeStream(self.lines, self.status)


def _collect(agen):
    async def run():
        return [c async for c in agen]
    return asyncio.run(run())


def test_content_streams_and_done(monkeypatch):
    lines = [
        'data: {"choices":[{"delta":{"content":"Hel"}}]}',
        'data: {"choices":[{"delta":{"reasoning_content":"hidden cot"}}]}',
        'data: {"choices":[{"delta":{"content":"lo"}}]}',
        'data: [DONE]',
    ]
    fake = _FakeClient(lines)
    monkeypatch.setattr(llm, "get_client", lambda: fake)
    chunks = _collect(llm.stream_chat_openai(
        "m", [{"role": "user", "content": "x"}], "http://x/v1", api_key="sekret"))
    assert [c.get("content") for c in chunks if "content" in c] == ["Hel", "lo"]
    assert chunks[-1] == {"done": True}
    # reasoning_content must never leak into content chunks
    assert all("reasoning" not in json.dumps(c) for c in chunks)
    # URL and auth header shape
    assert fake.last_kwargs["url"] == "http://x/v1/chat/completions"
    assert fake.last_kwargs["headers"]["Authorization"] == "Bear" + "er " + "sekret"


def test_tool_call_deltas_accumulate(monkeypatch):
    lines = [
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call1",'
        '"function":{"name":"web_","arguments":""}}]}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
        '"function":{"name":"search","arguments":"{\\"q\\":"}}]}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
        '"function":{"arguments":"\\"hi\\"}"}}]},"finish_reason":"tool_calls"}]}',
        'data: [DONE]',
    ]
    fake = _FakeClient(lines)
    monkeypatch.setattr(llm, "get_client", lambda: fake)
    chunks = _collect(llm.stream_chat_openai(
        "m", [{"role": "user", "content": "x"}], "http://x/v1"))
    tcs = [c["tool_calls"] for c in chunks if "tool_calls" in c]
    assert len(tcs) == 1  # emitted once, not duplicated at [DONE]
    tc = tcs[0][0]
    assert tc["id"] == "call1"
    assert tc["function"]["name"] == "web_search"
    assert tc["function"]["arguments"] == {"q": "hi"}  # parsed to dict (Ollama shape)
    assert chunks[-1] == {"done": True}


def test_error_status_yields_error_chunk(monkeypatch):
    fake = _FakeClient([], status=503)
    monkeypatch.setattr(llm, "get_client", lambda: fake)
    chunks = _collect(llm.stream_chat_openai("m", [], "http://x/v1"))
    assert len(chunks) == 1 and "error" in chunks[0] and "503" in chunks[0]["error"]


def test_router_backend_openai_dispatches(monkeypatch):
    lines = ['data: {"choices":[{"delta":{"content":"ok"}}]}', "data: [DONE]"]
    fake = _FakeClient(lines)
    monkeypatch.setattr(llm, "get_client", lambda: fake)
    chunks = _collect(llm.stream_chat(
        "m", [{"role": "user", "content": "x"}], "http://x/v1", backend="openai"))
    assert fake.last_kwargs["url"] == "http://x/v1/chat/completions"
    assert [c.get("content") for c in chunks if "content" in c] == ["ok"]


def test_router_default_is_ollama_native(monkeypatch):
    class _R:
        status_code = 200
        async def aiter_lines(self):
            yield json.dumps({"message": {"content": "hi"}, "done": True})
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
    captured = {}

    class _FakeClient:
        def stream(self, method, url, **kwargs):
            captured["url"] = url
            return _R()
    monkeypatch.setattr(llm, "get_client", lambda: _FakeClient())
    chunks = _collect(llm.stream_chat("m", [], "http://x:11434"))
    assert captured["url"] == "http://x:11434/api/chat"
    assert any(c.get("content") == "hi" for c in chunks)
