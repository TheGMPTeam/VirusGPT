"""In-app update engine for the VirusGPT desktop app.

The frozen desktop bundle is self-contained (no .venv / PyInstaller at runtime),
so "update" means: pull the latest source from git and REBUILD the bundle with
the dev venv (which has PyInstaller), then replace the running apps/VirusGPT.app
and relaunch.

Flow (driven by the frontend update popup):
  GET  /api/version        -> currently running version/commit + updatability
  GET  /api/update/check   -> compare current commit vs origin/main; return notes
  POST /api/update/apply    -> start a background update (git pull + rebuild)
  GET  /api/update/status   -> poll progress/state of the in-flight update

All git/build work is isolated here so server.py stays thin and it is easy to
test (monkeypatch the subprocess/git calls).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

# ROOT is the repo root when running from source, or the frozen Resources dir
# (apps/VirusGPT.app/Contents/Resources) when running from the built app.
ROOT = Path(__file__).resolve().parent.parent
BUILDINFO = ROOT / "app" / "buildinfo.json"
VERSION_FILE = ROOT / "app" / "version.json"

# In-flight update state, polled by the frontend via /api/update/status.
_STATE = {
    "running": False,
    "stage": "idle",       # idle | fetching | rebuilding | replacing | done | error
    "progress": 0,         # 0..100
    "message": "",
    "error": None,
    "started_at": None,
    "finished_at": None,
}


# --------------------------------------------------------------------------
# Version / build info
# --------------------------------------------------------------------------
def _read_json(path: Path):
    try:
        return json.loads(path.read_text()) if path.exists() else {}
    except Exception:
        return {}


def get_buildinfo() -> dict:
    """Where to rebuild from. Written by desktop/build-*.py at build time.

    Falls back to sensible defaults so dev runs (source tree) are updatable too.
    """
    info = _read_json(BUILDINFO)
    source_dir = info.get("source_dir") or str(ROOT)
    venv_py = info.get("venv") or str(ROOT / ".venv" / "bin" / "python")
    return {
        "source_dir": source_dir,
        "venv": venv_py,
        "updatable": os.path.isdir(source_dir) and os.path.exists(venv_py),
    }


def get_version() -> dict:
    v = _read_json(VERSION_FILE)
    info = get_buildinfo()
    return {
        "version": v.get("version", "dev"),
        "commit": v.get("commit", _git("rev-parse", "--short", "HEAD") or "unknown"),
        "build_time": v.get("build_time"),
        "source_dir": info["source_dir"],
        "updatable": info["updatable"],
    }


def _git(*args, cwd=None, timeout=30):
    try:
        r = subprocess.run(
            ["git", *args], cwd=str(cwd or ROOT),
            capture_output=True, text=True, timeout=timeout,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def _git_ok(cwd=None):
    return bool(_git("rev-parse", "--is-inside-work-tree", cwd=cwd, timeout=10))


# --------------------------------------------------------------------------
# Update check
# --------------------------------------------------------------------------
def check_update() -> dict:
    """Compare the running commit against the latest remote (origin/main).

    Returns current/latest commits, whether we are behind, and a short list of
    commit subjects that would be applied. Never raises — surfaces errors.
    """
    info = get_buildinfo()
    src = info["source_dir"]
    current = get_version().get("commit", "")
    out = {
        "current": current,
        "latest": current,
        "behind": False,
        "updatable": info["updatable"],
        "notes": [],
        "error": None,
    }
    if not info["updatable"] or not _git_ok(src):
        out["error"] = "not_updatable"
        return out
    try:
        _git("fetch", "origin", cwd=src, timeout=60)
        latest = _git("rev-parse", "origin/main", cwd=src, timeout=30)[:7]
        if not latest:
            latest = _git("rev-parse", "HEAD", cwd=src, timeout=30)[:7]
        out["latest"] = latest
        if latest and current and latest != current:
            # commits on origin/main that we don't have
            ahead = _git("log", "--oneline", f"{current}..{latest}", cwd=src, timeout=30)
            out["notes"] = [l for l in ahead.splitlines() if l.strip()][:20]
            out["behind"] = True
        return out
    except Exception as e:  # pragma: no cover - network/git failure
        out["error"] = f"check_failed: {e}"
        return out


# --------------------------------------------------------------------------
# Apply update (background): git pull + rebuild + replace + relaunch
# --------------------------------------------------------------------------
def get_status() -> dict:
    return dict(_STATE)


def apply_update() -> dict:
    """Kick off a background update. Returns the initial status dict."""
    if _STATE["running"]:
        return get_status()
    t = threading.Thread(target=_run_update, daemon=True)
    t.start()
    return get_status()


def _set(stage, progress, message, **extra):
    _STATE["stage"] = stage
    _STATE["progress"] = progress
    _STATE["message"] = message
    _STATE.update(extra)


def _run_update():
    info = get_buildinfo()
    src = Path(info["source_dir"])
    venv_py = info["venv"]
    apps_app = src / "apps" / "VirusGPT.app"
    _STATE["running"] = True
    _STATE["started_at"] = time.time()
    _STATE["error"] = None
    try:
        # 1) Pull latest
        _set("fetching", 10, "Fetching latest source…")
        if not _git_ok(src):
            raise RuntimeError("not a git work tree")
        if _git("fetch", "origin", cwd=src, timeout=120) == "" and not _git_ok(src):
            pass  # fetch may print to stderr; check below
        # Ensure we track origin/main
        _git("remote", "set-branches", "origin", "main", cwd=src, timeout=30)
        _git("fetch", "origin", "main", cwd=src, timeout=120)
        _git("checkout", "main", cwd=src, timeout=30)
        _git("reset", "--hard", "origin/main", cwd=src, timeout=60)

        # 2) Rebuild via dev venv (has PyInstaller) -> replaces apps/VirusGPT.app
        _set("rebuilding", 40, "Rebuilding app bundle…")
        build = src / "desktop" / "build-macos.py"
        proc = subprocess.run(
            [venv_py, str(build)], cwd=str(src),
            capture_output=True, text=True, timeout=600,
        )
        if proc.returncode != 0:
            raise RuntimeError("build failed:\n" + (proc.stderr or proc.stdout)[-1500:])

        if not apps_app.exists():
            raise RuntimeError("build did not produce apps/VirusGPT.app")

        _set("replacing", 90, "Replacing app and restarting…")
        # Relaunch the freshly built app, then quit this (old) process.
        try:
            subprocess.Popen(["open", str(apps_app)])
        except Exception:
            pass
        _STATE["finished_at"] = time.time()
        _set("done", 100, "Updated. Restarting…")
        time.sleep(1.5)
        # Hard exit so the new app (just opened) becomes the live instance.
        os._exit(0)
    except Exception as e:
        _STATE["error"] = str(e)
        _STATE["stage"] = "error"
        _STATE["finished_at"] = time.time()
        _STATE["running"] = False
