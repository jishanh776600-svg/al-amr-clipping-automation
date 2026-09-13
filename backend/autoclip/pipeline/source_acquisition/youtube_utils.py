"""YouTube URL helpers, ID extraction, and multimedia stream merging."""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..ffmpeg import ffmpeg_path

log = logging.getLogger(__name__)

YOUTUBE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{11}$")


def extract_youtube_id(url_or_id: str) -> str | None:
    """Extract standard 11-character YouTube video ID from various URL formats.

    Supports:
    - https://www.youtube.com/watch?v=VIDEO_ID
    - https://youtu.be/VIDEO_ID
    - https://www.youtube.com/shorts/VIDEO_ID
    - https://www.youtube.com/embed/VIDEO_ID
    - https://m.youtube.com/watch?v=VIDEO_ID
    - https://youtube-nocookie.com/embed/VIDEO_ID
    - Raw 11-char string
    """
    if not url_or_id:
        return None

    raw = url_or_id.strip()
    if YOUTUBE_ID_PATTERN.match(raw):
        return raw

    try:
        parsed = urlparse(raw)
    except Exception:
        return None

    netloc = parsed.netloc.lower()
    path = parsed.path

    if "youtu.be" in netloc:
        parts = [p for p in path.split("/") if p]
        if parts and YOUTUBE_ID_PATTERN.match(parts[0]):
            return parts[0]

    if any(h in netloc for h in ("youtube.com", "youtube-nocookie.com")):
        if path == "/watch" or path == "/watch_popup":
            qs = parse_qs(parsed.query)
            v = qs.get("v")
            if v and YOUTUBE_ID_PATTERN.match(v[0]):
                return v[0]

        for prefix in ("/shorts/", "/embed/", "/v/", "/live/"):
            if path.startswith(prefix):
                candidate = path[len(prefix) :].split("/")[0].split("?")[0]
                if YOUTUBE_ID_PATTERN.match(candidate):
                    return candidate

    # Fallback regex search across the raw string
    m = re.search(r"(?:v=|\/embed\/|\/shorts\/|\/v\/|youtu\.be\/)([a-zA-Z0-9_-]{11})", raw)
    if m:
        return m.group(1)

    return None


def is_youtube_url(url: str) -> bool:
    """Check whether a given URL points to YouTube."""
    return extract_youtube_id(url) is not None


def merge_video_audio(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
    timeout_s: float = 60.0,
) -> Path:
    """Merge separate video and audio streams into a single MP4 container using FFmpeg.

    Tries stream copy first for zero quality loss and instant speed.
    Falls back to re-encoding audio to AAC if stream copy is rejected by the container.
    """
    cmd_copy = [
        ffmpeg_path(),
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(output_path),
    ]

    try:
        subprocess.run(
            cmd_copy,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            timeout=timeout_s,
        )
        return output_path
    except (subprocess.CalledProcessError, subprocess.TimeoutError) as exc:
        log.warning("Stream copy failed; falling back to AAC audio transcoding: %s", exc)

    # Fallback: copy video, transcode audio to AAC
    cmd_transcode = [
        ffmpeg_path(),
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]

    subprocess.run(
        cmd_transcode,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        timeout=timeout_s,
    )
    return output_path
