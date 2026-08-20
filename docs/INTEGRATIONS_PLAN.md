# VirusGPT Creative + Research + Publishing Pipeline — Architecture & Build Plan

> Status: **PLAN** (no code yet for this subsystem). This document is the contract
> for the phased build. It builds on work that already exists (see "Foundation" below).
>
> Goal: turn VirusGPT from a chat/agent app into a **self-running creative studio**:
> it researches a topic, builds assets (images/3D/video via ComfyUI/Blender/ffmpeg),
> orchestrates the workflow (n8n), fact-checks + self-reflects (the Dreamer), and
> publishes finished, sorted videos to Gmail / YouTube / Snapchat (marton.ai).

---

## 1. Operating model (matches the rest of the stack)

All heavy apps run on the **Windows Docker box** and are reached over the LAN —
exactly like TTS (:49152), STT (:8181) and Ollama (:11434) already are. VirusGPT
is the **orchestrator/control plane**; it never runs the GPU/CPU-heavy work locally.

```
┌───────────────────────── Mac (VirusGPT control plane) ─────────────────────────┐
│  server.py  ── API + chat + autonomous + memory + selfdev(Dreamer)               │
│  services/        n8n.py, comfyui.py, blender.py, ffmpeg.py, marton.py (clients) │
│  autonomous/tools.py   + media tools (render_image, render_video, publish_*)     │
│  docker-compose.yml     (already has ollama/pockettts/whisper/virusgpt)         │
└──────────────┬───────────────────────────────────────────────┬─────────────────┘
               │ LAN (http)                                      │ LAN (http)
┌──────────────▼────────── Windows Docker box ──────────────────▼────────────────┐
│  n8n (:5678)   ComfyUI (:8188)   Blender (headless)   ffmpeg   marton.ai (cloud) │
└────────────────────────────────────────────────────────────────────────────────┘
```

Config symmetry: every new service is a block in `config.json` (`base_url` +
timeouts) and a client in `services/<name>.py` mirroring `services/tts.py`.

---

## 2. Foundation that already exists (REUSE, do not rebuild)

| Piece | Location | Role in this pipeline |
|-------|----------|-----------------------|
| Tool harness | `autonomous/tools.py` (`run_tool`) | Hosts the new `render_image`, `render_video`, `publish_*` tools; agents call them via Ollama function-calling |
| Dreamer | `autonomous/selfdev.py` | The `research → fact_check → dream` loop becomes `research → build → test → check → upload` |
| Memory store | `memory/store.py` + `services/memory.py` | Pipeline records what it learned/built; graph shows media concepts |
| Service client pattern | `services/tts.py`, `services/stt.py`, `services/__init__.py` (shared httpx) | Template for `n8n.py / comfyui.py / blender.py / ffmpeg.py / marton.py` |
| Modular LAN config | `config.json` + `docker-compose.yml` | Where the new services register |

> NOTE: a prior `config.json` had an `n8n_url` slot — it is **not** present in the
> current `config.json`. The plan re-introduces the `services` block with all five
> new services. No code from that era exists; this is green-field on top of the
> foundation above.

---

## 3. New service clients (in `services/`)

Each is a thin async wrapper over the shared httpx client, same shape as `tts.py`.

### 3.1 `services/n8n.py`
- Webhook trigger: `POST {n8n_url}/webhook/{id}` with a JSON payload (the pipeline
  spec). Returns execution id; poll `GET /api/v1/executions/{id}`.
- Use case: n8n is the **workflow glue** — it fans research → media jobs → upload,
  and reports status back to VirusGPT via a callback URL.

### 3.2 `services/comfyui.py`
- `POST {comfy_url}/prompt` with a workflow graph (or saved `workflow_api.json`);
  poll `/history/{prompt_id}` for the output image/video paths.
- Provides `render_image(prompt, workflow, params)` and `render_video(...)` for
  AI-generated keyframes / motion.

### 3.3 `services/blender.py`
- Launch headless: `blender --background scene.blend --render-frame N --render-output`
  (or `--render-anim`). Wrap as an async job; poll a status file/port.
- Provides `render_scene(scene_path, frames)` → returns frame/image sequence.

### 3.4 `services/ffmpeg.py`
- Local binary on the **Windows box** (or a tiny ffmpeg API container). Abstraction:
  `concat(clips)`, `add_transition(a,b,type)`, `add_effects(clip, fx)`,
  `render(out_path)`. Builds the final "sorted" video with transitions + effects.

### 3.5 `services/marton.py`  (config slot now, key later)
- `POST {marton_url}/v1/{gmail|youtube|snapchat}/...` with `Authorization:
  Bearer <MARTON_API_KEY>` from `config.json` (`marton.api_key`, env-overridable
  `VG_MARTON_KEY`).
- `upload_youtube(title, file, desc)`, `send_gmail(to, subject, body, attach)`,
  `upload_snapchat(file, caption)`.
- Until the key arrives, the client raises a clear "marton not configured" error
  and the pipeline marks that stage `skipped` (does not hard-fail).

---

## 4. Tool harness extensions (`autonomous/tools.py`)

Add media tools so agents can drive the pipeline through the ReAct loop:

| Tool | Calls | Purpose |
|------|-------|---------|
| `render_image` | `comfyui.render_image` | generate a keyframe / thumbnail |
| `render_video` | `comfyui.render_video` + `blender.render_scene` | generate motion / 3D |
| `edit_video` | `ffmpeg.concat/transition/effects` | assemble "sorted" video w/ transitions |
| `publish_youtube` | `marton.upload_youtube` | upload final video |
| `publish_gmail` | `marton.send_gmail` | email a cut / report |
| `publish_snapchat` | `marton.upload_snapchat` | upload short |
| `run_workflow` | `n8n.trigger` | kick a multi-step n8n workflow |

Each tool is registered exactly like the existing 8 (schema + async fn + allowlist).

---

## 5. The pipeline (research → build → test → check → upload)

Implemented as a **Dreamer subclass / new `autonomous/pipeline.py`** reusing the
selfdev research+factcheck machinery:

1. **Research** — `selfdev.research_topic(topic)` (web search + fetch + store).
2. **Build** — plan assets: ComfyUI image gen → Blender scene → ffmpeg assemble.
   Write a spec, hand to `n8n.run_workflow` OR call media tools directly.
3. **Test** — render a low-res draft; self-check with the LLM ("does this match
   the brief?"). Loop back to Build if it fails (bounded retries, like missions).
4. **Check** — `selfdev.fact_check` the claims; verify assets exist + codec/length
   sane (ffmpeg probe). Flag stale/bad; trim if needed.
5. **Upload (sorted)** — `marton.publish_*` uploads to Gmail/YouTube/Snapchat in
   the requested sort order; records the published URLs into memory.

Every stage writes to `data/pipeline/runs/<id>.json` (audit trail) and to memory
as `learned`/`insight` concepts so the Dreamer keeps improving it.

---

## 6. Docker Compose (Windows box)

Add to the existing `docker-compose.yml` (or a new `docker-compose.media.yml`):

```yaml
  n8n:      image: n8nio/n8n;            ports: 5678:5678
  comfyui:  image: <comfyui+cuda>;       ports: 8188:8188
  blender:  image: <blender-headless>;   (job runner, no port)
  ffmpeg:   image: jrottenberg/ffmpeg;   (job runner, no port)
  # marton.ai is cloud — no container; just the API client + key
```
VirusGPT's `config.json` points at these via LAN IPs (same as Ollama `10.0.0.120`).

---

## 7. Config schema additions (`config.json`)

```json
"services": {
  "n8n":     { "base_url": "http://10.0.0.120:5678", "timeout": 120 },
  "comfyui":  { "base_url": "http://10.0.0.120:8188", "timeout": 300 },
  "blender":  { "base_url": "http://10.0.0.120:9999", "timeout": 600 },
  "ffmpeg":   { "base_url": "http://10.0.0.120:5005", "timeout": 300 },
  "marton":   { "api_url": "https://api.marton.ai/v1", "api_key": "" }
}
```
`marton.api_key` overridable by env `VG_MARTON_KEY` (never committed).

---

## 8. Phased build order (one PR each, CI-gated)

- **Phase 0 — Config + clients (no behavior yet)**
  `config.json` `services` block; `services/n8n.py, comfyui.py, blender.py,
  ffmpeg.py, marton.py` (all degrade gracefully when URL empty). Endpoints
  `/api/services/status` showing each service health. *(Do this first; safe,
  testable, matches existing pattern.)*

- **Phase 1 — Tool harness media tools**
  Add the 7 media tools to `autonomous/tools.py` (schema + allowlist). Unit-test
  each with a mocked client.

- **Phase 2 — Pipeline orchestration**
  `autonomous/pipeline.py` implementing research→build→test→check→upload reusing
  `selfdev`. Endpoint `/api/pipeline/run` + run history. Wire into the Dreamer's
  curiosity queue (optional auto-runs).

- **Phase 3 — Compose + Windows deploy**
  `docker-compose.media.yml` + `install.sh --media` flag (Docker path). README
  section. Verify from the Mac against the Windows box.

- **Phase 4 — marton.ai connector (gated on your key)**
  Real `marton.py` calls; upload to Gmail/YouTube/Snapchat. Until key present,
  stages are `skipped` with a clear log.

- **Phase 5 — UI**
  A "Studio" tab: pick a topic → watch Research/Build/Test/Check/Upload stages
  live; preview generated media; one-click publish. Reuses the Kanban/SSE pattern.

---

## 9. How this beats the comparison set (Hermes / OpenClaw / OpenCode)

Those are chat/agent shells. VirusGPT becomes an **agentic creative studio** that
*produces and ships artifacts* (videos, posts) end-to-end, **self-improves** (Dreamer
research/fact-check/trim), and **owns its memory** (no external pool). The media
pipeline is the differentiator: it doesn't just talk about making a video — it
researches, renders, assembles with transitions, fact-checks, and publishes.
