"""VirusGPT Desktop — cross-platform native shell (macOS / Windows / Linux).

Wraps the existing VirusGPT web stack in a native window using `pywebview`
(OS-native webview: WKWebView on macOS, WebView2 on Windows, WebKit on Linux).

Two run modes:
  • SELF-CONTAINED (default): the FastAPI server (server.py) is launched
    IN-PROCESS so the frozen app is fully self-contained — no external
    `python` / `.venv` is required. The window loads http://localhost:8500.
  • REMOTE BACKEND: if VG_BACKEND_URL (or config.json -> backend_url) is set,
    the app does NOT start a server and instead loads the web UI from that
    URL. This is the "thin Windows client" path: the backend runs in Docker
    on another host and the .exe is just a WebView2 shell.

Run:  python desktop/run.py
Build: see desktop/build-macos.py / build-windows.py / build-linux.py

Env overrides:
  VG_PORT        port the in-process server + window use (default 8500)
  VG_NO_GUI      1/true/yes -> serve without opening a window (headless smoke test)
  VG_BACKEND_URL http(s)://host:port -> remote backend mode (no in-process server)
"""
from __future__ import annotations

import json
import os
import sys
import time
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("VG_PORT", "8500"))
NO_GUI = os.environ.get("VG_NO_GUI", "").lower() in ("1", "true", "yes")


def _free_port_8500():
    """Kill any process currently holding TCP 8500 so this app can bind it
    (e.g. a stale dev server.py started by launch.sh). Best-effort and safe:
    only targets the single port we need. If nothing holds it, this is a no-op.
    This makes the desktop app the authoritative instance on 8500 instead of
    silently loading another server's UI in its window."""
    try:
        import subprocess
        out = subprocess.run(["lsof", "-ti", "tcp:8500"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        for pid in out.split():
            try:
                subprocess.run(["kill", "-9", pid], timeout=10)
            except Exception:
                pass
    except Exception:
        pass

def _backend_url_from_config() -> str:
    """Read an optional remote backend URL from config.json (desktop.backend_url)."""
    cfg_path = ROOT / "config.json"
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text())
            url = (cfg.get("desktop", {}) or {}).get("backend_url") or cfg.get("backend_url") or ""
            return (url or "").strip()
        except Exception:
            return ""
    return ""


def backend_url() -> str:
    """Resolve the remote backend URL, if any.

    Precedence: VG_BACKEND_URL env -> config.json desktop.backend_url ->
    config.json backend_url -> empty (self-contained mode).
    """
    env = (os.environ.get("VG_BACKEND_URL") or "").strip()
    if env:
        return env.rstrip("/")
    return _backend_url_from_config().rstrip("/")


def _wait_health(url: str, timeout: float = 30.0) -> bool:
    try:
        import httpx
    except Exception:
        # fallback: crude socket check
        import socket
        host, _, port = url.replace("http://", "").replace("https://", "").partition(":")
        port = int(port)
        end = time.time() + timeout
        while time.time() < end:
            try:
                with socket.create_connection((host, port), timeout=1):
                    return True
            except OSError:
                time.sleep(0.5)
        return False
    end = time.time() + timeout
    while time.time() < end:
        try:
            if httpx.get(url + "/api/health", timeout=1.0).status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def _serve():
    """Run the FastAPI server in the current process.

    Importing `server` here makes PyInstaller collect server.py AND every module
    it pulls in (fastapi, uvicorn, services, autonomous, memory, gateway, ...),
    so the frozen bundle is fully self-contained — no external interpreter needed.
    """
    import uvicorn
    import server  # bundled module (also collects all its dependencies)
    uvicorn.run(server.app, host="127.0.0.1", port=PORT, log_level="warning")


def on_closed():
    # Best-effort: the in-process server rides a daemon thread, reaped on exit.
    pass


def main():
    import webview

    remote = backend_url()
    if remote:
        # THIN CLIENT MODE: backend runs elsewhere (e.g. Docker). No server here.
        url = remote
        print(f"[desktop] remote backend mode -> {url}", flush=True)
        if not _wait_health(url):
            print("[desktop] WARNING: remote backend did not respond; loading UI anyway.", flush=True)
        webview.create_window(
            "VirusGPT",
            url,
            width=1280,
            height=800,
            min_size=(900, 600),
            text_select=True,
            confirm_close=False,
        )
        try:
            webview.start()
        except Exception as exc:  # pragma: no cover - GUI failures shouldn't crash
            print(f"[desktop] webview start failed: {exc}", flush=True)
        sys.exit(0)

    # SELF-CONTAINED MODE: start the server in-process, then open localhost.
    # Use the fixed port (default 8500). If something else already holds it
    # (e.g. a stale dev server.py / launch.sh), take it over so THIS bundle's
    # UI is what the window loads.
    global PORT
    _free_port_8500()
    srv = threading.Thread(target=_serve, daemon=True)
    srv.start()
    url = f"http://localhost:{PORT}"
    print(f"[desktop] starting server (in-process) on {url}...", flush=True)
    if not _wait_health(url):
        print("[desktop] WARNING: server did not come up in time", flush=True)

    if NO_GUI:
        # Headless smoke-test mode: serve forever, no window (used by CI / build
        # verification). Kill the process to stop.
        print(f"[desktop] VG_NO_GUI set — serving at {url} (no window).", flush=True)
        while True:
            time.sleep(3600)
        return

    webview.create_window(
        "VirusGPT",
        url,
        width=1280,
        height=800,
        min_size=(900, 600),
        text_select=True,
        confirm_close=False,
    )
    try:
        webview.start()
    except Exception as exc:  # pragma: no cover - GUI failures shouldn't crash
        print(f"[desktop] webview start failed: {exc}", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    main()
