# Desktop App

Cross-platform native shell for VirusGPT. Wraps the existing web stack in a
native desktop window using `pywebview` — no rewrite of the JS/server needed.

## Why pywebview

- Pure-Python, tiny, embeds the OS-native webview (macOS WKWebView / Windows
  WebView2 / Linux WebKit).
- No Chromium download, no Node, no build step.
- The FastAPI server is launched in-process; the window loads
  `http://localhost:8500`.

## Run

```bash
pip install pywebview
python desktop/run.py
```

## Build

```bash
python desktop/build-macos.py    # → .app bundle
python desktop/build-windows.py  # → .exe (PyInstaller)
python desktop/build-linux.py    # → .AppImage / .deb
```

## Layout

```
desktop/
├── __init__.py
├── app.py            # tray + window + server lifecycle
├── run.py            # entry point: python desktop/run.py
├── build-macos.py    # macOS .app packaging
├── build-windows.py  # Windows .exe packaging
├── build-linux.py    # Linux .AppImage / .deb packaging
└── requirements.txt  # pywebview + deps
```

## How it works

1. `app.py` starts the FastAPI server as a child process.
2. Waits for `/api/health` to return 200.
3. Opens a native webview window pointed at `http://localhost:8500`.
4. Adds a system tray icon with start/stop/quit.
5. On quit, tears down the server process.

## Notes

- The desktop layer **only launches and frames** the existing server. All
  logic stays in `server.py` and `app/`.
- Build scripts use PyInstaller (Windows/Linux) or `py2app` (macOS).
- First-run setup wizard and auto-update are planned (see ROADMAP.md §1).
