"""Tests for the VirusGPT autonomous orchestrator.

Covers the planner -> worker -> synthesis pipeline and the cross-restart
resume / recovery behaviour, all without touching a live LLM or the real DB.

Run from the project root:
    .venv/bin/python -m pytest autonomous/ -q
"""
from __future__ import annotations

import json

import pytest

import autonomous.database as dbmod
import autonomous.orchestrator as orch
from autonomous.orchestrator import Supervisor, INTERRUPTIBLE_STATUSES


def _new_supervisor(isolated_db):
    # A fresh supervisor bound to the isolated repo + a reset cancel map.
    s = Supervisor()
    orch.repo = isolated_db
    return s


# ---------------------------------------------------------------------------
# Planner -> Worker -> Synthesis
# ---------------------------------------------------------------------------

def test_planner_worker_synthesis_happy_path(isolated_db, personas, fake_llm, force_sync, spy):
    s = _new_supervisor(isolated_db)
    result = s.start_mission("Build a small feature", personas)
    mid = result["id"]

    mission = isolated_db.get_mission(mid)
    assert mission.status == "completed", mission.status
    assert mission.final_result, "synthesis should have written a final result"

    tasks = isolated_db.list_mission_tasks(mid)
    assert len(tasks) == 2, [t.agent for t in tasks]  # two workers
    assert all(t.status == "completed" for t in tasks), [(t.agent, t.status) for t in tasks]
    # Both worker subtasks were actually executed (not just planned).
    assert spy.executed, "no worker subtasks were executed"
    # events were persisted for the audit trail
    events = isolated_db.mission_events(mid)
    kinds = {e.event for e in events}
    assert "plan.created" in kinds
    assert "task.completed" in kinds
    assert "mission.synthesized" in kinds


def test_planner_persists_personas(isolated_db, personas, fake_llm, force_sync):
    s = _new_supervisor(isolated_db)
    mid = s.start_mission("Plan and go", personas)["id"]
    mission = isolated_db.get_mission(mid)
    stored = json.loads(mission.personas)
    assert isinstance(stored, list) and stored[0]["name"] == "Planner"


# ---------------------------------------------------------------------------
# Retry + recovery
# ---------------------------------------------------------------------------

def test_recovery_after_repeated_failure(isolated_db, personas, fake_llm, force_sync, monkeypatch):
    """A worker whose regular attempts all fail verification gets them retried,
    exhausts retries, then a planner-led recovery attempt succeeds."""
    # Fail the first three executions per task (the 3 regular attempts); the
    # planner-led recovery execution (4th) is allowed to succeed.
    state = {"fails_left": 3}
    from autonomous.agents import runtime as rt

    orig_execute = rt.AgentRuntime.execute

    async def patched_execute(self, mission_id, task_id, persona, goal, title):
        if state["fails_left"] > 0:
            state["fails_left"] -= 1
            return {"status": "failed", "summary": "boom", "findings": [], "confidence": 0.0}
        return await orig_execute(self, mission_id, task_id, persona, goal, title)

    monkeypatch.setattr(rt.AgentRuntime, "execute", patched_execute)

    s = _new_supervisor(isolated_db)
    mid = s.start_mission("Recover from failure", personas)["id"]

    mission = isolated_db.get_mission(mid)
    assert mission.status == "completed"
    tasks = isolated_db.list_mission_tasks(mid)
    # The retry machinery engaged (attempts advanced past the first).
    assert any(t.attempts >= 2 for t in tasks), [t.attempts for t in tasks]
    # At least one task reached completion via the recovery path.
    assert all(t.status == "completed" for t in tasks), [(t.agent, t.status) for t in tasks]


# ---------------------------------------------------------------------------
# Cross-restart resume
# ---------------------------------------------------------------------------

def test_resume_reuses_completed_tasks(isolated_db, personas, fake_llm, force_sync, spy):
    """A mission interrupted mid-flight (status left 'running', some tasks
    already completed) resumes by skipping the done tasks and only re-running
    the unfinished ones."""
    s = _new_supervisor(isolated_db)
    mid = s.start_mission("Resume me", personas)["id"]

    # Simulate a crash: mark the mission back to an in-flight status and leave
    # one task completed, one still pending (as if it died during dispatch).
    mission = isolated_db.get_mission(mid)
    mission.status = "running"
    isolated_db.update_mission(mission)
    tasks = isolated_db.list_mission_tasks(mid)
    tasks[0].status = "completed"
    tasks[0].result = json.dumps({"status": "completed", "summary": "done"})
    isolated_db.update_task(tasks[0])
    tasks[1].status = "pending"
    tasks[1].result = None
    isolated_db.update_task(tasks[1])

    spy.executed.clear()
    s.resume_mission(mid)

    resumed_mission = isolated_db.get_mission(mid)
    assert resumed_mission.status == "completed"
    # The already-completed task must NOT have been re-executed.
    assert tasks[0].title not in spy.executed, spy.executed
    # The pending task must have been executed.
    assert tasks[1].title in spy.executed, spy.executed
    # Final result present now.
    assert resumed_mission.final_result


def test_resume_interrupted_missions_picks_up_inflight(isolated_db, personas, fake_llm, force_sync):
    """resume_interrupted_missions re-drives every in-flight mission once."""
    s = _new_supervisor(isolated_db)
    mid = s.start_mission("Inflight", personas)["id"]
    # Leave it in an in-flight (interrupted) state.
    mission = isolated_db.get_mission(mid)
    mission.status = "verifying"
    isolated_db.update_mission(mission)

    resumed = s.resume_interrupted_missions(isolated_db)
    assert mid in resumed
    assert isolated_db.get_mission(mid).status == "completed"


def test_resume_skips_terminal_missions(isolated_db, personas, fake_llm, force_sync):
    """Completed / failed / blocked / cancelled missions are NOT resumed."""
    s = _new_supervisor(isolated_db)
    mid = s.start_mission("Terminal", personas)["id"]
    mission = isolated_db.get_mission(mid)
    mission.status = "completed"
    isolated_db.update_mission(mission)

    resumed = s.resume_interrupted_missions(isolated_db)
    assert resumed == []
    assert isolated_db.get_mission(mid).status == "completed"


def test_resume_mission_rejects_terminal(isolated_db, personas, fake_llm, force_sync):
    s = _new_supervisor(isolated_db)
    mid = s.start_mission("Done", personas)["id"]
    mission = isolated_db.get_mission(mid)
    mission.status = "failed"
    isolated_db.update_mission(mission)

    out = s.resume_mission(mid)
    assert out["ok"] is False
    assert "failed" in out["error"]


def test_resume_mission_unknown(isolated_db):
    s = _new_supervisor(isolated_db)
    out = s.resume_mission("does-not-exist")
    assert out["ok"] is False and out["error"] == "mission not found"


def test_interruptible_status_set():
    assert INTERRUPTIBLE_STATUSES == {"planning", "running", "verifying"}
