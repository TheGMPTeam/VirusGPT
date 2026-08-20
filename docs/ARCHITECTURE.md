# VirusGPT — Architecture

> Onboarding doc for sub-agents / swarms. Read this + `STATUS.md` + `ROADMAP.md`
> before touching code. Everything here is **current as of the last commit on `main`**.

VirusGPT is a **local, offline, private** AI agent-chat stack. It runs an Ollama LLM,
a PocketTTS voice service, a Whisper STT service, and its **own** concept-memory
graph — all on the LAN, no external cloud (except optional marton.ai for publishing,
still unbuilt).

## 1. Top-level layout

```
virusgpt-mac/
├── server.py              # FastAPI app: serves client + proxies to all services
├── config.json            # single source of truth for URLs/timeouts/DB
├── services/              # thin async clients (shared httpx pool)
│   ├── __init__.py        # get_client() — one pooled AsyncClient for the process
│   ├── config.py          # loads config.json + VG_* env overrides; service_url()
│   ├── llm.py             # Ollama chat streaming + native tool_calls
│   ├── tts.py             # PocketTTS OpenAI-compatible client
│   ├── stt.py             # Whisper client
│   └── memory.py          # pass-through to memory/store.py (own memory)
├── autonomous/            # the agent engine
│   ├── orchestrator.py    # Supervisor state machine (missions)
│   ├── agents/runtime.py  # ReAct loop w/ native Ollama function calling
│   ├── tools.py           # self-contained tool harness (8 tools + schemas)
│   ├── database.py        # SQLite Repository + DB backup/auto-heal
│   ├── events.py          # in-process event bus
│   ├── task_graph.py      # persistent task graph / state machine
│   └── selfdev.py         # the "Dreamer": research→fact-check→dream→trim
├── memory/                # OWN concept store (OKF-style markdown)
│   └── store.py           # concepts as data/memory/<type>/<name>.md
├── gateway/               # supervisor: heartbeats + cron (auto-heal the stack)
│   └── service.py
├── app/                   # vanilla-JS frontend (no framework, script tags)
│   ├── index.html
│   ├── assets/css/styles.css
│   └── assets/js/*.js     # config, utils, tts, messages, chat, personas,
│                          # sessions, autocomplete, team, memory, ui, main
├── pockettts/             # local TTS server (separate venv, port 49152)
├── whisper/               # local STT server (separate, port 8181)
├── data/                  # runtime artifacts (gitignored): memory/, virusgpt.db,
│                          # db_backups/, gateway/, selfdev/, tasks/
├── install.sh / launch.sh / run.sh / vgctl.py
└── docker-compose.yml / Dockerfile   # full-stack container build
```

## 2. Request flow

```
Browser ──► server.py (FastAPI :8500)
                ├── /api/chat ───────► services/llm.py ──► Ollama :11434
                ├── /api/tts/* ──────► services/tts.py ──► PocketTTS :49152
                ├── /api/stt ────────► services/stt.py ──► Whisper :8181
                ├── /api/memory/* ───► services/memory.py ──► memory/store.py (local md)
                ├── /api/autonomous/* ► autonomous/orchestrator + runtime + tools
                │       (tools call web/Ollama via the harness; NOT Hermes)
                ├── /api/selfdev/* ──► autonomous/selfdev.py (Dreamer)
                └── static client ◄── app/ (index.html + assets)
```

Every heavy service is reached over the **LAN by URL** (config.json). VirusGPT is
the **control plane**; it never runs GPU/CPU-heavy work locally (except the
Mac-side FastAPI + Ollama if co-located).

## 3. Key subsystems

### 3.1 Memory graph (OWN, no Hermes/Understory)
`memory/store.py` — concepts live as `data/memory/<type>/<name>.md` (YAML
frontmatter + body with `[[links]]`). `services/memory.py` is a thin async wrapper.
Endpoints: `/api/memory/graph`, `/api/memory/{name}`, `/api/memory/update`,
`/api/memory/remove`, `/api/memory/autolink`, `/api/memory/query`. The frontend
(`memory.js`) renders a **living force-directed graph**; clicking a node opens
relink/remove/dream/fact-check controls.

### 3.2 Autonomous engine
`autonomous/orchestrator.py` (Supervisor) runs a mission as a background asyncio
task; `agents/runtime.py` is a ReAct loop using **native Ollama function calling**
(not a custom protocol). `tools.py` is a **self-contained harness** (no Hermes
dependency): `web_search, web_fetch, shell, read_file, write_file, memory_query,
calc, git_commit`. Missions drive the Team Workflow Kanban board.

### 3.3 Self-dev (the "Dreamer")
`autonomous/selfdev.py`: `research_topic` (web search → fetch → store new concept),
`fact_check_concept` (verify against live web, mark stale), `dream_cycle` (auto-link,
trim stale orphans, synthesize insight). Runs on the gateway cron (hourly) and via
`/api/selfdev/*` endpoints. This is what makes VirusGPT **self-improving**.

### 3.4 DB + backup/auto-heal
`autonomous/database.py`: SQLite `virusgpt.db` (WAL) via `Repository`. `backup_db()`
snapshots to `data/db_backups/` (keeps 10, daily cron). `verify_db()` runs
`PRAGMA integrity_check`. `auto_heal_db()` restores the newest good backup if the
live DB is corrupt — called at server startup before any writes. Endpoints:
`/api/db/status`, `/api/db/backup`, `/api/db/restore`.

### 3.5 Gateway supervisor
`gateway/service.py`: heartbeat loop (probes `/api/health` → `data/gateway/
heartbeat.json`; revives stack via `launch.sh` if down) + cron scheduler
(launch_check 60s, memory_maintain 30m, db_backup 24h, selfdev 1h). Status at
`/api/gateway/status`.

### 3.6 Personas / Sessions (DB-backed)
Personas + chat sessions (rooms) persist in SQLite via `Repository` (not
localStorage). Endpoints `/api/personas`, `/api/sessions` (+POST/DELETE). A
`data/personas.json` mirror is kept for CLI/doctor tooling.

## 4. Config contract (`config.json`)
```jsonc
{
  "ollama":  {"base_url": "http://10.0.0.120:11434", "default_model": "qwen2.5:3b"},
  "tts":     {"enabled": true, "base_url": "http://localhost:49152", "default_voice": "alba"},
  "stt":     {"enabled": true, "base_url": "http://localhost:8181"},
  "memory":  {"enabled": true, "bundle": "data/memory"},
  "database":{"backend": "sqlite", "sqlite_path": "virusgpt.db"},
  "services":{ /* planned: n8n/comfyui/blender/ffmpeg/marton — NOT yet wired */ }
}
```
All URLs overridable by `VG_*` env vars (used by Docker Compose).

## 5. How to run (dev)
```bash
.venv/bin/python server.py        # :8500  (or ./run.sh / ./launch.sh)
# gateway (heartbeats + cron) is launched by launch.sh automatically
# Docker full stack: docker compose up -d --build
```
Python 3.11 venv (`.venv`). No external credentials. SSH key is local-only.

## 6. For sub-agents picking up work
- **One file per concern** — don't merge modules. Add a new service as
  `services/<name>.py` mirroring `services/tts.py`.
- **Test against the live server** (`:8500`) with `curl` before claiming done.
- **Memory concepts are markdown in `data/memory/`** — record build-outs there as
  nodes so the graph + Dreamer stay aware (this is the project's "source of truth"
  for what exists).
- **CI must stay green** (`b57cdc3` GitHub Actions: py syntax + requirements +
  JSON validate) before pushing.
