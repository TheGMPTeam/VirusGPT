# Autonomous Engine

Server-side autonomous mission runtime for VirusGPT.

## Modules

- `autonomous/__init__.py`
- `autonomous/database.py` — SQLite/PlanetScale persistence layer (`missions`, `tasks`, `events`, `agent_memory`, `artifacts`). Auto-migrates the schema at startup (e.g. adds the `missions.personas` column used for resume).
- `autonomous/orchestrator.py` — `Supervisor` runtime: starts a mission in the background, runs planner → workers → synthesis, with cooperative cancellation **and cross-restart recovery/resume**.
- `autonomous/agents/runtime.py` — `AgentRuntime` executes subtasks on behalf of personas
- `autonomous/events.py` — in-process `EventBus`; every publish is also persisted to the `events` table (durable audit trail)
- `autonomous/conftest.py` / `autonomous/test_orchestrator.py` — pytest suite (offline; fakes the LLM). Run with `python -m pytest autonomous/ -q` from the project root.

## REST

- `POST /api/autonomous/start` — `{goal, room_personas?}` → starts a mission in the background and returns `mission_id`, `stream_url`, `status` immediately
- `GET /api/autonomous/stream/{mission_id}` — SSE status stream
- `GET /api/autonomous/status/{mission_id}` — mission + tasks + persisted events
- `POST /api/autonomous/stop/{mission_id}` — cooperative cancel (checked before each step)
- `POST /api/autonomous/resume/{mission_id}` — manually re-drive an interrupted mission whose background task died (e.g. after a crash) but whose row is still in-flight
- `GET /api/missions` — recent missions
- `GET /api/missions/{mission_id}` — mission detail

## Lifecycle

Mission:
`planning → running → verifying → completed`
(or `cancelled` via cooperative stop at any stage)

Task (per subtask, with up to `max_attempts` retries):
`pending → running → (completed | recovering → running …) → blocked`

A task that fails verification is retried (2s backoff between attempts). After
all retries are exhausted, the planner attempts **recovery** — one revised,
simpler subtask. If that also fails, the task is marked `blocked` and the final
synthesis notes which subtasks could not be completed.

## Cross-restart recovery / resume

Missions persist their plan, per-task state, and (since this version) the
`room_personas` payload used to run them. **In-flight missions (status
`planning` / `running` / `verifying`) are re-driven automatically when the
server starts**, because the in-memory `asyncio` task that was executing a
mission does not survive a process restart. On resume the orchestrator:

1. reuses the already-persisted subtasks instead of re-planning (an
   `plan.reused` event is emitted),
2. skips tasks already in a terminal state (`completed` / `blocked` /
   `cancelled`) — a `task.skipped_resume` event is emitted for each,
3. runs the remaining pending/running tasks, and
4. synthesizes the final answer only if it was not already produced before the
   interruption.

You can also trigger recovery on demand with `POST /api/autonomous/resume/{mission_id}`
(e.g. if a mission was orphaned by a crash and the automatic startup sweep was
not desired at that moment). Terminal missions (`completed` / `failed` /
`blocked` / `cancelled`) are never resumed.

## Database

Select backend in `config.json`:

- `database.backend = "sqlite"` → `data/virusgpt.db`
- `database.backend = "planetscale"` or `"mysql"` → remote MySQL

`pymysql` is required for MySQL. The schema self-migrates on startup, so adding
the engine to an existing DB is safe.

## Notes

- A mission runs as a background `asyncio.Task`; `start_mission` returns immediately so the client can stream status without blocking.
- Events are persisted on every `EventBus.publish`, so the audit trail survives restarts.
- Cancellation is cooperative: the supervisor checks a per-mission flag before each step.
- Recovery/resume across server restarts IS now implemented (automatic on startup + manual `POST /api/autonomous/resume/{mission_id}`).
