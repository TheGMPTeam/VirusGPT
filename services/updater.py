"""In-app update engine for the VirusGPT desktop app.

The frozen desktop bundle is self-contained (no .venv / PyInstaller at runtime),
so "update" means: pull the latest source from git and REBUILD the bundle with
the dev venv (which has PyInstaller), then replace the running apps/VirusGPT.app
and relaunch.

Flow (driven by the frontend update popup):
  GET  /api/version        -> currently running version/commit + updatability
  GET  /api/update/check   -> compare current commit vs origin/<branch>; return notes
  POST /api/update/apply    -> start a background update (git pull + rebuild)
  GET  /api/update/status   -> poll progress/state of the in-flight update

The tracked branch defaults to `beta` (so production `main` is never auto-pulled
into a running app); set VG_UPDATE_BRANCH to override (e.g. `main` for a prod
hotfix, or a feature branch). The build + relaunch runs in a DETACHED process
so the app can fully quit before its own bundle is replaced.

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
from shlex import quote as shquote

# ROOT is the repo root when running from source, or the frozen Resources dir
# (apps/VirusGPT.app/Contents/Resources) when running from the built app.
ROOT = Path(__file__).resolve().parent.parent
APP_NAME = "VirusGPT"
BUILDINFO = ROOT / "app" / "buildinfo.json"
VERSION_FILE = ROOT / "app" / "version.json"

# Branch the updater tracks. Persisted in app/update_branch.json (default beta)
# so the user can switch between beta and main from the popup; VG_UPDATE_BRANCH
# (env) overrides everything. Production `main` is never auto-pulled unless chosen.
BRANCH_FILE = ROOT / "app" / "update_branch.json"
def _read_branch_file() -> str:
    try:
        return (json.loads(BRANCH_FILE.read_text()).get("branch") or "").strip()
    except Exception:
        return ""
def update_branch() -> str:
    return (os.environ.get("VG_UPDATE_BRANCH")
            or _read_branch_file()
            or "beta").strip() or "beta"
def set_branch(branch: str) -> str:
    """Persist the tracked branch (beta/main/...). Returns the stored value."""
    branch = (branch or "").strip() or "beta"
    try:
        BRANCH_FILE.parent.mkdir(parents=True, exist_ok=True)
        BRANCH_FILE.write_text(json.dumps({"branch": branch}))
    except Exception:
        pass
    return branch
def get_branches() -> dict:
    """List the channels the updater can target. `main` and `beta` are the two
    fixed channels and are ALWAYS offered (so the user can always switch), plus
    whatever branch is currently tracked (in case it differs)."""
    tracked = update_branch()
    available = ["beta", "main"]
    if tracked not in available:
        available.append(tracked)
    return {"available": available, "tracked": tracked}


# Feature flags keyed on the tracked channel. `main` is the STABLE channel:
# incomplete / experimental features are disabled there and only enabled on
# `beta`. The in-app UPDATER is NOT gated — updating works from any channel
# (you can update from main, or switch to beta and update). Add experimental
# features here as they are built.
def features() -> dict:
    return {
        "channel": update_branch(),
        "is_beta": update_branch() == "beta",
        "in_app_updater": True,   # updating always allowed
    }

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
        br = update_branch()
        latest = _git("rev-parse", f"origin/{br}", cwd=src, timeout=30)[:7]
        if not latest:
            latest = _git("rev-parse", "HEAD", cwd=src, timeout=30)[:7]
        out["latest"] = latest
        if latest and current and latest != current:
            # commits on origin/<branch> that we don't have
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
    br = update_branch()
    apps_app = Path(f"/Applications/{APP_NAME}.app")
    if not apps_app.exists():
        apps_app = src / "apps" / f"{APP_NAME}.app"
    _STATE["running"] = True
    _STATE["started_at"] = time.time()
    _STATE["error"] = None
    try:
        # 1) Fast git steps IN-PROCESS (low risk, quick): fetch + point tree at the
        #    tracked branch. The build/relaunch below runs DETACHED so the app can
        #    fully quit before its own bundle is replaced (no self-overwrite race).
        _set("fetching", 10, "Fetching latest source…")
        if not _git_ok(src):
            raise RuntimeError("not a git work tree")
        _git("remote", "set-branches", "origin", br, cwd=src, timeout=30)
        _git("fetch", "origin", br, cwd=src, timeout=120)
        _git("checkout", br, cwd=src, timeout=30)
        _git("reset", "--hard", f"origin/{br}", cwd=src, timeout=60)

        # 2) Launch the build + relaunch DETACHED (own session, independent of this
        #    process) so it can replace /Applications/VirusGPT.app after we exit.
        _set("rebuilding", 50, "Rebuilding app (detached)…")
        log_path = src / "update_runner.log"
        detach = (
            f'cd {shquote(str(src))} && '
            f'{shquote(venv_py)} desktop/build-macos.py && '
            f'open {shquote(str(apps_app))}'
        )
        # start_new_session -> detached from the app; we do NOT wait on it.
        subprocess.Popen(
            ["/bin/sh", "-c", detach],
            stdout=open(log_path, "w"), stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        _STATE["finished_at"] = time.time()
        _set("done", 100, "Update pulled. Rebuilding & restarting…")
        time.sleep(1.0)
        # Quit THIS (old) app so the detached rebuild can replace the bundle safely.
        os._exit(0)
    except Exception as e:
        _STATE["error"] = str(e)
        _STATE["stage"] = "error"
        _STATE["finished_at"] = time.time()
        _STATE["running"] = False
