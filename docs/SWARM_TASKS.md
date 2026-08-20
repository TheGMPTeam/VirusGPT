# VirusGPT Swarm — Task Order (build the desktop app + optimize/update/repair/UI-check)

> Work order for the `virusgpt-swarm` skill. Each task is owned by ONE agent.
> Load `virusgpt-swarm` first. Follow its §0 iron rules + §9 swarm protocol.
> Repo root: `/Users/Master/virusgpt-mac`. Server `:8500`. Docs in `docs/`.

## T0 — Environment & reconcile (pre-flight, do first)
1. `cd /Users/Master/virusgpt-mac && .venv/bin/pip install -r desktop/requirements.txt`
   (pywebview + httpx). PyInstaller also needed for builds:
   `.venv/bin/pip install pyinstaller`.
2. `vgctl audit` MUST stay clean (0 failures) after every task.
3. `vgctl memory status` → graph healthy, 0 orphans.
4. Reconcile SKILL.md §3 against actual routes: `grep -oE '@app\.(get|post|delete)\("[^"]+"' server.py`.
   Diff shows 39 routes vs ~30 documented — UPDATE `docs/SKILL.md` §3 to list ALL
   real routes (including `/api/autonomous/resume/{id}`, `/api/memory/autolink`, etc.).

## T1 — OPTIMIZE (backend)
- Chat path: confirm small-context trim (`chat.max_history=24`, `max_history_tokens=2800`)
  holds under long sessions; no Ollama context overflow on qwen2.5:3b.
- Memory retrieval: `retrieve_context()` is O(N) over concepts — fine at ~20, but add
  a short-circuit if concept count > 200 (cap scan). Keep it dependency-free.
- DB: `backup_db()` runs on 24h cron — verify it fires + prunes to 10 (run once manually
  via `/api/db/backup`, confirm `data/db_backups/` has ≤10).
- Gateway: confirm `builtin_jobs()` cadences (launch_check 60s, memory_maintain 30m,
  db_backup 24h, selfdev 1h) and that `launch.sh` actually starts the gateway.
- Report: any latency wins, the prune behavior, cron proof.

## T2 — UPDATE (deps, config, skill, docs)
- Bump `desktop/requirements.txt` to pinned `pywebview>=5.0` (already) + add `pyinstaller`
  so a fresh `install-deps` yields a buildable app.
- Update `docs/SKILL.md` §3 with the FULL route list (fix drift from T0).
- Update `docs/STATUS.md` + `docs/ROADMAP.md` to reflect the latest verified state
  (small-context, memory-RAG, desktop, audit all ✅; creative-pipeline clients 🔴).
- Add any new concepts needed to the memory graph (e.g. a `component/Desktop App` node
  linking VirusGPT + pywebview + the build scripts).
- Keep `config.json` `chat` block as the single source of truth for context behavior.

## T3 — REPAIR (robustness)
- Scan for silent 500s: hit every endpoint in SKILL.md §3 with a benign payload, log
  non-200s that shouldn't fail. Fix root causes (don't just catch-all).
- `server.py` reads `index.html` once at startup — confirm a restart picks up HTML
  changes (it does; document the restart step in SKILL.md gotchas).
- DB auto-heal: re-verify corruption→restore (overwrite virusgpt.db with garbage,
  restart server, confirm `/api/db/status` healthy + backup restored). Keep the test
  backup pruned after.
- TTS: speaker button is the ONLY autoplay gate; manual ▶/⟲ ungated (playSentenceNow).
  Confirm playSentenceNow works when speaker OFF — if broken, repair tts.js.
- Report: endpoints tested, failures found/fixed, auto-heal proof.

## T4 — UI CHECK (every panel, no JS errors)
Using the DESKTOP app (or browser at :8500), verify each panel renders + functions:
- Chat: send a message → streams, system prompt + memory context injected (see T1).
- Kanban/Team (`#team-plan-panel`): create mission → tasks appear as cards → progress.
- Personas (`#personas`): add/edit/save → persists (DB).
- Memory Graph (`#memory`): force-directed render, click node → detail with
  relink/remove/dream/fact-check buttons work.
- Sessions (`#sessions-panel`): new session, switch, messages persist.
- Settings (`#settings-overlay`): toggles work.
- Console: **0 JS errors**. If errors, fix in the relevant `app/assets/js/*.js`.
- Bump `?v=N` on any edited asset tag; restart server after HTML/CSS/JS changes.
- Report: per-panel pass/fail + console errors fixed.

## T5 — BUILD THE DESKTOP APP (primary deliverable, all-OS)
- macOS (BUILD NOW, verify empirically):
  `cd /Users/Master/virusgpt-mac && .venv/bin/python desktop/build-macos.py`
  → expect `dist/VirusGPT.app`. Then smoke-launch: open the .app, confirm a native
  window opens and `GET /api/health` returns 200 inside it. If PyInstaller misses
  hidden imports, add them to `desktop/build-macos.py` and rebuild.
- Windows + Linux (BUILD SCRIPTS READY, document + (if a builder is available) run):
  `desktop/build-windows.py`, `desktop/build-linux.py` use the same PyInstaller spec.
  Document the one-folder output + the WebView2/WebKit runtime requirement.
- `vgctl desktop run|build|install-deps` must work end-to-end.
- Keep `desktop/app.py` zero-JS-rewrite: it only boots server.py + opens :8500.
- Report: build artifact path, launch proof (screenshot or health check), any
  hidden-import fixes.

## DONE criteria (all must hold before reporting complete)
- [ ] `vgctl audit` → 0 failures
- [ ] `vgctl memory status` → healthy, 0 orphans
- [ ] All T1–T5 tasks executed with evidence (curl outputs / screenshots / build path)
- [ ] `docs/SKILL.md` §3 matches real routes; STATUS.md/ROADMAP.md current
- [ ] Desktop `.app` built + launches (macOS verified); Win/Linux documented
- [ ] Changes committed + pushed to `origin main` (SSH, no PAT)
- [ ] Each agent updated its owned section of STATUS.md + added a memory node
