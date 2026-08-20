#!/usr/bin/env bash
# VirusGPT macOS launcher — idempotent + health-aware.
# Ensures the VirusGPT server (:8500), PocketTTS (:49152) and Whisper STT (:8181)
# are running AND healthy. Starts/restart any that are down or unhealthy.
# Safe to run repeatedly (cron / gateway hook / manual).
set -u
ROOT=/Users/Master/virusgpt-mac
HOME=${HOME:-/Users/Master}
POCKET_VENV=$ROOT/pockettts/.venv/bin
VGPT_VENV=$ROOT/.venv/bin
WHISPER_VENV=$ROOT/whisper/.venv/bin
LOGDIR=$ROOT/logs
mkdir -p "$LOGDIR"
ts() { date "+%Y-%m-%d %H:%M:%S"; }

# Is the service on $1 healthy? Tests the port AND an optional health URL.
# Returns 0 if healthy, 1 otherwise.
health_ok() {
  local port="$1" url="${2:-}"
  # port must be listening
  lsof -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1 || return 1
  # if a health URL is given, it must return 2xx
  if [ -n "$url" ]; then
    local code
    code=$(curl -sk -m 5 -o /dev/null -w "%{http_code}" "$url" 2>/dev/null)
    [ "$code" = "200" ] || return 1
  fi
  return 0
}

# Kill anything still bound to $1 (stale/hung process), then start $2.. as the
# service command. Waits briefly and re-checks.
restart_service() {
  local name="$1" port="$2" url="$3"; shift 3
  echo "[launch $(ts)] $name unhealthy on :$port — restarting"
  # kill process holding the port
  local pids
  pids=$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
  if [ -n "$pids" ]; then
    kill $pids 2>/dev/null || true
    sleep 1
    pids=$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
    [ -n "$pids" ] && kill -9 $pids 2>/dev/null || true
  fi
  sleep 1
  # start fresh
  ( "$@" >>"$LOGDIR/${name}.log" 2>&1 & )
  sleep 2
  if health_ok "$port" "$url"; then
    echo "[launch $(ts)] $name back up on :$port"
  else
    echo "[launch $(ts)] WARNING: $name still not healthy on :$port"
  fi
}

# --- Whisper STT (:8181) ---
if health_ok 8181 "http://127.0.0.1:8181/health"; then
  echo "[launch $(ts)] Whisper STT ok on :8181"
else
  restart_service whisper 8181 "http://127.0.0.1:8181/health" \
    env -i PATH="$WHISPER_VENV:/usr/bin:/bin" HOME="$HOME" \
    WHISPER_MODEL="${WHISPER_MODEL:-base}" WHISPER_HOST=127.0.0.1 WHISPER_PORT=8181 \
    "$WHISPER_VENV/python" "$ROOT/whisper/server.py"
fi

# --- PocketTTS (:49152) ---
if health_ok 49152 "http://127.0.0.1:49152/health"; then
  echo "[launch $(ts)] PocketTTS ok on :49152"
else
  restart_service pockettts 49152 "http://127.0.0.1:49152/health" \
    env -i PATH="$POCKET_VENV:/usr/bin:/bin" HOME="$HOME" \
    POCKET_TTS_PORT=49152 POCKET_TTS_HOST=127.0.0.1 \
    "$POCKET_VENV/python" "$ROOT/pockettts/server.py"
fi

# --- VirusGPT server (:8500) ---
if health_ok 8500 "http://localhost:8500/api/health"; then
  echo "[launch $(ts)] VirusGPT server ok on :8500"
else
  restart_service virusgpt 8500 "http://localhost:8500/api/health" \
    env -i PATH="$VGPT_VENV:/usr/bin:/bin" HOME="$HOME" \
    "$VGPT_VENV/python" "$ROOT/server.py"
fi

echo "[launch $(ts)] done"

# --- VirusGPT Gateway (heartbeats + cron) ---
if health_ok 8500 "http://localhost:8500/api/health"; then
  GW_PID=$(pgrep -f "gateway/service.py" || true)
  if [ -z "$GW_PID" ]; then
    ( env -i PATH="$VGPT_VENV:/usr/bin:/bin" HOME="$HOME" \
        "$VGPT_VENV/python" "$ROOT/gateway/service.py" >>"$LOGDIR/gateway.log" 2>&1 & )
    echo "[launch $(ts)] gateway started"
  else
    echo "[launch $(ts)] gateway already running (pid $GW_PID)"
  fi
else
  echo "[launch $(ts)] server not up — gateway not started (relaunch after server is healthy)"
fi
