"""Memory service for VirusGPT.

VirusGPT owns its memory: a self-contained, OKF-style concept store
(see memory/store.py) that lives under data/memory/. This module is a thin async
wrapper so the rest of the server talks to memory the same way, but the data never
leaves this machine / this project — it's reachable on the LAN through the main
server port, like TTS and STT.
"""
from __future__ import annotations

import logging
from typing import Optional

from memory import store as _store

logger = logging.getLogger("vg.memory")


async def memory_status(base_url: str = "") -> Optional[dict]:
    """Return the local OKF status blob. (base_url kept for call-compat.)"""
    try:
        return _store.memory_status()
    except Exception as exc:  # noqa: BLE001
        logger.warning("memory_status failed: %s", exc)
        return None


async def memory_query(question: str, base_url: str = "") -> str:
    try:
        return await _store.memory_query(question)
    except Exception as exc:  # noqa: BLE001
        logger.warning("memory_query failed: %s", exc)
        return ""


async def memory_add(name: str, text: str, typ: str = "concept") -> dict:
    try:
        return _store.memory_add(name, text, typ)
    except Exception as exc:  # noqa: BLE001
        logger.warning("memory_add failed: %s", exc)
        return {"ok": False, "error": str(exc)}


async def memory_health(base_url: str = "") -> bool:
    return await memory_status(base_url) is not None


# --- graph management (sync store ops exposed for the server/UI) -----------
def memory_get(name: str) -> Optional[dict]:
    try:
        return _store.memory_get(name)
    except Exception as exc:  # noqa: BLE001
        logger.warning("memory_get failed: %s", exc)
        return None


def memory_update(name: str, body=None, typ=None, links=None) -> dict:
    try:
        return _store.memory_update(name, body=body, typ=typ, links=links)
    except Exception as exc:  # noqa: BLE001
        logger.warning("memory_update failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def memory_remove(name: str) -> dict:
    try:
        return _store.memory_remove(name)
    except Exception as exc:  # noqa: BLE001
        logger.warning("memory_remove failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def memory_autolink() -> dict:
    try:
        return _store.memory_autolink()
    except Exception as exc:  # noqa: BLE001
        logger.warning("memory_autolink failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def retrieve_context(question: str, k: int = 4) -> str:
    """Keyword-ranked relevant concepts as a compact system-prompt block."""
    try:
        return _store.retrieve_context(question, k=k)
    except Exception as exc:  # noqa: BLE001
        logger.warning("retrieve_context failed: %s", exc)
        return ""
