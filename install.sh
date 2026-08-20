#!/usr/bin/env bash
# VirusGPT installer — modular, multi-machine aware.
#
# Install DIFFERENT parts on DIFFERENT local machines, then point them at each
# other via config.json. For example:
#   • Machine A (GPU/quiet):  ./install.sh --core --models
#   • Machine B (has torch):  ./install.sh --tts --stt
#   • Machine C (light):      ./install.sh --core
# and set backend_url / tts.base_url / stt.base_url in config.json so they
# talk over the LAN.
#
# Flags:
#   --all           install everything (default if no component flag given)
#   --core          FastAPI server + services + app (the web UI)
#   --tts           PocketTTS voice server (torch CPU, heavy)
#   --stt           Whisper STT server (faster-whisper)
#   --models        pull the Ollama LLM model(s)
#   --autonomous    mission engine (no extra deps; bundled with --core)
#   --memory        VirusGPT's own local concept-memory store (data/memory/; bundled with --core)
#   --gateway       Gateway supervisor: heartbeats + cron jobs (auto-heals the stack)
#   --ollama-url    Ollama base URL for --models (default http://localhost:11434)
#   --model         model to pull (default from config.json or qwen2.5:3b)
#   --prefix DIR    install venvs under DIR (default: repo root)
#   --skip-deps     build venvs but skip pip installs (offline/dev)
#   --dry-run       print what would happen, make no changes
#   -h, --help      show this help
#
# Platforms:
#   macOS / Linux : full native install (this script).
#   Windows       : use Docker — see docker-compose.yml (docker compose up -d --build).
#                   Native Windows is not supported (torch/PocketTTS need a POSIX env).
#
# Supported: macOS, Linux (apt-based). Windows: use the Docker Compose stack.

set -u
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# ---------- defaults ----------
DO_ALL=1
DO_CORE=0; DO_TTS=0; DO_STT=0; DO_MODELS=0; DO_AUTO=0; DO_MEM=0; DO_GW=0; DO_DOCKER=0
OLLAMA_URL="http://localhost:11434"
MODEL=""
PREFIX="$ROOT"
SKIP_DEPS=0
DRY_RUN=0

# ---------- parse args ----------
while [ $# -gt 0 ]; do
  case "$1" in
    --all)        DO_ALL=1 ;;
    --core)       DO_ALL=0; DO_CORE=1 ;;
    --tts)        DO_ALL=0; DO_TTS=1 ;;
    --stt)        DO_ALL=0; DO_STT=1 ;;
    --models)     DO_ALL=0; DO_MODELS=1 ;;
    --autonomous) DO_ALL=0; DO_AUTO=1 ;;
    --memory)     DO_ALL=0; DO_MEM=1 ;;
    --gateway)    DO_ALL=0; DO_GW=1 ;;
    --docker)     DO_DOCKER=1 ;;
    --ollama-url) OLLAMA_URL="$2"; shift ;;
    --model)      MODEL="$2"; shift ;;
    --prefix)     PREFIX="$2"; shift ;;
    --skip-deps)  SKIP_DEPS=1 ;;
    --dry-run)    DRY_RUN=1 ;;
    -h|--help)    sed -n '2,40p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $1 (try --help)"; exit 2 ;;
  esac
  shift
done

if [ "$DO_ALL" = "1" ]; then
  DO_CORE=1; DO_TTS=1; DO_STT=1; DO_MODELS=1; DO_AUTO=1; DO_MEM=1; DO_GW=1
fi

# ---------- helpers ----------
log()  { echo "[install] $*"; }
run()  { if [ "$DRY_RUN" = "1" ]; then echo "   (dry-run) $*"; else eval "$@"; fi; }
need() { command -v "$1" >/dev/null 2>&1 || { echo "ERROR: required tool '$1' not found"; exit 1; }; }

# OS detection
OS="$(uname -s)"
case "$OS" in
  Darwin) PKG="macos" ;;
  Linux)  PKG="linux" ;;
  MINGW*|CYGWIN*|MSYS*|Windows_NT) PKG="windows" ;;
  *) echo "ERROR: unsupported OS '$OS'"; exit 1 ;;
esac

# Windows has no native POSIX env for torch/PocketTTS — route to Docker.
if [ "$PKG" = "windows" ]; then
  if [ "$DO_DOCKER" = "1" ]; then
    log "detected OS: windows (Docker mode requested)"
  else
    echo
    echo "ERROR: native install is not supported on Windows."
    echo "        Run the full stack with Docker instead:"
    echo
    echo "            docker compose up -d --build"
    echo
    echo "        (requires Docker Desktop for Windows + WSL2)"
    echo "        Re-run with:  ./install.sh --docker   to acknowledge and exit cleanly."
    echo
    exit 3
  fi
fi
log "detected OS: $PKG ($OS)"

need python3
PY="$(command -v python3)"
log "python: $PY ($($PY --version 2>&1))"

# Default model: from config.json if present, else qwen2.5:3b
if [ -z "$MODEL" ]; then
  if [ -f config.json ]; then
    MODEL="$(python3 -c "import json;print(json.load(open('config.json')).get('ollama',{}).get('default_model','qwen2.5:3b'))" 2>/dev/null)"
  fi
  [ -z "$MODEL" ] && MODEL="qwen2.5:3b"
fi
log "LLM model target: $MODEL"

mkvenv() { # $1 = venv path
  local v="$1"
  if [ ! -d "$v" ]; then
    log "creating venv: $v"
    run "$PY -m venv '$v'"
  fi
}

# ---------- component installs ----------
install_core() {
  log "== core (server + services + app) =="
  local v="$PREFIX/.venv"
  mkvenv "$v"
  if [ "$SKIP_DEPS" = "0" ]; then
    run "'$v/bin/pip' install -q --upgrade pip"
    run "'$v/bin/pip' install -q -r requirements.txt"
  fi
  log "core ready. Launch with: ./run.sh  (or: $v/bin/python server.py)"
}

install_autonomous() {
  log "== autonomous mission engine =="
  log "no extra deps — bundled with --core (uses services/ + autonomous/)."
}

install_memory() {
  log "== local concept-memory store (data/memory/) =="
  log "self-contained OKF-style store; no external Docker/Hermes pool."
  local v="$PREFIX/.venv"
  # Seed fresh VirusGPT concepts on first launch (server does this automatically,
  # but we touch the bundle now so it exists and is LAN-reachable like the rest).
  run "'$v/bin/python' -c \"import asyncio, services.memory as m; print('memory concepts:', (asyncio.run(m.memory_status()) or {}).get('concepts'))\""
  log "memory ready. It is served through the main server port (like TTS/STT)."
}

install_gateway() {
  log "== gateway supervisor (heartbeats + cron) =="
  log "no extra deps — uses the server venv + launch.sh. Starts automatically with launch.sh."
  local v="$PREFIX/.venv"
  run "'$v/bin/python' -c \"import ast; ast.parse(open('gateway/service.py').read()); print('gateway syntax ok')\""
  log "gateway ready. Start the full stack with: ./launch.sh  (it launches the gateway too)"
}

install_docker() {
  log "== Docker stack (cross-platform, includes Windows) =="
  if ! command -v docker >/dev/null 2>&1; then
    log "docker CLI not found. Install Docker Desktop first:"
    case "$PKG" in
      windows) log "   https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe" ;;
      macos)   log "   https://desktop.docker.com/mac/main/amd64/Docker.dmg (or: brew install --cask docker)" ;;
      linux)   log "   https://docs.docker.com/engine/install/" ;;
    esac
    return 0
  fi
  if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
    log "docker compose plugin missing. See https://docs.docker.com/compose/install/"
    return 0
  fi
  log "Building + starting the full stack (virusgpt + pockettts + whisper + ollama)..."
  run "docker compose up -d --build"
  log "Stack up. Web UI: http://localhost:8500  (TTS :49152, STT :8181, Ollama :11434)"
}

install_tts() {
  log "== PocketTTS voice server =="
  local v="$PREFIX/pockettts/.venv"
  mkvenv "$v"
  if [ "$SKIP_DEPS" = "0" ]; then
    run "'$v/bin/pip' install -q --upgrade pip"
    run "'$v/bin/pip' install -q -r pockettts/requirements.txt"
  fi
  log "tts ready. Launch: POCKET_TTS_PORT=49152 '$v/bin/python' pockettts/server.py"
}

install_stt() {
  log "== Whisper STT server =="
  local v="$PREFIX/whisper/.venv"
  mkvenv "$v"
  if [ "$SKIP_DEPS" = "0" ]; then
    run "'$v/bin/pip' install -q --upgrade pip"
    run "'$v/bin/pip' install -q -r whisper/requirements.txt"
  fi
  log "stt ready. Launch: WHISPER_MODEL=base '$v/bin/python' whisper/server.py"
}

install_models() {
  log "== Ollama LLM model(s) from $OLLAMA_URL =="
  if ! command -v ollama >/dev/null 2>&1; then
    log "ollama CLI not found. Install it first:"
    case "$PKG" in
      macos) log "   brew install ollama   (or https://ollama.com/download)" ;;
      linux) log "   curl -fsSL https://ollama.com/install.sh | sh" ;;
    esac
    log "Then re-run: ./install.sh --models"
    return 0
  fi
  run "ollama pull '$MODEL'"
  log "model '$MODEL' pulled."
}

# ---------- run selected ----------
[ "$DO_CORE"   = "1" ] && install_core
[ "$DO_AUTO"   = "1" ] && install_autonomous
[ "$DO_MEM"    = "1" ] && install_memory
[ "$DO_GW"     = "1" ] && install_gateway
[ "$DO_DOCKER" = "1" ] && install_docker
[ "$DO_TTS"    = "1" ] && install_tts
[ "$DO_STT"    = "1" ] && install_stt
[ "$DO_MODELS" = "1" ] && install_models

# ---------- summary ----------
echo
log "=== install summary ==="
echo "  OS:            $PKG"
echo "  components:    $([ $DO_CORE = 1 ] && echo -n 'core ')$([ $DO_TTS = 1 ] && echo -n 'tts ')$([ $DO_STT = 1 ] && echo -n 'stt ')$([ $DO_MODELS = 1 ] && echo -n 'models ')$([ $DO_AUTO = 1 ] && echo -n 'autonomous ')"
echo "  venv prefix:   $PREFIX"
echo "  model:         $MODEL"
echo
echo "Next: edit config.json so backend_url / tts.base_url / stt.base_url point at"
echo "the right machines, then run ./run.sh (or ./launch.sh for the full stack)."
echo "Multi-machine example:"
echo "   machine A: ./install.sh --core --models"
echo "   machine B: ./install.sh --tts --stt"
echo "   machine A config.json: tts.base_url -> http://<B-ip>:49152, stt.base_url -> http://<B-ip>:8181"
