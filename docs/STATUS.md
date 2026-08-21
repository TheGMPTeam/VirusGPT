# VirusGPT — Build Status

> Accurate as of `main` HEAD. Green = live & verified. Amber = built in module,
> partial wiring. Red = not started. Sub-agents: pick an amber/red item, read
> `ARCHITECTURE.md`, implement, verify against `:8500`, commit, push.

## Legend
✅ live & verified · 🟡 built-but-partial · 🔴 not started

## Chat / context engine
| Feature | Status | Notes |
|---|---|---|
| Small-context window (history trim) | ✅ | `chat.max_history=24`, `max_history_tokens=2800` in config.json |
| Default system prompt (context) | ✅ | injected on every turn; persona context preserved from frontend |
| Memory graph as DEFAULT context (RAG) | ✅ | `memory.retrieve_context()` keyword-ranked top-k injected every turn |
| Retrieval (no extra LLM call) | ✅ | `memory/store.py: retrieve()/retrieve_context()` |
| Memory query endpoint | ✅ | `/api/memory/query` |

## Memory (own graph)
| Feature | Status | Notes |
|---|---|---|
| Concept store (OKF md) | ✅ | `memory/store.py`, 18 concepts, 0 orphans |
| Graph endpoint | ✅ | `/api/memory/graph` |
| Query endpoint | ✅ | `/api/memory/query` (Ollama RAG over concepts) |
| CRUD: get/update/remove/autolink | ✅ | endpoints + store fns live |
| Living force-directed UI | ✅ | `memory.js`: drag/pan/zoom, legend, detail w/ relink/remove/dream/fact-check |
| No Hermes/Understory refs | ✅ | self-contained; blocklist as safety net |

## Autonomous engine
| Feature | Status | Notes |
|---|---|---|
| Mission supervisor | ✅ | `orchestrator.py`, `/api/autonomous/start|stop|stream|status` |
| ReAct runtime (Ollama tools) | ✅ | `agents/runtime.py` |
| Self-contained tool harness | ✅ | `tools.py`: 8 tools, no Hermes dep |
| Live tool-call list in UI | ✅ | `/api/tools` + team.js log |
| Mission → Kanban board | ✅ | planner tasks → cards Backlog→Done |
| Event/task persistence | ✅ | `database.py` Repository (SQLite) |

## Self-dev (Dreamer)
| Feature | Status | Notes |
|---|---|---|
| research_topic (web→store) | ✅ | `/api/selfdev/research` verified live |
| fact_check_concept | ✅ | `/api/selfdev/factcheck` |
| dream_cycle (trim/link/insight) | ✅ | `/api/selfdev/dream` |
| full cycle | ✅ | `/api/selfdev/cycle` |
| hourly cron | ✅ | gateway selfdev job |
| status endpoint | ✅ | `/api/selfdev/status` |

## DB resilience
| Feature | Status | Notes |
|---|---|---|
| backup_db (snapshot + prune) | ✅ | `data/db_backups/`, keeps 10 (prune **bug fixed** in T1 — was a silent no-op) |
| verify_db (integrity_check) | ✅ | |
| auto_heal on corruption | ✅ | ran at startup; verified restores good backup |
| endpoints | ✅ | `/api/db/status|backup|restore` |
| daily cron backup | ✅ | gateway db_backup job |

## Gateway / ops
| Feature | Status | Notes |
|---|---|---|
| heartbeat + revive | ✅ | `gateway/service.py`, `/api/gateway/status` |
| cron scheduler | ✅ | launch_check/memory_maintain/db_backup/selfdev |
| install.sh (modular flags) | ✅ | --core/--tts/--stt/--models/--autonomous/--memory/--gateway/--docker |
| Windows via Docker | ✅ | docs + --docker flag; compose stack is canonical Win path |
| vgctl CLI | ✅ | health/doctor/fix/settings (+ memory/gateway planned) |
| GitHub Actions CI | ✅ | green on push |

## Creative / publishing pipeline
| Feature | Status | Notes |
|---|---|---|
| Architecture + phased plan | ✅ | `docs/INTEGRATIONS_PLAN.md` |
| Windows-box findings (Hermes memory) | ✅ | recorded as graph node; ComfyUI/Blender/FFmpeg/n8n already on Win box |
| service clients (comfyui) | ✅ | `services/comfyui.py` live: health/models/render_image; degrades gracefully |
| config `services` block | ✅ | `config.json` `services` (n8n/comfyui/blender/ffmpeg/marton) + VG_* env overrides |
| `/api/services/status` | ✅ | live; reports ComfyUI enabled/healthy/models |
| `/api/generate` + `/api/generated/{file}` | ✅ | image gen via ComfyUI, served from `data/generated/` (path-confined) |
| media tools in harness (render_image) | 🟡 | `render_image` tool wired into autonomous `tools.py`; video/edit/publish planned |
| pipeline orchestration (research→build→test→check→upload) | 🔴 | `autonomous/pipeline.py` not built |
| marton.ai connector (Gmail/YouTube/Snapchat) | 🔴 | only external integration not existing anywhere |
| Studio UI tab | 🔴 | live stage tracker + preview |
| **Desktop app build-out** | ✅ | pywebview shell + `desktop/build-*.py`; native window wraps `:8500` (see ROADMAP §1) |

## Backend tuning — T1 (verified)
| Item | Status | Notes |
|---|---|---|
| Chat small-context trim | ✅ | `trim_history()` caps history at `max_history=24` msgs / `max_history_tokens=2800` chars → qwen2.5:3b never overflows. Live-tested with a 35,556-char input. |
| Memory retrieval scan cap | ✅ | `retrieve()` short-circuits at `MEMORY_SCAN_CAP=200` (scores only most-recent concepts); dependency-free, bounded per-turn latency. |
| DB backup prune | ✅ | **Bug fixed** — prune loop dropped `.db` ext (`old.stem + ""`), never deleted main backup. Now prunes to `BACKUP_KEEP=10` (verified 16→10). |
| Gateway cron cadences | ✅ | `builtin_jobs()`: launch_check 60s, memory_maintain 30m, db_backup 24h, selfdev 1h. `launch.sh` starts the gateway supervisor. |

## Test status
- CI: ✅ green (py syntax + requirements + JSON validate + STATUS drift gate +
  **full pytest suite** — backend + Playwright frontend + splitter unit).
- Live endpoint smoke tests: ✅ all core/memory/selfdev/sessions/db/gateway 200.
- **Automated suites** (`python -m pytest tests/ -q`): 71 tests — 54 backend
  (every `/api/*` route, A2A mission run, tool-call logging), 16 Playwright
  Chromium UI tests (per-sentence ▶ isolation, /clear & /new mute auto-play,
  speaker toggle, `/team` Agent-to-Agent round, image gen, tabs, settings/theme,
  Enter-to-send, responsive shots at 390/820/1440/2560px), 1 splitter unit.
- `autonomous/test_orchestrator.py`: 9 tests (planner → worker → synthesis,
  cross-restart resume).

## Reports (generated this cycle)
- [`docs/OPTIMIZATION.md`](OPTIMIZATION.md) — footprint, hot paths, dead code,
  bundle/rebuild follow-ups.
- [`docs/AUDIT.md`](AUDIT.md) — security, structure, docs-drift gate, coverage,
  bugs found & fixed.
- [`docs/REFACTORING.md`](REFACTORING.md) — analysis of the chat-TTS, auto-play,
  tool-logging, and orchestrator refactors.
