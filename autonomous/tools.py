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
    """Public catalog — what the UI renders as the 'tool-call list'."""
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


# --------------------------------------------------------------------------
# Tool-call parser (ReAct protocol)
# --------------------------------------------------------------------------
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
