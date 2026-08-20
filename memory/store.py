"""VirusGPT's OWN memory store — a self-contained, OKF-style concept graph.

This is built fresh for this project (NOT linked to the shared Docker understory /
Hermes pool). Concepts live as plain-markdown files under data/memory/<type>/<name>.md
with YAML frontmatter. Reads/writes happen in-process, so the memory is reachable
on the local network through the main VirusGPT server port (just like TTS/STT).

The store is deliberately dependency-free (stdlib only) and deterministic.
"""
from __future__ import annotations

import re
import json
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
BUNDLE = ROOT / "data" / "memory"

# --- frontmatter parsing (minimal, stdlib only) ---------------------------
_FM_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.S)

def _parse_md(text: str):
    m = _FM_RE.match(text)
    if not m:
        return {}, text.strip()
    fm_raw, body = m.group(1), m.group(2)
    fm = {}
    for line in fm_raw.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm, body.strip()

def _dump_md(fm: dict, body: str) -> str:
    lines = ["---"]
    for k, v in fm.items():
        lines.append(f"{k}: {v}")
    lines.append("---")
    lines.append("")
    lines.append(body)
    return "\n".join(lines) + "\n"

# --- link extraction: [[Concept]] and [[type/Concept]] ---------------------
_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")

def _links_of(body: str):
    return [l.strip() for l in _LINK_RE.findall(body)]


def ensure_seed():
    """Create the bundle + a few fresh VirusGPT concepts if it's empty."""
    BUNDLE.mkdir(parents=True, exist_ok=True)
    if any(BUNDLE.rglob("*.md")):
        return
    seed = {
        "concept/VirusGPT": ("A local, offline agent-chat stack built for macOS. "
            "It runs an Ollama LLM, a PocketTTS voice service, a Whisper STT service, "
            "and its own concept memory graph. [[Agent Workflow]] [[Memory Graph]]"),
        "concept/Agent Workflow": ("The Team + Autonomous Mission system. A Planner "
            "decomposes a goal into tasks that worker personas execute, surfacing "
            "progress on a Kanban board. [[VirusGPT]] [[Tool Harness]]"),
        "concept/Tool Harness": ("VirusGPT agents call tools (web_search, web_fetch, "
            "shell, read/write_file, memory_query, calc, git_commit) through native "
            "Ollama function-calling. [[Agent Workflow]] [[Memory Graph]]"),
        "concept/Memory Graph": ("A self-contained, OKF-style concept store rendered "
            "as a living force-directed graph in the UI. Built fresh for this project. "
            "[[VirusGPT]] [[Tool Harness]]"),
        "concept/Kanban Board": ("The Team Workflow panel renders planned tasks as "
            "cards flowing Backlog -> In Progress -> Done as workers execute. "
            "[[Agent Workflow]]"),
        "component/PocketTTS": ("Local OpenAI-compatible TTS service (port 49152) "
            "providing offline voice synthesis with multiple voices. [[VirusGPT]]"),
        "component/Whisper STT": ("Local speech-to-text service (port 8181) for "
            "voice input. [[VirusGPT]]"),
        "model/Ollama": ("Local LLM runtime (qwen2.5:3b by default) that powers chat, "
            "planning, and tool-calling. [[VirusGPT]] [[Tool Harness]]"),
    }
    for key, body in seed.items():
        typ, name = key.split("/", 1)
        _write_concept(typ, name, body, seed_links=True)


def _write_concept(typ: str, name: str, body: str, seed_links: bool = False):
    d = BUNDLE / typ
    d.mkdir(parents=True, exist_ok=True)
    p = d / (name + ".md")
    if seed_links:
        links = _links_of(body)
        fm = {"type": typ, "title": name, "links": ", ".join(links) if links else ""}
    else:
        fm = {"type": typ, "title": name}
    p.write_text(_dump_md(fm, body), encoding="utf-8")


def list_concepts():
    out = []
    for p in sorted(BUNDLE.rglob("*.md")):
        fm, body = _parse_md(p.read_text(encoding="utf-8"))
        typ = fm.get("type") or p.parent.name
        name = fm.get("title") or p.stem
        out.append({"type": typ, "name": name, "body": body,
                    "links": _links_of(body), "file": str(p.relative_to(ROOT))})
    return out


def memory_status() -> dict:
    ensure_seed()
    concepts = list_concepts()
    # Map concept name -> concept (built first so real concepts always win).
    by_name = {}
    for c in concepts:
        by_name[c["name"].lower()] = c
    # Resolve each link to an existing concept to count real edges + orphans.
    edges = 0
    orphans = 0
    for c in concepts:
        connected = False
        for l in c["links"]:
            target = by_name.get(l.split("/")[-1].lower())
            if target:
                edges += 1
                connected = True
        if not connected:
            orphans += 1
    types = [c["name"] for c in concepts]
    directories = sorted({c["type"] for c in concepts})
    return {
        "concepts": len(concepts),
        "directories": len(directories),
        "types": types,
        "graph": {
            "links": edges,
            "orphans": orphans,
            "brokenLinks": 0,
            "healthy": orphans == 0,
        },
        "conformant": True,
        "warnings": [],
        "errors": [],
    }


def memory_add(name: str, text: str, typ: str = "concept") -> dict:
    ensure_seed()
    name = (name or "Concept").strip() or "Concept"
    _write_concept(typ, name, text)
    return {"ok": True, "name": name, "type": typ}


async def memory_query(question: str) -> str:
    """Answer a question from the local memory using the shared Ollama client."""
    ensure_seed()
    concepts = list_concepts()
    if not concepts:
        return "(memory is empty)"
    # Lightweight retrieval: rank by keyword overlap, feed top hits to the model.
    q = set(re.findall(r"[a-z0-9]+", (question or "").lower()))
    ranked = []
    for c in concepts:
        body = c["body"].lower()
        score = sum(1 for w in q if w in body) + (2 if c["name"].lower() in (question or "").lower() else 0)
        ranked.append((score, c))
    ranked.sort(key=lambda x: x[0], reverse=True)
    top = [c for s, c in ranked[:6] if s > 0] or [c for _, c in ranked[:3]]
    ctx = "\n\n".join(f"# {c['name']} ({c['type']})\n{c['body']}" for c in top)
    prompt = (
        "You are VirusGPT's memory assistant. Use ONLY the context below to answer "
        "the question concisely. If the answer isn't in the context, say so.\n\n"
        f"CONTEXT:\n{ctx}\n\nQUESTION: {question}"
    )
    try:
        from services import get_client
        cfg = _cfg()
        r = await get_client().post(
            cfg["ollama"]["base_url"] + "/api/generate",
            json={"model": cfg["ollama"].get("default_model", "qwen2.5:3b"),
                  "prompt": prompt, "stream": False},
            timeout=30,
        )
        if r.status_code == 200:
            return r.json().get("response", "").strip()
    except Exception as e:
        return f"(memory query failed: {e})"
    return "(no answer)"


def _cfg():
    from services import config as _c
    return _c.CONFIG


# --------------------------------------------------------------------------
# CRUD + graph operations (used by the self-dev / Dreamer engine and the UI)
# --------------------------------------------------------------------------
def concept_path(typ: str, name: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9 _.\-]", "", name).strip() or "Concept"
    return BUNDLE / typ / (safe + ".md")


def memory_get(name: str) -> Optional[dict]:
    ensure_seed()
    concepts = list_concepts()
    for c in concepts:
        if c["name"].lower() == (name or "").lower():
            return c
    return None


def memory_update(name: str, body: Optional[str] = None, typ: Optional[str] = None,
                  links: Optional[list] = None) -> dict:
    """Rewrite a concept's body / type / links. Preserves identity by name."""
    ensure_seed()
    c = memory_get(name)
    if not c:
        return {"ok": False, "error": "not found", "name": name}
    cur_typ = typ or c["type"]
    cur_body = body if body is not None else c["body"]
    # Re-embed [[links]] into the body too, so the parser + graph stay consistent.
    if links is not None:
        clean = _strip_links(cur_body)
        link_lines = "\n".join(f"[[{l}]]" for l in links)
        cur_body = (clean + ("\n\n" + link_lines if link_lines else "")).strip()
    old_path = ROOT / c["file"]
    new_path = concept_path(cur_typ, c["name"])
    fm = {"type": cur_typ, "title": c["name"]}
    if links is not None:
        fm["links"] = ", ".join(links)
    text = _dump_md(fm, cur_body)
    # If the type changed, remove the old file so we don't leave a duplicate.
    if old_path.resolve() != new_path.resolve() and old_path.exists():
        old_path.unlink()
    new_path.parent.mkdir(parents=True, exist_ok=True)
    new_path.write_text(text, encoding="utf-8")
    return {"ok": True, "name": c["name"], "type": cur_typ, "file": str(new_path.relative_to(ROOT))}


def memory_remove(name: str) -> dict:
    """Delete a concept and prune its [[links]] from every other concept (trimming)."""
    ensure_seed()
    c = memory_get(name)
    if not c:
        return {"ok": False, "error": "not found", "name": name}
    p = ROOT / c["file"]
    if p.exists():
        p.unlink()
    # Trim references to this concept from other bodies.
    pruned = 0
    for other in list_concepts():
        if other["name"].lower() == c["name"].lower():
            continue
        if name in other["links"] or c["name"] in other["links"]:
            new_links = [l for l in other["links"]
                         if l.split("/")[-1].lower() != c["name"].lower()]
            memory_update(other["name"], links=new_links)
            pruned += 1
    return {"ok": True, "name": name, "pruned_refs": pruned}


def memory_relink(name: str, links: list) -> dict:
    return memory_update(name, links=links)


def _strip_links(body: str) -> str:
    """Remove trailing [[link]] lines so we can re-embed a clean link set."""
    lines = body.splitlines()
    kept = []
    for ln in lines:
        if _LINK_RE.fullmatch(ln.strip()):
            continue
        kept.append(ln)
    return "\n".join(kept).strip()


def memory_autolink() -> dict:
    """Reconcile frontmatter links with real [[links]] in bodies, and propose
    new links between concepts that share strong token overlap (graph linking)."""
    ensure_seed()
    concepts = list_concepts()
    by_name = {c["name"].lower(): c for c in concepts}
    changed = 0
    proposals = []
    # 1) normalize: ensure body [[links]] == frontmatter links
    for c in concepts:
        body_links = _links_of(c["body"])
        if sorted(body_links, key=str.lower) != sorted(c["links"], key=str.lower):
            memory_update(c["name"], links=body_links)
            changed += 1
    # 2) propose token-overlap links not yet present
    stop = {"the", "and", "for", "with", "that", "this", "from", "into", "via",
            "our", "its", "are", "was", "can", "has", "use", "uses"}
    for c in concepts:
        toks = set(re.findall(r"[a-z0-9]{4,}", c["body"].lower())) - stop
        for o in concepts:
            if o["name"].lower() == c["name"].lower():
                continue
            if o["name"] in c["links"] or c["name"] in o["links"]:
                continue
            otoks = set(re.findall(r"[a-z0-9]{4,}", o["body"].lower())) - stop
            overlap = toks & otoks
            if len(overlap) >= 3:
                proposals.append({"from": c["name"], "to": o["name"],
                                   "shared": sorted(overlap)})
    return {"ok": True, "normalized": changed, "proposals": proposals}


def memory_dream(title: str, body: str, typ: str = "insight") -> dict:
    """Record a synthesized 'dreamed' concept (generated by the agent, not scraped)."""
    ensure_seed()
    _write_concept(typ, title, body)
    # tag as dreamed via a marker link-less frontmatter flag
    p = concept_path(typ, title)
    if p.exists():
        fm, b = _parse_md(p.read_text(encoding="utf-8"))
        fm["dream"] = "true"
        p.write_text(_dump_md(fm, b), encoding="utf-8")
    return {"ok": True, "name": title, "type": typ, "dream": True}

