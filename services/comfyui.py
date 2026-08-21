"""ComfyUI service client for VirusGPT.

Thin async wrapper around a LAN ComfyUI instance (the `comfyui-cu130`
Docker container on the Windows box). Provides:
  * comfyui_health()  -> bool
  * comfyui_models()  -> list of checkpoint filenames (for default_model)
  * render_image(prompt, **opts) -> {status, file?, url?, error?}

Generation follows the canonical ComfyUI loop:
  1. GET /object_info/CheckpointLoaderSimple to learn available checkpoints.
  2. POST /prompt with an API-format txt2img graph (random seed).
  3. Poll GET /history/{prompt_id} until the run completes/fails.
  4. GET /view to download the output PNG, store it under data/generated/.

Everything degrades gracefully: if ComfyUI is unreachable the client returns a
clear error dict (never raises), so the agent tool and /api/health stay green.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from services import config as cfg, get_client

# Where generated images are persisted (served via /api/generated/{file}).
GEN_DIR = Path(__file__).resolve().parent.parent / "data" / "generated"
GEN_DIR.mkdir(parents=True, exist_ok=True)


def _base() -> str:
    return (cfg.service_cfg("comfyui").get("base_url") or "http://10.0.0.120:8188").rstrip("/")


def _timeout(default: float = 120.0) -> float:
    return cfg.service_timeout("comfyui", default)


async def comfyui_health() -> bool:
    try:
        r = await get_client().get(f"{_base()}/system_stats", timeout=6.0)
        return r.status_code == 200
    except Exception:
        return False


async def comfyui_models() -> List[str]:
    """Return checkpoint filenames available on the server (empty if unknown)."""
    try:
        r = await get_client().get(f"{_base()}/object_info/CheckpointLoaderSimple", timeout=10.0)
        if r.status_code != 200:
            return []
        info = r.json()
        cks = (info.get("CheckpointLoaderSimple", {})
               .get("input", {}).get("required", {})
               .get("ckpt_name", [[]])[0])
        return [str(c) for c in cks] if isinstance(cks, list) else []
    except Exception:
        return []


def _default_model() -> str:
    return cfg.service_cfg("comfyui").get("default_model") or ""


def _build_workflow(prompt: str, model: str, seed: int,
                    steps: int, cfg_scale: float, width: int, height: int,
                    negative: str) -> Dict[str, Any]:
    """API-format txt2img graph (class_type per node).

    Node ids are stable so the output node (9) is always the SaveImage.
    """
    return {
        "3": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": model},
        },
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt, "clip": ["3", 1]},
        },
        "7": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": negative, "clip": ["3", 1]},
        },
        "5": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        },
        "4": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": steps,
                "cfg": cfg_scale,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1.0,
                "model": ["3", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0],
            },
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["4", 0], "vae": ["3", 2]},
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {"images": ["8", 0], "filename_prefix": "virusgpt"},
        },
    }


async def render_image(
    prompt: str,
    *,
    model: Optional[str] = None,
    negative_prompt: str = "",
    seed: int = -1,
    steps: int = 25,
    cfg_scale: float = 7.0,
    width: int = 1024,
    height: int = 1024,
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    """Generate one image from `prompt`. Returns a result dict.

    On success:  {"status": "completed", "file": "...png",
                  "url": "/api/generated/...png", "prompt_id": "..."}
    On failure:  {"status": "failed", "error": "..."}
    """
    prompt = (prompt or "").strip()
    if not prompt:
        return {"status": "failed", "error": "missing prompt"}
    base = _base()

    # Fail fast with a clear message if the server isn't reachable.
    if not await comfyui_health():
        return {"status": "failed", "error": f"ComfyUI unreachable at {base}"}

    to = timeout or _timeout(180.0)

    # Resolve model: explicit > config default > first available checkpoint.
    chosen = model or _default_model()
    if not chosen:
        models = await comfyui_models()
        if models:
            chosen = models[0]
    if not chosen:
        return {"status": "failed",
                "error": "no ComfyUI checkpoint available (server has none loaded?)"}

    import random
    if seed is None or seed == -1:
        seed = random.randint(0, 2**31 - 1)

    workflow = _build_workflow(prompt, chosen, seed, steps, cfg_scale,
                               width, height, negative_prompt)

    try:
        # 1. submit
        submit = await get_client().post(
            f"{base}/prompt",
            json={"prompt": workflow, "client_id": str(uuid.uuid4())},
            timeout=15.0,
        )
        if submit.status_code != 200:
            return {"status": "failed",
                    "error": f"ComfyUI /prompt returned {submit.status_code}: {submit.text[:200]}"}
        prompt_id = submit.json().get("prompt_id")
        if not prompt_id:
            return {"status": "failed", "error": "ComfyUI did not return a prompt_id"}

        # 2. poll history until done
        final: Dict[str, Any] = {}
        import asyncio
        deadline = asyncio.get_event_loop().time() + to
        while asyncio.get_event_loop().time() < deadline:
            hist = await get_client().get(f"{base}/history/{prompt_id}", timeout=10.0)
            if hist.status_code == 200:
                data = hist.json().get(prompt_id, {})
                status = data.get("status", {})
                if status.get("status_str") == "error" or status.get("completed") is True:
                    final = data
                    break
            await asyncio.sleep(1.5)
        else:
            return {"status": "failed", "error": "timed out waiting for ComfyUI to finish"}

        if final.get("status", {}).get("status_str") == "error":
            msgs = final.get("status", {}).get("messages", [])
            err = str(msgs[-1]) if msgs else "unknown ComfyUI error"
            return {"status": "failed", "error": f"ComfyUI execution error: {err[:300]}"}

        # 3. extract output image from node 9 (SaveImage)
        outputs = final.get("outputs", {})
        node_out = outputs.get("9") or next(iter(outputs.values()), {})
        images = node_out.get("images") or []
        if not images:
            return {"status": "failed", "error": "ComfyUI finished but produced no image"}
        img = images[0]
        fname = img.get("filename")
        subfolder = img.get("subfolder", "")
        itype = img.get("type", "output")

        # 4. download + persist
        dl = await get_client().get(
            f"{base}/view",
            params={"filename": fname, "subfolder": subfolder, "type": itype},
            timeout=30.0,
        )
        if dl.status_code != 200:
            return {"status": "failed", "error": f"failed to download image: {dl.status_code}"}
        safe = f"{uuid.uuid4().hex}_{fname}"
        out_path = GEN_DIR / safe
        out_path.write_bytes(dl.content)
        return {
            "status": "completed",
            "file": safe,
            "url": f"/api/generated/{safe}",
            "prompt_id": prompt_id,
            "model": chosen,
            "seed": seed,
        }
    except httpx.ConnectError:
        return {"status": "failed", "error": f"ComfyUI unreachable at {base}"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "error": f"ComfyUI error: {exc}"}
