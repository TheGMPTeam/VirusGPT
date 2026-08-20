"""VirusGPT Desktop — cross-platform native shell (macOS / Windows / Linux).

Wraps the existing VirusGPT web stack in a native window using `pywebview`
(OS-native webview: WKWebView on macOS, WebView2 on Windows, WebKit on Linux).
The FastAPI server (server.py) is launched IN-PROCESS so the frozen app is fully
self-contained — no external `python` / `.venv` is required. The window loads
http://localhost:8500. NO rewrite of the existing JS/server is needed.

Run:  python desktop/run.py
Build: see desktop/build-macos.py / build-windows.py / build-linux.py

Env overrides:
  VG_PORT      port the server + window use (default 8500)
  VG_NO_GUI    1/true/yes -> serve without opening a window (headless smoke test)
"""
from __future__ import annotations

import os
import sys
import time
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("VG_PORT", "8500"))
NO_GUI = os.environ.get("VG_NO_GUI", "").lower() in ("1", "true", "yes")


def _wait_health(url: str, timeout: float = 30.0) -> bool:
    try:
        import httpx
    except Exception:
        # fallback: crude socket check
        import socket
        host, _, port = url.replace("http://", "").partition(":")
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

    srv = threading.Thread(target=_serve, daemon=True)
    srv.start()
    url = f"http://localhost:{PORT}"
    print("[desktop] starting server (in-process)...", flush=True)
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
