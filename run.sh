#!/usr/bin/env bash
# Launch the VirusGPT macOS cyberpunk client+server on port 8500.
# Builds a clean venv with the Hermes 3.11 interpreter (system 3.9 is too old
# for FastAPI). Usage: ./run.sh   (Ctrl-C to stop)
set -e
cd "$(dirname "$0")"

PY="$(command -v python3.11 || command -v python3)"
if [ ! -x "$PY" ]; then
  echo "[setup] no python3 found" >&2
  exit 1
fi

if [ ! -d .venv ]; then
  echo "[setup] creating venv with $PY ..."
  "$PY" -m venv .venv
  # install with a clean environment (no inherited PYTHONPATH)
  env -i PATH="$PWD/.venv/bin:/usr/bin:/bin" HOME="$HOME" \
    .venv/bin/pip install -q -r requirements.txt
fi

exec env -i PATH="$PWD/.venv/bin:/usr/bin:/bin" HOME="$HOME" \
  .venv/bin/python server.py
