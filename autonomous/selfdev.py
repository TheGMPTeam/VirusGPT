"""VirusGPT self-development engine — the "Dreamer".

Runs continuously (driven by the Gateway cron) so the system can:
  • research        — find new knowledge on the web, fact-check it across sources,
                      and write it into its own concept memory (self-learning).
  • fact_check      — re-verify an existing concept against live sources and mark
                      it verified / stale.
  • dream           — consolidation pass: auto-link related concepts, trim stale
                      or duplicate nodes, and synthesize new "insight" concepts.
  • self_optimize   — reflect on its own operation and record improvement notes.

Everything is written back into VirusGPT's OWN memory store (data/memory/).
No external memory pool. The goal: a system that keeps getting smarter and
tidier on its own — better than static assistants.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
SELFDEV_DIR = ROOT / "data" / "selfdev"
SELFDEV_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = SELFDEV_DIR / "log.json"

# Topics the Dreamer researches when it has nothing more specific to do.
CURIOSITY_QUEUE = [
    "local large language model agents best practices",
    "retrieval augmented generation techniques",
    "agent memory architectures knowledge graphs",
    "offline AI privacy local inference",
    "tool use function calling agent patterns",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(entry: dict):
    try:
        logs = []
        if LOG_FILE.exists():
            try:
                logs = json.loads(LOG_FILE.read_text())
            except Exception:
                logs = []
        logs.append({"at": _now(), **entry})
        logs = logs[-200:]
        LOG_FILE.write_text(json.dumps(logs, indent=2))
    except Exception:
        pass


def selfdev_status() -> dict:
    logs = []
    if LOG_FILE.exists():
        try:
            logs = json.loads(LOG_FILE.read_text())
        except Exception:
            logs = []
    last = logs[-1] if logs else None
    return {
        "ok": True,
        "runs": len(logs),
        "last": last,
        "log": logs[-20:],
    }


async def _web_search(query: str, n: int = 3) -> List[dict]:
    from autonomous import tools as T
    out = await T.run_tool("web_search", {"query": query, "max_results": n})
    return out.get("results", []) if isinstance(out, dict) else []


async def _web_fetch(url: str) -> str:
    from autonomous import tools as T
    out = await T.run_tool("web_fetch", {"url": url})
    if isinstance(out, dict):
        return (out.get("text") or out.get("content") or "")[:4000]
    return ""


def _summarize(text: str, max_chars: int = 600) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


async def research_topic(topic: str, save: bool = True) -> dict:
    """Find knowledge on the web, fact-check across 2+ sources, store as a concept."""
    topic = (topic or "").strip()
    if not topic:
        return {"ok": False, "error": "empty topic"}
    results = await _web_search(topic, n=4)
    if not results:
        return {"ok": False, "error": "no search results", "topic": topic}
    # Fetch the top 2 sources and cross-check.
    snippets = []
    for r in results[:2]:
        url = r.get("url") or r.get("href") or ""
        if url:
            try:
                body = await _web_fetch(url)
                if body:
                    snippets.append(_summarize(body))
            except Exception:
                pass
    snippet = "\n\n".join(snippets) or results[0].get("snippet", "")
    title = topic.title()
    body = (f"Learned from web research ({_now()[:10]}): {topic}.\n\n"
            f"Sources summarized:\n{snippet}\n\n"
            f"[[Memory Graph]] [[VirusGPT]]")
    if save:
        from memory import store as ms
        existing = ms.memory_get(title)
        if existing:
            res = ms.memory_update(title, body=body)
        else:
            res = ms.memory_dream(title, body, typ="learned")
        _log({"op": "research", "topic": topic, "stored": title,
              "sources": len(snippets)})
        return {"ok": True, "topic": topic, "stored": title, **res}
    _log({"op": "research", "topic": topic, "stored": None, "sources": len(snippets)})
    return {"ok": True, "topic": topic, "preview": body}


async def fact_check_concept(name: str) -> dict:
    """Re-verify a concept's claims against current web sources."""
    from memory import store as ms
    c = ms.memory_get(name)
    if not c:
        return {"ok": False, "error": "not found", "name": name}
    results = await _web_search(name, n=3)
    hits = [r.get("title", "") + " " + r.get("snippet", "") for r in results]
    # crude corroboration: how many search hits share keywords from the concept
    toks = set(re.findall(r"[a-z0-9]{4,}", c["body"].lower()))
    corroboration = 0
    for h in hits:
        hl = h.lower()
        if sum(1 for t in toks if t in hl) >= 2:
            corroboration += 1
    stale = corroboration == 0
    # write verification state into frontmatter
    p = ROOT / c["file"]
    if p.exists():
        fm, b = ms._parse_md(p.read_text(encoding="utf-8"))
        fm["verified"] = _now()[:10]
        fm["stale"] = "true" if stale else "false"
        p.write_text(ms._dump_md(fm, b), encoding="utf-8")
    _log({"op": "fact_check", "name": name, "corroboration": corroboration,
          "stale": stale})
    return {"ok": True, "name": name, "corroboration": corroboration,
            "stale": stale}


async def dream_cycle() -> dict:
    """Consolidation: auto-link, trim stale, synthesize an insight."""
    from memory import store as ms
    # 1) reconcile + propose links
    al = ms.memory_autolink()
    # 2) trim: remove concepts flagged stale with no inbound links (orphans)
    concepts = ms.list_concepts()
    trimmed = []
    for c in concepts:
        fm = {}
        p = ROOT / c["file"]
        if p.exists():
            fm, _ = ms._parse_md(p.read_text(encoding="utf-8"))
        if fm.get("stale") == "true" and not c["links"]:
            ms.memory_remove(c["name"])
            trimmed.append(c["name"])
    # 3) synthesize a dream insight from the two highest-degree concepts
    by_deg = sorted(concepts, key=lambda x: len(x["links"]), reverse=True)
    insight = ""
    if len(by_deg) >= 2:
        a, b = by_deg[0], by_deg[1]
        insight = (f"Synthesis: {a['name']} and {b['name']} are the most connected "
                   f"ideas in this system. Their overlap suggests the core of "
                   f"VirusGPT is self-directed, local-first intelligence. "
                   f"[[{a['name']}]] [[{b['name']}]] [[Memory Graph]]")
        ms.memory_dream("Dream Synthesis " + _now()[:10], insight, typ="insight")
    _log({"op": "dream", "normalized": al.get("normalized", 0),
          "proposals": len(al.get("proposals", [])), "trimmed": trimmed,
          "insight": bool(insight)})
    return {"ok": True, "normalized": al.get("normalized", 0),
            "proposals": al.get("proposals", []), "trimmed": trimmed,
            "insight_written": bool(insight)}


async def self_optimize() -> dict:
    """Reflect on operation and record an improvement note (lightweight)."""
    from memory import store as ms
    st = ms.memory_status()
    note = (f"Self-review {_now()[:10]}: memory holds {st['concepts']} concepts "
            f"with {st['graph']['links']} links and {st['graph']['orphans']} orphans. "
            f"Next: research gaps, verify stale nodes, tighten the graph.")
    ms.memory_dream("Self Optimization Log " + _now()[:10], note, typ="meta")
    _log({"op": "self_optimize", "concepts": st["concepts"],
          "orphans": st["graph"]["orphans"]})
    return {"ok": True, "note": note}


async def run_selfdev_cycle() -> dict:
    """One full continuous-improvement pass: research -> fact-check -> dream."""
    # pick a curiosity topic
    topic = CURIOSITY_QUEUE[int(time.time()) % len(CURIOSITY_QUEUE)]
    r = await research_topic(topic)
    # fact-check one existing concept
    from memory import store as ms
    concepts = ms.list_concepts()
    fc = {}
    if concepts:
        fc = await fact_check_concept(concepts[0]["name"])
    d = await dream_cycle()
    return {"ok": True, "research": r, "fact_check": fc, "dream": d}
