<p align="center">
  <img src="app/assets/images/hero.svg" alt="VirusGPT hero banner" width="100%">
</p>

<p align="center">
  <img src="app/assets/images/logo.svg" alt="VirusGPT logo" width="120">
</p>

<h1 align="center">VirusGPT</h1>

<p align="center">
  <b>Offline · Local · Private</b> AI agent chat — personas, team auto-chat, TTS/STT, a memory graph, and autonomous missions.
  <br>Everything runs on your own machine (Ollama + PocketTTS + Whisper). No cloud, no accounts, no data leaves the box.
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green.svg" alt="MIT License"></a>
  <img src="https://img.shields.io/badge/runs%20on-macOS%20%2F%20Linux-blue.svg" alt="platform">
</p>

---

## ✨ Features

- **Multi-persona chat** — switch, create, and clone voices for each persona. The LLM decides who answers, or you pick.
- **Team auto-chat** — a Planner decomposes a task (silently) and Workers execute in-chat with TTS. Triggers: `/team <task>`, `@team`, `team:`, `#team`.
- **AI suggestions & Improve** — free-text typing shows AI completion chips; the ✨ Improve button rewrites your draft.
- **Voice** — sentence-streamed TTS (PocketTTS) with no-overlap playback, plus Whisper STT via the mic button.
- **Memory graph** — OKF-style knowledge-graph stats with a radial visualizer.
- **Autonomous missions** — long-running multi-agent goals streamed via SSE.
- **Theming** — Cyber Matrix / Amber Gears / Ice Neon, with a matrix-rain background.

## 🚀 Quick start

```bash
# 1. (macOS) install & launch the local AI stack
./launch.sh            # starts the server on :8500 and PocketTTS on :49152

# …or run the server directly inside the venv
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python server.py
```

Open <http://localhost:8500>. Requires a local Ollama with `qwen2.5:3b` (and any
persona voices) plus a running PocketTTS instance for voice.

> Configure the backend, model, default voice, and think-time-out in the in-app
> **⚙ Settings** panel.

## 📦 Installer (modular / multi-machine)

`install.sh` installs **different components on different local machines** and
points them at each other via `config.json`. This is handy when one box has a
GPU/torch and another is light.

```bash
# Machine A (LLM + web UI):
./install.sh --core --models

# Machine B (voice + speech, has torch / GPU):
./install.sh --tts --stt

# then on A, set in config.json:
#   "tts":  { "base_url": "http://<B-ip>:49152" }
#   "stt":  { "base_url": "http://<B-ip>:8181"  }
```

Flags: `--all` (default), `--core`, `--tts`, `--stt`, `--models`, `--autonomous`,
`--memory`, `--gateway`, `--ollama-url URL`, `--model NAME`, `--prefix DIR`,
`--skip-deps`, `--dry-run`, `--docker`.
Run `./install.sh --help` for the full list. macOS and Linux (apt) are supported
natively; **Windows uses the Docker stack** (native Windows can't run the torch/
PocketTTS POSIX build). The script is idempotent and safe to re-run.

```bash
# Windows (WSL2 + Docker Desktop):
docker compose up -d --build
# or acknowledge the Docker path via the installer:
./install.sh --docker
```

After install, launch with `./run.sh` (server only) or `./launch.sh` (full stack:
server + PocketTTS + Whisper, health-aware, safe for cron).

## 🩺 CLI: `vgctl.py`

A dependency-free control tool for ops without the browser.

```bash
.venv/bin/python vgctl.py health                         # probe /api/health
.venv/bin/python vgctl.py doctor                         # full diagnostic of stack + config
.venv/bin/python vgctl.py fix --kill-ports              # clear stale processes on stack ports
.venv/bin/python vgctl.py fix --ensure-venv --restart   # rebuild venv + relaunch stack
.venv/bin/python vgctl.py settings list                 # print config.json
.venv/bin/python vgctl.py settings get ollama.default_model
.venv/bin/python vgctl.py settings set ollama.default_model qwen2.5:3b
.venv/bin/python vgctl.py settings reset tts.enabled    # remove key -> server default
```

`settings` writes land in `config.json` and apply on the next server boot
(`set` auto-types bool/int/float/str). `fix --restart` calls `launch.sh`.

## 🐳 Docker (full stack)

Run the entire local AI stack — web server, TTS, STT, and Ollama — with one command:

```bash
docker compose up -d --build
# web UI:  http://localhost:8500
# tts:     http://localhost:49152
# stt:     http://localhost:8181
# ollama:  http://localhost:11434
```

Service URLs are wired via the compose network automatically (no `config.json`
edits needed — the server reads `VG_OLLAMA_URL` / `VG_TTS_URL` / `VG_STT_URL`
env vars, see `docker-compose.yml`). GPU passthrough for Ollama is enabled by
default; comment out the `deploy.resources` block for CPU-only. Pull the model
once the stack is up:

```bash
docker compose exec ollama ollama pull qwen2.5:3b
```

## 🗂 Project layout

The frontend was refactored out of a single `index.html` into focused modules:

```
virusgpt-mac/
├── server.py                 # FastAPI server (chat SSE, TTS/STT, memory, missions, /assets)
├── config.json               # backend / title / default theme
├── launch.sh                 # one-shot local stack launcher
├── requirements.txt
├── assets/
│   ├── images/               # logo.svg, logo.png, favicon.svg/png, hero.svg
│   ├── css/
│   │   └── styles.css        # full theme (cyber / amber / ice)
│   └── js/
│       ├── config.js         # globals, API base, personas/rooms state, localStorage
│       ├── utils.js          # $, tabs, health probe, themes, matrix rain
│       ├── tts.js            # sentence-streamed TTS + serial drain
│       ├── messages.js       # markdown strip, sentence split, bubble render, replay
│       ├── personas.js       # room lineup + Personas management tab (clone/save/delete)
│       ├── sessions.js       # Sessions panel: switch / create / rename / remove / save
│       ├── chat.js           # send(), persona routing, slash commands, streaming turn
│       ├── autocomplete.js   # / @ # popup + AI suggestion chips + Improve button
│       ├── team.js           # planner→workers→synthesis auto-chat + stop-all
│       ├── memory.js         # OKF graph stats + radial canvas
│       ├── ui.js             # settings modal, input wiring, mic, missions panel
│       └── main.js           # boot() + init order
├── pockettts/                # bundled PocketTTS voice server (separate subproject)
└── services/                 # server-side services (llm, tts, stt, memory, config)
```

`index.html` is now a thin shell: it carries the markup and loads the CSS +
JS modules in dependency order (`config → utils → tts → messages → personas →
sessions → chat → autocomplete → team → memory → ui → main`).

## 🧩 Architecture notes

- **No build step.** Plain ES (no bundler) — the `<script>` tags load in order, so
  `config.js` globals (`$`, `API`, `personas`, `rooms`, …) are available to later files.
- **State** lives in `localStorage` (`vg_personas`, `vg_rooms`) and is mirrored to the
  server where the API allows (personas).
- **Server** serves `app/index.html` and mounts `app/assets` at `/assets`.

## 📜 License

Released under the [MIT License](LICENSE). © 2026 TheGMPTeam.
