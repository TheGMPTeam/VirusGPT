# Project Overview

This subtree vendors the PocketTTS service used by VirusGPT.
It exposes a local, OpenAI-compatible TTS API for the macOS app.

## What it provides

- `POST /v1/audio/speech` for speech generation
- `GET /v1/voices` for voice listing
- `GET /v1/model` and `POST /v1/model` for active model inspection/switching
- `GET /health` for startup/health checks
- Built-in voice ids plus custom voice resolution from a directory/cache
- Streaming and text preprocessing options

## Runtime shape

```
server.py
  └── create_app()
      ├── app/routes.py
      ├── app/config.py
      └── app/services/
          ├── tts.py
          ├── audio.py
          ├── preprocess.py
          ├── versions.py
          └── voice_cache.py
```

## Important files

| File | Purpose |
|------|---------|
| `server.py` | CLI entry point and production server startup |
| `app/routes.py` | Flask routes for health, voices, model state, and speech |
| `app/services/tts.py` | Model loading, voice validation, generation, caching |
| `app/services/audio.py` | Output format conversion and streaming helpers |
| `app/config.py` | Env-driven config defaults and bundle paths |

## Voice resolution

1. Built-in voice ids such as `alba`, `cosette`, `azelma`
2. Voices in `POCKET_TTS_VOICES_DIR`
3. Absolute file paths when supported by the runtime
4. PocketTTS fallback behavior

## Configuration keys

- `POCKET_TTS_HOST`
- `POCKET_TTS_PORT`
- `POCKET_TTS_MODEL_PATH`
- `POCKET_TTS_LANGUAGE`
- `POCKET_TTS_QUANTIZE`
- `POCKET_TTS_VOICES_DIR`
- `POCKET_TTS_STREAM_DEFAULT`
- `POCKET_TTS_TEXT_PREPROCESS_DEFAULT`
- `POCKET_TTS_LOG_LEVEL`
- `POCKET_TTS_LOG_DIR`
- `POCKET_TTS_DEFAULT_VOICE`
- `POCKET_TTS_VOICE_CACHE_DIR`

## Development notes

- `--language` and `--model-path` are mutually exclusive.
- Waitress is preferred when available; Flask dev server is the fallback.
- This service is intentionally local-only and unauthenticated for VirusGPT.
