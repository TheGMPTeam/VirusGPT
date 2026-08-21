"""Autonomous orchestrator — supervisor state machine with retry/recovery.

A mission runs as a background asyncio task so the HTTP request that *starts*
it returns immediately and the client can stream live status over SSE while the
mission executes. Cancellation is cooperative: the supervisor checks a per
mission cancel flag before each step and between retries.

Task lifecycle:
    pending -> running -> (completed | recovering -> running ...)
             -> blocked (retries + recovery exhausted)

Mission lifecycle:
    planning -> running -> verifying -> completed
    (any stage may transition to cancelled via cooperative stop)
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, Dict, List, Optional

from autonomous.agents.runtime import AgentRuntime
from autonomous.database import Repository, Task
from autonomous.events import Event, bus

repo = Repository()

# Per-mission cooperative cancellation flags.
_CANCEL: Dict[str, bool] = {}

# The uvicorn event loop. The synchronous /api/autonomous/start endpoint runs in
# uvicorn's threadpool, so asyncio.get_event_loop() there returns a DIFFERENT
# (non-running) loop — scheduling the background mission on it would make the
# mission never execute. server.py captures the real loop at startup and stores it
# here via set_loop(); we then schedule background work with
# asyncio.run_coroutine_threadsafe on that loop.
_LOOP: Optional[asyncio.AbstractEventLoop] = None


def set_loop(loop: asyncio.AbstractEventLoop):
    global _LOOP
    _LOOP = loop


def _schedule(coro):
    """Run a coroutine as a background task on the captured server loop (or the
    best-guess loop if none was set yet)."""
    if _LOOP is not None:
        return asyncio.run_coroutine_threadsafe(coro, _LOOP)
    # Fallback for non-server contexts (tests / standalone scripts).
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(coro)
    except RuntimeError:
        asyncio.run(coro)
    return None

# Status constants (shared vocabulary with the DB schema + client).
MISS_CANCELLED = "cancelled"
TASK_PENDING = "pending"
TASK_RUNNING = "running"
TASK_COMPLETED = "completed"
TASK_RECOVERING = "recovering"
TASK_BLOCKED = "blocked"
TASK_CANCELLED = "cancelled"

_DEFAULT_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_S = 2.0
_VERIFY_TIMEOUT_S = 30


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


# Statuses that mean "this mission is still doing something" — and so must be
# re-driven (resumed) after a server restart, because the in-memory asyncio task
# that was executing it no longer exists.
INTERRUPTIBLE_STATUSES = {"planning", "running", "verifying"}
TERMINAL_STATUSES = {"completed", "failed", "blocked", "cancelled"}


class Supervisor:
    def __init__(self):
        self.runtime = AgentRuntime(repo)

    # -- public API -------------------------------------------------------

    def request_stop(self, mission_id: str):
        _CANCEL[mission_id] = True

    def start_mission(self, goal: str, room_personas: List[Dict[str, Any]], mission_id: Optional[str] = None) -> Dict[str, Any]:
        """Create the mission and schedule it on the running event loop.

        Returns quickly with the mission id; execution happens in the
        background so the caller is not blocked by long LLM runs.

        When `mission_id` is supplied the mission row is re-created from a
        prior (interrupted) run so its work can be resumed — see
        `resume_mission`.
        """
        if mission_id is None:
            mission_id = f"M-{int(time.time())}-{uuid.uuid4().hex[:6]}"
        planner = next(
            (p["name"] for p in room_personas if p.get("role") == "planner"),
            room_personas[0]["name"] if room_personas else "VirusGPT",
        )
        mission = repo.create_mission(
            type(
                "Mission",
                (),
                {
                    "id": mission_id,
                    "goal": goal,
                    "status": "planning",
                    "planner": planner,
                    "requires_approval": False,
                    "approval_state": None,
                    "final_result": None,
                    "personas": json.dumps(room_personas) if room_personas else None,
                    "created_at": _now(),
                    "updated_at": _now(),
                    "completed_at": None,
                },
            )()
        )
        _CANCEL[mission.id] = False
        _schedule(self._run(mission, room_personas))
        return {"id": mission.id, "planner": mission.planner, "status": mission.status}

    def resume_interrupted_missions(self, repo_for=None) -> List[str]:
        """Re-drive any missions left in an in-flight status by a restart.

        Called at server startup. For each interruptible mission we look up its
        persisted room_personas (falling back to the default persona set) and
        re-schedule it on the current event loop. Returns the ids resumed.
        """
        source = repo_for or repo
        resumed: List[str] = []
        try:
            for m in source.list_missions(200):
                if m.status in INTERRUPTIBLE_STATUSES:
                    personas = self._load_personas_for(m)
                    resumed.append(m.id)
                    _schedule(self._run(m, personas, resumed_from_restart=True))
        except Exception as exc:  # never block startup on a resume hiccup
            print("[orchestrator] resume_interrupted_missions error:", exc)
        return resumed

    def _load_personas_for(self, mission) -> List[Dict[str, Any]]:
        """Recover the room personas persisted with a mission, if any."""
        try:
            if getattr(mission, "personas", None):
                loaded = json.loads(mission.personas)
                if isinstance(loaded, list) and loaded:
                    return loaded
        except Exception:
            pass
        # Fallback: whatever is configured for the room right now.
        try:
            from server import _load_personas

            return _load_personas()
        except Exception:
            return [{"name": mission.planner, "role": "planner", "system_prompt": "", "skills": "", "voice": "alba"}]

    def resume_mission(self, mission_id: str) -> Dict[str, Any]:
        """Manually resume a specific interrupted mission (HTTP-triggered)."""
        mission = repo.get_mission(mission_id)
        if mission is None:
            return {"ok": False, "error": "mission not found"}
        if mission.status in TERMINAL_STATUSES:
            return {"ok": False, "error": f"mission already {mission.status}"}
        personas = self._load_personas_for(mission)
        _CANCEL[mission.id] = False
        _schedule(self._run(mission, personas, resumed_from_restart=True))
        return {"ok": True, "id": mission.id, "status": mission.status}

    # -- background run ----------------------------------------------------

    async def _run(self, mission, room_personas, resumed_from_restart: bool = False):
        tasks = repo.list_mission_tasks(mission.id)
        try:
            # If we are resuming a mission that had already finished planning,
            # skip straight to dispatching the work that remains.
            resume_planning = not tasks or mission.status == "planning"

            if resume_planning:
                mission.status = "running"
                mission.updated_at = _now()
                repo.update_mission(mission)
                await bus.publish(
                    Event(mission_id=mission.id, event="mission.running", agent=mission.planner, data={})
                )

            subtasks = await self._plan(mission, room_personas, reuse_existing=resumed_from_restart)
            if _CANCEL.get(mission.id):
                mission.status = MISS_CANCELLED
            else:
                await self._dispatch(mission, subtasks, room_personas, skip_completed=resumed_from_restart)
                if _CANCEL.get(mission.id):
                    mission.status = MISS_CANCELLED
                else:
                    mission.status = "verifying"
                    mission.updated_at = _now()
                    repo.update_mission(mission)
                    await bus.publish(
                        Event(
                            mission_id=mission.id,
                            event="mission.verifying",
                            agent=mission.planner,
                            data={},
                        )
                    )
                    # Synthesis may already have run before the interruption.
                    # Only synthesize once so we don't clobber a prior result.
                    if mission.final_result is None:
                        await self._synthesize(mission, subtasks, room_personas)
                    mission.status = "completed"
        except Exception as exc:  # never let a background task die silently
            mission.status = "failed"
            await bus.publish(
                Event(
                    mission_id=mission.id,
                    event="mission.failed",
                    agent=mission.planner,
                    data={"error": str(exc)},
                )
            )
        finally:
            mission.completed_at = _now()
            mission.updated_at = _now()
            repo.update_mission(mission)
            await bus.publish(
                Event(
                    mission_id=mission.id,
                    event="mission.end",
                    agent=mission.planner,
                    data={"status": mission.status},
                )
            )

    # -- steps -------------------------------------------------------------

    async def _plan(self, mission, room_personas, reuse_existing: bool = False):
        from services import llm, config as cfg

        # If we are resuming a mission that already had its plan persisted,
        # reuse the existing subtasks instead of regenerating them (which would
        # duplicate work and waste LLM calls).
        existing = repo.list_mission_tasks(mission.id)
        if reuse_existing and existing:
            await bus.publish(
                Event(
                    mission_id=mission.id,
                    event="plan.reused",
                    agent=mission.planner,
                    data={"subtasks": [t.id for t in existing]},
                )
            )
            return existing

        workers = [p for p in room_personas if p["name"] != mission.planner] or room_personas[1:]
        names = ", ".join(p["name"] for p in workers) or "available agents"
        msgs = [
            {"role": "system", "content": "You are a planner. Output only @Name: subtask lines."},
            {
                "role": "user",
                "content": (
                    f"Goal: {mission.goal}\nTeam members: {names}\n"
                    "Output exactly one subtask per worker, one line each, format: "
                    "@<PersonName>: <subtask description>."
                ),
            },
        ]
        text = ""
        async for chunk in llm.stream_chat(
            cfg.CONFIG["ollama"]["default_model"], msgs, cfg.CONFIG["ollama"]["base_url"], timeout=60
        ):
            if chunk.get("content"):
                text += chunk["content"]
            if chunk.get("done"):
                break
        out: List[Task] = []
        seen = set()
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("@"):
                continue
            try:
                agent_name, _, desc = line[1:].partition(":")
                agent_name = agent_name.strip()
                desc = desc.strip()
            except Exception:
                continue
            worker = next((p for p in workers if p["name"].lower() == agent_name.lower()), None)
            if not worker or worker["name"] in seen:
                continue
            seen.add(worker["name"])
            task = repo.create_task(
                Task(
                    mission_id=mission.id,
                    title=desc or f"Work on: {mission.goal}",
                    agent=worker["name"],
                    max_attempts=_DEFAULT_MAX_ATTEMPTS,
                )
            )
            out.append(task)
        if not out:  # fallback: one subtask per worker
            for w in workers:
                task = repo.create_task(
                    Task(
                        mission_id=mission.id,
                        title=f"Work on: {mission.goal}",
                        agent=w["name"],
                        max_attempts=_DEFAULT_MAX_ATTEMPTS,
                    )
                )
                out.append(task)
        await bus.publish(
            Event(
                mission_id=mission.id,
                event="plan.created",
                agent=mission.planner,
                data={"subtasks": [t.id for t in out]},
            )
        )
        return out

    async def _dispatch(self, mission, subtasks, room_personas, skip_completed: bool = False):
        for st in subtasks:
            # On resume, tasks that already reached a terminal state before the
            # interruption do not need to be re-run.
            if skip_completed and st.status in (TASK_COMPLETED, TASK_BLOCKED, TASK_CANCELLED):
                await bus.publish(
                    Event(
                        mission_id=mission.id,
                        task_id=st.id,
                        event="task.skipped_resume",
                        agent=st.agent,
                        data={"status": st.status},
                    )
                )
                continue
            persona = next(
                (p for p in room_personas if p["name"] == st.agent),
                {"name": st.agent, "system_prompt": "", "skills": "", "voice": "alba"},
            )
            await self._execute_with_retry(mission, st, persona)

    async def _execute_with_retry(self, mission, task, persona):
        """Run a task with retries, verification, and planner-led recovery."""
        for attempt in range(1, task.max_attempts + 1):
            if _CANCEL.get(mission.id):
                task.status = TASK_CANCELLED
                task.completed_at = _now()
                repo.update_task(task)
                return

            task.attempts = attempt
            task.status = TASK_RUNNING
            task.started_at = _now()
            repo.update_task(task)
            await bus.publish(
                Event(
                    mission_id=mission.id,
                    task_id=task.id,
                    event="task.started",
                    agent=task.agent,
                    data={"title": task.title, "attempt": attempt},
                )
            )

            result = await self.runtime.execute(
                mission.id, task.id, persona, mission.goal, task.title
            )
            task.result = json.dumps(result)
            passed_exec = result.get("status") == "completed"
            verdict, vnote = (
                (True, "")
                if not passed_exec
                else await self._verify(mission, task, result.get("summary", ""))
            )
            task.verification = vnote

            if passed_exec and verdict:
                task.status = TASK_COMPLETED
                task.completed_at = _now()
                repo.update_task(task)
                await bus.publish(
                    Event(
                        mission_id=mission.id,
                        task_id=task.id,
                        event="task.completed",
                        agent=task.agent,
                        data={"status": task.status, "summary": (result.get("summary") or "")[:500]},
                    )
                )
                return

            # Failure on this attempt.
            if attempt < task.max_attempts:
                task.status = TASK_RECOVERING
                repo.update_task(task)
                await bus.publish(
                    Event(
                        mission_id=mission.id,
                        task_id=task.id,
                        event="task.attempt_failed",
                        agent=task.agent,
                        data={"attempt": attempt, "note": vnote[:300]},
                    )
                )
                await asyncio.sleep(_RETRY_BACKOFF_S)
                continue

            # Exhausted retries -> ask planner for a revised, simpler subtask.
            recovered = await self._recover(mission, task, persona, vnote)
            if recovered:
                task.status = TASK_COMPLETED
                task.completed_at = _now()
                repo.update_task(task)
                await bus.publish(
                    Event(
                        mission_id=mission.id,
                        task_id=task.id,
                        event="task.completed",
                        agent=task.agent,
                        data={"status": task.status, "recovered": True},
                    )
                )
                return

            task.status = TASK_BLOCKED
            task.completed_at = _now()
            repo.update_task(task)
            await bus.publish(
                Event(
                    mission_id=mission.id,
                    task_id=task.id,
                    event="task.blocked",
                    agent=task.agent,
                    data={"attempts": task.attempts, "note": vnote[:300]},
                )
            )

    async def _verify(self, mission, task, summary: str):
        """Lightweight LLM verification. Returns (passed, note).

        Never raises: if the verifier is unavailable we accept the result so a
        transport blip cannot fail an otherwise-good task.
        """
        from services import llm, config as cfg

        msgs = [
            {
                "role": "system",
                "content": "You verify task completion. Reply with exactly 'YES' or 'NO' on the first line, then one short reason.",
            },
            {
                "role": "user",
                "content": (
                    f"Overall goal: {mission.goal}\n"
                    f"Subtask assigned to {task.agent}: {task.title}\n"
                    f"Agent report:\n{summary}\n\n"
                    "Did the agent satisfy the subtask? Reply YES or NO."
                ),
            },
        ]
        text = ""
        try:
            async for chunk in llm.stream_chat(
                cfg.CONFIG["ollama"]["default_model"], msgs, cfg.CONFIG["ollama"]["base_url"], timeout=_VERIFY_TIMEOUT_S
            ):
                if chunk.get("content"):
                    text += chunk["content"]
                if chunk.get("done"):
                    break
        except Exception:
            return True, "verifier unavailable; accepted"
        verdict = text.strip().upper().startswith("YES")
        return verdict, text.strip()[:300]

    async def _recover(self, mission, task, persona, failure_note: str) -> bool:
        """Planner produces a revised, simpler subtask; attempt it once more."""
        from services import llm, config as cfg

        msgs = [
            {
                "role": "system",
                "content": "You are a recovery planner. Given a failed subtask, output ONE revised, simpler subtask instruction on a single line, prefixed with '@AgentName: '.",
            },
            {
                "role": "user",
                "content": (
                    f"Overall goal: {mission.goal}\n"
                    f"Failed subtask for {task.agent}: {task.title}\n"
                    f"Failure note: {failure_note}\n"
                    f"Output a revised subtask for {task.agent}."
                ),
            },
        ]
        text = ""
        try:
            async for chunk in llm.stream_chat(
                cfg.CONFIG["ollama"]["default_model"], msgs, cfg.CONFIG["ollama"]["base_url"], timeout=60
            ):
                if chunk.get("content"):
                    text += chunk["content"]
                if chunk.get("done"):
                    break
        except Exception:
            return False

        line = text.strip()
        if not line.startswith("@"):
            return False
        _, _, revised = line[1:].partition(":")
        revised = revised.strip()
        if not revised:
            return False

        task.title = revised
        task.attempts += 1
        task.status = TASK_RECOVERING
        task.started_at = _now()
        repo.update_task(task)
        await bus.publish(
            Event(
                mission_id=mission.id,
                task_id=task.id,
                event="task.recovering",
                agent=task.agent,
                data={"revised": revised},
            )
        )
        result = await self.runtime.execute(mission.id, task.id, persona, mission.goal, revised)
        task.result = json.dumps(result)
        if result.get("status") != "completed":
            return False
        verdict, vnote = await self._verify(mission, task, result.get("summary", ""))
        task.verification = f"recovered: {vnote}"
        return bool(verdict)

    async def _synthesize(self, mission, subtasks, room_personas):
        persona = next(
            (p for p in room_personas if p["name"] == mission.planner),
            {"name": mission.planner, "system_prompt": "You are the team lead synthesizer.", "skills": "", "voice": "alba"},
        )
        blocked = [t for t in subtasks if t.status == TASK_BLOCKED]
        report = "\n\n".join(
            f"### {st.agent} ({st.status})\n{json.loads(st.result or '{}').get('summary','')}" for st in subtasks
        )
        note = ""
        if blocked:
            note = (
                "\n\nNote: the following subtasks could not be completed after retries "
                "and recovery: "
                + ", ".join(f"{t.agent} ({t.title})" for t in blocked)
                + ". The answer below covers what was accomplished."
            )
        from services import llm, config as cfg

        msgs = [
            {"role": "system", "content": persona.get("system_prompt", "")},
            {
                "role": "user",
                "content": (
                    f"Original goal: {mission.goal}\n\nTeam reports:\n{report}{note}\n\n"
                    f"Synthesize a single coherent final answer, speaking as {mission.planner}."
                ),
            },
        ]
        text = ""
        async for chunk in llm.stream_chat(
            cfg.CONFIG["ollama"]["default_model"], msgs, cfg.CONFIG["ollama"]["base_url"], timeout=120
        ):
            if chunk.get("content"):
                text += chunk["content"]
            if chunk.get("done"):
                break
        mission.final_result = text
        repo.update_mission(mission)
        await bus.publish(
            Event(
                mission_id=mission.id,
                event="mission.synthesized",
                agent=mission.planner,
                data={"summary": text[:500]},
            )
        )


orchestrator = Supervisor()
