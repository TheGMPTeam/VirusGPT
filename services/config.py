"""Config loader for the macOS VirusGPT server."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"

_DEFAULTS: dict[str, Any] = {
    "host": "0.0.0.0",
    "port": 8500,
    "title": "VirusGPT",
    "default_theme": "cyber",
    "ollama": {"base_url": "http://10.0.0.120:11434", "default_model": "qwen2.5:3b"},
    "tts": {"enabled": True, "base_url": "http://localhost:49152", "default_voice": "nova", "timeout": 120},
    "stt": {"enabled": True, "base_url": "http://localhost:8181", "timeout": 30},
    "memory_mcp": {"url": "http://10.0.0.120:3800/mcp"},
}


def load_config() -> dict:
    import copy
    cfg = copy.deepcopy(_DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            user = json.loads(CONFIG_PATH.read_text())
            _deep_merge(cfg, user)
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] config.json parse error: {exc}")
    return cfg


def _deep_merge(base: dict, override: dict) -> None:
    """Recursively merge override into base (override wins), preserving
    untouched nested keys in base (shallow .update would drop them)."""
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


CONFIG = load_config()
