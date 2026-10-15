"""Mission Control telemetry bridge.

Every agent action — a chat tool call, an autonomous task step, a TTS synthesis,
a browser playback event — is recorded as a lifecycle event on a Mission Control
mission and streamed live to the UI. This is the single spine the Mission
Control panel subscribes to, so no agent path can run "dark".

Event vocabulary (stored in the shared events table):
  mission.created / mission.completed   mission lifecycle
  agent.start / agent.finish            one agent turn (team round, chat reply)
  tool.start                            tool call dispatched (call_id, tool, args)
  tool.call                             tool call finished  (call_id, tool, args, result)
  tts.request                           /api/tts got a synthesis request
  tts.synth                             backend finished synthesizing (chunks)
  tts.error                             synthesis failed
  voice.play / voice.end / voice.error  real browser playback lifecycle (reported)
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

from .database import _now, Event, Mission

# One Mission Control mission per kind ("agent" = chat/team activity,
# "voice" = TTS/playback activity). Reused while fresh so a whole team round
# lands on ONE mission instead of one per call.
_ACTIVE: dict[str, str] = {}
_FRESH_SECONDS = 900.0
_LOCK = asyncio.Lock()


async def ensure_mission(repo, kind: str, goal: str) -> str:
    """Return the id of the active Mission Control mission for *kind*,
    creating a fresh one when none exists or the old one went stale."""
    async with _LOCK:
        mid = _ACTIVE.get(kind)
        if mid:
            m = repo.get_mission(mid)
            if (m is not None and m.status == "running"
                    and _age_seconds(m.created_at) < _FRESH_SECONDS):
                return mid
        m = Mission(goal=goal, status="running", planner="mission-control")
        repo.create_mission(m)
        _ACTIVE[kind] = m.id
        repo.add_event(Event(mission_id=m.id, agent="mission-control",
                             event="mission.created",
                             data=json.dumps({"kind": kind, "goal": goal})))
        return m.id


def _age_seconds(iso: str) -> float:
    try:
        import datetime
        dt = datetime.datetime.fromisoformat(iso)
        return (datetime.datetime.now() - dt).total_seconds()
    except Exception:
        return 1e9


async def emit(repo, kind: str, event: str, data: dict[str, Any],
               agent: str = "") -> None:
    """Record *event* on the active mission for *kind* and broadcast it live.
    Never raises: telemetry must not break the request it describes."""
    try:
        mid = await ensure_mission(repo, kind, _DEFAULT_GOALS.get(kind, kind))
        e = Event(mission_id=mid, agent=agent or kind, event=event,
                  data=json.dumps(data, ensure_ascii=False))
        repo.add_event(e)
    except Exception:
        # A telemetry failure must never take down chat/TTS.
        return


_DEFAULT_GOALS = {
    "agent": "Agent activity (chat + team tool calls)",
    "voice": "Voice output (TTS synthesis + playback)",
}


async def report_frontend(repo, event: str, data: dict[str, Any],
                          agent: str = "") -> dict[str, Any]:
    """Bridge for browser-reported lifecycle events (playback, round turns).
    Only the safe vocabulary is accepted."""
    allowed = {"agent.start", "agent.finish", "voice.play", "voice.end",
               "voice.error", "round.start", "round.finish"}
    if event not in allowed:
        return {"ok": False, "error": f"event '{event}' not accepted"}
    kind = "voice" if event.startswith("voice.") else "agent"
    await emit(repo, kind, event, data, agent=agent)
    return {"ok": True}
