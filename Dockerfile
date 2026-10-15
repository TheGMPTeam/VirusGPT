# VirusGPT server image.
# Multi-stage: build venv, run slim.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps for building wheels (kept minimal; sqlite is in slim)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (better layer caching)
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# App source
COPY server.py .
COPY config.json .
COPY services/ ./services/
COPY autonomous/ ./autonomous/
COPY memory/ ./memory/
COPY gateway/ ./gateway/
COPY tools/ ./tools/
COPY app/ ./app/
COPY vgctl.py .

# Bake honest build metadata into the image. The committed app/buildinfo.json
# points at the macOS dev box; shipping it made /api/version lie
# (source_dir=/Users/Master/... and features() claimed an updater that
# /api/update/check then refused). Container builds get a container buildinfo
# (no source tree/venv -> updatable:false) plus a version.json stamped with
# the git commit and build time passed by the build script.
ARG BUILD_COMMIT=unknown
ARG BUILD_TIME=unknown
ARG BUILD_CHANNEL=beta
RUN printf '{"source_dir": "", "venv": ""}\n' > app/buildinfo.json \
 && printf '{"version": "beta", "commit": "%s", "build_time": "%s", "channel": "%s"}\n' "$BUILD_COMMIT" "$BUILD_TIME" "$BUILD_CHANNEL" > app/version.json

EXPOSE 8500

# Single container runs the FastAPI server (deps tts/stt/ollama are remote, see compose)
CMD ["sh", "-c", "python server.py"]
