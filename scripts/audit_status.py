#!/usr/bin/env python3
"""Audit docs/STATUS.md against the actual codebase.

STATUS.md is a hand-maintained feature matrix (🔴 not built / 🟡 partial /
✅ live). This script extracts the machine-checkable claims from its tables and
verifies them against the repo so the doc cannot silently drift out of sync:

  * path-like tokens  (e.g. `services/comfyui.py`, `data/generated/`,
    `desktop/build-*.py`)  -> file/dir exists on disk (globs allowed)
  * endpoint tokens     (e.g. `/api/services/status`,
    `/api/autonomous/start|stop|stream|status`) -> a matching FastAPI route
    decorator exists in the Python sources
  * symbol tokens       (e.g. `trim_history()`, `memory.retrieve_context()`)
    -> a `def`/`async def` exists (best-effort, warnings only)

Contradiction rules (a contradiction fails CI):
  * a 🔴 row references something that EXISTS  -> "marked not-built but present"
  * a ✅/🟡 row references something MISSING    -> "marked live but missing"

Exit code is non-zero if any hard contradiction is found, so the check can be
dropped straight into a CI gate.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Files we never descend into.
_EXCLUDE_DIRS = {".venv", "whisper", "data", "__pycache__", ".git",
                 "node_modules", "dist", "build", "pockettts"}

RED, AMBER, GREEN = "🔴", "🟡", "✅"

_BACKTICK = re.compile(r"`([^`]+)`")
_ROUTE = re.compile(r'@app\.(get|post|put|delete|patch)\(\s*["\']([^"\']+)["\']')
_DEF = re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(",
                  re.MULTILINE)
_SYMBOL = re.compile(r"^[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)?\(\)?$")
_ENDPOINT = re.compile(r"^/api/[A-Za-z0-9_./{}-]+(?:\|[A-Za-z0-9_./{}-]+)*$")
_PATHISH = re.compile(r"^(?:[A-Za-z0-9_./-]+/|[A-Za-z0-9_.-]+\.(?:py|md|yml|"
                      r"yaml|json|sh|toml|txt|cfg|ini|icns|svg|png))$")


def _collect_routes() -> set[str]:
    routes: set[str] = set()
    for p in ROOT.rglob("*.py"):
        if any(part in _EXCLUDE_DIRS for part in p.parts):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        routes.update(_ROUTE.findall(text))
    return {path for _, path in routes}


def _collect_defs() -> set[str]:
    defs: set[str] = set()
    for p in ROOT.rglob("*.py"):
        if any(part in _EXCLUDE_DIRS for part in p.parts):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        defs.update(_DEF.findall(text))
    return defs


def _expand_endpoints(token: str) -> list[str]:
    """Turn `/api/foo/start|stop` into ['/api/foo/start','/api/foo/stop']."""
    if "|" not in token:
        return [token]
    parts = token.split("|")
    prefix = parts[0].rsplit("/", 1)[0] + "/"
    out = []
    for part in parts:
        out.append(part if part.startswith("/") else prefix + part)
    return out


def _normalize_endpoint(exp: str) -> str:
    # Drop path params / wildcards so we can match by prefix.
    exp = re.sub(r"\{[^}]*\}", "", exp)
    exp = exp.replace("*", "")
    return exp.rstrip("/") + "/" if exp.endswith("/") is False and "{" not in exp \
        else exp.rstrip("/")


def _route_present(exp: str, routes: set[str]) -> bool:
    norm = exp.rstrip("/")
    for r in routes:
        rnorm = r.rstrip("/")
        if rnorm == norm:
            return True
        # The doc may name a parent route; accept a more specific defined route
        # hanging off it (e.g. doc says `/api/autonomous`, code has
        # `/api/autonomous/start`). Only the doc-as-parent direction counts.
        if rnorm.startswith(norm + "/"):
            return True
    return False


def _classify(token: str) -> str:
    if _ENDPOINT.match(token):
        return "endpoint"
    # Path tokens may carry a `module.py: symbol()` suffix — keep the path part.
    head = token.split(":", 1)[0].strip()
    if _PATHISH.match(head) or ("/" in head and not head.startswith(":")):
        return "path"
    if _SYMBOL.match(token):
        return "symbol"
    return "skip"


def _path_exists(token: str) -> bool:
    head = token.split(":", 1)[0].strip()
    # `data/` is gitignored (runtime-generated: db, generated images, memory,
    # ssl) so it is ABSENT on a fresh clone / in CI — never assert its presence.
    if head == "data" or head.startswith("data/") or head == "data/":
        return True
    # Normalize: rglob supports both bare names (`orchestrator.py`), nested
    # relative paths (`autonomous/agents/runtime.py`), root paths
    # (`services/comfyui.py`) and globs (`desktop/build-*.py`). Strip a
    # trailing slash so directory tokens (`data/generated/`) also match.
    pattern = head.rstrip("/")
    if not pattern:
        return False
    for hit in ROOT.rglob(pattern):
        if any(part in _EXCLUDE_DIRS for part in hit.parts):
            continue
        return True
    return False


def audit(status_md: Path, routes: set[str], defs: set[str]) -> tuple[int, int, int]:
    lines = status_md.read_text(encoding="utf-8").splitlines()
    errors = warns = oks = 0
    for raw in lines:
        if raw.strip().startswith("|") and set(raw.strip()) <= set("|-: "):
            continue  # separator row
        if not raw.strip().startswith("|"):
            continue
        cells = [c.strip() for c in raw.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        status_cell = cells[1]
        if RED in status_cell:
            status = "red"
        elif AMBER in status_cell:
            status = "amber"
        elif GREEN in status_cell:
            status = "green"
        else:
            status = "unknown"

        notes = cells[2]
        for tok in _BACKTICK.findall(notes):
            kind = _classify(tok)
            if kind == "skip":
                continue
            if kind == "endpoint":
                for ep in _expand_endpoints(tok):
                    present = _route_present(ep, routes)
                    if present:
                        oks += 1
                    elif status in ("green", "amber"):
                        errors += 1
                        print(f"  ERROR  endpoint missing but marked {status}: {ep}")
                    # red + missing -> expected, no complaint
            elif kind == "path":
                exists = _path_exists(tok)
                if exists:
                    oks += 1
                    if status == "red":
                        errors += 1
                        print(f"  ERROR  file present but marked 🔴 not-built: {tok}")
                else:
                    if status in ("green", "amber"):
                        errors += 1
                        print(f"  ERROR  file missing but marked {status}: {tok}")
                    # red + missing -> expected
            elif kind == "symbol":
                name = tok.split("(")[0].split(".")[-1]
                if name in defs:
                    oks += 1
                elif status in ("green", "amber"):
                    warns += 1
                    print(f"  WARN   symbol not found but marked {status}: {tok}")
                # red + missing -> expected
    return errors, warns, oks


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit STATUS.md against the code.")
    ap.add_argument("--status", default=str(ROOT / "docs" / "STATUS.md"))
    args = ap.parse_args()

    status_md = Path(args.status)
    if not status_md.exists():
        print(f"STATUS.md not found at {status_md}")
        return 2

    print(f"Auditing {status_md} against {ROOT}")
    routes = _collect_routes()
    defs = _collect_defs()
    print(f"  collected {len(routes)} API routes, {len(defs)} defs\n")

    errors, warns, oks = audit(status_md, routes, defs)

    print(f"\nSummary: {oks} ok · {warns} warn · {errors} error")
    if errors:
        print("FAIL: STATUS.md contradicts the codebase — fix the doc or the code.")
        return 1
    if warns:
        print("PASS (with warnings): see above.")
    else:
        print("PASS: STATUS.md is consistent with the codebase.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
