# Changelog

All notable changes to VirusGPT are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
semantic versioning (`v1.0` = first tagged, installable release).

## [Unreleased]

### Added
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
