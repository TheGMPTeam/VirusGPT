"""Self-contained tool harness for VirusGPT agents.

Mirrors the kinds of capabilities an assistant agent needs (web search, web
fetch, local shell, file read/write, memory query, arithmetic) without any
external agent framework. Each tool is a plain async function registered in
TOOLS with a JSON schema-like spec. Agents invoke tools through the ReAct
protocol implemented in `autonomous/agents/runtime.py` (a fenced ```tool block).

All local-execution tools are sandboxed:
  • shell commands run under a timeout and an allowlist of safe binaries.
  • file ops are confined to a configurable sandbox directory (config keys
    tools.sandbox_dir, default: <repo>/data/sandbox).
"""
from __future__ import annotations

import asyncio
import json
import math
import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List

from services import config as cfg
from services import memory as mem

# --------------------------------------------------------------------------
# Sandbox config
# --------------------------------------------------------------------------
_TOOLS_CFG = (cfg.CONFIG.get("tools") or {})
SANDBOX = Path(_TOOLS_CFG.get("sandbox_dir") or (Path(__file__).resolve().parent.parent / "data" / "sandbox"))
SANDBOX.mkdir(parents=True, exist_ok=True)
SHELL_TIMEOUT = int(_TOOLS_CFG.get("shell_timeout", 20))
# Permit only a small, safe set of read-mostly CLI tools.
_SHELL_ALLOW = set(_TOOLS_CFG.get("shell_allowlist") or [
    "ls", "cat", "echo", "pwd", "date", "whoami", "uname", "head", "tail",
    "wc", "grep", "sed", "awk", "sort", "uniq", "cut", "tr", "base64", "jq",
    "python3", "python", "node", "curl", "wget", "ping", "dig", "nslookup",
    "git", "tar", "zip", "unzip", "find", "stat", "df", "free", "ps",
])

# --------------------------------------------------------------------------
# Tool registry
# --------------------------------------------------------------------------
ToolFn = Callable[[dict], Awaitable[dict]]

TOOLS: Dict[str, Dict[str, Any]] = {}


def register(name: str, description: str, params: List[dict], fn: ToolFn):
    TOOLS[name] = {
        "name": name,
        "description": description,
        "parameters": params,
        "handler": fn,
    }


def list_tools() -> List[dict]:
    """Public catalog -- what the UI renders as the 'tool-call list'."""
    out = []
    for t in TOOLS.values():
        out.append({
            "name": t["name"],
            "description": t["description"],
            "parameters": t["parameters"],
        })
    return out


def tools_for_ollama() -> List[dict]:
    """Convert the registry into Ollama / OpenAI-style `tools` schema."""
    out = []
    for t in TOOLS.values():
        props = {}
        required = []
        for p in t["parameters"]:
            ptype = p.get("type", "string")
            json_type = "number" if ptype in ("int", "float", "number") else "string"
            props[p["name"]] = {"type": json_type, "description": p.get("description", "")}
            if p.get("required", True):
                required.append(p["name"])
        out.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": {
                    "type": "object",
                    "properties": props,
                    "required": required,
                },
            },
        })
    return out


def _confine(p: str) -> Path:
    """Resolve a path inside the sandbox; refuse to escape it."""
    target = (SANDBOX / p).resolve()
    if SANDBOX not in target.parents and target != SANDBOX:
        raise ValueError(f"path '{p}' escapes sandbox")
    return target


# --------------------------------------------------------------------------
# Web: search + fetch (server has outbound egress)
# --------------------------------------------------------------------------
async def _web_search(args: dict) -> dict:
    q = (args.get("query") or "").strip()
    if not q:
        return {"error": "missing query"}
    try:
        from services import get_client
        r = await get_client().post(
            "https://html.duckduckgo.com/html/",
            data={"q": q},
            headers={"User-Agent": "Mozilla/5.0 VirusGPT"},
            timeout=12.0,
        )
        text = r.text
        # Pull the result titles + snippets.
        items = re.findall(r'result__a"[^>]*>(.*?)</a>', text, re.S)
        snips = re.findall(r'result__snippet"[^>]*>(.*?)</a>', text, re.S)
        def clean(h): return re.sub(r"<[^>]+>", "", h).strip()
        results = [{"title": clean(i), "snippet": clean(s)} for i, s in zip(items, snips)]
        return {"query": q, "results": results[:5]}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"search failed: {exc}"}


async def _web_fetch(args: dict) -> dict:
    url = (args.get("url") or "").strip()
    if not url:
        return {"error": "missing url"}
    try:
        from services import get_client
        r = await get_client().get(url, timeout=15.0, follow_redirects=True,
                                   headers={"User-Agent": "Mozilla/5.0 VirusGPT"})
        body = r.text
        # Crude HTML -> text: drop scripts/styles, keep text nodes.
        body = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", body)
        body = re.sub(r"(?is)<[^>]+>", " ", body)
        body = re.sub(r"\s+", " ", body).strip()
        return {"url": url, "title": re.search(r"<title>(.*?)</title>", r.text, re.S) and re.search(r"<title>(.*?)</title>", r.text, re.S).group(1).strip() or url, "text": body[:4000]}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"fetch failed: {exc}"}


# --------------------------------------------------------------------------
# Local shell (sandboxed + allowlisted)
# --------------------------------------------------------------------------
async def _shell(args: dict) -> dict:
    cmd = (args.get("command") or "").strip()
    if not cmd:
        return {"error": "missing command"}
    # Disallow obvious escape attempts.
    if any(tok in cmd for tok in ("; rm -rf", "sudo", ">> /", "chmod 777", "&>/dev/null")):
        return {"error": "command blocked by policy"}
    first = cmd.split()[0].split("/")[-1]
    if first not in _SHELL_ALLOW:
        return {"error": f"binary '{first}' not in allowlist"}
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=str(SANDBOX),
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=SHELL_TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            return {"error": f"command timed out after {SHELL_TIMEOUT}s"}
        return {
            "command": cmd,
            "stdout": (out or b"").decode(errors="replace")[:3000],
            "stderr": (err or b"").decode(errors="replace")[:1000],
            "returncode": proc.returncode,
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": f"shell error: {exc}"}


# --------------------------------------------------------------------------
# File read / write (sandboxed)
# --------------------------------------------------------------------------
async def _read_file(args: dict) -> dict:
    p = (args.get("path") or "").strip()
    if not p:
        return {"error": "missing path"}
    try:
        target = _confine(p)
        if not target.exists():
            return {"error": "file not found"}
        return {"path": str(target), "content": target.read_text(errors="replace")[:4000]}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


async def _write_file(args: dict) -> dict:
    p = (args.get("path") or "").strip()
    content = args.get("content") or ""
    if not p:
        return {"error": "missing path"}
    try:
        target = _confine(p)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return {"path": str(target), "bytes": len(content.encode())}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


# --------------------------------------------------------------------------
# Memory query (shared OKF graph)
# --------------------------------------------------------------------------
async def _memory_query(args: dict) -> dict:
    q = (args.get("question") or "").strip()
    if not q:
        return {"error": "missing question"}
    try:
        txt = await mem.memory_query(q, cfg.CONFIG.get("memory_mcp", {}).get("url", ""))
        return {"question": q, "answer": (txt or "")[:2000]}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


# --------------------------------------------------------------------------
# Calculator
# --------------------------------------------------------------------------
async def _calc(args: dict) -> dict:
    expr = (args.get("expression") or "").strip()
    if not expr:
        return {"error": "missing expression"}
    if not re.fullmatch(r"[0-9+\-*/().\s%^]+", expr):
        return {"error": "only arithmetic allowed"}
    try:
        val = eval(expr, {"__builtins__": {}}, {"math": math})
        return {"expression": expr, "result": val}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


# --------------------------------------------------------------------------
# Git: commit sandbox work into the local repo ("update repo" step)
# --------------------------------------------------------------------------
async def _git_commit(args: dict) -> dict:
    """Commit any new/changed files under the sandbox into the local git repo.
    This is how an agent 'publishes' its verified output (artifacts, notes)."""
    message = (args.get("message") or "agent: update repo with mission output").strip()
    try:
        repo_root = Path(__file__).resolve().parent.parent
        # Only ever commit what's inside the sandbox dir (safe by construction).
        proc = await asyncio.create_subprocess_exec(
            "git", "add", "-A", str(SANDBOX),
            cwd=str(repo_root),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=20)
        proc = await asyncio.create_subprocess_exec(
            "git", "commit", "-q", "-m", message,
            cwd=str(repo_root),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=20)
        if proc.returncode != 0:
            e = err.decode(errors="replace").strip()
            if "nothing to commit" in e:
                return {"status": "noop", "message": "nothing to commit"}
            return {"error": e[:500]}
        return {"status": "committed", "message": message}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


# --------------------------------------------------------------------------
# Register
# --------------------------------------------------------------------------
async def _render_image(args: dict) -> dict:
    """Generate an image with ComfyUI (the LAN diffusion engine at 10.0.0.120:8188).

    Returns a result dict; on success the agent gets a local URL it can show the
    user. Degrades gracefully to an error dict if ComfyUI is unreachable.
    """
    prompt = (args.get("prompt") or "").strip()
    if not prompt:
        return {"error": "missing prompt"}
    try:
        from services import comfyui as c
        res = await c.render_image(
            prompt,
            model=(args.get("model") or None),
            negative_prompt=(args.get("negative_prompt") or ""),
            steps=int(args.get("steps") or 25),
            cfg_scale=float(args.get("cfg_scale") or 7.0),
            width=int(args.get("width") or 1024),
            height=int(args.get("height") or 1024),
        )
        return res
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "error": f"render_image error: {exc}"}


async def _ask_hermes(args: dict) -> dict:
    """Relay a message to the Hermes Agent over A2A and return its reply.

    Thin async wrapper around the Hermes A2A client. Returns the client's dict
    ({'status':'ok','reply':...} or {'status':'failed','error':...}) unchanged
    so the model sees exactly what the peer answered.
    """
    text = (args.get("message") or "").strip()
    if not text:
        return {"error": "missing message"}
    try:
        from services import hermes as h
        return await h.ask_hermes(text)
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "error": f"ask_hermes error: {exc}"}


# --------------------------------------------------------------------------
# Planner interview: ask a clarifying question (persistent record)
# --------------------------------------------------------------------------
async def _ask_interview_question(args: dict) -> dict:
    """Create a persistent interview question for the team/human to answer.

    The Planner uses this before planning to clarify mission specs. The
    question is stored server-side (SQLite) so it survives page reloads and
    can be answered by other team agents or a human.
    """
    question = (args.get("question") or "").strip()
    if not question:
        return {"error": "missing question"}
    try:
        from autonomous.database import Question
        from server import _auto_repo
        q = Question(
            mission_id=(args.get("mission_id") or None),
            room_id=(args.get("room_id") or None),
            question=question,
            asked_by=(args.get("asked_by") or "planner"),
        )
        _auto_repo.create_question(q)
        return {"status": "created", "question_id": q.id, "question": question}
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "error": f"ask_interview_question error: {exc}"}


# --------------------------------------------------------------------------
# Database query (read-only SELECT against the app database)
# --------------------------------------------------------------------------
async def _db_query(args: dict) -> dict:
    """Run a read-only SELECT against the app's own database.

    Guards: statement must start with SELECT (or WITH...SELECT), no semicolon
    stacking, results capped. Never allows DML/DDL.
    """
    sql = (args.get("sql") or "").strip().rstrip(";").strip()
    if not sql:
        return {"error": "missing sql"}
    lowered = sql.lower()
    if ";" in sql:
        return {"error": "only a single statement is allowed"}
    if not (lowered.startswith("select") or lowered.startswith("with")):
        return {"error": "only SELECT queries are permitted (read-only tool)"}
    if any(w in lowered for w in ("insert ", "update ", "delete ", "drop ", "alter ",
                                  "attach ", "pragma ", "vacuum", "into outfile")):
        return {"error": "statement contains a disallowed keyword (read-only tool)"}
    try:
        from autonomous import database as db
        import sqlite3
        conn = sqlite3.connect(str(db.DB_PATH), timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.execute(sql)
            rows = [dict(r) for r in cur.fetchmany(200)]
            return {"sql": sql, "row_count": len(rows), "rows": rows}
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"db_query failed: {exc}"}


# --------------------------------------------------------------------------
# TTS output (speak text through the configured TTS service)
# --------------------------------------------------------------------------
async def _tts_speak(args: dict) -> dict:
    """Synthesize speech for `text` using the configured TTS service.

    Returns a sandboxed audio filename the UI/user can play. Does NOT auto-play.
    """
    text = (args.get("text") or "").strip()
    if not text:
        return {"error": "missing text"}
    tts_cfg = cfg.CONFIG.get("tts", {})
    base_url = tts_cfg.get("base_url") or ""
    if not tts_cfg.get("enabled", True) or not base_url:
        return {"status": "not_configured",
                "error": "TTS service is not configured (set the TTS base URL in Settings)"}
    try:
        from services import tts as _tts
        voice = args.get("voice") or tts_cfg.get("default_voice") or "nova"
        audio = await _tts.synthesize(text[:2000], voice, base_url,
                                      timeout=float(tts_cfg.get("timeout", 120)))
        if not audio:
            return {"status": "failed", "error": "TTS service returned no audio"}
        import hashlib
        fname = "tts-" + hashlib.sha1((voice + text).encode()).hexdigest()[:10] + ".mp3"
        (SANDBOX / fname).write_bytes(audio)
        return {"status": "ok", "file": fname, "voice": voice, "bytes": len(audio)}
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "error": f"tts_speak error: {exc}"}


# --------------------------------------------------------------------------
# STT transcribe (audio file in the sandbox -> text via Whisper service)
# --------------------------------------------------------------------------
async def _stt_transcribe(args: dict) -> dict:
    """Transcribe an audio file that lives in the agent sandbox."""
    p = (args.get("path") or "").strip()
    if not p:
        return {"error": "missing path (audio file relative to the sandbox)"}
    stt_cfg = cfg.CONFIG.get("stt", {})
    base_url = stt_cfg.get("base_url") or ""
    if not stt_cfg.get("enabled", True) or not base_url:
        return {"status": "not_configured",
                "error": "STT service is not configured (set the STT base URL in Settings)"}
    try:
        target = _confine(p)
        if not target.exists():
            return {"error": "audio file not found in sandbox"}
        from services import stt as _stt
        mime = "audio/wav" if target.suffix == ".wav" else "audio/mpeg" if target.suffix in (".mp3", ".m4a") else "audio/webm"
        res = await _stt.transcribe(target.read_bytes(), mime, base_url,
                                    timeout=float(stt_cfg.get("timeout", 60)))
        if not res:
            return {"status": "failed", "error": "STT service unavailable or returned nothing"}
        return {"status": "ok", "file": p, "text": res.get("text", "")}
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "error": f"stt_transcribe error: {exc}"}


# --------------------------------------------------------------------------
# Export report (markdown artifact written into the sandbox)
# --------------------------------------------------------------------------
async def _export_report(args: dict) -> dict:
    """Write a Markdown report file into the sandbox and return its path."""
    title = (args.get("title") or "report").strip()
    body = args.get("content") or ""
    if not body:
        return {"error": "missing content"}
    try:
        safe = re.sub(r"[^A-Za-z0-9 _-]", "", title).strip().replace(" ", "-") or "report"
        fname = f"reports/{safe}.md"
        target = _confine(fname)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# {title}\n\n{body}\n")
        return {"status": "ok", "file": fname, "bytes": target.stat().st_size}
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "error": f"export_report error: {exc}"}


# --------------------------------------------------------------------------
# Structured notes + task board (reuse the autonomous DB)
# --------------------------------------------------------------------------
async def _note_create(args: dict) -> dict:
    """Save a structured note into agent memory (namespaced 'notes')."""
    content = (args.get("content") or "").strip()
    if not content:
        return {"error": "missing content"}
    try:
        from autonomous.database import AgentMemory
        from server import _auto_repo
        m = AgentMemory(agent=(args.get("agent") or "notes"),
                        mission_id=(args.get("mission_id") or ""),
                        namespace="notes",
                        content=content,
                        importance=float(args.get("importance") or 0.7))
        _auto_repo.add_memory(m)
        return {"status": "ok", "note_id": m.id}
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "error": f"note_create error: {exc}"}


async def _task_board(args: dict) -> dict:
    """List or create mission tasks (the shared team task board)."""
    action = (args.get("action") or "list").strip().lower()
    try:
        from autonomous.database import Task
        from server import _auto_repo
        if action == "list":
            mid = args.get("mission_id") or ""
            if mid:
                tasks = _auto_repo.list_mission_tasks(mid)
            else:
                tasks = []
                for m in _auto_repo.list_missions(limit=5):
                    tasks.extend(_auto_repo.list_mission_tasks(m.id))
            return {"status": "ok", "tasks": [
                {"id": t.id, "mission_id": t.mission_id, "title": t.title,
                 "status": t.status, "agent": t.agent, "priority": t.priority}
                for t in tasks][:100]}
        elif action == "create":
            title = (args.get("title") or "").strip()
            if not title:
                return {"error": "missing title"}
            t = Task(mission_id=(args.get("mission_id") or ""),
                     title=title,
                     description=(args.get("description") or ""),
                     agent=(args.get("agent") or ""),
                     priority=int(args.get("priority") or 60))
            _auto_repo.create_task(t)
            return {"status": "ok", "task_id": t.id}
        elif action == "update":
            tid = (args.get("task_id") or "").strip()
            t = _auto_repo.get_task(tid)
            if not t:
                return {"error": "task not found"}
            if args.get("status"):
                t.status = str(args["status"])
            if args.get("agent"):
                t.agent = str(args["agent"])
            _auto_repo.update_task(t)
            return {"status": "ok", "task_id": t.id, "task_status": t.status}
        return {"error": f"unknown action '{action}' (list|create|update)"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "error": f"task_board error: {exc}"}


# --------------------------------------------------------------------------
# Draft message (compose ONLY -- never sends)
# --------------------------------------------------------------------------
_drafts: Dict[str, dict] = {}

async def _draft_message(args: dict) -> dict:
    """Compose a message draft and store it for human review.

    HARD RULE: this tool never sends anything. It returns the draft text and a
    draft id so a human can review and send it manually.
    """
    to = (args.get("to") or "").strip()
    body = (args.get("body") or "").strip()
    subject = (args.get("subject") or "").strip()
    if not body:
        return {"error": "missing body"}
    draft_id = "D-" + uuid.uuid4().hex[:8]
    _drafts[draft_id] = {"to": to, "subject": subject, "body": body,
                         "created_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    return {"status": "drafted", "draft_id": draft_id, "to": to,
            "subject": subject, "body": body,
            "note": "DRAFT ONLY -- nothing was sent. A human must review and send it."}


# --------------------------------------------------------------------------
# Fact check (web search + cross-source comparison)
# --------------------------------------------------------------------------
async def _fact_check(args: dict) -> dict:
    """Search the web for a claim and return sources to cross-check it."""
    claim = (args.get("claim") or "").strip()
    if not claim:
        return {"error": "missing claim"}
    try:
        search = await _web_search({"query": claim})
        results = (search or {}).get("results") or []
        sources = []
        for r in results[:4]:
            sources.append({"title": r.get("title", ""), "snippet": r.get("snippet", "")})
        return {"claim": claim, "source_count": len(sources), "sources": sources,
                "note": "Compare the sources above. If they disagree, say so; do not pick a winner without evidence."}
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "error": f"fact_check error: {exc}"}


# --------------------------------------------------------------------------
# Doc compare (diff + summarize two sandbox files)
# --------------------------------------------------------------------------
async def _doc_compare(args: dict) -> dict:
    """Diff two text files inside the sandbox and summarize the changes."""
    a = (args.get("file_a") or "").strip()
    b = (args.get("file_b") or "").strip()
    if not a or not b:
        return {"error": "missing file_a or file_b"}
    try:
        import difflib
        ta = _confine(a)
        tb = _confine(b)
        if not ta.exists() or not tb.exists():
            return {"error": "one or both files not found in sandbox"}
        la = ta.read_text(errors="replace").splitlines()
        lb = tb.read_text(errors="replace").splitlines()
        diff = list(difflib.unified_diff(la, lb, fromfile=a, tofile=b, lineterm=""))
        added = sum(1 for d in diff if d.startswith("+") and not d.startswith("+++"))
        removed = sum(1 for d in diff if d.startswith("-") and not d.startswith("---"))
        return {"file_a": a, "file_b": b, "lines_added": added,
                "lines_removed": removed, "identical": not diff,
                "diff": "\n".join(diff[:200])[:6000]}
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "error": f"doc_compare error: {exc}"}


# --------------------------------------------------------------------------
# Honest stubs: real-time data feed + calendar (no backend configured)
# --------------------------------------------------------------------------
async def _realtime_feed(args: dict) -> dict:
    """Real-time data feed. Honest stub until a feed backend is configured."""
    feed_cfg = cfg.CONFIG.get("realtime_feed", {})
    url = feed_cfg.get("base_url") or ""
    if not url:
        return {"status": "not_configured",
                "error": "No real-time data feed backend is configured. "
                         "Set services.realtime_feed.base_url to enable live data. "
                         "Use web_search for current public information instead."}
    try:
        from services import get_client
        r = await get_client().get(url.rstrip("/") + "/feed",
                                   params={"topic": args.get("topic", "")},
                                   timeout=10.0)
        return {"status": "ok", "data": r.json()}
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "error": f"realtime_feed error: {exc}"}


async def _calendar_helper(args: dict) -> dict:
    """Calendar helper. Honest stub until a calendar backend is configured."""
    cal_cfg = cfg.CONFIG.get("calendar", {})
    if not (cal_cfg.get("base_url") or cal_cfg.get("connected")):
        return {"status": "not_configured",
                "error": "No calendar backend is connected. "
                         "Connect a calendar service in Settings to enable scheduling. "
                         "Do not invent events or availability."}
    return {"status": "not_configured",
            "error": "Calendar backend is configured but the integration is not implemented yet."}


# -------------------------------------------------------------------------
# Register
# -------------------------------------------------------------------------
register(
    "web_search", "Search the web and return the top results (title + snippet).",
    [{"name": "query", "type": "string", "description": "search query"}],
    _web_search,
)
register(
    "web_fetch", "Fetch a URL and return its cleaned text content.",
    [{"name": "url", "type": "string", "description": "fully-qualified http(s) URL"}],
    _web_fetch,
)
register(
    "shell", "Run a sandboxed shell command (allowlisted binaries, read-only-ish, timeout).",
    [{"name": "command", "type": "string", "description": "shell command to run"}],
    _shell,
)
register(
    "read_file", "Read a text file inside the agent sandbox.",
    [{"name": "path", "type": "string", "description": "path relative to the sandbox"}],
    _read_file,
)
register(
    "write_file", "Write text content to a file inside the agent sandbox.",
    [{"name": "path", "type": "string", "description": "path relative to the sandbox"},
     {"name": "content", "type": "string", "description": "file contents"}],
    _write_file,
)
register(
    "memory_query", "Ask the shared long-term memory (OKF graph) a question.",
    [{"name": "question", "type": "string", "description": "natural-language question"}],
    _memory_query,
)
register(
    "calc", "Evaluate a safe arithmetic expression.",
    [{"name": "expression", "type": "string", "description": "e.g. '(12*8)/3'"}],
    _calc,
)
register(
    "git_commit", "Commit new/changed files from the agent sandbox into the local git repo (publish verified output).",
    [{"name": "message", "type": "string", "description": "commit message"}],
    _git_commit,
)
register(
    "render_image", "Generate an image from a text prompt using ComfyUI (LAN diffusion engine). Returns a local URL on success.",
    [
        {"name": "prompt", "type": "string", "description": "image description / prompt"},
        {"name": "negative_prompt", "type": "string", "description": "optional things to avoid", "required": False},
        {"name": "model", "type": "string", "description": "optional checkpoint name (auto-detected if omitted)", "required": False},
        {"name": "steps", "type": "int", "description": "sampling steps (default 25)", "required": False},
        {"name": "cfg_scale", "type": "float", "description": "CFG scale (default 7.0)", "required": False},
        {"name": "width", "type": "int", "description": "image width (default 1024)", "required": False},
        {"name": "height", "type": "int", "description": "image height (default 1024)", "required": False},
    ],
    _render_image,
)

register(
    "ask_hermes",
    "Relay a message to the Hermes Agent over A2A and return its reply verbatim. "
    "ALWAYS use this for the Hermes persona: pass the user's exact message as 'message'. "
    "Returns {'status':'ok','reply':...} or {'status':'failed','error':...}.",
    [{"name": "message", "type": "string",
      "description": "the exact message to send to the Hermes agent"}],
    _ask_hermes,
)

register(
    "ask_interview_question",
    "Planner interview tool: ask a clarifying question to better understand plan specs. "
    "The question is stored persistently and other team agents (or a human) can answer it. "
    "Use BEFORE planning when the mission goal is ambiguous.",
    [{"name": "question", "type": "string", "description": "the clarifying question to ask"},
     {"name": "room_id", "type": "string", "description": "team round/room id", "required": False},
     {"name": "mission_id", "type": "string", "description": "mission id if known", "required": False},
     {"name": "asked_by", "type": "string", "description": "persona name asking", "required": False}],
    _ask_interview_question,
)

register(
    "db_query",
    "Run a READ-ONLY SELECT query against the app database (virusgpt.db). "
    "Only SELECT statements are allowed; tables include missions, tasks, events, sessions, questions, agent_memory.",
    [{"name": "sql", "type": "string", "description": "a single SELECT statement"}],
    _db_query,
)

register(
    "tts_speak",
    "Convert text to speech audio using the configured TTS service. "
    "Returns a sandbox audio filename; the human plays it -- nothing auto-plays.",
    [{"name": "text", "type": "string", "description": "text to speak"},
     {"name": "voice", "type": "string", "description": "voice name (default from settings)", "required": False}],
    _tts_speak,
)

register(
    "stt_transcribe",
    "Transcribe an audio file (in the agent sandbox) to text using the configured Whisper STT service.",
    [{"name": "path", "type": "string", "description": "audio file path relative to the sandbox"}],
    _stt_transcribe,
)

register(
    "export_report",
    "Write a Markdown report file (reports/<title>.md) into the sandbox and return its path.",
    [{"name": "title", "type": "string", "description": "report title"},
     {"name": "content", "type": "string", "description": "markdown report body"}],
    _export_report,
)

register(
    "note_create",
    "Save a structured note to shared agent memory (namespace 'notes') for later recall.",
    [{"name": "content", "type": "string", "description": "note text"},
     {"name": "agent", "type": "string", "description": "author persona name", "required": False},
     {"name": "importance", "type": "float", "description": "0..1 importance (default 0.7)", "required": False}],
    _note_create,
)

register(
    "task_board",
    "Shared team task board: action 'list' shows tasks, 'create' adds one, 'update' changes status/agent.",
    [{"name": "action", "type": "string", "description": "list | create | update"},
     {"name": "mission_id", "type": "string", "description": "mission id (list/create)", "required": False},
     {"name": "title", "type": "string", "description": "task title (create)", "required": False},
     {"name": "description", "type": "string", "description": "task description (create)", "required": False},
     {"name": "task_id", "type": "string", "description": "task id (update)", "required": False},
     {"name": "status", "type": "string", "description": "new status (update)", "required": False},
     {"name": "agent", "type": "string", "description": "assigned agent (create/update)", "required": False}],
    _task_board,
)

register(
    "draft_message",
    "Compose an email/message DRAFT for human review. NEVER sends anything -- it only returns the draft text and id.",
    [{"name": "to", "type": "string", "description": "recipient label"},
     {"name": "subject", "type": "string", "description": "subject line", "required": False},
     {"name": "body", "type": "string", "description": "message body"}],
    _draft_message,
)

register(
    "fact_check",
    "Verify a factual claim: web-searches it and returns sources to cross-compare. Report agreement/disagreement honestly.",
    [{"name": "claim", "type": "string", "description": "the claim to verify"}],
    _fact_check,
)

register(
    "doc_compare",
    "Diff two text files inside the sandbox and summarize added/removed lines.",
    [{"name": "file_a", "type": "string", "description": "first file (sandbox-relative)"},
     {"name": "file_b", "type": "string", "description": "second file (sandbox-relative)"}],
    _doc_compare,
)

register(
    "realtime_feed",
    "Real-time data feed (news/prices/events). Returns 'not_configured' until a feed backend is set in Settings -- "
    "then use web_search for current public info instead. Never fabricate live data.",
    [{"name": "topic", "type": "string", "description": "feed topic to fetch", "required": False}],
    _realtime_feed,
)

register(
    "calendar_helper",
    "Calendar scheduling helper. Returns 'not_configured' until a calendar backend is connected in Settings. "
    "Never invent events or availability.",
    [{"name": "action", "type": "string", "description": "list | create | free_busy", "required": False}],
    _calendar_helper,
)


# -------------------------------------------------------------------------
# Tool-call parser (ReAct protocol)
# -------------------------------------------------------------------------
_TOOL_BLOCK = re.compile(r"```tool\s*(\{.*?\})\s*```", re.S)


def parse_tool_call(text: str) -> dict | None:
    """Extract the first ```tool {json}``` block from agent text, or None."""
    m = _TOOL_BLOCK.search(text)
    if not m:
        return None
    try:
        obj = json.loads(m.group(1))
        if "tool" not in obj or "args" not in obj:
            return None
        return obj
    except Exception:
        return None


async def run_tool(name: str, args: dict) -> dict:
    tool = TOOLS.get(name)
    if not tool:
        return {"error": f"unknown tool '{name}'"}
    try:
        return await tool["handler"](args or {})
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
