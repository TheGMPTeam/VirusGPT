"""Build the VirusGPT desktop app for macOS (one-folder PyInstaller bundle).

Builds dist/VirusGPT.app, stamps it with the git version + commit (written to
app/version.json and injected into the frozen index.html as window.__VG_VERSION),
then INSTALLS it into /Applications/VirusGPT.app — replacing any prior build there
— so there is exactly ONE shipped app. Falls back to a repo-local apps/ copy if
/Applications isn't writable. The app launches the FastAPI server in-process and
opens a native WebView window (pywebview -> WKWebView). Cross-platform: the
Linux/Windows variants use the same spec with a different --name/icon.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_NAME = "VirusGPT"


def git_version():
    """Return (version, commit) from git. version = latest tag (or '1.0'),
    commit = short hash of HEAD. Falls back to ('dev', 'unknown') if git is
    unavailable.

    The commit field is ALWAYS the short commit hash (never the tag name) so the
    updater can compare running vs origin/<branch> by hash. The tag only drives
    the human-readable version label.
    """
    version = "1.0"
    commit = "unknown"
    try:
        # Commit hash first — this is what the updater compares.
        commit = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip() or "unknown"
        # Version label = most recent tag (if any), else 1.0.
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
    """Write app/version.json and app/buildinfo.json, return (version, commit)."""
    version, commit = git_version()
    payload = {"version": version, "commit": commit, "build_time": _now_iso()}
    (ROOT / "app" / "version.json").write_text(json.dumps(payload))
    # buildinfo tells the RUNNING app where to rebuild from (source tree + venv).
    buildinfo = {
        "source_dir": str(ROOT),
        "venv": str(ROOT / ".venv" / "bin" / "python"),
    }
    (ROOT / "app" / "buildinfo.json").write_text(json.dumps(buildinfo))
    print(f"[build] version: v{version} · {commit}")
    return version, commit


def _now_iso():
    try:
        return __import__("datetime").datetime.now().isoformat(timespec="seconds")
    except Exception:
        return ""


def _quit_running_app(app_name):
    """Quit any already-running instance of the app before we rebuild.

    Building over a live .app (or leaving a stale process around) causes the
    installer to fail or the old process to linger. We terminate the running
    process by name; on macOS this cleanly quits the GUI app.
    """
    import time as _time
    # 1) Kill the running process by bundle/executable name.
    for sig in ("TERM", "KILL"):
        try:
            rc = subprocess.run(
                ["pgrep", "-f", f"{app_name}.app/Contents/MacOS/{app_name}"],
                capture_output=True, text=True, timeout=10,
            )
            pids = [p for p in rc.stdout.split() if p.strip()]
            if not pids:
                break
            subprocess.run(["pkill", "-f", f"{app_name}.app/Contents/MacOS/{app_name}"], timeout=10)
            if sig == "TERM":
                _time.sleep(1.5)
        except Exception as e:
            print(f"[build] (warn) could not quit running {app_name}: {e}")
            break
    # 2) Also drop any stray python that holds the bundle's MacOS binary.
    try:
        subprocess.run(["pkill", "-f", f"MacOS/{app_name}"], timeout=10)
    except Exception:
        pass


def build():
    # Quit any running instance so we rebuild cleanly (no live bundle / stale proc).
    _quit_running_app(APP_NAME)
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
        "--add-data", f"config.json{os_sep()}.",
        "--add-data", f"server.py{os_sep()}.",
        "--add-data", f"desktop{os_sep()}desktop",
        "desktop/run.py",
    ]
    # PyInstaller one-folder build
    rc = subprocess.call(spec, cwd=str(ROOT))
    if rc != 0:
        return rc
    # Move the freshly built bundle into /Applications, replacing any previous
    # build, so there is exactly ONE shipped app. /Applications needs admin, so
    # fall back to a repo-local apps/ copy (and reveal it in Finder) if we cannot
    # write there.
    built = ROOT / "dist" / f"{APP_NAME}.app"
    if not built.exists():
        print(f"[build] ERROR: expected built bundle at {built}, none found")
        return 1
    placed = _install_to_applications(built, APP_NAME)
    DEST_APP = placed
    print(f"[build] installed bundle -> {DEST_APP}")
    # Inject the version global into the frozen index.html so the bottom bar shows
    # it even if version.json fetch is blocked (file:// / offline WebView).
    channel = "beta"
    try:
        channel = json.loads((ROOT / "app" / "update_branch.json").read_text()).get("branch", "beta") or "beta"
    except Exception:
        pass
    inject_version_global(DEST_APP / "Contents" / "Resources" / "app" / "index.html",
                          version, commit, channel)
    # Remove the leftover dist/ copy so there is never a second app on disk.
    left = ROOT / "dist" / f"{APP_NAME}.app"
    if left.exists():
        shutil.rmtree(left)
    return 0


def _install_to_applications(built, app_name):
    """Install built .app into /Applications/VirusGPT.app (replace in place).

    Uses `sudo mv` for the admin write, falling back to a repo-local apps/ copy
    if /Applications isn't writable. Returns the final installed path.
    """
    dest = Path(f"/Applications/{app_name}.app")
    repo_apps = ROOT / "apps"
    # 1) try a plain move (works if user already owns /Applications or SIP allows)
    try:
        if dest.exists():
            shutil.rmtree(dest)
        shutil.move(str(built), str(dest))
        return dest
    except PermissionError:
        pass
    # 2) try sudo mv
    try:
        if dest.exists():
            subprocess.run(["sudo", "rm", "-rf", str(dest)], check=True)
        subprocess.run(["sudo", "mv", str(built), str(dest)], check=True)
        return dest
    except Exception as e:
        print(f"[build] (warn) could not write to /Applications ({e}); "
              f"installing into repo apps/ instead.")
    # 3) fallback: repo-local apps/
    repo_apps.mkdir(parents=True, exist_ok=True)
    fallback = repo_apps / f"{app_name}.app"
    if fallback.exists():
        shutil.rmtree(fallback)
    shutil.move(str(built), str(fallback))
    return fallback


def _reveal_in_finder(path):
    """No-op: the user does not want a Finder reveal window."""
    return


def inject_version_global(index_html, version, commit, channel="beta"):
    if not index_html.exists():
        print(f"[build] (warn) frozen index.html not found at {index_html}")
        return
    html = index_html.read_text()
    marker = "<!--__VG_VERSION__-->"
    snippet = (f'<script>window.__VG_VERSION={{"version":"{version}",'
               f'"commit":"{commit}"}};window.__VG_CHANNEL="{channel}";</script>')
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
    print(f"[build] stamped frozen index.html with v{version} · {commit} ({channel})")


def os_sep():
    return ";" if sys.platform.startswith("win") else ":"


if __name__ == "__main__":
    raise SystemExit(build())
