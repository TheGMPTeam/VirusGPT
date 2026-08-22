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

    Two listeners are started:
      • HTTPS on 0.0.0.0:PORT  — for the LAN / Android phone. Mobile Chrome only
        grants the mic (Whisper STT) in a secure context, so the phone needs HTTPS.
      • HTTP  on 127.0.0.1:PORT+1 — for the desktop WebView window. pywebview's
        WKWebView refuses self-signed certificates (no "accept" dialog), so the
        native window loads plain HTTP on localhost, which is fine: the desktop
        app uses its own native mic, not the browser, so no secure context needed.
    """
    import uvicorn
    import server  # bundled module (also collects all its dependencies)
    from services import config as _cfg
    import threading

    http_port = PORT + 1

    # HTTPS listener for the LAN / phone (skipped if cert unavailable).
    ssl_kwargs = {}
    if _cfg.CONFIG.get("https"):
        cert = Path(_cfg.CONFIG.get("ssl_certfile", ""))
        key = Path(_cfg.CONFIG.get("ssl_keyfile", ""))
        if not (cert.exists() and key.exists()):
            try:
                import socket, subprocess
                ssl_dir = cert.parent
                ssl_dir.mkdir(parents=True, exist_ok=True)
                lan_ip = ""
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    s.connect(("8.8.8.8", 80))
                    lan_ip = s.getsockname()[0]
                    s.close()
                except Exception:
                    pass
                if not lan_ip or lan_ip.startswith("127."):
                    lan_ip = socket.gethostbyname(socket.gethostname())
                san = f"IP:{lan_ip},DNS:localhost"
                cnf = ssl_dir / "openssl_vg.cnf"
                cnf.write_text("distinguished_name = dn\n[dn]\n[san]\nsubjectAltName = " + san + "\n")
                subprocess.run([
                    "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                    "-keyout", str(key), "-out", str(cert), "-days", "825",
                    "-subj", "/CN=VirusGPT",
                    "-reqexts", "san", "-extensions", "san", "-config", str(cnf),
                ], check=True, capture_output=True)
                cnf.unlink(missing_ok=True)
                print(f"[desktop] generated self-signed cert (SAN {san})", flush=True)
            except Exception as exc:
                print(f"[desktop] WARNING: HTTPS cert gen failed ({exc})", flush=True)
        if cert.exists() and key.exists():
            ssl_kwargs = {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}
            print(f"[desktop] serving HTTPS on 0.0.0.0:{PORT} (phone/LAN)", flush=True)
        else:
            print(f"[desktop] WARNING: HTTPS configured but cert unavailable; LAN will be HTTP", flush=True)

    # HTTP listener for the desktop window (always on, localhost only).
    def _run_http():
        uvicorn.run(server.app, host="127.0.0.1", port=http_port, log_level="warning")
    threading.Thread(target=_run_http, daemon=True).start()
    print(f"[desktop] serving HTTP on 127.0.0.1:{http_port} (desktop window)", flush=True)

    if ssl_kwargs:
        uvicorn.run(server.app, host="0.0.0.0", port=PORT, log_level="warning", **ssl_kwargs)
    else:
        uvicorn.run(server.app, host="0.0.0.0", port=PORT, log_level="warning")


# The desktop WebView window loads the localhost HTTP listener (WKWebView
# cannot accept a self-signed cert). The phone uses the HTTPS LAN listener.
def _window_url() -> str:
    return f"http://127.0.0.1:{PORT + 1}"


def on_closed():
    # Best-effort: the in-process server rides a daemon thread, reaped on exit.
    pass


def main():
    import webview
    from services import updater as _upd

    # Single quit path used by BOTH the in-app updater AND the window Close (✕)
    # button: request_quit() -> this hook. destroy() asks Cocoa to close the
    # window on the main run loop; os._exit(0) then GUARANTEES the process ends
    # (webview.start() does not reliably return after window.close on macOS, which
    # previously left the app frozen). The detached rebuild (update) or the user
    # (Close) both get the same clean, reliable teardown.
    def _quit_app():
        try:
            if webview.windows:
                webview.windows[0].destroy()
        except Exception:
            pass
        os._exit(0)

    _upd.register_quit_hook(_quit_app)

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
            background_color="#05070a",
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
    _free_port_8500()
    srv = threading.Thread(target=_serve, daemon=True)
    srv.start()
    # The desktop WebView window loads the LOCALHOST HTTP listener
    # (127.0.0.1:8501) — see _window_url(). WKWebView (pywebview) refuses
    # self-signed certs with no "accept" dialog, so we never point the native
    # window at the HTTPS URL. The phone uses the HTTPS LAN listener instead.
    url = _window_url()
    print(f"[desktop] starting server (in-process); window -> {url}...", flush=True)
    if not _wait_health(url):
        print("[desktop] WARNING: server did not come up in time", flush=True)

    if NO_GUI:
        # Headless smoke-test mode: serve forever, no window (used by CI / build
        # verification). Kill the process to stop.
        print(f"[desktop] VG_NO_GUI set — serving at {url} (no window).", flush=True)
        while True:
            time.sleep(3600)

    # JS bridge so the custom (frameless) window controls in the header can
    # minimize / maximize / close the native window.
    class WindowApi:
        def minimize(self):
            try: webview.windows[0].minimize()
            except Exception: pass
        def toggle_maximize(self):
            try: webview.windows[0].toggle_fullscreen()
            except Exception: pass
        def close(self):
            # Same clean exit path as the in-app updater: request_quit() tears
            # down the native window on the GUI thread (no hard os._exit), so
            # the Close (✕) button and the update flow exit identically and
            # gracefully. The quit hook is registered above in main().
            try:
                _upd.request_quit()
            except Exception:
                # Last-resort fallback if the hook isn't registered.
                try:
                    if webview.windows:
                        webview.windows[0].destroy()
                except Exception:
                    pass
                os._exit(0)

    webview.create_window(
        "VirusGPT",
        url,
        js_api=WindowApi(),
        width=1280,
        height=800,
        min_size=(900, 600),
        frameless=True,
        easy_drag=True,
        text_select=True,
        confirm_close=False,
        background_color="#05070a",
    )
    try:
        webview.start()
    except Exception as exc:  # pragma: no cover - GUI failures shouldn't crash
        print(f"[desktop] webview start failed: {exc}", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    main()
