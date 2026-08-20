"""Build the VirusGPT desktop app for Windows (one-folder PyInstaller bundle).

Produces dist/VirusGPT.exe. Native WebView2 via pywebview.

Two build variants:
  • FULL (default): bundles server.py + uvicorn + all deps so the .exe is
    self-contained and starts the backend in-process (localhost:8500).
  • CLIENT-ONLY (set VG_CLIENT_ONLY=1): does NOT bundle the server. The app
    loads the web UI from a remote backend (VG_BACKEND_URL / config.json
    desktop.backend_url) — the "thin Windows client" for a Docker backend.

Run this ON Windows (or cross-build with wine). One-folder is portable.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_NAME = "VirusGPT"
CLIENT_ONLY = os.environ.get("VG_CLIENT_ONLY", "").lower() in ("1", "true", "yes")


def build():
    sep = ";" if sys.platform.startswith("win") else ":"
    spec = [
        sys.executable, "-m", "PyInstaller",
        "--name", APP_NAME,
        "--windowed",
        "--onedir",
        "--hidden-import", "webview",
        "--add-data", f"app{sep}app",
        "--add-data", f"config.json{sep}config.json",
        "--add-data", f"desktop{sep}desktop",
        "desktop/run.py",
    ]
    if not CLIENT_ONLY:
        # Self-contained build bundles the server + its dependency tree.
        spec += [
            "--hidden-import", "services",
            "--hidden-import", "autonomous",
            "--hidden-import", "memory",
            "--hidden-import", "gateway",
        ]
    return subprocess.call(spec, cwd=str(ROOT))


if __name__ == "__main__":
    raise SystemExit(build())
