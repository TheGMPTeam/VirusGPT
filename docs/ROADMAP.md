# VirusGPT — Roadmap (future work + swarm onboarding)

> Read after `ARCHITECTURE.md` and `STATUS.md`. The **desktop app is now the
> primary app** — the browser/web build is deprecated in favor of a native
> cross-platform desktop shell that wraps the existing stack. This doc tells
> sub-agents/swarms WHAT to build next and HOW to pick up a slice.

## 0. HEADLINE DECISION (2026-08-20)
- **Desktop app = the product.** The vanilla-JS UI in `app/` is no longer served
  to a browser; it is rendered inside a **native desktop window**.
- **Must run on all OS**: macOS, Windows, Linux.
- **Chosen shell: `pywebview`** — pure-Python, embeds the OS-native webview
  (macOS WKWebView / Windows WebView2 / Linux WebKit), launches the FastAPI
  server in-process, and loads `http://localhost:8500`. **Zero rewrite** of the
  existing JS/server. (Alternatives documented in §6: Tauri, Electron.)

## 1. Desktop app (PRIMARY — build now)
| Phase | Work | Files | Status |
|---|---|---|---|
| D0 | pywebview launcher: boot server, wait health, open window at :8500, tray + quit | `desktop/app.py`, `desktop/__init__.py` | ✅ scaffolded |
| D0b | CLI control: `vgctl desktop run|build|install-deps` + 3 build scripts | `vgctl.py: cmd_desktop`, `desktop/build-{macos,windows,linux}.py` | ✅ |
| D1 | Cross-OS packaging: macOS `.app`, Windows `.exe` (PyInstaller), Linux `.AppImage`/`.deb` | `desktop/build-*.py`, `install.sh --desktop` | 🟡 scripts ready, not yet run |
| D2 | Native-menubar/tray: start/stop stack, open logs, health, selfdev trigger | `desktop/app.py` | 🔴 |
| D3 | Auto-update + first-run setup wizard (pick modules, point at Windows Docker box) | `desktop/` | 🔴 |
| D4 | Replace raw `app/` script-tag UI with a bundled build later (optional; Vite) | `app/` | 🔴 |

**Onboarding for a desktop sub-agent:** read `ARCHITECTURE.md` §3 (server +
services), then own `desktop/app.py`. Keep the server as-is; the desktop layer
only *launches* it and *frames* it. Test: run `desktop/run.py`, confirm a native
window opens and `/api/health` is 200 inside it.

## 2. Creative / publishing pipeline (big next build)
Per `docs/INTEGRATIONS_PLAN.md` + Hermes-memory findings (Windows box already
has ComfyUI/Blender/FFmpeg/n8n):
- **Service clients** — one file each: `services/n8n.py`, `comfyui.py`,
  `blender.py`, `ffmpeg.py` (mirror `services/tts.py`); `services/marton.py`
  (Gmail/YouTube/Snapchat — the only integration not existing anywhere).
- **Harness media tools**: `render_image`, `render_video`, `edit_video`,
  `publish_youtube`, `publish_gmail`, `publish_snapchat`, `run_workflow`.
- **Orchestration**: `autonomous/pipeline.py` = research→build→test→check→upload,
  reusing the Dreamer. Endpoint `/api/pipeline/run`.
- **marton.ai** is the gap to build first (nothing exists). Key via
  `VG_MARTON_KEY` / config; stages `skipped` until key present.

## 3. Self-dev (Dreamer) hardening
- Auto-run research on a curiosity queue during idle (gateway cron — done).
- Graph trimming/linking already live; add **conflict detection** (two concepts
  saying opposite things → flag for fact-check).
- **Cross-agent memory**: let missions read/write memory concepts directly.

## 4. Resilience / ops
- DB backup/auto-heal — ✅ done; add **off-machine backup** (copy snapshots to a
  configured S3/NAS) for the "all OS" story.
- Gateway: add **Windows-box health** probes (the Docker services on 10.0.0.120).
- `vgctl`: add `memory`, `gateway`, `db`, `desktop` subcommands.
- **Security audit system** — ✅ done (`vgctl audit`): scans git secrets,
  `.gitignore` coverage, marton.api_key hardcoding, shell-tool sandboxing,
  no eval/exec of model output, CORS/debug flags, DB-backup presence,
  world-readable keys. Returns CRITICAL/WARN/INFO with pass/fail count.

## 5. Feature backlog (future)
- Multi-user / rooms across LAN (DB sessions done; add auth later).
- Voice-clone persona presets (PocketTTS gated weights noted in memory).
- Mobile companion (read-only) — later.
- Plugin/extension API for third-party tools in the harness.

## 6. Framework notes (desktop)
- **pywebview** (chosen): Python-only, tiny, native webview per OS, no Chromium
  download, no Node. Best fit for "all OS" + existing Python stack.
- **Tauri** (alternative): smallest binaries, but needs Rust + a JS build step
  (would require bundling `app/` with Vite). Consider if distribution size
  matters more than dev speed.
- **Electron** (alternative): heaviest (ships Chromium), but zero friction for
  vanilla JS; consider only if pywebview webview quirks block features.

## 7. How a swarm picks up work
1. Read `ARCHITECTURE.md` → `STATUS.md` (find a 🔴/🟡 item) → `ROADMAP.md` (this).
2. One concern = one file. Don't rewrite shared modules.
3. Verify against `:8500` (desktop: inside the window) before claiming done.
4. Keep `docs/` accurate: update STATUS.md when you flip an item to ✅.
5. Record non-obvious build-outs as **memory-graph nodes** (`data/memory/`) so the
   Dreamer + other agents stay aware. This is the project's shared "what exists".
6. CI must stay green before push.
