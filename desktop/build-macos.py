"""Build the VirusGPT desktop app for macOS (one-folder PyInstaller bundle).

Produces dist/VirusGPT.app, then moves it into apps/VirusGPT.app (replacing any
prior build there) so the shipped binary lives in the project's apps/ folder.
The app launches the FastAPI server in-process and opens a native WebView window
(pywebview -> WKWebView). Cross-platform: the Linux/Windows variants use the same
spec with a different --name/icon.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_NAME = "VirusGPT"
APPS_DIR = ROOT / "apps"
DEST_APP = APPS_DIR / f"{APP_NAME}.app"


def build():
    spec = [
        sys.executable, "-m", "PyInstaller",
        "--name", APP_NAME,
        "--windowed",
        "--onedir",
        "--noconfirm",
        "--icon", str(ROOT / "desktop" / "VirusGPT.icns"),
        "--hidden-import", "webview",
        "--hidden-import", "server",
        "--hidden-import", "uvicorn",
        "--hidden-import", "fastapi",
        "--hidden-import", "starlette",
        "--hidden-import", "services",
        "--hidden-import", "autonomous",
        "--hidden-import", "memory",
        "--hidden-import", "gateway",
        "--add-data", f"app{os_sep()}app",
        "--add-data", f"config.json{os_sep()}config.json",
        "--add-data", f"server.py{os_sep()}server.py",
        "--add-data", f"desktop{os_sep()}desktop",
        "desktop/run.py",
    ]
    # PyInstaller one-folder build
    rc = subprocess.call(spec, cwd=str(ROOT))
    if rc != 0:
        return rc
    # Move the freshly built bundle into apps/, replacing any previous build.
    built = ROOT / "dist" / f"{APP_NAME}.app"
    if not built.exists():
        print(f"[build] ERROR: expected built bundle at {built}, none found")
        return 1
    APPS_DIR.mkdir(parents=True, exist_ok=True)
    if DEST_APP.exists():
        print(f"[build] removing previous {DEST_APP}")
        shutil.rmtree(DEST_APP)
    shutil.move(str(built), str(DEST_APP))
    print(f"[build] moved bundle -> {DEST_APP}")
    # dist/ is now empty; leave it (gitignored) or clean it
    return 0


def os_sep():
    return ";" if sys.platform.startswith("win") else ":"


if __name__ == "__main__":
    raise SystemExit(build())
