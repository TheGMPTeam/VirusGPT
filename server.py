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
import re
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from services import config as cfg
from services import llm, tts, stt, memory
from services import n8n as _n8n
from services import marton as _marton
from services import settings_base as base
from services import n8n_settings as _n8n_set, comfyui_settings as _comfy_set
from services import youtube_settings as _yt_set, gmail_settings as _gmail_set, snapchat_settings as _snap_set
from services import marton_settings as _mart
from services import close_client

# Per-service settings/tools dispatcher (each connected service has its own
# settings module). Add new services here as their settings modules land.
SERVICE_MODULES = {
    "n8n": _n8n_set,
    "comfyui": _comfy_set,
    "youtube": _yt_set,
    "gmail": _gmail_set,
    "snapchat": _snap_set,
    "marton": _mart,
}

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
    from services import hermes as _hermes
    hf = await _hermes.hermes_status()
    models = await llm.list_models(cfg.CONFIG["ollama"]["base_url"]) if oll else []
    voices = await tts.list_voices(cfg.CONFIG["tts"]["base_url"]) if tt else []
    return JSONResponse({
        "ollama": oll,
        "tts": tt,
        "whisper": st,
        "comfyui": cf,
        "hermes": hf,
        "models": models or [cfg.CONFIG["ollama"]["default_model"]],
        "default_model": cfg.CONFIG["ollama"]["default_model"],
        "voices": voices or ["alba", "azelma", "cosette", "eponine", "fantine", "javert", "jean", "marius"],
        "default_voice": cfg.CONFIG["tts"].get("default_voice", "alba"),
    })


# -------------------------------------------------------------------------
# In-app updater — version reporting + git-based rebuild/replace
# -------------------------------------------------------------------------
from services import updater as _updater


@app.get("/api/version")
async def api_version():
    return JSONResponse(_updater.get_version())


@app.get("/api/update/check")
async def api_update_check(target: str | None = None):
    return JSONResponse(_updater.check_update(target))


@app.post("/api/update/apply")
async def api_update_apply(req: Request):
    # Kicks off a background git-pull + rebuild that replaces apps/VirusGPT.app.
    # Optional body {"target": "beta"|"main"} switches the channel being updated to.
    try:
        data = await req.json()
    except Exception:
        data = {}
    target = (data or {}).get("target") if isinstance(data, dict) else None
    return JSONResponse(_updater.apply_update(target))


@app.get("/api/update/status")
async def api_update_status():
    return JSONResponse(_updater.get_status())


@app.get("/api/update/branches")
async def api_update_branches():
    return JSONResponse(_updater.get_branches())


@app.post("/api/update/branch")
async def api_update_branch(req: Request):
    try:
        data = await req.json()
    except Exception:
        data = {}
    branch = _updater.set_branch((data or {}).get("branch", ""))
    return JSONResponse({"branch": branch, **_updater.get_branches()})


@app.get("/api/features")
async def api_features():
    # Channel-gated feature flags: main = stable (experimental disabled),
    # beta = experimental features enabled.
    return JSONResponse(_updater.features())


# -------------------------------------------------------------------------
# Chat — proxy to Ollama, stream SSE {content|done|error}
# -------------------------------------------------------------------------
def split_code_spans(text):
    """Split text into [(kind, chunk)] where kind is 'text' or 'code'.

    Fenced blocks (``` / ~~~) and inline `code` spans become 'code'; the rest
    is 'text'. Used to keep implementation out of TTS and to package project
    code files.
    """
    spans = []
    pos = 0
    for m in re.finditer(r"(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]+`)", text or ""):
        if m.start() > pos:
            spans.append(("text", text[pos:m.start()]))
        spans.append(("code", m.group(0)))
        pos = m.end()
    if pos < len(text or ""):
        spans.append(("text", text[pos:]))
    return spans


def extract_code_blocks(text):
    """Return [(language, content)] for every fenced/inline code span.

    `language` is the fence tag ('powershell', 'py', ...) or '' when absent.
    """
    out = []
    for kind, chunk in split_code_spans(text):
        if kind != "code":
            continue
        body = chunk.strip()
        lang = ""
        for fence in ("```", "~~~"):
            if body.startswith(fence) and body.endswith(fence) and len(body) >= 6:
                inner = body[3:-3]
                nl = inner.find("\n")
                if 0 <= nl <= 20 and re.fullmatch(r"[a-zA-Z0-9+_-]{1,12}", inner[:nl].strip()):
                    lang = inner[:nl].strip()
                    inner = inner[nl + 1:]
                break
        else:
            inner = body.strip("`")
        inner = inner.strip()
        if inner:
            out.append((lang, inner))
    return out


def speech_text(text):
    """Return a speech-safe copy with every code form removed.

    This deliberately handles both complete and UNFINISHED fences/spans. The
    browser streams tokens and may send a sentence while a closing backtick is
    still in a later chunk; in that case dropping the remainder is safer than
    ever reading source code aloud.
    """
    s = text or ""
    # Fenced blocks first (complete or open-to-end), then inline code (complete
    # or open-to-newline/end). Support both Markdown fence styles.
    s = re.sub(r"```[\s\S]*?(?:```|$)", " ", s)
    s = re.sub(r"~~~[\s\S]*?(?:~~~|$)", " ", s)
    s = re.sub(r"`[^`\n]*(?:`|$)", " ", s)
    s = re.sub(r"[*_]{1,3}", "", s)
    s = re.sub(r"^#{1,6}\s*", "", s, flags=re.M)
    s = re.sub(r"^\s*>\s?", "", s, flags=re.M)
    s = re.sub(r"^\s*[-*+]\s+", "", s, flags=re.M)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip()


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


# Max tool-call / observation cycles for a single /api/chat turn that uses tools.
_CHAT_TOOL_ROUNDS = 4


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

    # --- Resolve persona + allowed tools (relay/agent personas can call tools) ---
    # The frontend sends `tools` (a list of tool names) for the chosen persona;
    # if it only sends a persona name we look the tool allow-list up locally.
    persona_name = (body.get("persona") or "").strip()
    allowed = body.get("tools")
    if (not allowed) and persona_name:
        _p = next((x for x in _load_personas() if x.get("name") == persona_name), None)
        if _p:
            allowed = _p.get("tools")
    ollama_tools = None
    if allowed:
        from autonomous import tools as agent_tools
        if isinstance(allowed, list) and allowed and isinstance(allowed[0], str):
            _names = set(allowed)
            ollama_tools = [t for t in agent_tools.tools_for_ollama()
                            if t["function"]["name"] in _names]
        else:
            ollama_tools = allowed  # already an Ollama tool schema
    # Relay personas with a single tool: force that tool so the message is
    # ALWAYS relayed (e.g. Hermes -> ask_hermes), never answered locally.
    tool_choice = None
    if ollama_tools and len(ollama_tools) == 1:
        tool_choice = {"type": "function",
                       "function": {"name": ollama_tools[0]["function"]["name"]}}

    # --- Small-context history window (trim to last N messages / char cap) ---
    # A caller may raise the window for one request (team synthesis passes the
    # full turn transcript and must not have it trimmed away mid-request).
    max_hist = int(body.get("max_history") or chat_cfg.get("max_history", 24))
    max_chars = int(body.get("max_history_tokens") or chat_cfg.get("max_history_tokens", 2800))
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
    chat_timeout = float(cfg.CONFIG.get("chat_timeout", 60))

    async def gen():
        # No tools -> plain streaming chat (original behavior, untouched).
        if not ollama_tools:
            async for chunk in llm.stream_chat(model, messages, base, timeout=chat_timeout):
                yield f"data: {json.dumps(chunk)}\n\n"
            return
        # ReAct loop: stream the model, execute any tool_calls, then loop until
        # the model answers with no further tool calls.
        from autonomous import tools as agent_tools
        _tc = tool_choice
        for _ in range(_CHAT_TOOL_ROUNDS):
            assistant_msg = {"role": "assistant", "content": ""}
            tool_calls = []
            async for chunk in llm.stream_chat(model, messages, base,
                                               timeout=chat_timeout,
                                               tools=ollama_tools,
                                               tool_choice=_tc):
                if chunk.get("content"):
                    assistant_msg["content"] += chunk["content"]
                    yield f"data: {json.dumps({'content': chunk['content']})}\n\n"
                if chunk.get("tool_calls"):
                    tool_calls.extend(chunk["tool_calls"])
                if chunk.get("error"):
                    yield f"data: {json.dumps({'error': chunk['error']})}\n\n"
                    return
                if chunk.get("done"):
                    break
            if not tool_calls:
                # Final answer (already streamed) — nothing more to do.
                break
            # Record the assistant turn (with its tool_calls) for context.
            assistant_msg["tool_calls"] = tool_calls
            messages.append(assistant_msg)
            # Execute every requested tool and feed the result back.
            for call in tool_calls:
                fn = (call.get("function") or {})
                name = fn.get("name", "")
                try:
                    args = fn.get("arguments", {}) or {}
                    if isinstance(args, str):
                        args = json.loads(args or "{}")
                except Exception:
                    args = {}
                result = await agent_tools.run_tool(name, args)
                messages.append({
                    "role": "tool",
                    "content": json.dumps(result, ensure_ascii=False)[:4000],
                    "name": name,
                })
            # Only force the relay tool on the first round; afterwards let the
            # model answer normally with the tool result in context.
            _tc = None
        yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


# --------------------------------------------------------------------------
# Project export - package a finished team round into a compact, verifiable ZIP
# --------------------------------------------------------------------------
def _export_slug(value, fallback="team-project", max_len=50):
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return (slug[:max_len].rstrip("-") or fallback)


def _export_topic(heading, task):
    text = f"{heading or ''} {task or ''}".lower()
    rules = [
        ("image-copy", (r"image\s+copy", r"\bwim\b", r"\bdism\b", r"capture image", r"apply image")),
        ("hud-ui", (r"\bhud\b", r"\bui\b", r"\bxaml\b", r"interface", r"layout", r"window")),
        ("architecture", (r"architecture", r"system design", r"component", r"orchestrat")),
        ("deployment", (r"deploy", r"install", r"bootstrap", r"entry point", r"setup")),
        ("security", (r"security", r"credential", r"permission", r"hardening")),
        ("testing", (r"test", r"validat", r"verification", r"quality")),
        ("configuration", (r"config", r"setting", r"environment", r"manifest")),
        ("networking", (r"network", r"download", r"http", r"proxy")),
        ("data-storage", (r"database", r"storage", r"schema", r"data model")),
        ("documentation", (r"document", r"readme", r"guide", r"instructions")),
    ]
    for name, patterns in rules:
        if any(re.search(pattern, text) for pattern in patterns):
            return name
    return _export_slug(heading or task, "general", 32)


def _export_fenced_parts(text, task, transcript_line):
    """Split one turn into coherent prose and fenced-code parts with line anchors."""
    lines = (text or "").splitlines()
    parts = []
    heading = task or "General"
    prose = []
    prose_line = transcript_line

    def flush_prose():
        nonlocal prose, prose_line
        content = "\n".join(prose).strip()
        if content:
            parts.append({"kind": "prose", "content": content, "language": "",
                          "heading": heading, "line": prose_line})
        prose = []

    i = 0
    while i < len(lines):
        line = lines[i]
        heading_match = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", line)
        fence_match = re.match(r"^\s{0,3}(```|~~~)\s*([a-zA-Z0-9+_.-]{0,20})\s*$", line)
        if heading_match:
            flush_prose()
            heading = heading_match.group(1).strip()
            prose_line = transcript_line + i + 1
            i += 1
            continue
        if fence_match:
            flush_prose()
            fence = fence_match.group(1)
            language = fence_match.group(2).strip()
            start = i
            i += 1
            code = []
            while i < len(lines) and not re.match(rf"^\s{{0,3}}{re.escape(fence)}\s*$", lines[i]):
                code.append(lines[i])
                i += 1
            if i < len(lines):
                i += 1
            content = "\n".join(code).strip()
            if content:
                parts.append({"kind": "code", "content": content, "language": language,
                              "heading": heading, "line": transcript_line + start})
            prose_line = transcript_line + i
            continue
        if not prose:
            prose_line = transcript_line + i
        prose.append(line)
        i += 1
    flush_prose()
    return parts


def _export_code_type(language, content):
    """Classify by content first; return (extension, fence language) or prose."""
    lang = (language or "").lower()
    stripped = content.strip()
    lines = [line for line in stripped.splitlines() if line.strip()]
    joined = "\n".join(lines)

    if (re.search(r"<(?:Window|Page|UserControl|Grid|StackPanel)\b", joined)
            and ("xmlns=" in joined or re.search(r"</(?:Window|Page|UserControl)>", joined))):
        return ".xaml", "xaml"

    ps_patterns = [r"\[CmdletBinding\(\)\]", r"^\s*param\s*\(", r"\$[A-Za-z_]\w*",
                   r"\b(?:Write|Get|Set|New|Remove|Mount|Dismount|Start|Stop)-[A-Za-z]+\b",
                   r"^\s*function\s+[A-Za-z]+-[A-Za-z]+"]
    ps_score = sum(bool(re.search(pattern, joined, re.M)) for pattern in ps_patterns)
    if ps_score >= 2 or (lang in {"ps1", "powershell"} and ps_score >= 1):
        return ".ps1", "powershell"

    extmap = {"py": ".py", "python": ".py", "bash": ".sh", "sh": ".sh",
              "shell": ".sh", "js": ".js", "javascript": ".js", "json": ".json",
              "yaml": ".yaml", "yml": ".yaml", "html": ".html", "css": ".css",
              "dockerfile": ".dockerfile", "sql": ".sql", "toml": ".toml",
              "ts": ".ts", "typescript": ".ts", "csharp": ".cs", "cs": ".cs",
              "cpp": ".cpp", "c": ".c", "xml": ".xml"}
    if lang in extmap:
        return extmap[lang], lang
    if lang in {"text", "txt", "md", "markdown", "prose"}:
        return None, "text"
    if re.search(r"^\s*(?:def |class |import |from \w+ import )", joined, re.M):
        return ".py", "python"
    if re.search(r"^\s*(?:#!/|sudo |apt |curl |docker |winget )", joined, re.M):
        return ".sh", "bash"
    if re.search(r"[{};]", joined) and re.search(r"\b(?:const|let|function|return)\b", joined):
        return ".js", "javascript"
    return None, "text"


def _export_semantic_name(content, heading, topic, extension):
    first = next((line.strip() for line in content.splitlines() if line.strip()), "")
    comment_name = re.match(r"^(?:#|//|<!--)\s*([A-Za-z0-9_.-]+)(?:\s*-->)?$", first)
    if comment_name:
        candidate = comment_name.group(1)
        if "." in candidate:
            candidate = candidate.rsplit(".", 1)[0]
        return _export_slug(candidate, topic, 42)
    if extension == ".xaml":
        xclass = re.search(r"x:Class=[\"']([^\"']+)[\"']", content)
        if xclass:
            return _export_slug(xclass.group(1).split(".")[-1], "main-window", 42)
    function = re.search(r"^\s*function\s+([A-Za-z0-9_-]+)", content, re.M)
    if function:
        return _export_slug(function.group(1), topic, 42)
    return _export_slug(heading, f"{topic}-snippet", 42)


@app.post("/api/export/project")
async def export_project(req: Request):
    """Build a compact topic-grouped project ZIP with integrity checks."""
    import hashlib
    import io
    import zipfile
    from datetime import datetime, timezone

    body = await req.json()
    goal = (body.get("goal") or "team project").strip()
    plan = (body.get("plan") or "").strip()
    turns = body.get("turns") or []
    synthesis = (body.get("synthesis") or "").strip()
    slug = _export_slug(body.get("name") or goal)
    root = f"{slug}/"
    revision = max(1, min(int(body.get("export_revision") or 1), 999))
    exported = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    stable_payload = json.dumps({"goal": goal, "plan": plan, "turns": turns,
                                 "synthesis": synthesis}, sort_keys=True,
                                ensure_ascii=False).encode("utf-8")
    round_id = str(body.get("round_id") or f"round-{hashlib.sha256(stable_payload).hexdigest()[:12]}")
    round_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", round_id).strip("-")[:80] or "round"

    # README retains the original human summary layout.
    readme = [f"# {goal}", "", f"_Exported {exported} from VirusGPT team round._", ""]
    if plan:
        readme += ["## Plan", "", plan, ""]
    readme += ["## Contributions", ""]
    for i, turn in enumerate(turns, 1):
        readme.append(f"{i}. **{turn.get('who','agent')}** - {turn.get('task','')}")
    readme.append("")
    if synthesis:
        readme += ["## Final synthesis", "", synthesis, ""]

    # Transcript stays full and untrimmed while recording exact line anchors.
    transcript = [f"GOAL: {goal}", ""]
    turn_lines = []
    for i, turn in enumerate(turns, 1):
        transcript.append(f"--- [{i}] {turn.get('who','agent')} | task: {turn.get('task','')} ---")
        output_start = len(transcript) + 1
        output = (turn.get("output") or "").strip()
        transcript.extend(output.splitlines())
        transcript.append("")
        turn_lines.append(output_start)
    if synthesis:
        transcript.append("--- FINAL SYNTHESIS ---")
        transcript.extend(synthesis.splitlines())
    transcript_text = "\n".join(transcript)

    decisions = [f"# Decisions: {goal}", ""]
    if plan:
        decisions += ["## Plan", "", plan, ""]
    if synthesis:
        decisions += ["## Consolidated decision", "", synthesis, ""]
    decisions += ["## Design record", ""]
    for turn in turns:
        output = re.sub(r"```[\s\S]*?```|~~~[\s\S]*?~~~", "", turn.get("output") or "").strip()
        output = re.sub(r"\n{3,}", "\n\n", output).strip()
        if output:
            decisions += [f"### {turn.get('who','agent')}",
                          f"Task: {turn.get('task','')}", "", output, ""]

    topics = {}
    topic_order = []
    snippet_rows = []
    snippet_number = 0
    used_paths = set()

    def topic_bucket(topic):
        if topic not in topics:
            topic_order.append(topic)
            topics[topic] = {"number": len(topic_order), "notes": [], "files": {}}
        return topics[topic]

    for turn_index, turn in enumerate(turns):
        who = turn.get("who") or "agent"
        task = turn.get("task") or ""
        parts = _export_fenced_parts(turn.get("output") or "", task, turn_lines[turn_index])
        for part in parts:
            content = part["content"].strip()
            if not content:
                continue
            topic = _export_topic(part["heading"], task)
            bucket = topic_bucket(topic)
            folder = f"{bucket['number']:02d}-{topic}"
            snippet_number += 1
            snippet_id = f"S{snippet_number:03d}"
            anchor = f"transcript.md#L{part['line']}"
            extension, fence_language = (None, "text")
            if part["kind"] == "code":
                extension, fence_language = _export_code_type(part["language"], content)
            line_count = len(content.splitlines())

            if part["kind"] == "prose" or extension is None or line_count < 10:
                notes_path = f"snippets/{folder}/notes.md"
                section = [f"# --- from {who}, transcript line {part['line']} ---", ""]
                if part["kind"] == "code" and extension is not None:
                    section += [f"```{fence_language}", content, "```", ""]
                else:
                    section += [content, ""]
                bucket["notes"].extend(section)
                output_path = notes_path
            else:
                semantic = _export_semantic_name(content, part["heading"], topic, extension)
                base_path = f"snippets/{folder}/{snippet_number:03d}-{semantic}{extension}"
                output_path = base_path
                duplicate = 2
                while output_path in used_paths:
                    output_path = base_path[:-len(extension)] + f"-{duplicate}{extension}"
                    duplicate += 1
                used_paths.add(output_path)
                bucket["files"][output_path] = content.rstrip() + "\n"

            snippet_rows.append({"id": snippet_id, "agent": who, "topic": topic,
                                 "anchor": anchor, "path": output_path})

    archive_files = {
        "README.md": "\n".join(readme).rstrip() + "\n",
        "transcript.md": transcript_text.rstrip() + "\n",
        "decisions.md": "\n".join(decisions).rstrip() + "\n",
    }
    for topic in topic_order:
        bucket = topics[topic]
        folder = f"{bucket['number']:02d}-{topic}"
        if bucket["notes"]:
            archive_files[f"snippets/{folder}/notes.md"] = (
                f"# {topic.replace('-', ' ').title()}\n\n" + "\n".join(bucket["notes"]).rstrip() + "\n")
        archive_files.update(bucket["files"])

    index = ["# Snippet index", "",
             "| Snippet | Agent | Topic | Transcript anchor | File |",
             "|---|---|---|---|---|"]
    for row in snippet_rows:
        cells = [str(row[key]).replace("|", "\\|").replace("\n", " ")
                 for key in ("id", "agent", "topic", "anchor", "path")]
        index.append("| " + " | ".join(cells) + " |")
    archive_files["snippets/index.md"] = "\n".join(index).rstrip() + "\n"

    encoded = {path: content.encode("utf-8") for path, content in archive_files.items()}
    agents = []
    for turn in turns:
        name = turn.get("who") or "agent"
        if name not in agents:
            agents.append(name)
    manifest = {
        "schema": "virusgpt.team-export.v2",
        "export_version": 2,
        "round_id": round_id,
        "export_revision": revision,
        "exported_at": exported,
        "project": slug,
        "agents": agents,
        "checksum_scope": "all archive files except MANIFEST.json",
        "snippet_count": len(snippet_rows),
        "files": [{"path": path, "bytes": len(data),
                   "sha256": hashlib.sha256(data).hexdigest()}
                  for path, data in sorted(encoded.items())],
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, data in sorted(encoded.items()):
            archive.writestr(root + path, data)
        archive.writestr(root + "MANIFEST.json",
                         json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8"))

    suffix = "" if revision == 1 else f"-v{revision}"
    from fastapi.responses import Response
    return Response(content=buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition":
                             f'attachment; filename="{slug}{suffix}.zip"'})


# --------------------------------------------------------------------------
# AI text improvement - rewrite the user's typed draft into a polished version
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
_TTS_REWRITE_CACHE: dict = {}
_TTS_REWRITE_SYS = (
    "You rewrite ONE sentence so a text-to-speech voice reads it naturally and "
    "conversationally. Rules: keep the exact same meaning and facts; expand "
    "symbols, abbreviations, URLs, file paths, and math into spoken words "
    "(for example 'n8n' -> 'n eight n', '~/.hermes' -> 'dot hermes in the home "
    "folder'); remove markdown, code fences, asterisks, hashes, and emoji; "
    "split run-on fragments into short spoken clauses; never add commentary, "
    "greetings, or quotes. Output ONLY the rewritten sentence and nothing else."
)


async def _rewrite_for_tts(text: str, tts_cfg: dict) -> Optional[str]:
    """Return a speakability-rewritten copy of `text`, or None to pass through.

    Guards: cached per sentence; skipped for very short/numeric text; the
    rewrite is rejected if the model rambles (>3x length) or fails, in which
    case the original sentence is spoken unchanged.
    """
    t = (text or "").strip()
    if len(t) < 12 or not any(c.isalpha() for c in t):
        return None
    hit = _TTS_REWRITE_CACHE.get(t)
    if hit is not None:
        return hit
    from services import llm
    model = (tts_cfg.get("rewrite_model")
             or cfg.CONFIG["ollama"].get("default_model", "gemma4:e4b"))
    base = cfg.CONFIG["ollama"].get("base_url", "http://127.0.0.1:11434")
    out = await llm.complete(
        model,
        [{"role": "system", "content": _TTS_REWRITE_SYS},
         {"role": "user", "content": t}],
        base,
        timeout=float(tts_cfg.get("rewrite_timeout", 12)),
        options={"temperature": 0.2, "num_predict": 256},
        think=False,
    )
    out = (out or "").strip()
    if not out or len(out) > max(3 * len(t), len(t) + 300) or "\n\n" in out:
        return None  # rambling / failed: speak the original
    if len(_TTS_REWRITE_CACHE) > 512:
        _TTS_REWRITE_CACHE.clear()
    _TTS_REWRITE_CACHE[t] = out
    return out


@app.get("/api/tts")
async def tts_proxy(text: str = "", voice: str = "", format: str = "mp3"):
    if not text:
        return JSONResponse({"error": "missing text"}, status_code=400)
    # Mandatory final safety gate: remove fenced, inline, and even unfinished
    # streamed code BEFORE the rewriting model or synthesizer can see it.
    text = speech_text(text)
    if not text:
        from fastapi.responses import Response
        resp = Response(status_code=204)
        resp.headers["X-TTS-Spoken"] = ""
        resp.headers["X-TTS-Rewritten"] = "false"
        return resp
    v = voice or cfg.CONFIG["tts"].get("default_voice", "nova")
    # --- Speakability rewrite: rephrase the sentence for natural TTS delivery
    # (expand symbols/abbreviations, kill markdown remnants, keep meaning).
    # Runs per sentence; results are cached so replay/▶ buttons are instant.
    tts_cfg = cfg.CONFIG.get("tts", {})
    rewritten = False
    if tts_cfg.get("rewrite", True):
        spoken = await _rewrite_for_tts(text, tts_cfg)
        if spoken:
            text = spoken
            rewritten = True
    # Resolve a locally-cloned voice handle to its stored WAV path.
    # SECURITY: only allow files inside DATA/voices — guard against traversal.
    if v.startswith("local:"):
        name = os.path.basename(v[len("local:"):])
        vp = (DATA / "voices" / name).resolve()
        allowed = (DATA / "voices").resolve()
        if not str(vp).startswith(str(allowed) + os.sep) or not vp.is_file():
            return JSONResponse({"error": f"cloned voice not found: {v}"}, status_code=404)
        v = str(vp)
    # PocketTTS silently skips words in chunks over ~50 model tokens. The
    # client already splits long sentences, but the rewrite can grow a chunk
    # again, so enforce the limit here too: synthesize sub-sentences and
    # concatenate the MP3 frames (frame streams remain playable when joined).
    max_chars = int(tts_cfg.get("max_chunk_chars", 480))
    pieces = [text]
    if len(text) > max_chars:
        import re as _re
        sents = _re.split(r"(?<=[.!?])\s+", text)
        pieces, cur = [], ""
        for s in sents:
            if cur and len(cur) + len(s) + 1 > max_chars:
                pieces.append(cur.strip()); cur = s
            else:
                cur = (cur + " " + s).strip()
        if cur: pieces.append(cur.strip())
        # Hard-split any single oversized sentence at clause boundaries.
        fixed = []
        for p in pieces:
            while len(p) > max_chars:
                cut = max(p.rfind(", ", 0, max_chars), p.rfind(" ", 0, max_chars))
                cut = cut if cut > 60 else max_chars
                fixed.append(p[:cut].strip()); p = p[cut:].strip()
            if p: fixed.append(p)
        pieces = fixed
    audio = None
    for piece in pieces:
        part = await tts.synthesize(
            piece, v, cfg.CONFIG["tts"]["base_url"], response_format=format,
            timeout=float(cfg.CONFIG["tts"].get("timeout", 120)),
        )
        if part is None:
            if audio is None:
                return JSONResponse({"error": "TTS unavailable"}, status_code=502)
            break  # keep the audio we already have
        audio = part if audio is None else audio + part
    if audio is None:
        return JSONResponse({"error": "TTS unavailable"}, status_code=502)
    mt = "audio/mpeg" if format == "mp3" else "audio/wav"
    resp = StreamingResponse(iter([audio]), media_type=mt)
    # Debug/inspection: what the voice actually reads (after rewrite).
    resp.headers["X-TTS-Spoken"] = urllib.parse.quote(text)[:800]
    resp.headers["X-TTS-Rewritten"] = "true" if rewritten else "false"
    return resp


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
    """Upload a reference to PocketTTS and return its persistent voice ID."""
    form = await req.form()
    audio = form.get("audio")
    name = (form.get("name") or "My cloned voice").strip()
    consent = form.get("consent_confirmed")
    if consent != "true":
        return JSONResponse({"error": "confirm recording ownership or speaker permission"}, status_code=400)
    if not audio or not getattr(audio, "filename", None):
        return JSONResponse({"error": "no audio"}, status_code=400)
    data = await audio.read()
    if not data:
        return JSONResponse({"error": "empty audio"}, status_code=400)
    if len(data) > 10 * 1024 * 1024:
        return JSONResponse({"error": "reference exceeds 10 MB"}, status_code=413)
    record = await tts.clone_voice(
        data,
        os.path.basename(audio.filename),
        getattr(audio, "content_type", None) or "application/octet-stream",
        name,
        cfg.CONFIG["tts"]["base_url"],
        timeout=float(cfg.CONFIG["tts"].get("timeout", 120)),
    )
    if not record:
        return JSONResponse({"error": "voice cloning failed"}, status_code=502)
    return JSONResponse({"ok": True, "voice": record["id"], "name": record.get("name", name)})


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


@app.post("/api/memory/add")
async def memory_add_concept(req: Request):
    """Create a new memory concept (used by the UI 'save' action).

    Body: {name, body, type?}. If a concept with the same name already
    exists, this falls through to an update so the UI 'save' works for both
    new and existing nodes.
    """
    b = await req.json()
    name = b.get("name", "")
    body = b.get("body", "")
    typ = b.get("type") or "concept"
    if not name or not body:
        return JSONResponse({"ok": False, "error": "name and body required"}, status_code=400)
    existing = memory.memory_get(name)
    if existing is None:
        res = await memory.memory_add(name, body, typ)
    else:
        res = memory.memory_update(name, body=body, typ=typ, links=b.get("links"))
    return JSONResponse(res)


@app.post("/api/memory/update")
async def memory_update_concept(req: Request):
    b = await req.json()
    name = b.get("name", "")
    if not name:
        return JSONResponse({"ok": False, "error": "name required"}, status_code=400)
    # Auto-create if the node does not yet exist, so a single 'save' works for
    # both new and existing concepts.
    if memory.memory_get(name) is None:
        res = await memory.memory_add(name, b.get("body", ""), b.get("type") or "concept")
    else:
        res = memory.memory_update(name, body=b.get("body"),
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
    from services import comfyui as _comfy, n8n as _n8n, marton as _marton
    cf = False
    cf_models = []
    if cfg.CONFIG["services"]["comfyui"]["enabled"]:
        cf = await _comfy.comfyui_health()
        if cf:
            cf_models = await _comfy.comfyui_models()
    n8n_st = await _n8n.n8n_status()
    marton_st = await _marton.marton_status()
    return JSONResponse({
        "comfyui": {"enabled": cfg.CONFIG["services"]["comfyui"]["enabled"],
                    "healthy": cf,
                    "base_url": cfg.CONFIG["services"]["comfyui"]["base_url"],
                    "models": cf_models},
        "n8n": n8n_st,
        "marton": marton_st,
        "configured": {"comfyui": bool(cfg.CONFIG["services"]["comfyui"]["base_url"]),
                       "n8n": bool(n8n_st["base_url"]),
                       "marton": bool(marton_st["configured"])},
    })


@app.get("/api/n8n/list")
async def n8n_list(active_only: bool = False):
    """List n8n workflows (REST adapter; n8n-mcp as fallback)."""
    res = await _n8n.n8n_list_workflows(active_only=active_only)
    if res.get("status") == "ok":
        return JSONResponse(res)
    # Fallback to the n8n-mcp tool if REST is unavailable.
    from services import mcp_client as _mcp
    mcp_res = await _mcp.call_mcp_tool("n8n-mcp", "n8n_list_workflows",
                                       {"active_only": active_only})
    if mcp_res.get("status") == "ok":
        try:
            data = json.loads(mcp_res.get("result") or "{}")
            wfs = data.get("data", data) if isinstance(data, dict) else data
            if active_only and isinstance(wfs, list):
                wfs = [w for w in wfs if w.get("active")]
            return JSONResponse({"status": "ok", "count": len(wfs), "workflows": wfs})
        except Exception:
            return JSONResponse({"status": "ok", "raw": mcp_res.get("result")})
    return JSONResponse(res)


@app.post("/api/n8n/trigger")
async def n8n_trigger(req: Request):
    """Trigger an n8n workflow. Body: {workflow_id, data?}."""
    body = await req.json() if (await req.body()) else {}
    wid = (body or {}).get("workflow_id")
    if not wid:
        return JSONResponse({"status": "failed", "error": "missing workflow_id"}, status_code=400)
    res = await _n8n.n8n_trigger_workflow(wid, (body or {}).get("data"))
    if res.get("status") == "ok":
        return JSONResponse(res)
    from services import mcp_client as _mcp
    mcp_res = await _mcp.call_mcp_tool("n8n-mcp", "n8n_trigger_workflow",
                                        {"workflow_id": wid, "data": (body or {}).get("data")})
    if mcp_res.get("status") == "ok":
        return JSONResponse({"status": "ok", "raw": mcp_res.get("result")})
    return JSONResponse(res)


@app.post("/api/n8n/create")
async def n8n_create(req: Request):
    """Create (build) an n8n workflow. Body: {name, nodes, connections?, active?}."""
    body = await req.json()
    if not (body.get("name") and body.get("nodes")):
        return JSONResponse({"status": "failed", "error": "missing name/nodes"}, status_code=400)
    res = await _n8n.n8n_create_workflow(
        body["name"], body["nodes"], body.get("connections"), body.get("active", False))
    if res.get("status") == "ok":
        return JSONResponse(res)
    from services import mcp_client as _mcp
    mcp_res = await _mcp.call_mcp_tool("n8n-mcp", "n8n_create_workflow",
                                        {"name": body["name"], "nodes": body["nodes"],
                                         "connections": body.get("connections"),
                                         "active": body.get("active", False)})
    if mcp_res.get("status") == "ok":
        return JSONResponse({"status": "ok", "raw": mcp_res.get("result")})
    return JSONResponse(res)


@app.get("/api/n8n/get")
async def n8n_get(workflow_id: str):
    """Fetch a single n8n workflow definition (REST; n8n-mcp fallback)."""
    res = await _n8n.n8n_get_workflow(workflow_id)
    if res.get("status") == "ok":
        return JSONResponse(res)
    from services import mcp_client as _mcp
    mcp_res = await _mcp.call_mcp_tool("n8n-mcp", "n8n_get_workflow",
                                        {"id": workflow_id})
    if mcp_res.get("status") == "ok":
        try:
            return JSONResponse({"status": "ok", "workflow": json.loads(mcp_res.get("result") or "{}")})
        except Exception:
            return JSONResponse({"status": "ok", "raw": mcp_res.get("result")})
    return JSONResponse(res)


# -------------------------------------------------------------------------
# Maton API Gateway — YouTube / Gmail / Snapchat (full control)
# -------------------------------------------------------------------------
@app.get("/api/marton/status")
async def marton_status_route():
    """Connection state for each Maton-connected app (youtube/gmail/snapchat)."""
    return JSONResponse(await _marton.marton_status())


# --- YouTube ----------------------------------------------------------
@app.get("/api/marton/youtube/channel")
async def marton_yt_channel():
    return JSONResponse(await _marton.youtube_channel())


@app.get("/api/marton/youtube/playlists")
async def marton_yt_playlists(max_results: int = 25):
    return JSONResponse(await _marton.youtube_list_playlists(max_results))


@app.post("/api/marton/youtube/upload")
async def marton_yt_upload(req: Request):
    """Upload a video. Body: {file_path, title, description?, privacy?, confirm?}.

    `confirm=true` is REQUIRED (external publish side effect).
    """
    body = await req.json() if (await req.body()) else {}
    fp = body.get("file_path")
    title = body.get("title")
    if not fp or not title:
        return JSONResponse({"status": "failed", "error": "missing file_path/title"}, status_code=400)
    return JSONResponse(await _marton.youtube_upload_video(
        fp, title, body.get("description", ""), body.get("privacy", "private"),
        confirm=bool(body.get("confirm"))))


# --- Gmail ------------------------------------------------------------
@app.get("/api/marton/gmail/profile")
async def marton_gmail_profile():
    return JSONResponse(await _marton.gmail_profile())


@app.get("/api/marton/gmail/messages")
async def marton_gmail_messages(query: str = "", max_results: int = 10):
    return JSONResponse(await _marton.gmail_list_messages(query, max_results))


@app.post("/api/marton/gmail/send")
async def marton_gmail_send(req: Request):
    """Send email. Body: {to, subject, body, confirm?}. confirm=true REQUIRED."""
    body = await req.json() if (await req.body()) else {}
    if not (body.get("to") and body.get("subject")):
        return JSONResponse({"status": "failed", "error": "missing to/subject"}, status_code=400)
    return JSONResponse(await _marton.gmail_send(
        body["to"], body["subject"], body.get("body", ""), confirm=bool(body.get("confirm"))))


# --- Snapchat ---------------------------------------------------------
@app.get("/api/marton/snapchat/me")
async def marton_snap_me():
    return JSONResponse(await _marton.snapchat_me())


@app.get("/api/marton/snapchat/organizations")
async def marton_snap_orgs():
    return JSONResponse(await _marton.snapchat_organizations())


@app.get("/api/marton/snapchat/adaccounts/{organization_id}")
async def marton_snap_accts(organization_id: str):
    return JSONResponse(await _marton.snapchat_ad_accounts(organization_id))


@app.get("/api/marton/snapchat/campaigns/{ad_account_id}")
async def marton_snap_campaigns(ad_account_id: str):
    return JSONResponse(await _marton.snapchat_list_campaigns(ad_account_id))


@app.post("/api/marton/snapchat/upload")
async def marton_snap_upload(req: Request):
    """Upload a creative. Body: {ad_account_id, file_path, name?, confirm?}.

    confirm=true REQUIRED (external upload side effect).
    """
    body = await req.json() if (await req.body()) else {}
    if not (body.get("ad_account_id") and body.get("file_path")):
        return JSONResponse({"status": "failed", "error": "missing ad_account_id/file_path"}, status_code=400)
    return JSONResponse(await _marton.snapchat_upload_media(
        body["ad_account_id"], body["file_path"], body.get("name", ""),
        confirm=bool(body.get("confirm"))))


# -------------------------------------------------------------------------
# Per-service Settings + Tools (each connected service has its own module)
# -------------------------------------------------------------------------
@app.get("/api/services")
async def services_index():
    """List all connected services and their registered tool counts."""
    out = {}
    for name, mod in SERVICE_MODULES.items():
        try:
            tools = mod.list_tools().get("tools", [])
        except Exception:
            tools = []
        out[name] = {"tool_count": len(tools)}
    return JSONResponse(out)


@app.get("/api/services/{name}/settings")
async def service_settings_get(name: str):
    mod = SERVICE_MODULES.get(name)
    if not mod:
        return JSONResponse({"status": "failed", "error": f"unknown service '{name}'"}, status_code=404)
    return JSONResponse(mod.read_settings())


@app.post("/api/services/{name}/settings")
async def service_settings_post(name: str, req: Request):
    mod = SERVICE_MODULES.get(name)
    if not mod:
        return JSONResponse({"status": "failed", "error": f"unknown service '{name}'"}, status_code=404)
    body = await req.json() if (await req.body()) else {}
    return JSONResponse(mod.write_settings(body or {}))


@app.get("/api/services/{name}/status")
async def service_status_get(name: str):
    mod = SERVICE_MODULES.get(name)
    if not mod:
        return JSONResponse({"status": "failed", "error": f"unknown service '{name}'"}, status_code=404)
    return JSONResponse(await base.get_status(name))


@app.get("/api/services/{name}/tools")
async def service_tools_get(name: str):
    mod = SERVICE_MODULES.get(name)
    if not mod:
        return JSONResponse({"status": "failed", "error": f"unknown service '{name}'"}, status_code=404)
    return JSONResponse(mod.list_tools())


@app.post("/api/services/{name}/tools/run")
async def service_tools_run(name: str, req: Request):
    mod = SERVICE_MODULES.get(name)
    if not mod:
        return JSONResponse({"status": "failed", "error": f"unknown service '{name}'"}, status_code=404)
    body = await req.json() if (await req.body()) else {}
    tool = body.get("tool")
    if not tool:
        return JSONResponse({"status": "failed", "error": "missing 'tool'"}, status_code=400)
    kwargs = {k: v for k, v in (body or {}).items() if k != "tool"}
    return JSONResponse(await mod.run_tool(tool, **kwargs))


# -------------------------------------------------------------------------
# MCP bridge status
# -------------------------------------------------------------------------
@app.get("/api/mcp/status")
async def mcp_status():
    """Status of the MCP bridge (server + discovered client tools)."""
    from services import mcp_client as _mc
    return JSONResponse({
        "server_enabled": cfg.CONFIG.get("mcp", {}).get("server_enabled", False),
        "server_port": cfg.CONFIG.get("mcp", {}).get("server_port"),
        "client_enabled": _mc.mcp_client_enabled(),
        "discovered_tools": _mc.list_mcp_tools(),
    })


@app.post("/api/generate")
async def generate_image(req: Request):
    """Generate an image via ComfyUI and return a local URL.

    Body: {prompt, negative_prompt?, model?, steps?, cfg_scale?, width?,
           height?, seed?, sampler_name?, scheduler?}
    """
    if not cfg.CONFIG["services"]["comfyui"]["enabled"]:
        return JSONResponse({"status": "failed", "error": "ComfyUI service disabled"}, status_code=503)
    body = await req.json()
    if not (body.get("prompt") or "").strip():
        return JSONResponse({"status": "failed", "error": "missing prompt"}, status_code=400)
    from services import comfyui as _comfy
    refine_of = body.get("refine_of") or None
    denoise_default = 0.6 if refine_of else 1.0
    res = await _comfy.render_image(
        body["prompt"],
        model=body.get("model") or None,
        negative_prompt=body.get("negative_prompt") or "",
        steps=int(body.get("steps") or 25),
        cfg_scale=float(body.get("cfg_scale") or 7.0),
        width=int(body.get("width") or 1024),
        height=int(body.get("height") or 1024),
        seed=int(body.get("seed") or -1),
        sampler_name=body.get("sampler_name") or None,
        scheduler=body.get("scheduler") or None,
        transparent=bool(body.get("transparent")),
        refine_of=refine_of,
        denoise=float(body.get("denoise") if body.get("denoise") is not None else denoise_default),
    )
    if res.get("status") == "completed":
        return JSONResponse(res)
    return JSONResponse(res, status_code=502)


@app.get("/api/generated/{filename}")
async def generated_image(filename: str):
    """Serve a generated image from data/generated/ (path-confined).

    Looks in the canonical locations where render_image may have written the
    file: the repo/Resources data dir AND the PyInstaller Frameworks data dir
    (in a frozen .app the `data/` tree lands under Frameworks, not ROOT)."""
    name = os.path.basename(filename)
    if not name or name != filename:
        return JSONResponse({"error": "invalid filename"}, status_code=400)
    # candidate base dirs (all path-confined, no traversal possible)
    bases = [ROOT / "data" / "generated"]
    if getattr(sys, "frozen", False):
        try:
            base = Path(sys.executable).resolve().parent.parent  # .../Contents
            bases.append(base / "Frameworks" / "data" / "generated")
            bases.append(base / "Resources" / "data" / "generated")
        except Exception:
            pass
    for b in bases:
        try:
            p = (b / name).resolve()
            p.relative_to(b.resolve())  # confinement check
        except (ValueError, Exception):
            continue
        if p.exists() and p.is_file():
            return FileResponse(p)
    return JSONResponse({"error": "not found"}, status_code=404)


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

    # MCP bridge: start the MCP SERVER (SSE) in a daemon thread, and connect the
    # MCP CLIENT to any configured external servers + the n8n REST adapter.
    # Keep the SERVER and CLIENT independent: a failure in one must not skip the
    # other (e.g. a FastMCP server init error must not prevent n8n-mcp connect).
    try:
        from services import mcp_server, mcp_client as _mc
        if mcp_server.mcp_is_enabled():
            import threading
            threading.Thread(target=mcp_server.start_mcp_server, daemon=True).start()
    except Exception as exc:  # noqa: BLE001
        print(f"[startup] MCP server init skipped: {exc}", flush=True)
    try:
        from services import mcp_client as _mc
        if _mc.mcp_client_enabled():
            # connect_all is resilient (watchdog timeouts, non-fatal failures);
            # it runs in its own task and never blocks startup.
            _asyncio.create_task(_mc.connect_all())
    except Exception as exc:  # noqa: BLE001
        print(f"[startup] MCP client init skipped: {exc}", flush=True)


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
