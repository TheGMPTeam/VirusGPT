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
COPY app/ ./app/
COPY vgctl.py .

EXPOSE 8500

# Single container runs the FastAPI server (deps tts/stt/ollama are remote, see compose)
CMD ["sh", "-c", "python server.py"]
