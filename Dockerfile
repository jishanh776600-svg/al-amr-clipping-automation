# ==============================================================================
# AL AMR CLIPPING // Autonomous Video Production Control Room (Render Production)
# ==============================================================================
FROM python:3.11-slim-bookworm

# Prevent Python from writing .pyc files and enable unbuffered streaming logs
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    LOCAL_STORAGE_ROOT=/var/data/project_vault

# Install FFmpeg, OpenGL libraries for OpenCV/SceneDetect, FontConfig for Subtitles, and Curl for Healthchecks
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    fontconfig \
    fonts-dejavu-core \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Ensure persistent storage mount directory exists
RUN mkdir -p /var/data/project_vault /app/project_vault

# Copy dependency definition and source tree
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Install dependencies and project in editable production mode
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e .

# Expose Render standard port
EXPOSE 8000

# Healthcheck probe using Render's /healthz endpoint
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8000}/healthz || exit 1

# Start Web Service by default (Render passes dynamic $PORT)
CMD ["sh", "-c", "uvicorn clipping.ui.server:app --host 0.0.0.0 --port ${PORT:-8000}"]
