"""Backend API + engine tests for VirusGPT (offline, all upstreams stubbed)."""
from __future__ import annotations

import io
import hashlib
import json
import sys
import time
import zipfile

import pytest


# ---------------------------------------------------------------------------
# Health & static
# ---------------------------------------------------------------------------
def test_root_serves_html(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "VirusGPT" in r.text
    assert "no-store" in r.headers.get("cache-control", "")


def test_health_aggregates(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    d = r.json()
    # health aggregates ollama / tts / whisper (stt) / comfyui + model/voice lists
    assert "ollama" in d and "tts" in d and "whisper" in d and "comfyui" in d
    assert "models" in d and "default_model" in d and "voices" in d


def test_assets_served(client):
    r = client.get("/assets/js/main.js")
    assert r.status_code == 200
    assert r.text.startswith(("//", "/*", "/*"))


# ---------------------------------------------------------------------------
# Chat / improve / suggest
# ---------------------------------------------------------------------------
def test_chat_streams_sse(client, stub_llm):
    r = client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    assert r.text.startswith("data: ")
    # final content contains our stubbed text (frame-split across SSE chunks)
    assert "offline" in r.text and "assistant" in r.text


def test_chat_rejects_invalid_model(client):
    r = client.post("/api/chat", json={"model": "../../etc/passwd",
                                       "messages": [{"role": "user", "content": "x"}]})
    assert r.status_code == 400


def test_chat_rejects_huge_model(client):
    r = client.post("/api/chat", json={"model": "a" * 100,
                                       "messages": [{"role": "user", "content": "x"}]})
    assert r.status_code == 400


def test_chat_preserves_frontend_system(client, stub_llm):
    r = client.post("/api/chat", json={
        "messages": [{"role": "system", "content": "PERSONA: be terse"},
                     {"role": "user", "content": "hi"}]})
    assert r.status_code == 200


def test_improve(client, stub_llm):
    r = client.post("/api/improve", json={"text": "hello there friend"})
    assert r.status_code == 200
    assert "improved" in r.json()


def test_improve_empty(client):
    r = client.post("/api/improve", json={"text": "   "})
    assert r.status_code == 400


def test_improve_too_long(client):
    r = client.post("/api/improve", json={"text": "x" * 4001})
    assert r.status_code == 413


def test_suggest(client, stub_llm):
    r = client.post("/api/suggest", json={"text": "tell me about"})
    assert r.status_code == 200
    assert "suggestions" in r.json()


def test_suggest_empty(client):
    r = client.post("/api/suggest", json={"text": ""})
    assert r.status_code == 200
    assert r.json()["suggestions"] == []


# ---------------------------------------------------------------------------
# Team project export
# ---------------------------------------------------------------------------
def test_project_export_builds_grouped_verified_package(client):
    payload = {
        "name": "win11pe-installer-design",
        "round_id": "round-win11pe-001",
        "export_revision": 1,
        "goal": "Win11PE installer design",
        "plan": "Architecture first, then HUD UI, then image copy deployment.",
        "synthesis": "Use one deployment entry point and a progress-driven HUD.",
        "turns": [
            {
                "who": "Architect",
                "role": "planner",
                "task": "Define the architecture and deployment entry point",
                "output": """## Architecture
Use a single deployment entry point and explicit phases. Inline `Get-Date` is documentation, not a standalone file.

```powershell
# deploy-entry.ps1
[CmdletBinding()]
param([string]$ImagePath)
$ErrorActionPreference = 'Stop'
Write-Host 'Preparing media'
Mount-DiskImage -ImagePath $ImagePath
$disk = Get-DiskImage -ImagePath $ImagePath
$volume = $disk | Get-Volume
Write-Host $volume.DriveLetter
Write-Host 'Applying image'
Write-Host 'Installing boot files'
Write-Host 'Complete'
```

```powershell
Write-Host 'Phase start'
Write-Host 'Phase end'
```
""",
            },
            {
                "who": "HUD Designer",
                "role": "worker",
                "task": "Design the HUD UI in XAML",
                "output": """## HUD UI
The display uses one progress rail and a concise status message.

```xaml
<Window x:Class="Installer.MainWindow"
        xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        Title="Installer HUD"
        Width="900"
        Height="520">
  <Grid>
    <StackPanel>
      <TextBlock Text="Installing Windows" />
      <ProgressBar Minimum="0" Maximum="100" Value="42" />
      <TextBlock Text="Applying image" />
    </StackPanel>
  </Grid>
</Window>
```
""",
            },
            {
                "who": "Deployment Engineer",
                "role": "worker",
                "task": "Specify image copy and validation",
                "output": """## Image Copy
Copy the WIM in bounded chunks, verify the destination hash, and report progress.

```text
Source image must remain read-only.
Destination verification is mandatory.
```
""",
            },
        ],
    }
    r = client.post("/api/export/project", json=payload)
    assert r.status_code == 200
    assert 'filename="win11pe-installer-design.zip"' in r.headers["content-disposition"]

    with zipfile.ZipFile(io.BytesIO(r.content)) as archive:
        names = sorted(archive.namelist())
        root = "win11pe-installer-design/"
        assert all(name.startswith(root) for name in names)
        assert root + "MANIFEST.json" in names
        assert root + "README.md" in names
        assert root + "transcript.md" in names
        assert root + "decisions.md" in names
        assert root + "snippets/index.md" in names
        assert not any(name.endswith("context.md") for name in names)
        assert not any(name.endswith("PROJECT.json") for name in names)
        assert not any("/code/" in name for name in names)

        snippet_files = [name for name in names if "/snippets/" in name and not name.endswith("index.md")]
        assert any("/01-architecture/" in name and name.endswith("deploy-entry.ps1") for name in snippet_files)
        assert any("/02-hud-ui/" in name and name.endswith(".xaml") for name in snippet_files)
        assert any("/03-image-copy/notes.md" in name for name in snippet_files)
        # Inline code and short fenced snippets are merged into topic notes.
        assert not any("get-date" in name.lower() for name in snippet_files)
        assert len(snippet_files) <= 6

        manifest = json.loads(archive.read(root + "MANIFEST.json"))
        assert manifest["schema"] == "virusgpt.team-export.v2"
        assert manifest["round_id"] == "round-win11pe-001"
        assert manifest["export_revision"] == 1
        assert manifest["agents"] == ["Architect", "HUD Designer", "Deployment Engineer"]
        assert manifest["checksum_scope"] == "all archive files except MANIFEST.json"
        for entry in manifest["files"]:
            data = archive.read(root + entry["path"])
            assert entry["bytes"] == len(data)
            assert entry["sha256"] == hashlib.sha256(data).hexdigest()

        index = archive.read(root + "snippets/index.md").decode()
        assert "| Snippet | Agent | Topic | Transcript anchor | File |" in index
        assert "transcript.md#L" in index
        deploy_row = next(line for line in index.splitlines() if "deploy-entry.ps1" in line)
        assert "transcript.md#L7" in deploy_row
        assert "Architect" in index and "HUD Designer" in index
        assert "Architecture first" in archive.read(root + "decisions.md").decode()
        transcript = archive.read(root + "transcript.md").decode()
        assert "Source image must remain read-only." in transcript

    payload["export_revision"] = 2
    r2 = client.post("/api/export/project", json=payload)
    assert 'filename="win11pe-installer-design-v2.zip"' in r2.headers["content-disposition"]


# ---------------------------------------------------------------------------
# TTS
# ---------------------------------------------------------------------------
def test_tts_proxy(client, stub_tts):
    r = client.get("/api/tts?text=hello%20world&voice=nova&format=mp3")
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/mpeg"
    assert stub_tts["text"] == "hello world"


def test_tts_strips_complete_and_unfinished_code_before_rewrite(client, stub_tts, monkeypatch):
    import server
    seen = {}

    async def fake_rewrite(text, _cfg):
        seen["rewrite_input"] = text
        return "Natural spoken rewrite."

    monkeypatch.setattr(server, "_rewrite_for_tts", fake_rewrite)
    source = ("Before code. ```powershell Get-Process | Stop-Process ``` "
              "Use `ProgressBar.Value` carefully. After code.")
    r = client.get("/api/tts", params={"text": source, "voice": "nova", "format": "mp3"})
    assert r.status_code == 200
    assert seen["rewrite_input"] == "Before code. Use carefully. After code."
    assert stub_tts["text"] == "Natural spoken rewrite."
    assert r.headers["x-tts-rewritten"] == "true"
    assert "Get-Process" not in r.headers["x-tts-spoken"]


def test_tts_pure_or_unfinished_code_returns_no_audio(client):
    for source in ("```python\nprint('secret')\n```", "`unfinished.code_call("):
        r = client.get("/api/tts", params={"text": source, "voice": "nova"})
        assert r.status_code == 204
        assert r.headers["x-tts-spoken"] == ""


def test_speech_text_removes_tilde_fences_and_inline_code():
    from server import speech_text
    source = "Start. ~~~bash\ncurl secret.example\n~~~ Continue with `safe_call()` now."
    assert speech_text(source) == "Start. Continue with now."


def test_tts_missing_text(client):
    r = client.get("/api/tts")
    assert r.status_code == 400


def test_tts_voices(client, stub_tts):
    r = client.get("/api/tts/voices")
    assert r.status_code == 200
    vs = r.json()["voices"]
    assert any(v["id"] == "nova" for v in vs)


def test_tts_clone(client):
    buf = io.BytesIO(b"RIFF....WAVE....")
    buf.name = "ref.wav"
    r = client.post("/api/tts/clone", files={"audio": ("ref.wav", buf, "audio/wav"),
                                             "name": (None, "myvoice")})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["voice"].startswith("local:")


def test_tts_clone_no_audio(client):
    r = client.post("/api/tts/clone", data={})
    assert r.status_code == 400


def test_tts_preview(client, stub_tts):
    buf = io.BytesIO(b"RIFF....WAVE....")
    r = client.post("/api/tts/preview", files={"audio": ("ref.wav", buf, "audio/wav")})
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/mpeg"


# ---------------------------------------------------------------------------
# STT
# ---------------------------------------------------------------------------
def test_stt(client, stub_stt):
    buf = io.BytesIO(b"fake-audio-bytes")
    r = client.post("/api/stt", files={"audio": ("a.webm", buf, "audio/webm")})
    assert r.status_code == 200
    assert r.json()["text"] == "transcribed offline test audio"


def test_stt_no_audio(client):
    r = client.post("/api/stt", data={})
    assert r.status_code == 400


def test_stt_empty_audio(client):
    buf = io.BytesIO(b"")
    r = client.post("/api/stt", files={"audio": ("a.webm", buf, "audio/webm")})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------
def test_memory_graph(client):
    r = client.get("/api/memory/graph")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_memory_query(client):
    r = client.post("/api/memory/query", json={"question": "what is X?"})
    assert r.status_code == 200
    assert "results" in r.json()


def test_memory_get_concept(client):
    r = client.get("/api/memory/Something")
    assert r.status_code == 200
    assert r.json()["name"] == "Something"


def test_memory_update(client):
    r = client.post("/api/memory/update",
                    json={"name": "X", "body": "b", "type": "concept", "links": []})
    assert r.status_code == 200


def test_memory_remove(client):
    r = client.post("/api/memory/remove", json={"name": "X"})
    assert r.status_code == 200


def test_memory_autolink(client):
    r = client.post("/api/memory/autolink")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Selfdev (Dreamer)
# ---------------------------------------------------------------------------
def test_selfdev_status(client):
    r = client.get("/api/selfdev/status")
    assert r.status_code == 200


def test_selfdev_research(client):
    r = client.post("/api/selfdev/research", json={"topic": "clustering"})
    assert r.status_code == 200


def test_selfdev_factcheck(client):
    r = client.post("/api/selfdev/factcheck", json={"name": "entropy"})
    assert r.status_code == 200


def test_selfdev_dream(client):
    r = client.post("/api/selfdev/dream")
    assert r.status_code == 200


def test_selfdev_cycle(client):
    r = client.post("/api/selfdev/cycle")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Gateway
# ---------------------------------------------------------------------------
def test_gateway_status(client):
    r = client.get("/api/gateway/status")
    assert r.status_code == 200
    assert r.json()["ok"] is True


# ---------------------------------------------------------------------------
# Services / image generation
# ---------------------------------------------------------------------------
def test_services_status(client):
    r = client.get("/api/services/status")
    assert r.status_code == 200
    assert "comfyui" in r.json()


def test_generate_image(client):
    r = client.post("/api/generate", json={"prompt": "a cyberpunk city at night"})
    assert r.status_code == 200
    assert r.json()["status"] == "completed"


def test_generate_image_missing_prompt(client):
    r = client.post("/api/generate", json={"prompt": "  "})
    assert r.status_code == 400


def test_generated_image_served(client):
    # generate first to drop the png
    client.post("/api/generate", json={"prompt": "x"})
    r = client.get("/api/generated/test_render.png")
    assert r.status_code == 200


def test_zimage_template_graph_shape():
    # REGRESSION: graph must match the user's ComfyUI template — split
    # MultiGPU loaders on the configured device, AuraFlow shift+flow,
    # ConditioningZeroOut negative, easy ifElse latent switch, Rembg
    # transparent branch, SaveImageAdvanced output.
    from services.comfyui import _build_zimage_workflow
    prof = {"unet": "z_image_turbo_bf16.safetensors",
            "clip": "qwen_3_4b.safetensors", "clip_type": "lumina2",
            "vae": "ae.safetensors", "shift": 3.0}
    dev = {"unet_device": "cuda:1", "clip_device": "cuda:1", "vae_device": "cuda:1"}
    wf = _build_zimage_workflow("mascot", prof, 61615390298286, 8, 1.0, 512, 512,
                                dev, "euler_ancestral", "sgm_uniform")
    assert wf["11"]["class_type"] == "ModelSamplingAuraFlow"
    assert wf["11"]["inputs"]["sampling"] == "flow"
    assert wf["11"]["inputs"]["shift"] == 3.0
    assert wf["3"]["inputs"]["latent_image"] == ["77", 0]
    assert wf["3"]["inputs"]["negative"] == ["33", 0]
    assert wf["3"]["inputs"]["sampler_name"] == "euler_ancestral"
    assert wf["3"]["inputs"]["scheduler"] == "sgm_uniform"
    assert wf["70"]["inputs"]["device"] == "cuda:1"
    assert wf["64"]["inputs"]["device"] == "cuda:1"
    assert wf["63"]["inputs"]["device"] == "cuda:1"
    assert wf["33"]["class_type"] == "ConditioningZeroOut"
    # txt2img: ifElse true -> EmptyLatentImage
    assert wf["77"]["class_type"] == "easy ifElse"
    assert wf["77"]["inputs"]["boolean"] is True
    assert wf["78"]["class_type"] == "EmptyLatentImage"
    assert wf["90"]["class_type"] == "SaveImageAdvanced"
    assert wf["90"]["inputs"]["format.bit_depth"] == "16-bit"
    assert wf["98"]["class_type"] == "RMBG"
    assert wf["98"]["inputs"]["model"] == "RMBG-2.0"
    assert wf["82"]["class_type"] == "Image Comparer (rgthree)"
    assert wf["82"]["inputs"] == {"image_a": ["98", 0], "image_b": ["8", 0]}
    assert wf["91"]["class_type"] == "ImageBatchMulti"
    assert wf["91"]["inputs"]["image_1"] == ["98", 0]
    assert wf["91"]["inputs"]["image_2"] == ["8", 0]
    # refine: ifElse false -> VAEEncode of resized output image
    wf2 = _build_zimage_workflow("refine", prof, 7, 8, 1.0, 512, 512, dev,
                                 "euler_ancestral", "sgm_uniform",
                                 refine_of="ComfyUI_00004.png", denoise=0.6)
    assert wf2["74"]["class_type"] == "LoadImageOutput"
    assert wf2["75"]["class_type"] == "ImageResizeKJv2"
    assert wf2["76"]["class_type"] == "VAEEncode"
    assert wf2["77"]["inputs"]["boolean"] is False
    assert wf2["77"]["inputs"]["on_true"] == ["78", 0]
    assert wf2["77"]["inputs"]["on_false"] == ["76", 0]
    assert wf2["3"]["inputs"]["denoise"] == 0.6
    # Alpha fallback preserves compare and batched advanced save.
    wf3 = _build_zimage_workflow("cutout", prof, 1, 8, 1.0, 512, 512, dev,
                                 "euler_ancestral", "sgm_uniform",
                                 transparent=True, rembg_engine="alpha")
    assert wf3["98"]["class_type"] == "Image Remove Background (Alpha)"
    assert wf3["90"]["inputs"]["images"] == ["91", 0]


def test_generated_image_served_from_frameworks_dir(client, tmp_path, monkeypatch):
    # REGRESSION: in a frozen .app the data tree lands under Contents/Frameworks
    # but ROOT points at Contents/Resources. /api/generated must still serve a
    # file written there (otherwise chat images 404 and stay hidden).
    name = "frozen_layout_test.png"
    fw_dir = tmp_path / "Frameworks" / "data" / "generated"
    fw_dir.mkdir(parents=True)
    (fw_dir / name).write_bytes(b"fake-png-bytes-for-test")
    fake_exe = tmp_path / "MacOS" / "VirusGPT"
    fake_exe.parent.mkdir(parents=True)
    fake_exe.write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe))
    r = client.get("/api/generated/" + name)
    assert r.status_code == 200, r.text
    assert b"fake-png-bytes-for-test" in r.content


def test_generated_image_path_traversal(client):
    r = client.get("/api/generated/..%2f..%2fserver.py")
    assert r.status_code in (400, 404)


# ---------------------------------------------------------------------------
# Personas
# ---------------------------------------------------------------------------
def test_personas_roundtrip(client):
    p = [{"name": "Nova", "role": "assistant", "system_prompt": "be helpful"}]
    assert client.post("/api/personas", json=p).status_code == 200
    r = client.get("/api/personas")
    assert r.status_code == 200
    assert any(x["name"] == "Nova" for x in r.json())


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------
def test_sessions_crud(client):
    s = {"name": "RoomA", "messages": [{"role": "user", "content": "hi"}]}
    assert client.post("/api/sessions", json=s).status_code == 200
    r = client.get("/api/sessions")
    assert r.status_code == 200
    assert any(x["name"] == "RoomA" for x in r.json())
    assert client.delete("/api/sessions/RoomA").status_code == 200


def test_delete_missing_session(client):
    r = client.delete("/api/sessions/DoesNotExist")
    assert r.status_code in (200, 500)  # implementation returns 500 on error


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------
def test_db_status(client):
    r = client.get("/api/db/status")
    assert r.status_code == 200
    assert "backend" in r.json()


def test_db_backup(client):
    r = client.post("/api/db/backup")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Autonomous / A2A (Agent-to-Agent) — the core multi-agent flow
# ---------------------------------------------------------------------------
def test_tools_catalog(client):
    r = client.get("/api/tools")
    assert r.status_code == 200
    names = [t["name"] for t in r.json()]
    assert "shell" in names and "calc" in names and "web_search" in names


def test_autonomous_start_returns_mission(client):
    r = client.post("/api/autonomous/start", json={
        "goal": "Write a one-line hello script",
        "room_personas": [
            {"name": "Planner", "role": "planner", "system_prompt": "plan", "voice": "alba"},
            {"name": "Coder", "role": "agent", "system_prompt": "code", "voice": "alba"},
        ],
    })
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    assert d["mission_id"].startswith("M-")
    assert "/api/autonomous/stream/" in d["stream_url"]
    assert d["mission_id"]  # non-empty id returned


def test_autonomous_start_missing_goal(client):
    r = client.post("/api/autonomous/start", json={"goal": "  "})
    assert r.status_code == 400


def test_autonomous_status_missing(client):
    r = client.get("/api/autonomous/status/nope")
    assert r.status_code == 404


def test_missions_list_empty_is_list(client):
    r = client.get("/api/missions")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_autonomous_stop_unknown(client):
    r = client.post("/api/autonomous/stop/nope")
    assert r.status_code == 404


def test_autonomous_resume_unknown(client):
    r = client.post("/api/autonomous/resume/nope")
    assert r.status_code in (404, 409)


def test_autonomous_artifact_outside_data(client):
    r = client.get("/api/autonomous/artifact?path=/etc/passwd")
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Full A2A mission run (offline): plan -> tasks -> agents execute -> complete
# ---------------------------------------------------------------------------
def test_a2a_full_mission_run(client, stub_llm):
    """Start a mission and poll its status until it reaches a terminal state,
    asserting that at least one agent task was created and recorded."""
    import time

    start = client.post("/api/autonomous/start", json={
        "goal": "Summarise the README in one sentence",
        "room_personas": [
            {"name": "Planner", "role": "planner", "system_prompt": "plan", "voice": "alba"},
            {"name": "Writer", "role": "agent", "system_prompt": "write", "voice": "alba"},
        ],
    })
    assert start.status_code == 200
    mid = start.json()["mission_id"]

    # Poll status (the mission runs in a background thread on the captured loop).
    final = None
    for _ in range(40):
        r = client.get(f"/api/autonomous/status/{mid}")
        assert r.status_code == 200
        d = r.json()
        final = d
        if d["status"] in ("completed", "failed", "blocked", "cancelled"):
            break
        time.sleep(0.25)

    assert final is not None
    # The stubbed planner returns plain text (no '@Name:' lines) -> fallback
    # creates one subtask per worker, so at least 1 task must exist.
    assert len(final["tasks"]) >= 1
    assert final["status"] in ("completed", "failed", "blocked", "cancelled")
    # tool.call events (if any) must carry a parsed data dict, not a raw string
    for ev in final["events"]:
        if ev["event"] == "tool.call":
            assert isinstance(ev["data"], dict)
            assert "tool" in ev["data"]


def test_a2a_stream_endpoint(client, stub_llm):
    """The SSE stream endpoint should emit at least one data frame."""
    start = client.post("/api/autonomous/start", json={
        "goal": "Do a tiny task",
        "room_personas": [
            {"name": "Planner", "role": "planner", "system_prompt": "p", "voice": "alba"},
            {"name": "Bot", "role": "agent", "system_prompt": "b", "voice": "alba"},
        ],
    })
    mid = start.json()["mission_id"]
    with client.stream("GET", f"/api/autonomous/stream/{mid}") as s:
        frames = 0
        for line in s.iter_lines():
            if line.startswith("data: "):
                frames += 1
        assert frames >= 1


# ---------------------------------------------------------------------------
# A2A with tool-calling agent (tool.call event must be logged)
# ---------------------------------------------------------------------------
def test_a2a_agent_uses_tool(client, stub_llm):
    """Force the agent runtime to observe a tool_call by making llm emit one."""
    import time
    from services import llm

    # Patch only the runtime's llm usage via stub_llm state.
    stub_llm["tool_calls"] = [{
        "function": {"name": "calc", "arguments": {"expression": "2+2"}}
    }]

    start = client.post("/api/autonomous/start", json={
        "goal": "Compute 2+2",
        "room_personas": [
            {"name": "Planner", "role": "planner", "system_prompt": "p", "voice": "alba"},
            {"name": "MathBot", "role": "agent", "system_prompt": "m", "voice": "alba",
             "tools": ["calc"]},
        ],
    })
    mid = start.json()["mission_id"]
    final = None
    for _ in range(40):
        d = client.get(f"/api/autonomous/status/{mid}").json()
        final = d
        if d["status"] in ("completed", "failed", "blocked", "cancelled"):
            break
        time.sleep(0.25)
    # At least one tool.call event should have been recorded.
    tool_events = [e for e in final["events"] if e["event"] == "tool.call"]
    assert len(tool_events) >= 1
    assert tool_events[0]["data"]["tool"] == "calc"


# ---------------------------------------------------------------------------
# In-app updater endpoints (git/build stubbed -> fully offline + deterministic)
# ---------------------------------------------------------------------------
def test_version_endpoint(client, monkeypatch):
    from services import updater
    monkeypatch.setattr(updater, "get_version", lambda: {
        "version": "1.0", "commit": "abc1234", "updatable": True,
    })
    r = client.get("/api/version")
    assert r.status_code == 200
    d = r.json()
    assert d["version"] == "1.0" and d["commit"] == "abc1234" and d["updatable"] is True


def test_update_check_endpoint(client, monkeypatch):
    from services import updater
    monkeypatch.setattr(updater, "check_update", lambda target=None: {
        "current": "abc1234", "target": target or "beta", "latest": "def5678", "behind": True,
        "updatable": True, "notes": ["fix: thing", "feat: other"], "error": None,
    })
    r = client.get("/api/update/check")
    assert r.status_code == 200
    d = r.json()
    assert d["behind"] is True and len(d["notes"]) == 2
    # target query param is forwarded
    r2 = client.get("/api/update/check?target=main")
    assert r2.json()["target"] == "main"


def test_update_apply_then_status(client, monkeypatch):
    from services import updater
    state = {"running": False, "stage": "idle", "progress": 0, "message": "",
             "error": None, "started_at": None, "finished_at": None, "target": "beta"}
    monkeypatch.setattr(updater, "apply_update", lambda target=None: dict(state))
    monkeypatch.setattr(updater, "get_status", lambda: dict(state))
    r = client.post("/api/update/apply", json={"target": "main"})
    assert r.status_code == 200
    assert client.get("/api/update/status").json()["stage"] == "idle"


def test_update_check_not_updatable(client, monkeypatch):
    from services import updater
    monkeypatch.setattr(updater, "check_update", lambda target=None: {
        "current": "abc", "target": target or "beta", "latest": "abc", "behind": False,
        "updatable": False, "notes": [], "error": "not_updatable",
    })
    d = client.get("/api/update/check").json()
    assert d["error"] == "not_updatable" and d["updatable"] is False


def test_update_branches_and_set_branch(client, monkeypatch):
    from services import updater
    monkeypatch.setattr(updater, "_git", lambda *a, **k: "okhash")  # branches exist
    monkeypatch.setattr(updater, "get_branches", updater.get_branches)  # real
    monkeypatch.setattr(updater, "set_branch", updater.set_branch)
    r = client.get("/api/update/branches")
    assert r.status_code == 200
    assert "beta" in r.json()["available"] and "main" in r.json()["available"]
    r2 = client.post("/api/update/branch", json={"branch": "main"})
    assert r2.status_code == 200 and r2.json()["branch"] == "main"
    assert updater.update_branch() == "main"
    # reset back to beta for cleanliness
    client.post("/api/update/branch", json={"branch": "beta"})


def test_update_apply_spawns_detached_build_and_relaunch(monkeypatch, tmp_path):
    """REGRESSION: the build+relaunch must run in a DETACHED process
    (start_new_session=True) so the app can fully quit before its own bundle is
    replaced — and the detached command must rebuild AND re-open the app."""
    import services.updater as updater
    src = tmp_path / "repo"
    src.mkdir()
    (src / "desktop").mkdir()
    (src / "desktop" / "build-macos.py").write_text("")
    venv = tmp_path / "venv" / "bin" / "python"
    venv.parent.mkdir(parents=True)
    venv.write_text("")
    monkeypatch.setattr(updater, "get_buildinfo", lambda: {
        "source_dir": str(src), "venv": str(venv), "updatable": True,
    })
    monkeypatch.setattr(updater, "_git_ok", lambda *a, **k: True)
    monkeypatch.setattr(updater, "_git", lambda *a, **k: "")
    captured = {}
    def fake_popen(args, **kw):
        captured["args"] = args
        captured["kw"] = kw
        class _P:
            pass
        return _P()
    monkeypatch.setattr(updater.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(updater.os, "_exit", lambda c: None)
    updater.apply_update()
    # _run_update runs in a daemon thread; wait for it to spawn the detached build.
    for _ in range(50):
        if captured:
            break
        time.sleep(0.05)
    assert captured, "detached build was never spawned"
    cmd = captured["args"][-1] if isinstance(captured["args"], list) else captured["args"]
    assert captured["kw"].get("start_new_session") is True, "build must run detached"
    assert "desktop/build-macos.py" in cmd, "detached cmd must rebuild"
    assert "open" in cmd and "VirusGPT.app" in cmd, "detached cmd must relaunch the app"
