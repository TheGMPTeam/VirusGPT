<p align="center">
  <img src="app/assets/images/hero.svg" alt="VirusGPT hero banner" width="100%">
</p>

<p align="center">
  <img src="app/assets/images/logo_comfy.png" alt="VirusGPT logo (generated with ComfyUI)" width="120">
</p>

<h1 align="center">VirusGPT</h1>

<p align="center">
  <b>Offline · Local · Private</b> AI agent platform.<br>
  Desktop-first, memory-aware, multi-persona, voice-enabled, and fully runnable on your own machine.
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green.svg" alt="MIT License"></a>
  <img src="https://img.shields.io/badge/runs%20on-macOS%20%2F%20Windows%20%2F%20Linux-blue.svg" alt="platform">
</p>

---

## What VirusGPT is

VirusGPT is a **local AI workstation** built around a native desktop app that wraps a
FastAPI backend, a memory graph, and a multi-agent mission workflow.

Everything runs on your machine:

- no cloud API dependency for the core app flow
- no account login
- no external data sharing by default
- local personas, sessions, memory, and mission state

The **desktop app is the primary UI**. On macOS it installs to `/Applications/VirusGPT.app`
and runs the backend in-process (self-contained). Windows and Linux build scripts are
provided (they package the same app for those platforms).

---

## Features (all tested)

- **Native desktop app** — pywebview shell; backend runs in-process. Packaged via
  PyInstaller for macOS, Windows, and Linux.
- **Multi-persona chat** — rooms, persona lineups, LLM/mention/random routing.
- **Per-sentence voice playback** — replies are split into sentences; each gets its own
  ▶ button, plus a ⏯ play-all and a ⟲ replay. Auto-play streams one sentence at a time
  (no loops). All TTS audio is tied to its chat session and pruned when the session is
  removed/switched/cleared.
- **Voice input** — Whisper STT (mic) in the desktop app; secure-context aware for
  mobile/HTTPS.
- **Autonomous missions** — multi-agent work organized as a kanban board, streamed live,
  with a tool-activity log. `/studio` or `/mission` launches an image-team mission that
  renders a full branding/asset kit.
- **Images via ComfyUI** — single image with 🎨, or full multi-asset kits via
  `/studio` `/mission`. All image generation is rendered by a local **ComfyUI** instance.
- **Memory graph** — local concept graph with health stats and detail editing.
- **In-app updater** — click the version chip in the bottom bar to open an update popup
  that shows the running build, checks for a newer commit, and (when available) pulls,
  rebuilds, and replaces the installed app.
- **Settings** — backend URL, model, default voice, and timeouts, set locally.
- **Theming** — Cyber Matrix, Amber Gears, Ice Neon.

---

## Requirements

- **Ollama** running locally (chat/model backend)
- **PocketTTS** for voice output
- **Whisper** for voice input
- **ComfyUI** for image generation (any image feature)

The exact backend/model/voice/ComfyUI settings are configured via the in-app
**Settings** panel and `config.json`.

---

## Desktop app

### macOS (primary, self-contained)

The packaged app installs to `/Applications/VirusGPT.app` and starts its own backend:

```bash
open /Applications/VirusGPT.app
```

### Run from source (development)

```bash
cd /Users/Master/virusgpt-mac
.venv/bin/python desktop/run.py
```

### Rebuild the desktop bundle

```bash
cd /Users/Master/virusgpt-mac
.venv/bin/python desktop/build-macos.py
```

This builds, stamps the bundle with the git version + commit, and installs it to
`/Applications/VirusGPT.app` (falling back to a repo-local `apps/` copy if
`/Applications` isn't writable). It also writes `app/version.json` + `app/buildinfo.json`
so the in-app updater knows where to rebuild from.

Windows and Linux build scripts live in `desktop/build-windows.py` and
`desktop/build-linux.py`.

### Thin client + remote backend

The desktop app has two modes:

- **Self-contained** (default): bundles and starts the backend in-process.
- **Remote backend** (thin client): points at a backend running elsewhere (e.g. Docker).
  Set `desktop.backend_url` in `config.json`.

---

## Quick start (server + voice services)

`launch.sh` starts the local stack the app expects (server, PocketTTS, Whisper):

```bash
cd /Users/Master/virusgpt-mac
./launch.sh
```

Server only:

```bash
cd /Users/Master/virusgpt-mac
.venv/bin/python server.py
```

Then open `http://localhost:8500`.

---

## CLI control: `vgctl.py`

A dependency-free control tool for local ops.

```bash
.venv/bin/python vgctl.py health
.venv/bin/python vgctl.py doctor
.venv/bin/python vgctl.py fix --kill-ports
.venv/bin/python vgctl.py settings list
.venv/bin/python vgctl.py settings get ollama.default_model
.venv/bin/python vgctl.py settings set ollama.default_model qwen2.5:3b
```

---

## Docker stack (optional)

A full local stack can also run via Docker:

```bash
docker compose up -d --build
```

Services: web UI `:8500`, TTS `:49152`, STT `:8181`, Ollama `:11434`.

---

## Project layout

```text
virusgpt-mac/
├── server.py                 # FastAPI server (chat, memory, missions, assets, images)
├── config.json               # local config for backend/model/theme/services
├── launch.sh                 # one-shot local stack launcher
├── desktop/                  # native app shell + build scripts (macOS/Windows/Linux)
├── app/                      # frontend HTML/CSS/JS and images
├── autonomous/               # mission runtime and self-dev modules
├── memory/                   # memory graph store and helpers
├── gateway/                  # heartbeat / cron / recovery jobs
├── pockettts/                # bundled PocketTTS subproject (TTS)
├── whisper/                  # bundled Whisper STT subproject
├── services/                 # service clients (comfyui, stt, tts, config) + updater
└── docs/                     # architecture, status, roadmap, audit reports
```

### Frontend modules

```text
app/assets/js/
├── config.js                 # globals, API base, rooms/personas state
├── utils.js                  # DOM helpers, themes, health, matrix rain
├── tts.js                    # sentence-streamed TTS, queue drain, per-session audio pruning
├── messages.js               # message rendering + ▶/⏯/⟲ sentence buttons
├── personas.js               # persona management
├── sessions.js               # session management (create/rename/remove + audio prune)
├── chat.js                   # chat routing, slash commands, streaming TTS feed
├── autocomplete.js           # command/persona/tag suggestions
├── team.js                   # kanban mission workflow
├── memory.js                 # memory graph UI
├── ui.js                     # settings, input wiring, mission controls
├── updates.js                # version-bar click -> update popup
└── main.js                   # boot order
```

---

## Architecture notes

- **Desktop-first:** the native app wraps the web UI and starts the backend.
- **Local state:** personas, sessions, and memory are persisted locally.
- **ComfyUI for images:** every image (single 🎨 or `/studio` asset kits) is rendered by
  a local ComfyUI instance via `/api/generate`.
- **Mission-driven:** long-running work uses the mission/kanban flow.
- **No bundler:** the frontend is plain ES modules loaded directly in order.

---

## Testing

```bash
.venv/bin/activate
python -m pytest tests/ -q
```

- `tests/test_backend.py` — FastAPI endpoints (chat, health, memory, missions,
  ComfyUI image generation + path-traversal guards, updater endpoints).
- `tests/test_frontend.py` — Playwright UI suite: per-sentence ▶ isolation, play-all no
  infinite loop, streaming auto-play plays each sentence once, session-audio pruning,
  team round, image generation, responsive layouts (mobile/tablet/desktop/4k).
- `tests/test_split_sentences.py` — sentence splitter unit test.

**80 tests, all passing.**

---

## Documentation

| Path | Doc |
|------|-----|
| `autonomous/` | [README.md](autonomous/README.md) — mission runtime, lifecycle, recovery |
| `pockettts/` | [README.md](pockettts/README.md) — TTS server, voices |
| `whisper/` | [README.md](whisper/README.md) — STT server, model/device config |
| `memory/` | [README.md](memory/README.md) — memory graph store |
| `gateway/` | [README.md](gateway/README.md) — heartbeats, cron, auto-revive |
| `desktop/` | [README.md](desktop/README.md) — native shell and packaging |
| `services/` | [README.md](services/README.md) — async clients for local services |
| `docs/` | STATUS / AUDIT / OPTIMIZATION / REFACTORING / ARCHITECTURE / ROADMAP / UI_AUDIT |

---

## License

Released under the [MIT License](LICENSE). © 2026 TheGMPTeam.
