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

_YOUTUBE_HOSTS = ("youtube.com", "youtu.be", "www.youtube.com", "m.youtube.com")

#: yt-dlp error fragments that mean "YouTube wants proof you're a human".
_BOT_CHECK_MARKERS = (
    "sign in to confirm",
    "confirm you're not a bot",
    "confirm you are not a bot",
    "this content isn't available",
    "player response",
)


class IngestError(RuntimeError):
    """Ingestion failed in a way worth explaining to the user."""

    def __init__(self, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:
        base = super().__str__()
        return f"{base}\n\n{self.hint}" if self.hint else base


class YouTubeErrorCode:
    """Canonical error codes for YouTube ingestion."""

    INVALID_URL = "YOUTUBE_INVALID_URL"
    VIDEO_UNAVAILABLE = "YOUTUBE_VIDEO_UNAVAILABLE"
    AUTH_REQUIRED = "YOUTUBE_AUTH_REQUIRED"
    EXTRACTION_BLOCKED = "YOUTUBE_EXTRACTION_BLOCKED"
    NETWORK_ERROR = "YOUTUBE_NETWORK_ERROR"
    FORMAT_ERROR = "YOUTUBE_FORMAT_ERROR"
    DOWNLOAD_FAILED = "YOUTUBE_DOWNLOAD_FAILED"
    MEDIA_INVALID = "YOUTUBE_MEDIA_INVALID"


YOUTUBE_INVALID_URL = YouTubeErrorCode.INVALID_URL
YOUTUBE_VIDEO_UNAVAILABLE = YouTubeErrorCode.VIDEO_UNAVAILABLE
YOUTUBE_AUTH_REQUIRED = YouTubeErrorCode.AUTH_REQUIRED
YOUTUBE_EXTRACTION_BLOCKED = YouTubeErrorCode.EXTRACTION_BLOCKED
YOUTUBE_NETWORK_ERROR = YouTubeErrorCode.NETWORK_ERROR
YOUTUBE_FORMAT_ERROR = YouTubeErrorCode.FORMAT_ERROR
YOUTUBE_DOWNLOAD_FAILED = YouTubeErrorCode.DOWNLOAD_FAILED
YOUTUBE_MEDIA_INVALID = YouTubeErrorCode.MEDIA_INVALID


class YouTubeIngestError(IngestError):
    """Structured YouTube ingestion failure with explicit classification code."""

    def __init__(
        self,
        message: str,
        *,
        code: str = YouTubeErrorCode.DOWNLOAD_FAILED,
        hint: str = "",
    ) -> None:
        super().__init__(message, hint=hint)
        self.code = code

    def __str__(self) -> str:
        base = super().__str__()
        return f"[{self.code}] {base}"


def is_youtube_url(url: str) -> bool:
    from urllib.parse import urlparse

    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return host in _YOUTUBE_HOSTS


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
    if not path.exists():
        raise YouTubeIngestError(
            f"Downloaded media file does not exist: {path.name}",
            code=YouTubeErrorCode.MEDIA_INVALID,
        )
    if not path.is_file():
        raise YouTubeIngestError(
            f"Target path is not a regular file: {path.name}",
            code=YouTubeErrorCode.MEDIA_INVALID,
        )

    size_bytes = path.stat().st_size
    if size_bytes == 0:
        raise YouTubeIngestError(
            f"Downloaded media file {path.name} is completely empty (0 bytes).",
            code=YouTubeErrorCode.MEDIA_INVALID,
            hint="The download was interrupted or produced an empty response.",
        )

    # Check for HTML / error pages masquerading as media files
    try:
        with path.open("rb") as f:
            header_sample = f.read(512).lower()
        if b"<!doctype html" in header_sample or b"<html" in header_sample or b"<head" in header_sample:
            raise YouTubeIngestError(
                f"Downloaded file {path.name} is an HTML document, not valid video.",
                code=YouTubeErrorCode.MEDIA_INVALID,
                hint="YouTube or a proxy returned an HTML block page instead of media stream.",
            )
    except OSError as exc:
        raise YouTubeIngestError(
            f"Failed reading downloaded file header: {exc}",
            code=YouTubeErrorCode.MEDIA_INVALID,
        ) from exc

    # Run ffprobe validation
    try:
        info = ffmpeg.probe(path)
    except ffmpeg.FFmpegError as exc:
        raise YouTubeIngestError(
            f"{path.name} could not be parsed as valid media by FFprobe.",
            code=YouTubeErrorCode.MEDIA_INVALID,
            hint=str(exc),
        ) from exc

    if info.duration_s <= 0:
        raise YouTubeIngestError(
            f"{path.name} reports invalid or zero duration ({info.duration_s}s).",
            code=YouTubeErrorCode.MEDIA_INVALID,
            hint="The media stream is corrupt or truncated.",
        )

    if not info.has_video:
        raise YouTubeIngestError(
            f"{path.name} contains no video stream.",
            code=YouTubeErrorCode.MEDIA_INVALID,
            hint="A valid video stream is required for vertical clipping and framing.",
        )

    if not info.has_audio:
        raise YouTubeIngestError(
            f"{path.name} has no audio stream.",
            code=YouTubeErrorCode.MEDIA_INVALID,
            hint="AutoClip finds clips by transcribing speech, so an audio stream is required.",
        )

    return info


def ingest_youtube(
    url: str,
    settings: IngestSettings | None = None,
    *,
    on_progress: Callable[[float], None] | None = None,
) -> Source:
    """Download a YouTube video using upstream yt-dlp and return a validated source record.

    Preconditions:
        url points at content the user owns or has the rights to process.
    """
    import yt_dlp

    # Validate URL structure
    if not url or not str(url).strip():
        raise YouTubeIngestError("No YouTube URL provided.", code=YouTubeErrorCode.INVALID_URL)

    settings = settings or IngestSettings()
    source_id = new_id()
    target_dir = paths.source_media_dir(source_id)
    target_dir.mkdir(parents=True, exist_ok=True)

    def hook(status: dict) -> None:
        if not on_progress or status.get("status") != "downloading":
            return
        total = status.get("total_bytes") or status.get("total_bytes_estimate")
        done = status.get("downloaded_bytes")
        if total and done:
            on_progress(min(1.0, done / total))

    options: dict = {
        "format": settings.ytdlp_format,
        "outtmpl": str(target_dir / "source.%(ext)s"),
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "progress_hooks": [hook],
        "retries": 3,
        "fragment_retries": 3,
    }

    # Detect external JavaScript runtime for yt-dlp / yt-dlp-ejs challenge execution
    for candidate in ("deno", "node", "nodejs", "bun"):
        if shutil.which(candidate):
            options["js_runtimes"] = {candidate: {}}
            log.info("Upstream yt-dlp: JavaScript runtime '%s' discovered for challenges", candidate)
            break

    # Handle optional server-side cookies (explicit file or environment secret)
    cookies_file = settings.cookies_file or os.environ.get("AUTOCLIP_COOKIES_FILE")
    env_cookies_text = os.environ.get("YOUTUBE_COOKIES_TEXT") or os.environ.get("YOUTUBE_COOKIES")

    tmp_cookies: Path | None = None
    if env_cookies_text and not cookies_file:
        tmp_cookies = target_dir / "cookies.txt"
        tmp_cookies.write_text(env_cookies_text, encoding="utf-8")
        options["cookiefile"] = str(tmp_cookies)
    elif cookies_file and Path(cookies_file).expanduser().is_file():
        options["cookiefile"] = str(Path(cookies_file).expanduser())
    elif settings.cookies_from_browser:
        # Legacy local desktop fallback ONLY; cloud workers run browserless
        is_ci_or_headless = bool(
            os.environ.get("CI")
            or os.environ.get("GITHUB_ACTIONS")
            or os.environ.get("RENDER")
            or (os.name != "nt" and not os.environ.get("DISPLAY"))
        )
        if not is_ci_or_headless:
            options["cookiesfrombrowser"] = (settings.cookies_from_browser,)

    if settings.prefer_youtube_captions:
        options["writeautomaticsub"] = True
        options["subtitleslangs"] = ["en.*"]
        options["subtitlesformat"] = "json3"

    try:
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                metadata = ydl.extract_info(url, download=True)
        except yt_dlp.utils.DownloadError as exc:
            shutil.rmtree(target_dir, ignore_errors=True)
            raise _translate_ytdlp_error(exc, settings) from exc
        except Exception as exc:
            shutil.rmtree(target_dir, ignore_errors=True)
            raise _translate_ytdlp_error(exc, settings) from exc
    finally:
        if tmp_cookies and tmp_cookies.is_file():
            try:
                tmp_cookies.unlink()
            except Exception:
                pass

    downloaded = _find_downloaded_file(target_dir)
    if downloaded is None:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise YouTubeIngestError(
            "yt-dlp reported success but produced no media file.",
            code=YouTubeErrorCode.DOWNLOAD_FAILED,
        )

    try:
        info = validate_downloaded_media(downloaded)
    except Exception:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise

    return Source(
        id=source_id,
        type="youtube",
        url=url,
        path=str(downloaded),
        filename=downloaded.name,
        title=(metadata or {}).get("title") or downloaded.stem,
        channel=(metadata or {}).get("uploader") or (metadata or {}).get("channel"),
        duration_s=info.duration_s or float((metadata or {}).get("duration") or 0.0),
        width=info.width,
        height=info.height,
        fps=info.fps,
        has_audio=info.has_audio,
        has_video=info.has_video,
    )


def _translate_ytdlp_error(exc: Exception, settings: IngestSettings) -> YouTubeIngestError:
    """Classify yt-dlp failures into canonical error codes with actionable operator guidance."""
    message = str(exc).lower()

    if "unsupported url" in message or "not a valid url" in message:
        return YouTubeIngestError(
            "Invalid or unsupported YouTube URL.",
            code=YouTubeErrorCode.INVALID_URL,
            hint="Please verify the YouTube link format (e.g. https://www.youtube.com/watch?v=...).",
        )

    if "private video" in message or "members-only" in message or "is private" in message:
        return YouTubeIngestError(
            "This YouTube video is private or members-only.",
            code=YouTubeErrorCode.AUTH_REQUIRED,
            hint="AL AMR could not retrieve this YouTube video automatically. The video requires authentication. You can upload the source video directly.",
        )

    if "sign in" in message or "login required" in message or "age-restricted" in message or "confirm your age" in message:
        return YouTubeIngestError(
            "YouTube authentication required to access this video.",
            code=YouTubeErrorCode.AUTH_REQUIRED,
            hint="AL AMR could not retrieve this YouTube video automatically. The video may require authentication or YouTube may currently be refusing automated retrieval. You can upload the source video directly.",
        )

    if (
        "unavailable" in message
        or "removed" in message
        or "does not exist" in message
        or "deleted" in message
        or "404" in message
        or "not found" in message
    ):
        return YouTubeIngestError(
            "This YouTube video is unavailable or has been removed.",
            code=YouTubeErrorCode.VIDEO_UNAVAILABLE,
            hint="Please verify the video URL exists and is publicly accessible.",
        )

    if (
        any(marker in message for marker in _BOT_CHECK_MARKERS)
        or "bot" in message
        or "captcha" in message
        or "429" in message
        or "403" in message
        or "forbidden" in message
        or "too many requests" in message
        or "blocking" in message
        or "blocked" in message
    ):
        return YouTubeIngestError(
            "YouTube blocked automated retrieval for this video.",
            code=YouTubeErrorCode.EXTRACTION_BLOCKED,
            hint="AL AMR could not retrieve this YouTube video automatically. The video may require authentication or YouTube may currently be refusing automated retrieval. You can upload the source video directly.",
        )

    if "drm" in message:
        return YouTubeIngestError(
            "This content is DRM-protected and cannot be retrieved.",
            code=YouTubeErrorCode.AUTH_REQUIRED,
            hint="AL AMR does not circumvent DRM. Please upload unencrypted source media directly.",
        )

    if any(
        net in message
        for net in (
            "timed out",
            "timeout",
            "connection reset",
            "connection closed",
            "closed connection",
            "remote end closed",
            "name resolution",
            "temporary failure",
            "network is unreachable",
            "500",
            "502",
            "503",
            "504",
        )
    ):
        return YouTubeIngestError(
            f"Transient network error during YouTube extraction: {exc}",
            code=YouTubeErrorCode.NETWORK_ERROR,
            hint="The network connection to YouTube failed temporarily. AL AMR will retry.",
        )

    if "format" in message or "requested format" in message:
        return YouTubeIngestError(
            f"YouTube format selection error: {exc}",
            code=YouTubeErrorCode.FORMAT_ERROR,
            hint="No compatible video/audio format could be extracted.",
        )

    return YouTubeIngestError(
        f"YouTube download failed: {exc}",
        code=YouTubeErrorCode.DOWNLOAD_FAILED,
        hint="AL AMR could not retrieve this YouTube video automatically. The video may require authentication or YouTube may currently be refusing automated retrieval. You can upload the source video directly.",
    )



def _find_downloaded_file(directory: Path) -> Path | None:
    """Return the largest media file in a directory, ignoring sidecars."""
    candidates = [
        p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in ACCEPTED_SUFFIXES
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_size)


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
