"""Build the VirusGPT desktop app for macOS (one-folder PyInstaller bundle).

Produces dist/VirusGPT.app. The app launches the FastAPI server in-process and
opens a native WebView window (pywebview -> WKWebView). Cross-platform: the
Linux/Windows variants use the same spec with a different --name/icon.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_NAME = "VirusGPT"


def build():
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
        "--add-data", f"app{os_sep()}app",
        "--add-data", f"config.json{os_sep()}config.json",
        "--add-data", f"desktop{os_sep()}desktop",
        "desktop/run.py",
    ]
    # PyInstaller one-folder build
    return subprocess.call(spec, cwd=str(ROOT))


def os_sep():
    return ";" if sys.platform.startswith("win") else ":"


if __name__ == "__main__":
    raise SystemExit(build())
