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
    # TLS for secure-context features (mic/Whisper on mobile Chrome needs HTTPS).
    # If https=true and the cert/key below are missing, the server auto-generates
    # a self-signed cert into data/ssl/ on first start. Trust it once on the
    # phone (Settings -> install CA) or accept the browser warning.
    "https": True,
    "ssl_certfile": "data/ssl/virusgpt.crt",
    "ssl_keyfile": "data/ssl/virusgpt.key",
    "title": "VirusGPT",
    "default_theme": "cyber",
    "ollama": {"base_url": "http://10.0.0.120:11434", "default_model": "qwen2.5:3b"},
    "tts": {"enabled": True, "base_url": "http://localhost:49152", "default_voice": "nova", "timeout": 120},
    "stt": {"enabled": True, "base_url": "http://localhost:8181", "timeout": 30},
    "memory": {
        "enabled": True,
        "bundle": "data/memory",
        "note": "VirusGPT's own OKF-style concept store (built fresh for this project; not linked to any external Docker/Hermes pool)",
    },
    # Modular LAN services (media/automation stack on the Windows Docker box).
    # Each may be enabled/disabled independently; endpoints are overridable via
    # VG_* env vars (see load_config below). Missing services degrade gracefully.
    "services": {
        "n8n":     {"enabled": True,  "base_url": "http://10.0.0.120:5678", "timeout": 30,
                    "api_key": ""},
        "comfyui": {"enabled": True,  "base_url": "http://10.0.0.120:8188", "timeout": 180,
                    "default_model": ""},
        "blender": {"enabled": False, "base_url": "http://10.0.0.120:8008", "timeout": 300},
        "ffmpeg":  {"enabled": False, "base_url": "http://10.0.0.120:8009", "timeout": 300},
        "marton":  {"enabled": False, "base_url": "", "api_key": "", "timeout": 60},
    },
    # Chat behavior: small-context by default (good for qwen2.5:3b), inject the
    # knowledge graph as DEFAULT context, and constrain history to a window.
    "chat": {
        "system_prompt": (
            "You are VirusGPT, a helpful offline AI assistant running fully local. "
            "You have your own knowledge graph (memory) that is injected as context "
            "when relevant. Use it. Be concise, accurate, and friendly. "
            "Do not invent tools or claim capabilities you lack. "
            "If you are unsure, say so."
        ),
        "memory_enabled": True,      # inject retrieved memory concepts by default
        "memory_k": 4,               # top-k concepts to inject
        "max_history": 24,           # keep only the last N messages (small context)
        "max_history_tokens": 2800,  # hard cap on history chars sent to the model
    },
    # MCP (Model Context Protocol) bridge. VirusGPT is BOTH an MCP server
    # (exposes chat / image-gen / n8n workflow tools) and an MCP client
    # (connects to external MCP servers + an n8n-REST adapter).
    "mcp": {
        "server_enabled": True,     # run the MCP server (SSE) so others can call us
        "server_port": 8700,        # co-exists with the FastAPI :8500 app
        "client_enabled": True,     # connect to external MCP servers on startup
        "clients": [],              # e.g. [{"name":"my-mcp","transport":"sse","url":"http://host:9000/sse"}]
    },
}


def load_config() -> dict:
    import copy
    import os
    cfg = copy.deepcopy(_DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            user = json.loads(CONFIG_PATH.read_text())
            _deep_merge(cfg, user)
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] config.json parse error: {exc}")

    # Environment overrides (used by Docker Compose to wire service URLs
    # without editing config.json). VG_* vars take precedence.
    if os.environ.get("VG_OLLAMA_URL"):
        cfg["ollama"]["base_url"] = os.environ["VG_OLLAMA_URL"]
    if os.environ.get("VG_MODEL"):
        cfg["ollama"]["default_model"] = os.environ["VG_MODEL"]
    if os.environ.get("VG_TTS_URL"):
        cfg["tts"]["base_url"] = os.environ["VG_TTS_URL"]
    if os.environ.get("VG_STT_URL"):
        cfg["stt"]["base_url"] = os.environ["VG_STT_URL"]
    if os.environ.get("VG_PORT"):
        try:
            cfg["port"] = int(os.environ["VG_PORT"])
        except ValueError:
            pass
    # Modular media/automation services (run on the Windows Docker box, LAN).
    for svc, env in (
        ("n8n", "VG_N8N_URL"),
        ("comfyui", "VG_COMFYUI_URL"),
        ("blender", "VG_BLENDER_URL"),
        ("ffmpeg", "VG_FFMPEG_URL"),
    ):
        if os.environ.get(env) and isinstance(cfg.get("services"), dict) and svc in cfg["services"]:
            cfg["services"][svc]["base_url"] = os.environ[env]
    # n8n API key (sent as X-N8N-API-KEY). NEVER put the real token in the
    # tracked config.json — supply it via VG_N8N_TOKEN at runtime.
    if os.environ.get("VG_N8N_TOKEN") and isinstance(cfg.get("services"), dict) and "n8n" in cfg["services"]:
        cfg["services"]["n8n"]["api_key"] = os.environ["VG_N8N_TOKEN"]
    if os.environ.get("VG_MARTON_KEY") and isinstance(cfg.get("services"), dict) and "marton" in cfg["services"]:
        cfg["services"]["marton"]["api_key"] = os.environ["VG_MARTON_KEY"]
    return cfg


def service_cfg(name: str) -> dict:
    """Return a service block from config, defaulting to disabled/empty."""
    svcs = CONFIG.get("services") or {}
    return svcs.get(name, {}) or {}


def service_url(name: str) -> str:
    return (service_cfg(name).get("base_url") or "").strip()


def service_timeout(name: str, default: float = 120.0) -> float:
    try:
        return float(service_cfg(name).get("timeout", default))
    except (TypeError, ValueError):
        return default



def _deep_merge(base: dict, override: dict) -> None:
    """Recursively merge override into base (override wins), preserving
    untouched nested keys in base (shallow .update would drop them)."""
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


CONFIG = load_config()
