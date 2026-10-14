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

import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from services import config as cfg, get_client


def _auth_headers() -> Dict[str, str]:
    """Bearer token for ComfyUI-Login protected servers (optional).

    Token sources, in order: VG_COMFY_TOKEN env var, or the first line of the
    file at VG_COMFY_PASSWORD_FILE (bind-mounted, never logged). Empty when
    neither is set, so unauthenticated ComfyUI servers keep working.
    """
    import os
    tok = os.environ.get("VG_COMFY_TOKEN", "").strip()
    if not tok:
        pf = os.environ.get("VG_COMFY_PASSWORD_FILE", "").strip()
        if pf:
            try:
                tok = Path(pf).read_text().splitlines()[0].strip()
            except Exception:
                tok = ""
    return {"Authorization": f"Bearer {tok}"} if tok else {}

# Where generated images are persisted (served via /api/generated/{file}).
# In dev this is <repo>/data/generated. In a PyInstaller bundle the `data/`
# tree is collected under <app>/Contents/Frameworks, while the server's ROOT
# points at Contents/Resources — so we resolve to the directory that actually
# exists on disk (and is where the endpoint will look) instead of a phantom path.
def _resolve_gen_dir() -> Path:
    candidates = []
    # 1) repo / Resources layout (dev + server.py's ROOT/data/generated)
    try:
        from server import ROOT  # type: ignore
        candidates.append(Path(ROOT) / "data" / "generated")
    except Exception:
        pass
    # 2) PyInstaller Frameworks layout (frozen .app)
    if getattr(sys, "frozen", False):
        try:
            base = Path(sys.executable).resolve().parent.parent   # .../Contents
            candidates.append(base / "Frameworks" / "data" / "generated")
            candidates.append(base / "Resources" / "data" / "generated")
        except Exception:
            pass
    # 3) fallback next to this file
    candidates.append(Path(__file__).resolve().parent.parent / "data" / "generated")
    for c in candidates:
        if c.exists():
            return c
    # none exist yet -> create the first (repo/Resources) one
    first = candidates[0]
    first.mkdir(parents=True, exist_ok=True)
    return first

GEN_DIR = _resolve_gen_dir()


def _base() -> str:
    return (cfg.service_cfg("comfyui").get("base_url") or "http://10.0.0.120:8188").rstrip("/")


def _timeout(default: float = 120.0) -> float:
    return cfg.service_timeout("comfyui", default)


async def comfyui_health() -> bool:
    try:
        r = await get_client().get(f"{_base()}/system_stats", headers=_auth_headers(), timeout=6.0)
        return r.status_code == 200
    except Exception:
        return False


async def comfyui_models() -> List[str]:
    """Return checkpoint filenames available on the server (empty if unknown)."""
    try:
        r = await get_client().get(f"{_base()}/object_info/CheckpointLoaderSimple", headers=_auth_headers(), timeout=10.0)
        if r.status_code != 200:
            return []
        info = r.json()
        cks = (info.get("CheckpointLoaderSimple", {})
               .get("input", {}).get("required", {})
               .get("ckpt_name", [[]])[0])
        models = [str(c) for c in cks] if isinstance(cks, list) else []
        prof = _zimage_profile()
        if prof.get("unet") and prof.get("unet") not in models:
            models.append(prof["unet"])
        return models
    except Exception:
        return []


def _default_model() -> str:
    return cfg.service_cfg("comfyui").get("default_model") or ""


def _zimage_profile() -> Dict[str, str]:
    """Z-Image Turbo component profile from config (unet/clip/vae names)."""
    return cfg.service_cfg("comfyui").get("zimage") or {}


def _is_zimage(model: str) -> bool:
    prof = _zimage_profile()
    if not prof:
        return False
    m = (model or "").lower()
    return (m in ("z-image-turbo", "z_image", str(prof.get("name", "")).lower())
            or m == str(prof.get("unet", "")).lower()
            or "z_image" in m or "z-image" in m)


async def _multigpu_devices() -> Optional[Dict[str, str]]:
    """Device placement for the ComfyUI-MultiGPU loader node, or None.

    Returns None when the ComfyUI-MultiGPU custom node pack is absent, so the
    caller falls back to plain CheckpointLoaderSimple. Placement sources, in
    order: VG_COMFY_UNET_DEVICE / VG_COMFY_CLIP_DEVICE / VG_COMFY_VAE_DEVICE
    env vars, else the CUDA device with the most free VRAM (auto-balance
    across cards). 'cpu' is always a valid placement.
    """
    try:
        r = await get_client().get(
            f"{_base()}/object_info/CheckpointLoaderAdvancedMultiGPU",
            headers=_auth_headers(), timeout=10.0)
        if r.status_code != 200:
            return None
        req = (r.json().get("CheckpointLoaderAdvancedMultiGPU", {})
               .get("input", {}).get("required", {}))
        devices = req.get("unet_device", [[]])[0]
        if not devices:
            return None
        devices = [str(d) for d in devices]
    except Exception:
        return None

    import os
    fallback = devices[0] if devices else "cpu"

    def _pick(name: str) -> str:
        v = os.environ.get(name, "").strip()
        return v if v in devices else ""

    unet, clip, vae = _pick("VG_COMFY_UNET_DEVICE"), _pick("VG_COMFY_CLIP_DEVICE"), _pick("VG_COMFY_VAE_DEVICE")
    if not (unet and clip and vae):
        # Auto: greedily place on the CUDA device with the most free VRAM.
        best = fallback
        try:
            sr = await get_client().get(f"{_base()}/system_stats", headers=_auth_headers(), timeout=6.0)
            if sr.status_code == 200:
                devs = [(d.get("vram_free", 0), d.get("name", "").split(":")[0] + ":" + str(d.get("index", 0)))
                        for d in sr.json().get("devices", []) if d.get("type") == "cuda"]
                devs = [(free, n) for free, n in devs if n in devices]
                if devs:
                    best = max(devs)[1]
        except Exception:
            pass
        unet = unet or best
        clip = clip or best
        vae = vae or best
    return {"unet_device": unet, "clip_device": clip, "vae_device": vae}


def _build_workflow(prompt: str, model: str, seed: int,
                    steps: int, cfg_scale: float, width: int, height: int,
                    negative: str,
                    multigpu: Optional[Dict[str, str]] = None,
                    sampler_name: str = "dpmpp_2m", scheduler: str = "karras",
                    transparent: bool = False) -> Dict[str, Any]:
    """API-format txt2img graph (class_type per node).

    Node ids are stable so the output node (9) is always the SaveImage.
    When `multigpu` is provided (ComfyUI-MultiGPU pack detected), node 3
    becomes CheckpointLoaderAdvancedMultiGPU with explicit per-component
    device placement, spreading UNet/CLIP/VAE across cards and freeing the
    compute GPU for sampling.
    """
    if multigpu:
        loader = {
            "class_type": "CheckpointLoaderAdvancedMultiGPU",
            "inputs": {"ckpt_name": model, **multigpu},
        }
    else:
        loader = {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": model},
        }
    return {
        "3": loader,
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
                "sampler_name": sampler_name,
                "scheduler": scheduler,
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


def _build_zimage_workflow(prompt: str, prof: Dict[str, str], seed: int,
                           steps: int, cfg_scale: float, width: int, height: int,
                           devices: Dict[str, str],
                           sampler_name: str, scheduler: str,
                           transparent: bool = False) -> Dict[str, Any]:
    """API-format graph for Z-Image Turbo (Lumina2 flow model).

    Split loaders (UNET/CLIP/VAE) with MultiGPU device placement,
    ModelSamplingAuraFlow shift, SD3 latent, zeroed negative conditioning
    (cfg 1.0 flow model), and optional Inspyrenet alpha cutout before save.
    Node id 9 is always SaveImage.
    """
    dev_u = devices.get("unet_device", "cpu")
    dev_c = devices.get("clip_device", "cpu")
    dev_v = devices.get("vae_device", "cpu")
    wf: Dict[str, Any] = {
        "3": {
            "class_type": "UNETLoaderMultiGPU",
            "inputs": {"unet_name": prof["unet"], "weight_dtype": prof.get("weight_dtype", "default"),
                       "device": dev_u},
        },
        "11": {
            "class_type": "ModelSamplingAuraFlow",
            "inputs": {"shift": float(prof.get("shift", 3.0)), "model": ["3", 0]},
        },
        "2": {
            "class_type": "CLIPLoaderMultiGPU",
            "inputs": {"clip_name": prof["clip"], "type": prof.get("clip_type", "lumina2"),
                       "device": dev_c},
        },
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt, "clip": ["2", 0]},
        },
        "7": {
            "class_type": "ConditioningZeroOut",
            "inputs": {"conditioning": ["6", 0]},
        },
        "5": {
            "class_type": "EmptySD3LatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        },
        "4": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": steps,
                "cfg": cfg_scale,
                "sampler_name": sampler_name,
                "scheduler": scheduler,
                "denoise": 1.0,
                "model": ["11", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0],
            },
        },
        "1": {
            "class_type": "VAELoaderMultiGPU",
            "inputs": {"vae_name": prof["vae"], "device": dev_v},
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["4", 0], "vae": ["1", 0]},
        },
    }
    if transparent:
        wf["10"] = {
            "class_type": "easy imageRemBg",
            "inputs": {"images": ["8", 0], "rem_mode": "Inspyrenet",
                       "image_output": "Preview", "save_prefix": "ComfyUI",
                       "torchscript_jit": True, "add_background": "none",
                       "refine_foreground": False},
        }
        wf["9"] = {
            "class_type": "SaveImage",
            "inputs": {"images": ["10", 0], "filename_prefix": "virusgpt"},
        }
    else:
        wf["9"] = {
            "class_type": "SaveImage",
            "inputs": {"images": ["8", 0], "filename_prefix": "virusgpt"},
        }
    return wf


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
    sampler_name: Optional[str] = None,
    scheduler: Optional[str] = None,
    transparent: bool = False,
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

    devices = await _multigpu_devices()
    if _is_zimage(chosen):
        prof = _zimage_profile()
        # Z-Image Turbo winning settings: 8 steps, cfg 1.0,
        # euler_ancestral_cfg_pp + exponential scheduler.
        if steps == 25:
            steps = int(prof.get("steps", 8))
        if cfg_scale == 7.0:
            cfg_scale = float(prof.get("cfg", 1.0))
        sampler_name = sampler_name or prof.get("sampler", "euler_ancestral_cfg_pp")
        scheduler = scheduler or prof.get("scheduler", "exponential")
        workflow = _build_zimage_workflow(
            prompt, prof, seed, steps, cfg_scale, width, height,
            devices or {"unet_device": "cpu", "clip_device": "cpu", "vae_device": "cpu"},
            sampler_name, scheduler, transparent=transparent)
    else:
        sampler_name = sampler_name or "dpmpp_2m"
        scheduler = scheduler or "karras"
        workflow = _build_workflow(prompt, chosen, seed, steps, cfg_scale,
                                   width, height, negative_prompt,
                                   multigpu=devices,
                                   sampler_name=sampler_name, scheduler=scheduler)

    try:
        # 1. submit
        submit = await get_client().post(
            f"{base}/prompt",
            json={"prompt": workflow, "client_id": str(uuid.uuid4)},
            headers=_auth_headers(),
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
            hist = await get_client().get(f"{base}/history/{prompt_id}", headers=_auth_headers(), timeout=10.0)
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
            headers=_auth_headers(),
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
