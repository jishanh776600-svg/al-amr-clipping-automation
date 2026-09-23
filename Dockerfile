# syntax=docker/dockerfile:1

# AutoClip — Control Plane Image for blitz.cloud
# The heavy video rendering & AI pipeline (FFmpeg, Whisper, MediaPipe)
# runs on GitHub Actions workers. This container serves the FastAPI control plane,
# React web dashboard, SQLite state, and Telegram bot.

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
# Runtime base (Ultra-Lightweight Control Plane)
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    AUTOCLIP_HOME=/data \
    PORT=8000

# curl for container health check
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY backend/ ./backend/
COPY --from=frontend /backend/autoclip/static ./backend/autoclip/static

# Install control plane dependencies only.
# Heavy ML/rendering packages (torch, mediapipe, whisper) are executed
# remotely on GitHub Actions workers and are not needed on Blitz.
RUN pip install --no-cache-dir \
      "fastapi>=0.115" \
      "uvicorn[standard]>=0.32" \
      "sse-starlette>=2.1" \
      "python-multipart>=0.0.9" \
      "pydantic>=2.9" \
      "pydantic-settings>=2.5" \
      "keyring>=25.4" \
      "cryptography>=42.0" \
      "httpx[socks]>=0.27" \
      "socksio>=1.0.0" \
      "google-api-python-client>=2.100.0" \
      "google-auth>=2.20.0" \
      "pypdf>=4.0.0" \
      "python-docx>=1.1.0" \
      "typer>=0.12" \
      "rich>=13.9" \
      "requests>=2.31.0" \
      "yt-dlp>=2024.1.1" \
    && pip install --no-cache-dir --no-deps .

# Non-root user with persistent storage ownership
RUN mkdir -p /data \
    && groupadd --system autoclip \
    && useradd --system --gid autoclip --create-home autoclip \
    && chown -R autoclip:autoclip /data /app /home/autoclip

USER autoclip
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:${PORT:-8000}/health || exit 1

# Start via uvicorn with proxy headers for blitz.cloud edge proxy
CMD ["sh", "-c", "exec python -m uvicorn autoclip.app:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips '*' --log-level info"]
