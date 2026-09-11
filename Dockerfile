# syntax=docker/dockerfile:1

# AutoClip — Production CPU & GPU image for AL AMR Clipping Automation.
#
# Docker is the guaranteed path: it pins ffmpeg with libass, the exact Python
# version MediaPipe has wheels for, and the font situation.

# ---------------------------------------------------------------------------
# Frontend build
# ---------------------------------------------------------------------------
FROM node:22-slim AS frontend

WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund

COPY frontend/ ./
# Vite writes into ../backend/autoclip/static, so that path has to exist.
RUN mkdir -p /backend/autoclip && npm run build -- --outDir /backend/autoclip/static

# ---------------------------------------------------------------------------
# Runtime base
# ---------------------------------------------------------------------------
# Python 3.12: MediaPipe publishes 3.12 wheels, required for speaker reframing.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    AUTOCLIP_HOME=/data \
    PORT=8000

# ffmpeg with libass/libx264, fonts-liberation for subtitles, curl for health checks
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg \
      fonts-liberation \
      libgl1 \
      libglib2.0-0 \
      curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY backend/ ./backend/
COPY --from=frontend /backend/autoclip/static ./backend/autoclip/static

RUN pip install --no-cache-dir .

# Persistent storage volume
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:${PORT:-8000}/health || exit 1

# Start AutoClip service with dynamic PORT support
CMD ["sh", "-c", "autoclip serve --host 0.0.0.0 --port ${PORT:-8000} --no-open"]

# ---------------------------------------------------------------------------
# GPU variant
# ---------------------------------------------------------------------------
FROM base AS gpu

# CUDA 12 runtime libraries for CTranslate2 / faster-whisper GPU acceleration
RUN pip install --no-cache-dir \
      "nvidia-cublas-cu12>=12.4" \
      "nvidia-cudnn-cu12>=9.1"
