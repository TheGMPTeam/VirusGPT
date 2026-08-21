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
- `scripts/audit_status.py` — CI gate that cross-checks `docs/STATUS.md`'s
  🔴/🟡/✅ matrix against real files, `/api/*` routes and key symbols (stdlib
  only). Drift (a not-built item that exists, or a live item that's missing)
  fails the build. Wired into GitHub Actions as a new CI step.

### Fixed
- **Tool calls were never logged in the mission UI.** The autonomous status
  endpoint (`/api/autonomous/status/{id}`, which the kanban/tool-log UI polls)
  returned each event's `data` field as a raw JSON *string*, so the frontend's
  `if(ev.data && ev.data.tool)` guard was always false and `logToolCall` never
  fired. The event bus stores `data` as a string (durable) but the API now
  parses it back to a dict on the way out (matching the SSE stream handler).
  `logToolCall` also now defensively `JSON.parse`s string args.
- **Chat TTS per-sentence playback finalized inconsistently.** The streaming
  turn appended per-sentence ▶ buttons live, but the finalized bubble only got a
  `⟲ replay-all` (no per-sentence buttons, no end-of-message "play all"). Added
  `buildSentencePlays()` / `makePlayAll()` so every assistant bubble — both the
  live finalize and history messages — renders ONE ▶ button per clean sentence
  (each fetches its own PocketTTS mp3 on click) plus a click-only `⏯ Play all`
  at the end that does NOT auto-play unless clicked. Auto-play (speaker toggle)
  still streams sentences one-after-another in order during generation.
- **Per-sentence ▶ buttons played the WHOLE reply, not their own sentence.**
  Every ▶ wired into the shared global `ttsQueue`, so if auto-play had already
  queued the full reply (or was still draining), clicking any button appended on
  top and `pumpTTS` played everything. Added `playSingle()` — an isolated
  single-sentence player that stops any in-flight audio and clears the queue so
  clicking the 3rd button plays ONLY the 3rd sentence. ▶ now uses `playSingle`;
  the "Play all" ⏯ / "Replay all" ⟲ still use the full queue.
- **New session auto-play behavior.** Added a per-session `sessionAutoPlay`
  flag. `/clear` and `/new` now set it `false` (and `stopTTS()`), so a fresh
  session is quiet for streaming auto-play. Toggling the speaker button ON
  re-enables auto-play for subsequent messages; boot seeds it from the saved
  `TTS_ON` state. (Term "autopsy" in the request = the speaker/auto-play
  toggle.)
 
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
