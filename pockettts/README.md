# PocketTTS Server (vendored for VirusGPT)

This directory contains the PocketTTS OpenAI-compatible TTS server that VirusGPT
runs locally on macOS. It wraps `pocket-tts` with a Flask API and exposes a
small OpenAI-style surface for text-to-speech, voice listing, model switching,
and health checks.

## What this version does

- `POST /v1/audio/speech` — generate speech audio
- `GET /v1/voices` — list available voices
- `GET /v1/model` — inspect active model state
- `POST /v1/model` — request a runtime language/model switch
- `GET /health` — service health for orchestration
- Built-in voice names plus custom voices from a directory or cache
- CPU-friendly local inference with optional streaming and text preprocessing
- Waitress for production; Flask dev server as fallback

## How VirusGPT uses it

VirusGPT launches this server on `127.0.0.1:49152` through `launch.sh` and calls
it from the main app’s `/api/tts` proxy. The main app chooses the persona voice,
and PocketTTS turns that text into audio bytes.

## Configuration

Environment variables are the primary config surface:

- `POCKET_TTS_HOST` — bind address, default `0.0.0.0`
- `POCKET_TTS_PORT` — port, default `49112` upstream / `49152` in VirusGPT
- `POCKET_TTS_MODEL_PATH` — custom model path
- `POCKET_TTS_LANGUAGE` — built-in language preset
- `POCKET_TTS_QUANTIZE` — enable int8 quantization
- `POCKET_TTS_VOICES_DIR` — custom voice directory
- `POCKET_TTS_STREAM_DEFAULT` — stream by default
- `POCKET_TTS_TEXT_PREPROCESS_DEFAULT` — preprocess text by default
- `POCKET_TTS_LOG_LEVEL` — log level
- `POCKET_TTS_LOG_DIR` — log directory
- `POCKET_TTS_DEFAULT_VOICE` — fallback voice id
- `POCKET_TTS_VOICE_CACHE_DIR` — cache directory for cloned voices

## Built-in voices

The bundled/default voices are:

`alba`, `marius`, `javert`, `jean`, `fantine`, `cosette`, `eponine`, `azelma`

## Repository layout

- `server.py` — CLI entry point and server startup
- `app/routes.py` — HTTP routes
- `app/services/tts.py` — model loading, voice resolution, generation
- `app/services/audio.py` — audio conversion and streaming helpers
- `app/services/preprocess.py` — text cleanup
- `app/config.py` — environment/config defaults
- `tests/` — route and service tests

## Quick check

```bash
cd /Users/Master/virusgpt-mac/pockettts
. ../.venv/bin/activate
python server.py --help
curl http://localhost:49152/health
```

## Notes

- Custom voice files are resolved from the configured voice directory first.
- Voice clones require the PocketTTS model support present in the runtime.
- VirusGPT depends on the server staying local; no auth is configured.
