# Changelog

All notable changes to VirusGPT are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
semantic versioning (`v1.0` = first tagged, installable release).

## [Unreleased]

### Added
- Autonomous engine: cross-restart recovery/resume. In-flight missions
  (`planning`/`running`/`verifying`) are automatically re-driven on server
  startup; `POST /api/autonomous/resume/{mission_id}` allows manual recovery.
  Resume reuses persisted subtasks, skips completed tasks, and synthesizes only
  once. Room personas are persisted on the mission for faithful replay.
- `autonomous/conftest.py` + `autonomous/test_orchestrator.py` — pytest suite
  covering planner → worker → synthesis and the resume/recovery paths (offline;
  fakes the LLM). Run: `python -m pytest autonomous/ -q`.
- `vgctl.py` — dependency-free CLI control tool:
  - `health` — probe `/api/health` and summarize ollama/tts/whisper status.
  - `doctor` — full diagnostic of Python, `config.json`, venvs, ports, live health.
  - `fix` — `--kill-ports`, `--ensure-venv`, `--reinstall`, `--restart` (via `launch.sh`).
  - `settings` — `get` / `set` / `reset` / `list` `config.json` (auto-typed; applies on next boot).
- `install.sh` — modular installer with per-component flags (`--core`, `--tts`,
  `--stt`, `--models`, `--autonomous`) so the stack can be split across local
  machines; macOS + Linux support; `--dry-run` / `--skip-deps` / `--prefix`.
- Docker full-stack support:
  - `Dockerfile` (VirusGPT server), `pockettts/Dockerfile`, `whisper/Dockerfile`.
  - `docker-compose.yml` orchestrates `virusgpt` + `pockettts` + `whisper` + `ollama`.
  - `services/config.py` now honors `VG_OLLAMA_URL` / `VG_TTS_URL` / `VG_STT_URL` /
    `VG_MODEL` / `VG_PORT` env overrides so the compose network needs no config edits.

### Fixed
- **Autonomous mission "does nothing" when started from the UI.** The ▶ Start button
  was wired as `onclick = startMission`, so a click passed the DOM `Event` as the
  first argument; `startMission` then called `goalOverride.trim()` on the Event and
  threw `trim is not a function` (uncaught async rejection), leaving the status stuck
  at "No active mission" on both the desktop app and mobile. `startMission` now ignores
  a non-string argument and reads the goal from the textarea. (`app/assets/js/team.js`)
- **Missions never executed on the server.** `orchestrator.start_mission` scheduled the
  background run via `asyncio.get_event_loop().create_task()` from inside a *synchronous*
  route handler, which runs on uvicorn's threadpool and resolves a different (non-running)
  event loop — so the task was scheduled but never executed. Missions are now scheduled
  with `asyncio.run_coroutine_threadsafe` on the loop captured at startup.
  (`autonomous/orchestrator.py`, `server.py`)
- **Mission live updates were unreliable on mobile.** The UI now polls
  `/api/autonomous/status/{id}` every 1.5s instead of relying on `EventSource`/SSE.

### Maintenance
- Static-cleanup pass: removed ~20 unused imports across `server.py`, `desktop/run.py`,
  `services/comfyui.py`, `autonomous/{conftest,events,database,tools,test_orchestrator,
  selfdev,runtime}.py`, `gateway/service.py`, `memory/store.py`. `pyflakes` is clean
  (only harmless typing-annotation false-positives remain); `compileall` passes.
- **Fixed `vgctl.py doctor` crash** — the check-mark formatter referenced an undefined
  `yellow()` (only `green`/`red`/`yel` are defined), raising `NameError` on every
  `doctor` run. Now uses `yel()`.
- `docs/STATUS.md` corrected: the ComfyUI service client (`services/comfyui.py`),
  `services` config block, `/api/services/status`, `/api/generate` and
  `/api/generated/{file}` were previously marked 🔴 "not built" but are live.

## [v1.0] - 2026-08-20

First tagged, public, installable release.

### Added
- Frontend refactored from a single monolithic `index.html` into focused modules
  under `app/assets/js/` (config, utils, tts, messages, personas, sessions,
  chat, autocomplete, team, memory, ui, main) + `app/assets/css/styles.css`.
- Sessions panel in the left sidebar: switch / create / rename / remove / save.
- AI suggestions popup (free-text completions) + ✨ Improve button (rewrites typed text).
- `/ @ #` command/persona/tag autocomplete.
- Team auto-chat: `/team`, `@team`, `team:`, `#team` triggers; Planner → Workers → synthesis.
- Personas management pane (cards, voice clone, save/delete, persistence).
- Memory graph (OKF stats + radial visualizer) and autonomous missions panel.
- `server.py`: mounts `/assets`; `/api/improve` and `/api/suggest` endpoints.
- GitHub Actions CI: Python syntax check + `requirements.txt` install + JSON validate.
- MIT `LICENSE`; branding assets (`logo`, `favicon`, `hero` as SVG + PNG); `README.md`.
- `run.sh` (portable) and `launch.sh` (health-aware full-stack launcher).

### Changed
- Default LLM model is `qwen2.5:3b`; the `/api/suggest` parser tolerates
  plain-text model completions (not just JSON).
- `config.json` drives backend URLs, model, voice, timeouts, and DB backend.

[Unreleased]: https://github.com/TheGMPTeam/VirusGPT/compare/v1.0...HEAD
[v1.0]: https://github.com/TheGMPTeam/VirusGPT/releases/tag/v1.0
