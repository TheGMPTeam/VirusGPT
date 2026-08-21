<div align="center">

<img src="../app/assets/images/logo_comfy.png" width="92" alt="VirusGPT emblem">

# ⚙️ Build the Desktop App

<p>
  <b>Turn VirusGPT into a real app</b> for macOS, Windows, or Linux.<br>
  One command. No App Store. No account. It just runs on your machine.
</p>

</div>

---

## 🎨 The look

The desktop shell wraps the same web UI you already use — styled in the app's own
**Cyber Matrix** neon: green `#00ff9c`, cyan `#23e0ff`, magenta `#ff2bd6` on deep black.
What you see in the browser is what you get in the window.

## 🧩 What it actually is

A thin native frame around the existing stack:

- **`pywebview`** — uses your OS's real browser engine (WKWebView on macOS, WebView2
  on Windows, WebKit on Linux). No Chromium download, no Node, no bundler.
- The **FastAPI server runs inside the app** (in-process), so there's nothing else to
  start. The window just opens `http://localhost:8500`.
- On quit, the window and the server shut down together. Clean.

> The desktop layer only *opens and frames* the app. All the brains stay in
> `server.py` and `app/`. Nothing gets rewritten.

## 🚀 Build it

You need the project venv active first:

```bash
cd virusgpt-mac
. .venv/bin/activate
```

Then pick your platform:

| Platform | Command | Output |
| :--- | :--- | :--- |
| 🍎 macOS | `python desktop/build-macos.py` | `VirusGPT.app` → installed to **/Applications** |
| 🪟 Windows | `python desktop/build-windows.py` | `VirusGPT.exe` (PyInstaller) |
| 🐧 Linux | `python desktop/build-linux.py` | `.AppImage` / `.deb` |

On macOS the build stamps the bundle with the current git version + commit and drops it
into `/Applications/VirusGPT.app` (or a repo-local `apps/` copy if `/Applications` isn't
writable). The in-app updater reads `app/buildinfo.json` from there to rebuild later.

## 🛠️ Just run it (no build)

Handy while developing:

```bash
. .venv/bin/activate
python desktop/run.py
```

That opens the app straight from source.

## 🔌 Modes

- **Self-contained** (default) — server bundled and started in-process. Double-click and go.
- **Remote backend** — set `desktop.backend_url` in `config.json` (or `VG_BACKEND_URL`)
  and the app becomes a thin client loading a backend running elsewhere (e.g. Docker).

## 📂 Inside `desktop/`

```
desktop/
├── run.py            # run from source:  python desktop/run.py
├── app.py            # window + in-process server lifecycle
├── build-macos.py    # macOS .app packaging + /Applications install
├── build-windows.py  # Windows .exe packaging
├── build-linux.py    # Linux packaging
└── requirements.txt  # pywebview + deps
```

## 💡 Real-talk notes

- macOS is the primary, fully-verified target. Windows/Linux scripts ship the same shell.
- First run may ask for mic/camera permission (that's just the browser engine — needed
  for voice input). Say yes if you want to talk to it.
- Images in the app are rendered by your local **ComfyUI** — the desktop app just shows them.

---

<div align="center">
  <sub>Built for people who want their AI local. No cloud, no login, no nonsense.</sub>
</div>
