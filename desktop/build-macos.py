"""Build the VirusGPT desktop app for macOS (one-folder PyInstaller bundle).

Builds dist/VirusGPT.app, stamps it with the git version + commit (written to
app/version.json and injected into the frozen index.html as window.__VG_VERSION),
then MOVES it into apps/VirusGPT.app — replacing any prior build there — so there
is exactly ONE shipped app. The app launches the FastAPI server in-process and
opens a native WebView window (pywebview -> WKWebView). Cross-platform: the
Linux/Windows variants use the same spec with a different --name/icon.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_NAME = "VirusGPT"
APPS_DIR = ROOT / "apps"
DEST_APP = APPS_DIR / f"{APP_NAME}.app"


def git_version():
    """Return (version, commit) from git. version = latest tag (or '1.0'),
    commit = short hash. Falls back to ('dev', 'unknown') if git is unavailable."""
    version = "1.0"
    commit = "unknown"
    try:
        tag = subprocess.run(
            ["git", "-C", str(ROOT), "describe", "--tags", "--always"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        if tag:
            # describe gives e.g. "v1.0-3-gabcd123" or "gabcd123"; strip leading g/
            commit = tag.split("-")[-1].lstrip("g")
        else:
            commit = subprocess.run(
                ["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=10,
            ).stdout.strip()
        # use the most recent tag if present for the version label
        taglist = subprocess.run(
            ["git", "-C", str(ROOT), "tag", "--sort=-creatordate"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip().splitlines()
        if taglist:
            version = taglist[0].lstrip("v")
    except Exception as e:
        print(f"[build] version probe failed ({e}); using dev/unknown")
    return version, commit


def stamp_version():
    """Write app/version.json and return (version, commit)."""
    version, commit = git_version()
    payload = {"version": version, "commit": commit}
    (ROOT / "app" / "version.json").write_text(json.dumps(payload))
    print(f"[build] version: v{version} · {commit}")
    return version, commit


def build():
    version, commit = stamp_version()
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
    # Inject the version global into the frozen index.html so the bottom bar shows
    # it even if version.json fetch is blocked (file:// / offline WebView).
    inject_version_global(DEST_APP / "Contents" / "Resources" / "app" / "index.html",
                          version, commit)
    # Remove the leftover dist/ copy so there is never a second app on disk.
    left = ROOT / "dist" / f"{APP_NAME}.app"
    if left.exists():
        shutil.rmtree(left)
    return 0


def inject_version_global(index_html, version, commit):
    if not index_html.exists():
        print(f"[build] (warn) frozen index.html not found at {index_html}")
        return
    html = index_html.read_text()
    marker = "<!--__VG_VERSION__-->"
    snippet = (f'<script>window.__VG_VERSION={{"version":"{version}",'
               f'"commit":"{commit}"}};</script>')
    if marker in html:
        html = html.replace(marker, snippet)
    elif "window.__VG_VERSION" not in html:
        # inject just before </head> if present, else at top of <body>
        if "</head>" in html:
            html = html.replace("</head>", f"{snippet}\n</head>", 1)
        elif "<body" in html:
            html = html.replace("<body", f"<body>{snippet}", 1)
        else:
            html = snippet + html
    index_html.write_text(html)
    print(f"[build] stamped frozen index.html with v{version} · {commit}")


def os_sep():
    return ";" if sys.platform.startswith("win") else ":"


if __name__ == "__main__":
    raise SystemExit(build())
