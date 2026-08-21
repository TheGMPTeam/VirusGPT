<p align="center">
  <img src="app/assets/images/hero.svg" alt="VirusGPT hero banner" width="100%">
</p>

<p align="center">
  <img src="app/assets/images/logo.svg" alt="VirusGPT logo" width="120">
</p>

<h1 align="center">VirusGPT</h1>

<p align="center">
  <b>Offline · Local · Private</b> AI agent platform for macOS, Linux, and Windows.
  <br>
  Desktop-first, memory-aware, multi-persona, TTS/STT-enabled, and fully runnable on your own machine.
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green.svg" alt="MIT License"></a>
  <img src="https://img.shields.io/badge/runs%20on-macOS%20%2F%20Linux%20%2F%20Windows-blue.svg" alt="platform">
</p>

---

## What VirusGPT is

VirusGPT is a local AI workstation built around a native desktop app, a FastAPI backend, a memory graph, and a mission/kanban workflow.

It keeps everything on your machine:
- no cloud API dependency for the core app flow
- no account login
- no external data sharing by default
- local personas, sessions, memory, and mission state

The desktop app is the primary UI now.

---

## Key features

- **Native desktop app** — pywebview shell packaged for macOS, Windows, and Linux.
- **Multi-persona chat** — switch rooms, add personas, clone voices, and route responses by persona.
- **Autonomous Mission** — long-running multi-agent work is organized in a kanban board and streamed live.
- **Memory graph** — concept graph, links, health stats, and detail editing.
- **Voice** — sentence-streamed PocketTTS playback plus Whisper STT input.
- **AI assist** — suggestion chips and the ✨ Improve rewrite helper.
- **Local-first config** — backend, model, voice, and timeouts are set locally.
- **Theming** — Cyber Matrix, Amber Gears, and Ice Neon.

### Mission workflow

The mission area is now kanban-first:
- mission controls sit at the top of the right sidebar
- the mission plan is visible through the kanban board
- no separate team auto-chat output area is used
- mission progress and tool activity are surfaced visually in the board and tool log

---

## Desktop app

### Start the app during development

```bash
cd /Users/Master/virusgpt-mac
.venv/bin/python desktop/run.py
```

### Launch the packaged macOS app

```bash
open /Applications/VirusGPT.app
```

### Rebuild the desktop bundle

```bash
cd /Users/Master/virusgpt-mac
.venv/bin/python desktop/build-macos.py
```

Windows and Linux build scripts live in `desktop/build-windows.py` and `desktop/build-linux.py`.

### Thin Windows client + Docker backend

The desktop app supports two modes:

- **Self-contained** (default): bundles the backend and starts it in-process.
- **Remote backend** (thin client): points at a backend running elsewhere — e.g. Docker on another machine.

To run the Windows app against a Docker backend:

1. Start the backend stack on the Docker host:

```bash
docker compose -f docker-compose.windows.yml up -d --build
```

2. Build the Windows client-only app:

```powershell
set VG_CLIENT_ONLY=1
python desktop/build-windows.py
```

3. Run it with the backend URL (or set `desktop.backend_url` in `config.json`):

```powershell
set VG_BACKEND_URL=http://<docker-host-ip>:8500
dist\VirusGPT\VirusGPT.exe
```

The `.exe` is then just a WebView2 shell — no Python server bundled.

---

## Quick start

```bash
cd /Users/Master/virusgpt-mac
./launch.sh
```

That starts the local server and the voice services the app expects.

If you want the server only:

```bash
cd /Users/Master/virusgpt-mac
.venv/bin/python server.py
```

Then open:

- `http://localhost:8500`

Requirements:
- Ollama running locally
- PocketTTS available for voice output
- Whisper available for voice input

The exact backend/model/voice settings can be changed in the in-app **Settings** panel.

---

## Installation modes

`install.sh` can install different components on different machines and point them at each other through `config.json`.

### Example split setup

```bash
# Machine A: server + model
./install.sh --core --models

# Machine B: voice + speech
./install.sh --tts --stt
```

Then set remote service URLs in `config.json` if needed.

### Common flags

`--all`, `--core`, `--tts`, `--stt`, `--models`, `--autonomous`, `--memory`, `--gateway`, `--ollama-url`, `--model`, `--prefix`, `--skip-deps`, `--dry-run`, `--docker`

Run `./install.sh --help` for the complete list.

### Platform notes

- **macOS** and **Linux**: supported natively
- **Windows**: supported through the Docker stack

---

## CLI control: `vgctl.py`

A dependency-free control tool for local ops.

```bash
.venv/bin/python vgctl.py health
.venv/bin/python vgctl.py doctor
.venv/bin/python vgctl.py fix --kill-ports
.venv/bin/python vgctl.py fix --ensure-venv --restart
.venv/bin/python vgctl.py settings list
.venv/bin/python vgctl.py settings get ollama.default_model
.venv/bin/python vgctl.py settings set ollama.default_model qwen2.5:3b
.venv/bin/python vgctl.py settings reset tts.enabled
```

---

## Docker stack

VirusGPT also supports a full local stack using Docker:

```bash
docker compose up -d --build
```

Services:
- web UI: `http://localhost:8500`
- TTS: `http://localhost:49152`
- STT: `http://localhost:8181`
- Ollama: `http://localhost:11434`

---

## Project layout

```text
virusgpt-mac/
├── server.py                 # FastAPI server (chat, memory, missions, assets)
├── config.json               # local config for backend/model/theme/services
├── launch.sh                 # one-shot local stack launcher
├── desktop/                  # native app shell + build scripts
├── app/                      # frontend HTML/CSS/JS and images
├── autonomous/               # mission runtime and self-dev modules
├── memory/                   # memory graph store and helpers
├── gateway/                  # heartbeat / cron / recovery jobs
├── pockettts/                # bundled PocketTTS subproject
├── whisper/                  # bundled Whisper STT subproject
├── services/                 # service clients and runtime config helpers
└── docs/                     # architecture, status, roadmap, swarm docs
```

### Frontend modules

```text
app/assets/js/
├── config.js                 # globals, API base, rooms/personas state
├── utils.js                  # DOM helpers, themes, health, matrix rain
├── tts.js                    # sentence-streamed TTS and queue drain
├── messages.js               # message rendering helpers
├── personas.js               # persona management
├── sessions.js               # session management
├── chat.js                   # chat routing and slash commands
├── autocomplete.js           # command/persona/tag suggestions
├── team.js                   # kanban mission workflow + stop-all
├── memory.js                 # memory graph UI
├── ui.js                     # settings, input wiring, mission controls
└── main.js                   # boot order
```

`index.html` is a thin shell that loads those modules in order.

---

## Architecture notes

- **Desktop-first:** the native app wraps the web UI and starts the backend.
- **Local state:** personas and sessions are persisted locally.
- **Memory-aware:** memory graph retrieval is available by default in chat.
- **Mission-driven:** long-running work uses the mission/kanban flow rather than a separate auto-chat mode.
- **No bundler:** the frontend is plain ES modules loaded directly in order.

---

## Documentation

| Path | Doc | What it covers |
|------|-----|----------------|
| `autonomous/` | [README.md](autonomous/README.md) | Mission runtime, lifecycle, REST, recovery |
| `pockettts/` | [README.md](pockettts/README.md) | TTS server, voices, env vars |
| `whisper/` | [README.md](whisper/README.md) | STT server and model/device config |
| `memory/` | [README.md](memory/README.md) | Memory graph store and endpoints |
| `gateway/` | [README.md](gateway/README.md) | Heartbeats, cron jobs, auto-revive |
| `desktop/` | [README.md](desktop/README.md) | Native shell and packaging |
| `services/` | [README.md](services/README.md) | Async clients for local services |
| `docs/` | — | Architecture, status, roadmap, swarm docs |

---

## Quality & audit reports

- [`docs/STATUS.md`](docs/STATUS.md) — live build-status matrix (CI-gated against the code).
- [`docs/AUDIT.md`](docs/AUDIT.md) — security, structure, docs-drift, and test-coverage audit.
- [`docs/OPTIMIZATION.md`](docs/OPTIMIZATION.md) — footprint, hot paths, dead code, follow-ups.
- [`docs/REFACTORING.md`](docs/REFACTORING.md) — analysis of the recent TTS / auto-play / tool-logging / orchestrator refactors.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) · [`docs/ROADMAP.md`](docs/ROADMAP.md) · [`docs/UI_AUDIT.md`](docs/UI_AUDIT.md)

Run the full suite: `python -m pytest tests/ -q` (backend + Playwright UI + splitter unit).

## License

Released under the [MIT License](LICENSE). © 2026 TheGMPTeam.
