# PocketTTS voice server image (CPU-only torch).
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    POCKET_TTS_PORT=49152 \
    POCKET_TTS_HOST=0.0.0.0

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pockettts/requirements.txt .
# PocketTTS needs the PyTorch CPU index
RUN pip install --upgrade pip \
    && pip install --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt

COPY pockettts/ ./pockettts/
WORKDIR /app/pockettts

EXPOSE 49152

CMD ["sh", "-c", "python server.py"]
