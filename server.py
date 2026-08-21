#!/usr/bin/env python3
"""
Serve the client and proxy to Ollama (LLM), PocketTTS (TTS + clone),
Whisper (STT), and VirusGPT's own local concept-memory store. Reads CONFIG at
startup and renders client config inline via CFGJS injected into the HTML head.
"""
from __future__ import annotations

import json
import asyncio
import os
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from services import config as cfg
from services import llm, tts, stt, memory
from services import close_client

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
PERSONAS_FILE = DATA / "personas.json"

app = FastAPI(title="VirusGPT (macOS)")

# --------------------------------------------------------------------------
# Client host (inject runtime config)
# --------------------------------------------------------------------------
INDEX = (ROOT / "app" / "index.html").read_text(encoding="utf-8")
CFGJS = (
    "<script>window.__VG_CONFIG="
    + json.dumps({
        "backend": cfg.CONFIG.get("backend_url", ""),
        "title": cfg.CONFIG.get("title", "VirusGPT"),
        "default_theme": cfg.CONFIG.get("default_theme", "cyber"),
    })
    + ";</script>"
)
# Serve the split frontend assets (css/ + js/ modules) BEFORE the catch-all index.
app.mount("/assets", StaticFiles(directory=ROOT / "app" / "assets", html=False), name="assets")
if "<head>" in INDEX:
    INDEX = INDEX.replace("<head>", "<head>" + CFGJS, 1)
else:
    INDEX = CFGJS + INDEX


@app.get("/")
async def index():
    # no-store so the browser always fetches the latest client (no stale cache)
    return HTMLResponse(INDEX, headers={"Cache-Control": "no-store"})


# --------------------------------------------------------------------------
# Health — aggregate all backends
# --------------------------------------------------------------------------
@app.get("/api/health")
async def health():
    oll = await llm.ollama_healthy(cfg.CONFIG["ollama"]["base_url"])
    tt = await tts.tts_health(cfg.CONFIG["tts"]["base_url"]) if cfg.CONFIG["tts"]["enabled"] else False
    st = await stt.stt_health(cfg.CONFIG["stt"]["base_url"]) if cfg.CONFIG["stt"]["enabled"] else False
    # Modular LAN services (graceful: report reachability, never fail the health check)
    from services import comfyui as _comfy
    cf = await _comfy.comfyui_health() if cfg.CONFIG["services"]["comfyui"]["enabled"] else False
    models = await llm.list_models(cfg.CONFIG["ollama"]["base_url"]) if oll else []
    voices = await tts.list_voices(cfg.CONFIG["tts"]["base_url"]) if tt else []
    return JSONResponse({
        "ollama": oll,
        "tts": tt,
        "whisper": st,
        "comfyui": cf,
        "models": models or [cfg.CONFIG["ollama"]["default_model"]],
        "default_model": cfg.CONFIG["ollama"]["default_model"],
        "voices": voices or ["alba", "azelma", "cosette", "eponine", "fantine", "javert", "jean", "marius"],
        "default_voice": cfg.CONFIG["tts"].get("default_voice", "alba"),
    })


# -------------------------------------------------------------------------
# Chat — proxy to Ollama, stream SSE {content|done|error}
# -------------------------------------------------------------------------
def trim_history(messages, max_hist: int = 24, max_chars: int = 2800):
    """Small-context window: keep the last `max_hist` messages, then drop the
    oldest until total content chars <= `max_chars`. Returns the trimmed list.

    Guarantees the model never receives more than `max_chars` of history, which
    keeps qwen2.5:3b (8192-token ctx) far from an overflow on long sessions.
    Extracted from chat() so the bound is unit-testable and reusable.
    """
    if not messages:
        return []
    window = messages[-max_hist:] if len(messages) > max_hist else messages
    kept = list(window)
    total = sum(len(m.get("content") or "") for m in kept)
    while kept and total > max_chars:
        dropped = kept.pop(0)
        total -= len(dropped.get("content") or "")
    return kept


@app.post("/api/chat")
async def chat(req: Request):
    body = await req.json()
    model = (body.get("model") or cfg.CONFIG["ollama"]["default_model"]).strip()
    # Reject path/control chars (defense against malformed model strings).
    # Allow legitimate Ollama names like "qwen2.5:3b" or "digitsflow/bonsai-8b:latest"
    # (dots and forward-slashes are valid in model names) — only block backslash,
    # control chars, and ".." traversal.
    _bad = ("\\" in model) or ("\x00" in model) or ("\x1f" in model) or ".." in model
    if not model or _bad or len(model) > 64:
        return JSONResponse({"error": "invalid model"}, status_code=400)

    incoming = body.get("messages", [])
    chat_cfg = cfg.CONFIG.get("chat", {})

    # Preserve a frontend-supplied system message as PERSONA context (don't drop it).
    frontend_system = ""
    non_system = []
    for m in incoming:
        if m.get("role") == "system" and not frontend_system:
            frontend_system = (m.get("content") or "").strip()
        else:
            non_system.append(m)

    # --- Small-context history window (trim to last N messages / char cap) ---
    max_hist = int(chat_cfg.get("max_history", 24))
    max_chars = int(chat_cfg.get("max_history_tokens", 2800))
    kept = trim_history(non_system, max_hist, max_chars)

    # --- System prompt (default VirusGPT context) + memory graph injection ---
    system = (chat_cfg.get("system_prompt")
              or "You are VirusGPT, a helpful offline AI assistant.")
    # The most recent user turn drives memory retrieval (RAG, default-on).
    last_user = next((m.get("content", "") for m in reversed(kept)
                      if m.get("role") == "user"), "")
    mem_ctx = ""
    if chat_cfg.get("memory_enabled", True):
        try:
            import asyncio
            from services import memory as _mem
            mem_ctx = await asyncio.wait_for(
                asyncio.to_thread(_mem.retrieve_context, last_user,
                                  int(chat_cfg.get("memory_k", 4))),
                timeout=5.0,
            )
        except Exception:
            mem_ctx = ""
    if mem_ctx:
        system = system + "\n\n" + mem_ctx
    # Append any persona-specific instructions from the frontend.
    if frontend_system:
        system = system + "\n\n" + frontend_system

    messages = [{"role": "system", "content": system}] + kept

    base = cfg.CONFIG["ollama"]["base_url"]

    async def gen():
        async for chunk in llm.stream_chat(model, messages, base, timeout=float(cfg.CONFIG.get("chat_timeout", 60))):
            yield f"data: {json.dumps(chunk)}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


# --------------------------------------------------------------------------
# AI text improvement — rewrite the user's typed draft into a polished version
# --------------------------------------------------------------------------
@app.post("/api/improve")
async def improve(req: Request):
    body = await req.json()
    text = (body.get("text") or "").strip()
    if not text:
        return JSONResponse({"error": "nothing to improve"}, status_code=400)
    if len(text) > 4000:
        return JSONResponse({"error": "text too long (max 4000 chars)"}, status_code=413)
    model = (body.get("model") or cfg.CONFIG["ollama"]["default_model"]).strip()
    if not model or len(model) > 64:
        return JSONResponse({"error": "invalid model"}, status_code=400)
    sys = ("You are a crisp writing assistant for VirusGPT, a local AI cyber-tool. "
           "Rewrite the user's draft so it is clearer, more fluent and more effective, "
           "while keeping the original meaning, language, tone and intent. "
           "Do NOT add new facts, headings, disclaimers or commentary. "
           "Output ONLY the improved text, with no quotation marks or preamble.")
    messages = [{"role": "system", "content": sys}, {"role": "user", "content": text}]
    out = []
    async for chunk in llm.stream_chat(model, messages, cfg.CONFIG["ollama"]["base_url"],
                                       timeout=float(cfg.CONFIG.get("chat_timeout", 60))):
        if chunk.get("content"):
            out.append(chunk["content"])
        elif chunk.get("error"):
            return JSONResponse({"error": chunk["error"]}, status_code=502)
    return JSONResponse({"improved": "".join(out).strip()})


# --------------------------------------------------------------------------
# AI autocomplete suggestions for a partial message
# --------------------------------------------------------------------------
@app.post("/api/suggest")
async def suggest(req: Request):
    body = await req.json()
    text = (body.get("text") or "").strip()
    if not text or len(text) > 500:
        return JSONResponse({"suggestions": []})
    model = (body.get("model") or cfg.CONFIG["ollama"]["default_model"]).strip()
    if not model or len(model) > 64:
        return JSONResponse({"suggestions": []})
    sys = ("You are an autocomplete engine for a chat with VirusGPT, a local AI cyber-tool. "
           "Given the user's partial message, return up to 3 short, natural continuations "
           "that complete their thought. Each must be a SINGLE concise phrase (<= 8 words), "
           "no numbering, no quotation marks, no explanations. "
           "Return ONLY valid JSON: {\"suggestions\": [\"...\",\"...\",\"...\"]}.")
    messages = [{"role": "system", "content": sys}, {"role": "user", "content": text}]
    out = []
    async for chunk in llm.stream_chat(model, messages, cfg.CONFIG["ollama"]["base_url"],
                                       timeout=float(cfg.CONFIG.get("chat_timeout", 60))):
        if chunk.get("content"):
            out.append(chunk["content"])
        elif chunk.get("error"):
            return JSONResponse({"suggestions": []})
    raw = "".join(out).strip()
    sug = []
    try:
        # Strip ```json fences / surrounding prose, then locate the JSON array.
        cleaned = raw
        if "```" in cleaned:
            cleaned = cleaned.split("```")[1] if "```" in cleaned else cleaned
            cleaned = cleaned.replace("json", "", 1).strip()
        arr = None
        try:
            arr = json.loads(cleaned)
        except Exception:
            s = cleaned.find("["); e = cleaned.rfind("]")
            if s != -1 and e != -1:
                try: arr = json.loads(cleaned[s:e + 1])
                except Exception: arr = None
        if isinstance(arr, dict):
            arr = arr.get("suggestions", [])
        if isinstance(arr, list):
            sug = [str(s).strip().strip('"\'') for s in arr if str(s).strip()][:3]
        # Fallback: model returned a plain-text continuation instead of JSON.
        if not sug and raw:
            cand = [w.strip().strip('"\'') for w in raw.replace("\n", ",").split(",") if w.strip()]
            sug = cand[:3]
    except Exception:
        sug = []
    return JSONResponse({"suggestions": sug})


# --------------------------------------------------------------------------
# TTS — proxy to PocketTTS
# --------------------------------------------------------------------------
@app.get("/api/tts")
async def tts_proxy(text: str = "", voice: str = "", format: str = "mp3"):
    if not text:
        return JSONResponse({"error": "missing text"}, status_code=400)
    v = voice or cfg.CONFIG["tts"].get("default_voice", "nova")
    # Resolve a locally-cloned voice handle to its stored WAV path.
    # SECURITY: only allow files inside DATA/voices — guard against traversal.
    if v.startswith("local:"):
        name = os.path.basename(v[len("local:"):])
        vp = (DATA / "voices" / name).resolve()
        allowed = (DATA / "voices").resolve()
        if not str(vp).startswith(str(allowed) + os.sep) or not vp.is_file():
            return JSONResponse({"error": f"cloned voice not found: {v}"}, status_code=404)
        v = str(vp)
    audio = await tts.synthesize(
        text, v, cfg.CONFIG["tts"]["base_url"], response_format=format,
        timeout=float(cfg.CONFIG["tts"].get("timeout", 120)),
    )
    if audio is None:
        return JSONResponse({"error": "TTS unavailable"}, status_code=502)
    mt = "audio/mpeg" if format == "mp3" else "audio/wav"
    return StreamingResponse(iter([audio]), media_type=mt)


@app.get("/api/tts/voices")
async def tts_voices():
    if not cfg.CONFIG["tts"]["enabled"]:
        return JSONResponse({"voices": []})
    voices = await tts.list_voices(cfg.CONFIG["tts"]["base_url"])
    # merge any locally-cloned voices stored on this Mac
    clones = []
    vdir = DATA / "voices"
    if vdir.exists():
        for f in vdir.glob("*.wav"):
            clones.append({"id": f"local:{f.name}", "name": f.stem, "type": "cloned"})
    return JSONResponse({"voices": voices + clones})


@app.post("/api/tts/clone")
async def tts_clone(req: Request):
    """Accept a reference audio upload, store locally, return a voice handle
    PocketTTS can clone from (a local file path)."""
    form = await req.form()
    audio = form.get("audio")
    name = os.path.basename((form.get("name") or "voice").strip()).replace(" ", "_") or "voice"
    if not audio or not getattr(audio, "filename", None):
        return JSONResponse({"error": "no audio"}, status_code=400)
    data = await audio.read()
    vdir = DATA / "voices"
    vdir.mkdir(parents=True, exist_ok=True)
    # keep extension safe (.wav) and name contained within vdir
    name = name[:60]
    path = vdir / f"{name}.wav"
    path.write_bytes(data)
    return JSONResponse({"ok": True, "voice": f"local:{path.name}", "path": str(path)})


@app.post("/api/tts/preview")
async def tts_preview(req: Request):
    """One-shot: upload a reference WAV, return synthesized sample audio using
    it as the clone voice (does NOT persist)."""
    form = await req.form()
    audio = form.get("audio")
    text = form.get("text") or "This is a preview of the cloned voice."
    if not audio or not getattr(audio, "filename", None):
        return JSONResponse({"error": "no audio"}, status_code=400)
    data = await audio.read()
    import tempfile, os
    fd, tmp = tempfile.mkstemp(suffix=".wav", prefix="vg_prev_")
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    try:
        audio_out = await tts.synthesize(text, tmp, cfg.CONFIG["tts"]["base_url"],
                                         response_format="mp3",
                                         timeout=float(cfg.CONFIG["tts"].get("timeout", 120)))
    finally:
        try: os.remove(tmp)
        except OSError: pass
    if audio_out is None:
        return JSONResponse({"error": "TTS unavailable"}, status_code=502)
    return StreamingResponse(iter([audio_out]), media_type="audio/mpeg")


# --------------------------------------------------------------------------
# STT — proxy to Whisper
# --------------------------------------------------------------------------
@app.post("/api/stt")
async def stt_proxy(req: Request):
    form = await req.form()
    audio = form.get("audio")
    # form values can be UploadFile or str; only UploadFile has .read()
    if audio is None or not hasattr(audio, "read"):
        return JSONResponse({"error": "no audio"}, status_code=400)
    max_mb = float(cfg.CONFIG.get("max_upload_mb", 25))
    data = await audio.read()
    if len(data) == 0:
        return JSONResponse({"error": "empty audio"}, status_code=400)
    if len(data) > max_mb * 1024 * 1024:
        return JSONResponse({"error": f"audio too large (max {max_mb:.0f}MB)"}, status_code=413)
    mt = getattr(audio, "content_type", "audio/webm") or "audio/webm"
    res = await stt.transcribe(data, mt, cfg.CONFIG["stt"]["base_url"],
                               timeout=float(cfg.CONFIG["stt"].get("timeout", 30)))
    if res is None:
        return JSONResponse({"error": "STT unavailable"}, status_code=502)
    return JSONResponse(res)


# --------------------------------------------------------------------------
# Memory graph — live OKF stats from the shared MCP
# --------------------------------------------------------------------------
@app.get("/api/memory/graph")
async def memory_graph():
    status = await memory.memory_status()
    if status is None:
        return JSONResponse({"ok": False, "error": "memory store unavailable"}, status_code=502)
    # Normalize the OKF status blob into graph-friendly shape.
    return JSONResponse({
        "ok": True,
        "concepts": status.get("concepts"),
        "directories": status.get("directories"),
        "types": status.get("types", []),
        "graph": status.get("graph", {}),
        "conformant": status.get("conformant"),
        "warnings": status.get("warnings"),
        "errors": status.get("errors", []),
    })


@app.post("/api/memory/query")
async def memory_query(req: Request):
    body = await req.json()
    q = body.get("question", "")
    res = await memory.memory_query(q)
    return JSONResponse({"results": res})


# --- Memory graph management (CRUD + linking/dreaming) ---------------------
@app.get("/api/memory/{name}")
async def memory_get_concept(name: str):
    c = memory.memory_get(name)
    if not c:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(c)


@app.post("/api/memory/update")
async def memory_update_concept(req: Request):
    b = await req.json()
    res = memory.memory_update(b.get("name", ""), body=b.get("body"),
                               typ=b.get("type"), links=b.get("links"))
    return JSONResponse(res)


@app.post("/api/memory/remove")
async def memory_remove_concept(req: Request):
    b = await req.json()
    return JSONResponse(memory.memory_remove(b.get("name", "")))


@app.post("/api/memory/autolink")
async def memory_autolink_concepts():
    return JSONResponse(memory.memory_autolink())


# --- Self-development engine (the "Dreamer") -------------------------------
@app.get("/api/selfdev/status")
async def selfdev_status_ep():
    from autonomous import selfdev
    return JSONResponse(selfdev.selfdev_status())


@app.post("/api/selfdev/research")
async def selfdev_research_ep(req: Request):
    from autonomous import selfdev
    b = await req.json()
    return JSONResponse(await selfdev.research_topic(b.get("topic", "")))


@app.post("/api/selfdev/factcheck")
async def selfdev_factcheck_ep(req: Request):
    from autonomous import selfdev
    b = await req.json()
    return JSONResponse(await selfdev.fact_check_concept(b.get("name", "")))


@app.post("/api/selfdev/dream")
async def selfdev_dream_ep():
    from autonomous import selfdev
    return JSONResponse(await selfdev.dream_cycle())


@app.post("/api/selfdev/cycle")
async def selfdev_cycle_ep():
    from autonomous import selfdev
    return JSONResponse(await selfdev.run_selfdev_cycle())


@app.get("/api/gateway/status")
async def gateway_status():
    """Expose the local Gateway supervisor's heartbeat/cron status (read-only)."""
    hb = None
    p = ROOT / "data" / "gateway" / "heartbeat.json"
    if p.exists():
        try:
            hb = json.loads(p.read_text())
        except Exception:
            hb = None
    return JSONResponse({
        "ok": True,
        "gateway": bool(hb),
        "heartbeat": hb,
        "crontab": (ROOT / "data" / "gateway" / "crontab.json").exists(),
    })


# -------------------------------------------------------------------------
# Modular LAN services — status, image generation, and served output
# -------------------------------------------------------------------------
@app.get("/api/services/status")
async def services_status():
    """Health of every modular LAN service (media/automation stack)."""
    from services import comfyui as _comfy
    cf = False
    cf_models = []
    if cfg.CONFIG["services"]["comfyui"]["enabled"]:
        cf = await _comfy.comfyui_health()
        if cf:
            cf_models = await _comfy.comfyui_models()
    return JSONResponse({
        "comfyui": {"enabled": cfg.CONFIG["services"]["comfyui"]["enabled"],
                    "healthy": cf,
                    "base_url": cfg.CONFIG["services"]["comfyui"]["base_url"],
                    "models": cf_models},
        "configured": {"comfyui": bool(cfg.CONFIG["services"]["comfyui"]["base_url"])},
    })


@app.post("/api/generate")
async def generate_image(req: Request):
    """Generate an image via ComfyUI and return a local URL.

    Body: {prompt, negative_prompt?, model?, steps?, cfg_scale?, width?, height?}
    """
    if not cfg.CONFIG["services"]["comfyui"]["enabled"]:
        return JSONResponse({"status": "failed", "error": "ComfyUI service disabled"}, status_code=503)
    body = await req.json()
    if not (body.get("prompt") or "").strip():
        return JSONResponse({"status": "failed", "error": "missing prompt"}, status_code=400)
    from services import comfyui as _comfy
    res = await _comfy.render_image(
        body["prompt"],
        model=body.get("model") or None,
        negative_prompt=body.get("negative_prompt") or "",
        steps=int(body.get("steps") or 25),
        cfg_scale=float(body.get("cfg_scale") or 7.0),
        width=int(body.get("width") or 1024),
        height=int(body.get("height") or 1024),
    )
    if res.get("status") == "completed":
        return JSONResponse(res)
    return JSONResponse(res, status_code=502)


@app.get("/api/generated/{filename}")
async def generated_image(filename: str):
    """Serve a generated image from data/generated/ (path-confined)."""
    name = os.path.basename(filename)
    p = (ROOT / "data" / "generated" / name).resolve()
    allowed = (ROOT / "data" / "generated").resolve()
    try:
        p.relative_to(allowed)
    except ValueError:
        return JSONResponse({"error": "invalid filename"}, status_code=400)
    if not p.exists() or not p.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(p)


# --------------------------------------------------------------------------
# Personas — persisted on this Mac
# --------------------------------------------------------------------------
def _load_personas():
    if PERSONAS_FILE.exists():
        try:
            return json.loads(PERSONAS_FILE.read_text())
        except Exception:
            pass
    return []


def _save_personas(data):
    PERSONAS_FILE.write_text(json.dumps(data, indent=2))


@app.get("/api/personas")
async def get_personas():
    return JSONResponse(_load_personas())


@app.post("/api/personas")
async def save_personas(req: Request):
    data = await req.json()
    _save_personas(data)
    return JSONResponse({"ok": True, "count": len(data)})


@app.get("/api/sessions")
async def get_sessions():
    try:
        return JSONResponse(_auto_repo.get_sessions())
    except Exception as e:  # noqa
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/sessions")
async def save_session(req: Request):
    s = await req.json()
    try:
        _auto_repo.save_session(s)
        return JSONResponse({"ok": True, "name": s.get("name")})
    except Exception as e:  # noqa
        return JSONResponse({"error": str(e)}, status_code=500)


@app.delete("/api/sessions/{name}")
async def delete_session(name: str):
    try:
        _auto_repo.delete_session(name)
        return JSONResponse({"ok": True, "name": name})
    except Exception as e:  # noqa
        return JSONResponse({"error": str(e)}, status_code=500)


# --------------------------------------------------------------------------
# Autonomous engine — SQLite-backed mission/task runtime + SSE stream
# --------------------------------------------------------------------------
from autonomous.database import init_db as _init_auto_db, Repository as AutoRepo, auto_heal_db
from autonomous.orchestrator import Supervisor

# Self-heal: if the SQLite DB is corrupted, restore the newest good backup
# before any writes happen.
_heal = auto_heal_db()
if _heal.get("healed"):
    print(f"[db] AUTO-HEALED: restored from {_heal.get('backup')}")
elif _heal.get("action") == "no-good-backup":
    print("[db] WARNING: live DB corrupt and no good backup found")

_init_auto_db()
_auto_repo = AutoRepo()
_supervisor = Supervisor()

# Cross-restart recovery: re-drive any missions that were in-flight (planning /
# running / verifying) when the server last stopped. Their plan + task state are
# already persisted in SQLite, so we just re-schedule them on this process.
_resumed = _supervisor.resume_interrupted_missions(_auto_repo)
if _resumed:
    print(f"[orchestrator] resumed {len(_resumed)} interrupted mission(s): {_resumed}")


@app.get("/api/db/status")
async def db_status():
    """DB health + available backups (read-only)."""
    from autonomous import database as db
    return JSONResponse({
        "backend": db.DB_BACKEND,
        "healthy": db.verify_db() if db._sqlite_active() else None,
        "backups": db.list_backups(),
        "backup_dir": str(db.BACKUP_DIR),
    })


@app.post("/api/db/backup")
async def db_backup_ep():
    from autonomous import database as db
    path = db.backup_db(tag="manual")
    return JSONResponse({"ok": bool(path), "path": path})


@app.post("/api/db/restore")
async def db_restore_ep(req: Request):
    from autonomous import database as db
    b = await req.json()
    path = b.get("path", "")
    ok = db.restore_db(path)
    return JSONResponse({"ok": ok, "path": path})


@app.post("/api/autonomous/start")
async def autonomous_start(req: Request):
    body = await req.json()
    goal = (body.get("goal") or "").strip()
    room_personas = body.get("room_personas") or _load_personas()
    if not goal:
        return JSONResponse({"error": "missing goal"}, status_code=400)
    result = _supervisor.start_mission(goal, room_personas)
    return JSONResponse({
        "ok": True,
        "mission_id": result["id"],
        "planner": result.get("planner"),
        "status": result["status"],
        "stream_url": f"/api/autonomous/stream/{result['id']}",
    })


@app.get("/api/autonomous/stream/{mission_id}")
async def autonomous_stream(mission_id: str):
    async def gen():
        last = ""
        for _ in range(360):  # up to ~3 minutes
            mission = _auto_repo.get_mission(mission_id)
            if mission is None:
                yield f"data: {json.dumps({'error': 'mission not found'})}\n\n"
                break
            tasks = _auto_repo.list_mission_tasks(mission_id)
            artifacts = _auto_repo.mission_artifacts(mission_id)
            events = _auto_repo.mission_events(mission_id, limit=8)
            st = {
                "id": mission.id,
                "status": mission.status,
                "goal": mission.goal,
                "planner": mission.planner,
                "final_result": mission.final_result,
                "tasks": [
                    {
                        "id": t.id,
                        "title": t.title,
                        "agent": t.agent,
                        "status": t.status,
                        "result": t.result,
                        "verification": t.verification,
                    }
                    for t in tasks
                ],
                "artifacts": [
                    {
                        "id": a.id,
                        "kind": a.kind,
                        "path": a.path,
                        "task_id": a.task_id,
                        "agent": a.agent,
                    }
                    for a in artifacts
                ],
                "events": [
                    {"event": e.event, "agent": e.agent, "data": (json.loads(e.data) if isinstance(e.data, str) else e.data)}
                    for e in events
                ],
            }
            snap = json.dumps(st)
            if snap != last:
                last = snap
                yield f"data: {snap}\n\n"
            if mission.status in {"completed", "failed", "blocked", "cancelled"}:
                yield f"data: {json.dumps({'event': 'end', 'status': mission.status, 'final': mission.final_result})}\n\n"
                break
            await asyncio.sleep(0.5)
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/autonomous/status/{mission_id}")
async def autonomous_status(mission_id: str):
    mission = _auto_repo.get_mission(mission_id)
    if mission is None:
        return JSONResponse({"error": "mission not found"}, status_code=404)
    tasks = _auto_repo.list_mission_tasks(mission_id)
    events = _auto_repo.mission_events(mission_id, limit=50)
    return JSONResponse({
        "id": mission.id,
        "status": mission.status,
        "goal": mission.goal,
        "planner": mission.planner,
        "final_result": mission.final_result,
        "tasks": [
            {
                "id": t.id,
                "title": t.title,
                "agent": t.agent,
                "status": t.status,
                "priority": t.priority,
                "attempts": t.attempts,
                "dependencies": t.dependencies,
                "result": t.result,
                "verification": t.verification,
            }
            for t in tasks
        ],
        "events": [
            {
                "id": e.id,
                "event": e.event,
                "agent": e.agent,
                "data": json.loads(e.data) if isinstance(e.data, str) else e.data,
                "created_at": e.created_at,
            }
            for e in events
        ],
    })


@app.post("/api/autonomous/stop/{mission_id}")
async def autonomous_stop(mission_id: str):
    mission = _auto_repo.get_mission(mission_id)
    if mission is None:
        return JSONResponse({"error": "mission not found"}, status_code=404)
    _supervisor.request_stop(mission_id)
    mission.status = "cancelled"
    mission.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    _auto_repo.update_mission(mission)
    return JSONResponse({"ok": True})


@app.post("/api/autonomous/resume/{mission_id}")
async def autonomous_resume(mission_id: str):
    """Manually re-drive an interrupted mission whose background task died
    (e.g. after a crash or restart) but whose row is still in-flight."""
    result = _supervisor.resume_mission(mission_id)
    if not result.get("ok"):
        code = 404 if result.get("error") == "mission not found" else 409
        return JSONResponse(result, status_code=code)
    return JSONResponse(result)


@app.get("/api/autonomous/artifact")
async def autonomous_artifact(path: str):
    """Serve a mission artifact file from disk (read-only, path-confined)."""
    from pathlib import Path as PPath
    p = PPath(path).resolve()
    # Confine to the data dir so we never serve arbitrary filesystem paths.
    allowed = (ROOT / "data").resolve()
    try:
        p.relative_to(allowed)
    except ValueError:
        return JSONResponse({"error": "path outside data dir"}, status_code=400)
    if not p.exists() or not p.is_file():
        return JSONResponse({"error": "artifact not found"}, status_code=404)
    return FileResponse(p)


@app.get("/api/tools")
async def list_tools():
    """Catalog of tools agents can call (the 'tool-call list')."""
    from autonomous import tools as agent_tools
    return JSONResponse(agent_tools.list_tools())


@app.get("/api/missions")
async def list_missions():
    missions = _auto_repo.list_missions(50)
    return JSONResponse([
        {
            "id": m.id,
            "goal": m.goal,
            "status": m.status,
            "planner": m.planner,
            "updated_at": m.updated_at,
        }
        for m in missions
    ])


@app.get("/api/missions/{mission_id}")
async def get_mission(mission_id: str):
    mission = _auto_repo.get_mission(mission_id)
    if mission is None:
        return JSONResponse({"error": "mission not found"}, status_code=404)
    tasks = _auto_repo.list_mission_tasks(mission_id)
    events = _auto_repo.mission_events(mission_id, limit=100)
    return JSONResponse({
        "id": mission.id,
        "goal": mission.goal,
        "status": mission.status,
        "planner": mission.planner,
        "final_result": mission.final_result,
        "created_at": mission.created_at,
        "updated_at": mission.updated_at,
        "completed_at": mission.completed_at,
        "tasks": [
            {
                "id": t.id,
                "title": t.title,
                "description": t.description,
                "status": t.status,
                "priority": t.priority,
                "agent": t.agent,
                "dependencies": t.dependencies,
                "attempts": t.attempts,
                "result": t.result,
                "verification": t.verification,
                "created_at": t.created_at,
                "started_at": t.started_at,
                "completed_at": t.completed_at,
            }
            for t in tasks
        ],
        "events": [
            {
                "id": e.id,
                "event": e.event,
                "agent": e.agent,
                "data": json.loads(e.data) if isinstance(e.data, str) else e.data,
                "created_at": e.created_at,
            }
            for e in events
        ],
        })


@app.on_event("startup")
async def _startup():
    # Capture the running uvicorn event loop so the autonomous supervisor can
    # schedule background missions on the SAME loop (otherwise a sync route would
    # schedule them on a dead threadpool loop and they'd never run).
    import asyncio as _asyncio
    from autonomous.orchestrator import set_loop
    try:
        set_loop(_asyncio.get_running_loop())
    except RuntimeError:
        pass


@app.on_event("shutdown")
async def _shutdown():
    await close_client()


def main():
    import uvicorn
    from pathlib import Path as _P
    host = cfg.CONFIG["host"]
    port = int(cfg.CONFIG["port"])
    ssl_ctx = None
    if cfg.CONFIG.get("https"):
        cert = _P(cfg.CONFIG["ssl_certfile"])
        key = _P(cfg.CONFIG["ssl_keyfile"])
        if not (cert.exists() and key.exists()):
            # Auto-generate a self-signed cert so HTTPS (and thus mic on mobile)
            # works out of the box. SAN includes the LAN IP(s) so phones can trust it.
            try:
                import subprocess, socket
                cert.parent.mkdir(parents=True, exist_ok=True)
                # Resolve a real LAN IP (not 127.0.0.1 / ::1).
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
                # LibreSSL (macOS) is picky about -addext; use a config file instead.
                cnf = cert.parent / "openssl_vg.cnf"
                cnf.write_text(
                    "distinguished_name = dn\n[dn]\n[san]\nsubjectAltName = " + san + "\n"
                )
                subprocess.run([
                    "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                    "-keyout", str(key), "-out", str(cert), "-days", "825",
                    "-subj", "/CN=VirusGPT",
                    "-reqexts", "san", "-extensions", "san", "-config", str(cnf),
                ], check=True, capture_output=True)
                cnf.unlink(missing_ok=True)
                print(f"[VirusGPT] generated self-signed cert (SAN {san}) -> {cert}")
            except Exception as exc:  # noqa: BLE001
                print(f"[VirusGPT] WARNING: HTTPS enabled but cert gen failed ({exc}); falling back to HTTP")
                ssl_ctx = None
        if cert.exists() and key.exists():
            ssl_ctx = {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}
    scheme = "https" if ssl_ctx else "http"
    print(f"[VirusGPT] macOS server on {scheme}://{host}:{port}")
    print(f"[VirusGPT] Ollama -> {cfg.CONFIG['ollama']['base_url']}")
    print("[VirusGPT] Memory: local concept store (data/memory/)")
    if ssl_ctx:
        uvicorn.run(app, host=host, port=port, ssl_certfile=ssl_ctx["ssl_certfile"],
                    ssl_keyfile=ssl_ctx["ssl_keyfile"])
    else:
        uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
