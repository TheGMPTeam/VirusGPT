"""Shared pytest fixtures for the VirusGPT backend test suite.

Strategy: run the *real* server.py stack but stub every external dependency
(Ollama, PocketTTS, Whisper, ComfyUI, the memory MCP) so the whole API surface
exercises offline and deterministically.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Force SQLite backend into an isolated temp DB BEFORE importing the app.
import tempfile
_TMP = tempfile.mkdtemp(prefix="vgtest_")
os.environ.setdefault("VG_SQLITE_PATH", os.path.join(_TMP, "test.db"))

from services import config as cfg  # noqa: E402

cfg.CONFIG["database"] = {"backend": "sqlite", "sqlite_path": os.path.join(_TMP, "test.db")}
cfg.CONFIG["ollama"]["base_url"] = "http://127.0.0.1:1"      # unreachable; we monkeypatch llm
cfg.CONFIG["tts"]["base_url"] = "http://127.0.0.1:1"
cfg.CONFIG["tts"]["enabled"] = True
cfg.CONFIG["stt"]["base_url"] = "http://127.0.0.1:1"
cfg.CONFIG["stt"]["enabled"] = True
cfg.CONFIG["services"]["comfyui"]["enabled"] = True
cfg.CONFIG["services"]["comfyui"]["base_url"] = "http://127.0.0.1:1"


def _fake_stream(text: str):
    async def gen():
        for ch in text.split(" "):
            yield {"content": ch + " "}
            await asyncio.sleep(0)
        yield {"done": True}
    return gen()


@pytest.fixture
def stub_llm(monkeypatch):
    """Make llm.stream_chat return canned text (optionally tool_calls)."""
    from services import llm

    state = {"text": "Hello from the offline test assistant.", "tool_calls": None}

    async def fake_stream_chat(model, messages, base_url, timeout=120.0, tools=None):
        if state["tool_calls"] and tools:
            yield {"tool_calls": state["tool_calls"]}
            yield {"done": True}
            return
        async for c in _fake_stream(state["text"]):
            yield c

    monkeypatch.setattr(llm, "stream_chat", fake_stream_chat)
    yield state


@pytest.fixture
def stub_tts(monkeypatch):
    from services import tts
    captured = {}

    async def fake_synthesize(text, voice, base_url, response_format="mp3", timeout=120.0):
        captured["text"] = text
        captured["voice"] = voice
        # 1-second of silence as a valid-ish mp3 placeholder (header bytes).
        return b"\xff\xfb\x90\x00" + b"\x00" * 64

    async def fake_list_voices(base_url):
        return [{"id": "nova", "name": "Nova"}, {"id": "alba", "name": "Alba"}]

    monkeypatch.setattr(tts, "synthesize", fake_synthesize)
    monkeypatch.setattr(tts, "list_voices", fake_list_voices)
    yield captured


@pytest.fixture
def stub_stt(monkeypatch):
    from services import stt
    captured = {}

    async def fake_transcribe(data, mime, base_url, timeout=30.0):
        captured["bytes"] = len(data)
        return {"text": "transcribed offline test audio"}

    monkeypatch.setattr(stt, "transcribe", fake_transcribe)
    yield captured


@pytest.fixture
def stub_comfyui(monkeypatch):
    from services import comfyui as cf

    async def fake_health():
        return True

    async def fake_models():
        return ["sd_xl.safetensors"]

    async def fake_render(prompt, model=None, negative_prompt="", steps=25,
                          cfg_scale=7.0, width=1024, height=1024):
        # Write a tiny PNG so /api/generated/{name} can serve it.
        gen_dir = ROOT / "data" / "generated"
        gen_dir.mkdir(parents=True, exist_ok=True)
        name = "test_render.png"
        (gen_dir / name).write_bytes(b"\x89PNG\r\n\x1a\n")
        return {"status": "completed", "url": f"/api/generated/{name}", "file": name}

    monkeypatch.setattr(cf, "comfyui_health", fake_health)
    monkeypatch.setattr(cf, "comfyui_models", fake_models)
    monkeypatch.setattr(cf, "render_image", fake_render)
    yield {}


@pytest.fixture
def stub_memory(monkeypatch):
    from services import memory as mem

    async def fake_status(base_url=""):
        return {"concepts": 3, "directories": 1, "types": ["concept"],
                "graph": {"nodes": [], "edges": []}, "conformant": True,
                "warnings": [], "errors": []}

    async def fake_query(q, base_url=""):
        return "offline memory answer"

    def fake_get(name):
        return {"name": name, "body": "x", "type": "concept", "links": []}

    def fake_update(name, body=None, typ=None, links=None):
        return {"ok": True, "name": name}

    def fake_remove(name):
        return {"ok": True, "name": name}

    def fake_autolink():
        return {"ok": True, "linked": 0}

    monkeypatch.setattr(mem, "memory_status", fake_status)
    monkeypatch.setattr(mem, "memory_query", fake_query)
    monkeypatch.setattr(mem, "memory_get", fake_get)
    monkeypatch.setattr(mem, "memory_update", fake_update)
    monkeypatch.setattr(mem, "memory_remove", fake_remove)
    monkeypatch.setattr(mem, "memory_autolink", fake_autolink)
    yield {}


@pytest.fixture
def client(stub_llm, stub_tts, stub_stt, stub_comfyui, stub_memory):
    """A TestClient with all upstreams stubbed."""
    import server
    from fastapi.testclient import TestClient

    with TestClient(server.app) as c:
        yield c
