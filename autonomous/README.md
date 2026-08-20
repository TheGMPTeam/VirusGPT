# Autonomous Engine

Server-side autonomous mission runtime for VirusGPT.

## Modules

- `autonomous/__init__.py`
- `autonomous/database.py` — SQLite/PlanetScale persistence layer (`missions`, `tasks`, `events`, `agent_memory`, `artifacts`)
- `autonomous/orchestrator.py` — `Supervisor` runtime: starts a mission in the background, runs planner → workers → synthesis, with cooperative cancellation
- `autonomous/agents/runtime.py` — `AgentRuntime` executes subtasks on behalf of personas
- `autonomous/events.py` — in-process `EventBus`; every publish is also persisted to the `events` table (durable audit trail)

## REST

- `POST /api/autonomous/start` — `{goal, room_personas?}` → starts a mission in the background and returns `mission_id`, `stream_url`, `status` immediately
- `GET /api/autonomous/stream/{mission_id}` — SSE status stream
- `GET /api/autonomous/status/{mission_id}` — mission + tasks + persisted events
- `POST /api/autonomous/stop/{mission_id}` — cooperative cancel (checked before each step)
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

## Database

Select backend in `config.json`:

- `database.backend = "sqlite"` → `data/virusgpt.db`
- `database.backend = "planetscale"` or `"mysql"` → remote MySQL

`pymysql` is required for MySQL.

## Notes

- A mission runs as a background `asyncio.Task`; `start_mission` returns immediately so the client can stream status without blocking.
- Events are persisted on every `EventBus.publish`, so the audit trail survives restarts.
- Cancellation is cooperative: the supervisor checks a per-mission flag before each step.
- Recovery/resume across server restarts is not yet implemented.
