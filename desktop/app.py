"""VirusGPT Desktop — cross-platform native shell (macOS / Windows / Linux).

Wraps the existing VirusGPT web stack in a native window using `pywebview`
(OS-native webview: WKWebView on macOS, WebView2 on Windows, WebKit on Linux).
The FastAPI server (server.py) is launched in-process; the window loads
http://localhost:8500. NO rewrite of the existing JS/server is needed.

Run:  python desktop/run.py
Build: see desktop/build-macos.py / build-windows.py / build-linux.py
"""
from __future__ import annotations

import asyncio
import functools
import os
import subprocess
import sys
import time
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("VG_PORT", "8500"))


def _server_proc():
    """Start the FastAPI server as a child process; return the Popen."""
    venv_py = ROOT / ".venv" / "bin" / "python"
    py = venv_py if venv_py.exists() else "python3"
    return subprocess.Popen(
        [str(py), "server.py"],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


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


def on_closed():
    # Best-effort: the server child is reaped by the OS on app exit.
    pass


def main():
    import webview

    proc = _server_proc()
    url = f"http://localhost:{PORT}"
    print(f"[desktop] starting server (pid {proc.pid})...")
    if not _wait_health(url):
        print("[desktop] WARNING: server did not come up in time")

    webview.create_window(
        "VirusGPT",
        url,
        width=1280,
        height=800,
        min_size=(900, 600),
        text_select=True,
        confirm_close=False,
    )
    # On close, terminate the server child.
    try:
        proc.terminate()
    except Exception:
        pass


if __name__ == "__main__":
    main()
