"""Shared fixtures for autonomous-engine tests.

These tests run fully offline: the LLM calls are faked and the orchestrator's
background asyncio scheduling is forced down its synchronous `asyncio.run`
fallback so a test can assert on the final persisted state without a live event
loop.

All DB access is redirected to a throwaway sqlite file so the real
`data/virusgpt.db` is never touched.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from pathlib import Path

import pytest

import autonomous.database as dbmod
from services import llm as svc_llm

# Redirect the autonomous DB to a throwaway location at import time, so the
# orchestrator's module-level `repo` and the event bus's fresh `Repository()`
# never touch the real project data/virusgpt.db.
_SESSION_TMP = Path(tempfile.mkdtemp(prefix="vg_auto_test_"))
dbmod.DATA = _SESSION_TMP
dbmod.DB_PATH = _SESSION_TMP / "test_virusgpt.db"
dbmod.BACKUP_DIR = _SESSION_TMP / "db_backups"


def _make_personas():
    return [
        {"name": "Planner", "role": "planner", "system_prompt": "You are the planner lead.",
         "skills": "", "voice": "alba"},
        {"name": "WorkerA", "role": "worker", "system_prompt": "You are worker A.",
         "skills": "", "voice": "alba"},
        {"name": "WorkerB", "role": "worker", "system_prompt": "You are worker B.",
         "skills": "", "voice": "alba"},
    ]


@pytest.fixture
def personas():
    return _make_personas()


class _Spy:
    """Records which subtask titles the runtime actually executed."""
    def __init__(self):
        self.executed = []

    def record(self, title: str):
        self.executed.append(title)


@pytest.fixture
def spy():
    return _Spy()


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Point the autonomous DB at a throwaway sqlite file for this test."""
    monkeypatch.setattr(dbmod, "DATA", tmp_path)
    monkeypatch.setattr(dbmod, "DB_PATH", tmp_path / "test_virusgpt.db")
    monkeypatch.setattr(dbmod, "DB_BACKEND", "sqlite")
    repo = dbmod.Repository()
    # Make the orchestrator use this same isolated repo (it otherwise holds a
    # module-level global created at import time).
    import autonomous.orchestrator as orch
    monkeypatch.setattr(orch, "repo", repo)
    yield repo


@pytest.fixture
def fake_llm(monkeypatch, spy):
    """Replace the real Ollama stream with a deterministic offline one.

    - planner prompt   -> one "@Worker: subtask" line per team member
    - verification     -> "YES"
    - recovery planner -> a single revised "@Worker: ..." line
    - worker execution -> a final answer (recorded in `spy`)
    - synthesis        -> a final answer
    """

    async def fake_stream_chat(model, messages, base_url, timeout=60, tools=None):
        system = next((m.get("content", "") for m in messages if m.get("role") == "system"), "")
        last_user = next(
            (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), ""
        )
        sys_l = system.lower()

        if "verify task completion" in sys_l:
            yield {"content": "YES", "done": True}
            return

        if "recovery planner" in sys_l:
            m = re.search(r"Failed subtask for (\S+):", last_user)
            name = m.group(1) if m else "WorkerA"
            yield {"content": f"@{name}: revised, simpler subtask", "done": True}
            return

        if "planner" in sys_l and "recovery" not in sys_l:
            mm = re.search(r"Team members: ([^\n]+)", last_user)
            members = [s.strip() for s in (mm.group(1) if mm else "WorkerA, WorkerB").split(",")]
            lines = "\n".join(f"@{w}: work on the goal for {w}" for w in members)
            yield {"content": lines, "done": True}
            return

        # Worker execution and final synthesis both just need an answer.
        sub = re.match(r"Subtask:\s*(.+)", last_user)
        if sub:
            spy.record(sub.group(1).strip())
        yield {"content": "A coherent final answer covering the goal.", "done": True}

    monkeypatch.setattr(svc_llm, "stream_chat", fake_stream_chat)
    yield fake_stream_chat


@pytest.fixture
def force_sync(monkeypatch):
    """Force the orchestrator's `get_event_loop()` path to run synchronously.

    The orchestrator schedules background work with
    `loop.create_task(self._run(...))` and falls back to
    `asyncio.run(self._run(...))` if `get_event_loop()` raises. By making
    `get_event_loop` always raise we get deterministic, fully-completed runs
    inside a single test with no dangling tasks.
    """
    def _boom():
        raise RuntimeError("forced synchronous path")

    monkeypatch.setattr(asyncio, "get_event_loop", _boom)
    yield
