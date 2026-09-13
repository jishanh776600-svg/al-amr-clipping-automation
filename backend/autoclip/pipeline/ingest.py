"""Ingestion — YouTube URLs and local file uploads become source records.

Both paths converge on a validated :class:`~autoclip.db.models.Source` with a
file on disk and probed metadata.

A note on YouTube: as of 2026 most anonymous downloads hit a bot check, and
proof-of-origin tokens no longer clear it reliably — browser cookies do. So
:class:`IngestError` distinguishes that specific failure and tells the user how
to fix it, rather than surfacing a raw yt-dlp traceback.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from collections.abc import Callable
from pathlib import Path

from .. import paths
from ..config import IngestSettings
from ..db.models import Source, new_id
from . import ffmpeg

log = logging.getLogger(__name__)

#: Container and audio formats we accept for upload.
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}
ACCEPTED_SUFFIXES = VIDEO_SUFFIXES | AUDIO_SUFFIXES

from . import youtube_acquirer
from .youtube_acquirer import (
    AcquisitionResult,
    YouTubeErrorCode,
    YouTubeIngestError,
    YouTubeSourceAcquirer,
    is_youtube_url,
    translate_ytdlp_error,
    YOUTUBE_AUTH_REQUIRED,
    YOUTUBE_DOWNLOAD_FAILED,
    YOUTUBE_EXTRACTION_BLOCKED,
    YOUTUBE_FORMAT_ERROR,
    YOUTUBE_INVALID_URL,
    YOUTUBE_MEDIA_INVALID,
    YOUTUBE_NETWORK_ERROR,
    YOUTUBE_VIDEO_UNAVAILABLE,
)
from .source_acquisition import (
    AcquisitionResult as SourceAcquisitionResult,
    JobContext,
    SourceAcquisitionError,
    SourceAcquisitionRegistry,
    SourceErrorCode,
    get_default_registry,
)


def is_direct_media_url(url: str) -> bool:
    """Check if the URL points directly at a supported media file."""
    from urllib.parse import unquote, urlparse

    try:
        parsed = urlparse(url)
        path = unquote(parsed.path).lower()
    except Exception:
        return False
    return any(path.endswith(ext) for ext in ACCEPTED_SUFFIXES)


def slugify(text: str, *, max_length: int = 60) -> str:
    """Turn a title into a filesystem- and URL-safe slug."""
    slug = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip().lower()
    slug = re.sub(r"[\s_-]+", "-", slug).strip("-")
    return slug[:max_length] or "clip"


# --------------------------------------------------------------------------
# Direct URL Ingestion
# --------------------------------------------------------------------------


def ingest_direct_url(
    url: str,
    settings: IngestSettings | None = None,
    *,
    on_progress: Callable[[float], None] | None = None,
) -> Source:
    """Download a direct media URL using chunked HTTP streaming and register as a source."""
    from urllib.parse import unquote, urlparse
    import httpx

    parsed = urlparse(url)
    if not parsed.scheme or parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise IngestError(f"Invalid URL: {url}", hint="URLs must start with http:// or https://")

    clean_path = unquote(parsed.path)
    suffix = Path(clean_path).suffix.lower()
    if suffix not in ACCEPTED_SUFFIXES:
        suffix = ".mp4"

    source_id = new_id()
    target_dir = paths.source_media_dir(source_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"source{suffix}"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 AutoClip/0.1.0"
        ),
        "Accept": "*/*",
    }
    try:
        with httpx.Client(follow_redirects=True, timeout=60.0) as client:
            with client.stream("GET", url, headers=headers) as response:
                if response.status_code >= 400:
                    raise IngestError(
                        f"HTTP {response.status_code} while downloading {url}",
                        hint=f"Server returned status {response.status_code} ({response.reason_phrase}).",
                    )

                total_bytes = None
                content_len = response.headers.get("content-length")
                if content_len and content_len.isdigit():
                    total_bytes = int(content_len)

                downloaded = 0
                with target.open("wb") as f:
                    for chunk in response.iter_bytes(chunk_size=65536):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if on_progress and total_bytes and total_bytes > 0:
                            on_progress(min(1.0, downloaded / total_bytes))
    except IngestError:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise IngestError(f"Failed to download direct media URL: {exc}") from exc

    if not target.exists() or target.stat().st_size == 0:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise IngestError(f"Download from {url} produced an empty file.")

    info = _probe_and_validate(target)
    title = Path(clean_path).stem or "direct_clip"

    return Source(
        id=source_id,
        type="upload",
        url=url,
        path=str(target),
        filename=target.name,
        title=title,
        channel=parsed.netloc,
        duration_s=info.duration_s,
        width=info.width,
        height=info.height,
        fps=info.fps,
        has_audio=info.has_audio,
        has_video=info.has_video,
    )


def ingest_url(
    url: str,
    settings: IngestSettings | None = None,
    *,
    on_progress: Callable[[float], None] | None = None,
) -> Source:
    """Ingest any media URL — YouTube, streaming sites via yt-dlp, or direct HTTP files."""
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
        if not parsed.scheme or parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise IngestError(f"Invalid URL: {url}", hint="URLs must start with http:// or https://")
    except Exception as exc:
        raise IngestError(f"Invalid URL: {url}") from exc

    if is_direct_media_url(url):
        log.info("Detected direct media URL: %s", url)
        return ingest_direct_url(url, settings, on_progress=on_progress)

    # Try yt-dlp for YouTube and other supported streaming services
    try:
        return ingest_youtube(url, settings, on_progress=on_progress)
    except IngestError as exc:
        msg = str(exc).lower()
        # Fall back to direct HTTP streaming if yt-dlp doesn't recognise extractor
        if any(marker in msg for marker in ("unsupported url", "no suitable info extractor", "could not download")):
            log.info("yt-dlp could not handle %s; attempting direct HTTP download fallback.", url)
            try:
                return ingest_direct_url(url, settings, on_progress=on_progress)
            except Exception:
                raise exc
        raise


# --------------------------------------------------------------------------
# YouTube
# --------------------------------------------------------------------------


def validate_downloaded_media(path: Path) -> ffmpeg.MediaInfo:
    """Rigorous post-download media validation ensuring valid container, audio, and video streams."""
    return youtube_acquirer.validate_downloaded_media(path)


def _find_downloaded_file(directory: Path) -> Path | None:
    """Return the largest media file in a directory, ignoring sidecars."""
    return youtube_acquirer._find_downloaded_file(directory)


def _translate_ytdlp_error(exc: Exception, settings: IngestSettings | None = None) -> YouTubeIngestError:
    """Classify yt-dlp failures into canonical error codes with actionable operator guidance."""
    return youtube_acquirer.translate_ytdlp_error(exc, settings)


def ingest_youtube(
    url: str,
    settings: IngestSettings | None = None,
    *,
    on_progress: Callable[[float], None] | None = None,
) -> Source:
    """Download a YouTube video using YouTubeSourceAcquirer and return a validated source record.

    Preconditions:
        url points at content the user owns or has the rights to process.
    """
    settings = settings or IngestSettings()
    source_id = new_id()
    target_dir = paths.source_media_dir(source_id)

    registry = get_default_registry(settings)
    job_context = JobContext(source_id=source_id, settings=settings)
    result = registry.acquire(url, target_dir, job_context=job_context, on_progress=on_progress)

    downloaded = result.local_media_path
    info = result.media_info
    metadata = result.provider_metadata or {}

    source = Source(
        id=source_id,
        type="youtube",
        url=url,
        path=str(downloaded),
        filename=downloaded.name,
        title=metadata.get("title") or downloaded.stem,
        channel=metadata.get("channel") or "",
        duration_s=(getattr(info, "duration_s", 0.0) if info else result.duration) or result.duration,
        width=getattr(info, "width", None) if info else None,
        height=getattr(info, "height", None) if info else None,
        fps=getattr(info, "fps", None) if info else None,
        has_audio=getattr(info, "has_audio", True) if info else True,
        has_video=getattr(info, "has_video", True) if info else True,
    )
    if "provenance" in metadata:
        setattr(source, "source_acquisition", metadata["provenance"])
    return source


# --------------------------------------------------------------------------
# Upload
# --------------------------------------------------------------------------


def ingest_file(path: Path, *, move: bool = False, title: str | None = None) -> Source:
    """Register a local file as a source, copying it into AutoClip's media store.

    Copying rather than referencing in place means a job stays reproducible even
    if the user moves or deletes the original.

    Preconditions:
        path exists and is a readable media file.
    """
    path = Path(path)
    if not path.exists():
        raise IngestError(f"{path} does not exist.")
    if not path.is_file():
        raise IngestError(f"{path} is not a file.")

    suffix = path.suffix.lower()
    if suffix not in ACCEPTED_SUFFIXES:
        accepted = ", ".join(sorted(ACCEPTED_SUFFIXES))
        raise IngestError(
            f"{suffix or 'This file'} is not a supported format.",
            hint=f"Accepted formats: {accepted}",
        )

    source_id = new_id()
    target_dir = paths.source_media_dir(source_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"source{suffix}"

    try:
        if move:
            shutil.move(str(path), target)
        else:
            shutil.copy2(path, target)
    except OSError as exc:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise IngestError(f"Could not store {path.name}: {exc}") from exc

    try:
        info = _probe_and_validate(target)
    except Exception:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise

    return Source(
        id=source_id,
        type="upload",
        path=str(target),
        filename=path.name,
        title=title or info.title or path.stem,
        duration_s=info.duration_s,
        width=info.width,
        height=info.height,
        fps=info.fps,
        has_audio=info.has_audio,
        has_video=info.has_video,
    )


def _probe_and_validate(path: Path) -> ffmpeg.MediaInfo:
    """Probe a media file and reject anything the pipeline can't process."""
    try:
        info = ffmpeg.probe(path)
    except ffmpeg.FFmpegError as exc:
        raise IngestError(f"{path.name} could not be read as media.", hint=str(exc)) from exc

    if not info.has_audio:
        raise IngestError(
            f"{path.name} has no audio track.",
            hint=(
                "AutoClip finds clips by transcribing speech, so a file with no audio "
                "has nothing to work from."
            ),
        )
    if info.duration_s <= 0:
        raise IngestError(
            f"{path.name} reports zero duration.",
            hint="The file may be corrupt or still being written.",
        )

    return info
