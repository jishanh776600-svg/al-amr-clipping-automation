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

# --------------------------------------------------------------------------
# Non-root user — required by blitz.cloud (and a good security practice).
# The autoclip user owns /data (persistent disk) and /app.
# /data is created here so chown can set ownership before the runtime
# volume mount overwrites it. Docker preserves the UID/GID from the image
# layer so blitz's persistent volume is initialized with the right owner.
# --------------------------------------------------------------------------
RUN mkdir -p /data \
    && groupadd --system autoclip \
    && useradd --system --gid autoclip --create-home autoclip \
    && chown -R autoclip:autoclip /data /app /home/autoclip

USER autoclip

# Persistent storage volume — declared AFTER USER so blitz.cloud assigns the
# volume to the correct UID. Files written here survive container restarts.
VOLUME ["/data"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:${PORT:-8000}/health || exit 1

# Start via uvicorn directly so we can pass --proxy-headers.
# blitz.cloud terminates HTTPS at its edge proxy — without --proxy-headers,
# uvicorn would log all requests as HTTP and 127.0.0.1 instead of the real
# client IP/scheme. --forwarded-allow-ips=* is safe here because blitz's
# proxy is the only host that can reach port 8000.
CMD ["sh", "-c", "exec python -m uvicorn autoclip.app:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips '*' --log-level info"]
