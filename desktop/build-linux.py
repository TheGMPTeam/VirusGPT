"""Build the VirusGPT desktop app for Linux (one-folder PyInstaller bundle).

Produces dist/VirusGPT. Native WebKit via pywebview (requires libwebkit2gtk).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_NAME = "VirusGPT"


def build():
    sep = ";" if sys.platform.startswith("win") else ":"
    spec = [
        sys.executable, "-m", "PyInstaller",
        "--name", APP_NAME,
        "--windowed",
        "--onedir",
        "--hidden-import", "webview",
        "--hidden-import", "services",
        "--hidden-import", "autonomous",
        "--hidden-import", "memory",
        "--hidden-import", "gateway",
        "--add-data", f"app{sep}app",
        "--add-data", f"config.json{sep}config.json",
        "--add-data", f"desktop{sep}desktop",
        "desktop/run.py",
    ]
    return subprocess.call(spec, cwd=str(ROOT))


if __name__ == "__main__":
    raise SystemExit(build())
