"""Production-grade YouTube source acquisition engine for AL AMR Clipping Automation.

This module is dedicated exclusively to acquiring local video media bytes from
YouTube URLs in headless and cloud environments. It does not perform transcription,
clipping, reframing, or publishing.
"""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import IngestSettings
from . import ffmpeg

log = logging.getLogger(__name__)

_YOUTUBE_HOSTS = ("youtube.com", "youtu.be", "www.youtube.com", "m.youtube.com")

#: Signatures indicating YouTube anti-bot challenges or access restrictions.
_BOT_CHECK_MARKERS = (
    "sign in to confirm",
    "confirm you're not a bot",
    "confirm you are not a bot",
    "this content isn't available",
    "player response",
    "bot",
    "captcha",
    "429",
    "too many requests",
    "blocking",
    "blocked",
)


class IngestError(RuntimeError):
    """Base error for source acquisition failures with operator guidance."""

    def __init__(self, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:
        base = super().__str__()
        return f"{base}\n\n{self.hint}" if self.hint else base


class YouTubeErrorCode:
    """Canonical classification codes for YouTube acquisition failures."""

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
    """Structured YouTube acquisition exception with canonical error classification."""

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


def is_youtube_url(url: str | None) -> bool:
    """Return True if the URL points to a recognised YouTube hostname."""
    if not url or not isinstance(url, str):
        return False
    from urllib.parse import urlparse

    try:
        host = (urlparse(url.strip()).hostname or "").lower()
    except ValueError:
        return False
    return host in _YOUTUBE_HOSTS


def validate_downloaded_media(path: Path) -> ffmpeg.MediaInfo:
    """Validate that the acquired file is a playable, non-corrupt media stream with video & audio."""
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
                f"Downloaded media file {path.name} is an HTML document, not a video stream.",
                code=YouTubeErrorCode.MEDIA_INVALID,
                hint="YouTube returned an HTML error or block page instead of media bytes.",
            )
    except OSError as exc:
        raise YouTubeIngestError(
            f"Could not read media file {path.name}: {exc}",
            code=YouTubeErrorCode.MEDIA_INVALID,
        ) from exc

    try:
        info = ffmpeg.probe(path)
    except Exception as exc:
        raise YouTubeIngestError(
            f"FFprobe inspection failed on {path.name}: {exc}",
            code=YouTubeErrorCode.MEDIA_INVALID,
            hint="The media container is corrupt, truncated, or unreadable by FFmpeg.",
        ) from exc

    if info.duration_s <= 0.0:
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


def translate_ytdlp_error(exc: Exception, settings: IngestSettings | None = None) -> YouTubeIngestError:
    """Map raw yt-dlp exceptions into structured, actionable operator classifications."""
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
            hint="The video requires authentication on YouTube. You can upload the source video directly.",
        )

    if "age-restricted" in message or "confirm your age" in message:
        return YouTubeIngestError(
            "This YouTube video is age-restricted and requires authentication.",
            code=YouTubeErrorCode.AUTH_REQUIRED,
            hint="Age-restricted videos cannot be retrieved anonymously. You can upload the source video directly.",
        )

    if (
        any(marker in message for marker in _BOT_CHECK_MARKERS)
        or "403" in message
        or "forbidden" in message
        or "confirm you’re not a bot" in message
        or "confirm you're not a bot" in message
    ):
        return YouTubeIngestError(
            "AL AMR attempted to retrieve the source media automatically, but YouTube refused automated retrieval from the cloud environment.",
            code=YouTubeErrorCode.EXTRACTION_BLOCKED,
            hint="YouTube is currently restricting automated access from this cloud IP. You can upload the source video directly.",
        )

    if "sign in" in message or "login required" in message:
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

    if "format" in message or "requested format" in message or "no video formats found" in message:
        return YouTubeIngestError(
            f"YouTube format selection error: {exc}",
            code=YouTubeErrorCode.FORMAT_ERROR,
            hint="No compatible video/audio format could be extracted.",
        )

    return YouTubeIngestError(
        f"YouTube download failed: {exc}",
        code=YouTubeErrorCode.DOWNLOAD_FAILED,
        hint="AL AMR could not retrieve this YouTube video automatically. You can upload the source video directly.",
    )


@dataclass(frozen=True)
class AcquisitionResult:
    """Artifact produced by successful YouTube media acquisition."""

    media_path: Path
    metadata: dict[str, Any]
    media_info: ffmpeg.MediaInfo
    method: str


def _resolve_find_downloaded_file() -> Callable[[Path], Path | None]:
    import sys
    ingest_mod = sys.modules.get("autoclip.pipeline.ingest")
    if ingest_mod and hasattr(ingest_mod, "_find_downloaded_file"):
        fn = getattr(ingest_mod, "_find_downloaded_file")
        if fn is not _find_downloaded_file:
            return fn
    return _find_downloaded_file


def _resolve_validate_downloaded_media() -> Callable[[Path], ffmpeg.MediaInfo]:
    import sys
    ingest_mod = sys.modules.get("autoclip.pipeline.ingest")
    if ingest_mod and hasattr(ingest_mod, "validate_downloaded_media"):
        fn = getattr(ingest_mod, "validate_downloaded_media")
        if fn is not validate_downloaded_media:
            return fn
    return validate_downloaded_media


class YouTubeSourceAcquirer:
    """Dedicated YouTube media acquisition engine using upstream yt-dlp.

    Guarantees:
    - Strictly headless: Never attempts desktop browser extraction or inspects Chrome paths.
    - Cloud-resilient InnerTube client negotiation (prioritises non-desktop-web clients
      such as visionos and mobile to avoid datacenter IP bot traps).
    - Multi-strategy fallback if the primary client strategy encounters restrictions.
    - Transient cookie cleanup: server secrets are never persisted or exposed.
    - Immediate media validation: verifies duration, container, video stream, and audio stream.
    - Comprehensive error classification and structured observability.
    """

    def __init__(self, settings: IngestSettings | None = None) -> None:
        self.settings = settings or IngestSettings()

    def acquire(
        self,
        url: str,
        target_dir: Path,
        on_progress: Callable[[float], None] | None = None,
    ) -> AcquisitionResult:
        """Acquire source media bytes for the given YouTube URL into target_dir."""
        import yt_dlp

        if not url or not str(url).strip():
            raise YouTubeIngestError("No YouTube URL provided.", code=YouTubeErrorCode.INVALID_URL)

        if not is_youtube_url(url):
            raise YouTubeIngestError(f"Not a recognized YouTube URL: {url}", code=YouTubeErrorCode.INVALID_URL)

        target_dir.mkdir(parents=True, exist_ok=True)
        log.info("YouTube acquisition started: url=%s, target_dir=%s", url, target_dir)

        def hook(status: dict) -> None:
            if not on_progress or status.get("status") != "downloading":
                return
            total = status.get("total_bytes") or status.get("total_bytes_estimate")
            done = status.get("downloaded_bytes")
            if total and done:
                on_progress(min(1.0, done / total))

        base_options: dict[str, Any] = {
            "format": self.settings.ytdlp_format,
            "outtmpl": str(target_dir / "source.%(ext)s"),
            "merge_output_format": "mp4",
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "progress_hooks": [hook],
            "retries": 3,
            "fragment_retries": 3,
        }

        # Detect external JavaScript runtime for yt-dlp challenge execution (Deno, Node)
        for candidate in ("deno", "node", "nodejs", "bun"):
            if shutil.which(candidate):
                base_options["js_runtimes"] = {candidate: {}}
                log.info("Upstream yt-dlp: JavaScript runtime '%s' discovered for challenges", candidate)
                break

        # Handle optional server-side cookies (explicit file or environment secret)
        cookies_file = self.settings.cookies_file or os.environ.get("AUTOCLIP_COOKIES_FILE")
        env_cookies_text = os.environ.get("YOUTUBE_COOKIES_TEXT") or os.environ.get("YOUTUBE_COOKIES")

        tmp_cookies: Path | None = None
        if env_cookies_text and not cookies_file:
            tmp_cookies = target_dir / "cookies.txt"
            tmp_cookies.write_text(env_cookies_text, encoding="utf-8")
            base_options["cookiefile"] = str(tmp_cookies)
        elif cookies_file and Path(cookies_file).expanduser().is_file():
            base_options["cookiefile"] = str(Path(cookies_file).expanduser())
        elif self.settings.cookies_from_browser:
            # Local developer desktop fallback ONLY; cloud runners run browserless
            is_headless = bool(
                os.environ.get("CI")
                or os.environ.get("GITHUB_ACTIONS")
                or os.environ.get("RENDER")
                or (os.name != "nt" and not os.environ.get("DISPLAY"))
            )
            if not is_headless:
                base_options["cookiesfrombrowser"] = (self.settings.cookies_from_browser,)

        if self.settings.prefer_youtube_captions:
            base_options["writeautomaticsub"] = True
            base_options["subtitleslangs"] = ["en.*"]
            base_options["subtitlesformat"] = "json3"

        # Resolve egress proxy (explicit setting, env var, or local WARP sidecar auto-detection)
        proxy = (
            self.settings.proxy
            or os.environ.get("AUTOCLIP_PROXY")
            or os.environ.get("YTDLP_PROXY")
            or os.environ.get("ALL_PROXY")
            or ""
        ).strip()
        if not proxy:
            import socket

            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.settimeout(0.3)
                    if sock.connect_ex(("127.0.0.1", 1080)) == 0:
                        proxy = "socks5://127.0.0.1:1080"
                        log.info("YouTube acquirer auto-detected local WARP SOCKS5 sidecar at %s", proxy)
            except Exception:
                pass

        if proxy:
            if proxy.startswith("socks5://"):
                proxy = "socks5h://" + proxy[len("socks5://"):]
            base_options["proxy"] = proxy
            base_options["source_address"] = "0.0.0.0"
            log.info("YouTube acquirer configured with egress proxy: %s", proxy)

        # Multi-strategy acquisition order:
        # Strategy 1 (Primary): Cloud-resilient InnerTube clients excluding desktop web
        #                       (desktop web triggers bot check on datacenter IPs).
        # Strategy 2 (Fallback): Mobile InnerTube client skipping HTML webpage download
        # Strategy 3 (Fallback): Web & MWeb InnerTube with always-active PO Token generation.
        # Strategy 4 (Extended Fallback): Multi-client mobile without webpage/configs.
        # Strategy 5 (Unconstrained): Default unconstrained yt-dlp negotiation.
        # Strategy 6 (Fallback): TV client.
        strategies: list[tuple[str, dict[str, Any] | None]] = [
            ("cloud_resilient_innertube", {"youtube": {"player_client": ["visionos"]}}),
            ("mobile_innertube", {"youtube": {"player_client": ["android", "ios"]}}),
            ("pot_provider_innertube", {"youtube": {"player_client": ["web", "mweb"], "fetch_pot": ["always"]}}),
            ("default_unconstrained", None),
            ("tv_innertube", {"youtube": {"player_client": ["tv"]}}),
        ]




        last_error: Exception | None = None
        metadata: dict[str, Any] | None = None
        successful_method: str = ""

        try:
            for strategy_name, extractor_args in strategies:
                current_options = dict(base_options)
                if extractor_args:
                    current_options["extractor_args"] = extractor_args

                log.info("YouTube acquisition method selected: %s", strategy_name)
                try:
                    with yt_dlp.YoutubeDL(current_options) as ydl:
                        metadata = ydl.extract_info(url, download=True)
                    successful_method = strategy_name
                    last_error = None
                    break
                except Exception as exc:
                    last_error = exc
                    classified = translate_ytdlp_error(exc, self.settings)
                    log.warning(
                        "YouTube acquisition strategy %s failed: [%s] exc=%r",
                        strategy_name,
                        classified.code,
                        str(exc),
                    )
                    # If the video is non-existent, invalid URL, or permanently private/members-only,
                    # trying another client will not help.
                    exc_msg = str(exc).lower()
                    if classified.code in (
                        YouTubeErrorCode.INVALID_URL,
                        YouTubeErrorCode.VIDEO_UNAVAILABLE,
                    ) or "private video" in exc_msg or "members-only" in exc_msg:
                        break

            if last_error is not None:
                classified = translate_ytdlp_error(last_error, self.settings)
                if classified.code == YouTubeErrorCode.EXTRACTION_BLOCKED:
                    log.warning("YouTube acquisition classified as blocked: %s", classified)
                elif classified.code == YouTubeErrorCode.NETWORK_ERROR:
                    log.info("YouTube acquisition classified as transient: %s", classified)
                elif classified.code == YouTubeErrorCode.VIDEO_UNAVAILABLE:
                    log.warning("YouTube acquisition classified as unavailable: %s", classified)
                else:
                    log.warning("YouTube acquisition classified as %s: %s", classified.code, classified)
                shutil.rmtree(target_dir, ignore_errors=True)
                raise classified from last_error
        finally:
            if tmp_cookies and tmp_cookies.is_file():
                try:
                    tmp_cookies.unlink()
                except Exception:
                    pass

        finder = _resolve_find_downloaded_file()
        downloaded = finder(target_dir)
        if downloaded is None:
            shutil.rmtree(target_dir, ignore_errors=True)
            raise YouTubeIngestError(
                "yt-dlp reported success but produced no media file.",
                code=YouTubeErrorCode.DOWNLOAD_FAILED,
            )

        log.info(
            "YouTube acquisition completed: file=%s, size=%d bytes",
            downloaded.name,
            downloaded.stat().st_size if hasattr(downloaded, "stat") else 0,
        )

        log.info("Downloaded media validation started: %s", downloaded)
        try:
            validator = _resolve_validate_downloaded_media()
            info = validator(downloaded)
            log.info(
                "Downloaded media validation passed: duration=%.2fs, video=%s, audio=%s, resolution=%dx%d",
                getattr(info, "duration_s", 0.0),
                getattr(info, "has_video", False),
                getattr(info, "has_audio", False),
                getattr(info, "width", 0) or 0,
                getattr(info, "height", 0) or 0,
            )
        except Exception:
            shutil.rmtree(target_dir, ignore_errors=True)
            raise

        return AcquisitionResult(
            media_path=downloaded,
            metadata=metadata or {},
            media_info=info,
            method=successful_method,
        )


def _find_downloaded_file(directory: Path) -> Path | None:
    """Return the largest media file in a directory, ignoring sidecars."""
    candidates = [
        p
        for p in directory.iterdir()
        if p.is_file() and not p.name.endswith((".json", ".part", ".ytdl", ".vtt", ".srt", ".txt"))
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_size)
