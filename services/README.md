# Services

Thin async clients that connect VirusGPT to its backend services. Each service
is reached over the LAN by URL (configured in `config.json`).

## Layout

```
services/
├── __init__.py   # get_client() — one pooled AsyncClient for the process
├── config.py     # loads config.json + VG_* env overrides; service_url()
├── llm.py        # Ollama chat streaming + native tool_calls
├── tts.py        # PocketTTS OpenAI-compatible client
├── stt.py        # Whisper client
└── memory.py     # pass-through to memory/store.py (own memory)
```

## Clients

### `llm.py` — Ollama

- `chat(messages, model, stream=True)` → SSE stream of tokens
- `tools_chat(messages, tools, model)` → native Ollama function calling
- Uses the shared `AsyncClient` from `__init__.py`

### `tts.py` — PocketTTS

- `synthesize(text, voice, stream=False)` → audio bytes
- `list_voices()` → available voice ids
- OpenAI-compatible: `POST /v1/audio/speech`

### `stt.py` — Whisper

- `transcribe(audio_bytes, language=None)` → `{"text":"..."}`
- OpenAI-compatible: `POST /v1/audio/transcriptions`

### `memory.py` — Own concept store

- Thin async wrapper around `memory/store.py`
- `graph()`, `retrieve(query, k)`, `save_concept(...)`, etc.

## Configuration

All URLs come from `config.json` (overridable by `VG_*` env vars):

```jsonc
{
  "ollama": {"base_url": "http://10.0.0.120:11434", "default_model": "qwen2.5:3b"},
  "tts":    {"base_url": "http://localhost:49152", "default_voice": "alba"},
  "stt":    {"base_url": "http://localhost:8181"},
  "memory": {"bundle": "data/memory"}
}
```

## Notes

- One `AsyncClient` per process (connection pooling, shared timeout).
- Each client is a standalone module — add a new service by mirroring
  `services/tts.py`.
- No auth; all services are local-only.
