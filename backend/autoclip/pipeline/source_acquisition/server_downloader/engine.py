"""Server-side media acquisition engine adapted from mature open-source downloader services.

Design & Architectural Lineage:
- Adapted from patterns in yt-dlp-server (MIT License) and MeTube (GPL-3.0 License).
- Implements asynchronous server-side downloading, format negotiation, progress telemetry,
  container remuxing (MP4), process timeout watchdogs, and canonical error classification.
- Strictly zero-cost: uses local open-source libraries and system FFmpeg with no external paid APIs.
"""

from __future__ import annotations

import concurrent.futures
import logging
import os
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

from ..base import (
    AcquisitionResult,
    JobContext,
    SourceAcquisitionError,
    SourceErrorCode,
)
from ..security import safe_target_path, validate_remote_url
from ..validation import validate_media_gate

log = logging.getLogger(__name__)

#: Default download timeout in seconds
DEFAULT_DOWNLOAD_TIMEOUT_S = 180.0


@dataclass
class AcquisitionTelemetry:
    """Telemetry data captured during source acquisition attempt."""

    provider: str = "server-downloader"
    source_url: str = ""
    source_domain: str = ""
    job_id: str | None = None
    start_time: float = 0.0
    end_time: float = 0.0
    duration_s: float = 0.0
    bytes_acquired: int = 0
    http_status: int | None = None
    final_status: str = "PENDING"
    failure_code: str | None = None
    sha256: str | None = None
    media_duration: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)


class ServerDownloaderEngine:
    """Asynchronous server-side media downloader engine."""

    def __init__(
        self,
        *,
        timeout_s: float = DEFAULT_DOWNLOAD_TIMEOUT_S,
        max_size_bytes: int = 2 * 1024 * 1024 * 1024,  # 2 GiB
    ) -> None:
        self.timeout_s = timeout_s
        self.max_size_bytes = max_size_bytes

    def download(
        self,
        source_url: str,
        target_dir: Path,
        *,
        job_context: JobContext | None = None,
        on_progress: Callable[[float], None] | None = None,
    ) -> tuple[AcquisitionResult, AcquisitionTelemetry]:
        """Download remote video to target_dir synchronously with timeout and validation.

        Returns:
            Tuple of (AcquisitionResult, AcquisitionTelemetry) on success.

        Raises:
            SourceAcquisitionError on security violation, timeout, or download failure.
        """
        start_time = time.time()
        parsed_url = urlparse(source_url)
        domain = parsed_url.netloc.lower()
        job_id = job_context.job_id if job_context else None

        telemetry = AcquisitionTelemetry(
            provider="server-downloader",
            source_url=source_url,
            source_domain=domain,
            job_id=job_id,
            start_time=start_time,
        )

        # 1. Security perimeter validation
        validate_remote_url(source_url)
        target_dir = Path(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)

        # 2. Configure output template in target_dir
        out_template = str(target_dir / "acquired_source.%(ext)s")

        # 3. Build yt-dlp configuration for server-side downloader
        bytes_counter = [0]

        def _progress_hook(d: dict[str, Any]) -> None:
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes") or 0
                bytes_counter[0] = max(bytes_counter[0], downloaded)

                if downloaded > self.max_size_bytes:
                    raise SourceAcquisitionError(
                        f"Download exceeded maximum permitted size of {self.max_size_bytes} bytes.",
                        code=SourceErrorCode.SOURCE_MEDIA_INVALID,
                        provider_name="server-downloader",
                        hint="Please select a shorter video or upload the file directly.",
                    )

                if on_progress and total > 0:
                    fraction = min(0.95, downloaded / total)
                    try:
                        on_progress(fraction)
                    except Exception:
                        pass

        selected_format = (
            job_context.settings.ytdlp_format
            if job_context and getattr(job_context, "settings", None) and hasattr(job_context.settings, "ytdlp_format")
            else (
                "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/"
                "bestvideo[height<=1080]+bestaudio/"
                "bestvideo+bestaudio/"
                "best[height<=1080][ext=mp4]/"
                "best[height<=1080]/"
                "best"
            )
        )

        ydl_opts: dict[str, Any] = {
            "format": selected_format,
            "outtmpl": out_template,
            "merge_output_format": "mp4",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "retries": 2,
            "fragment_retries": 2,
            "socket_timeout": 30,
            "progress_hooks": [_progress_hook],
            "nocheckcertificate": False,
        }

        # Discover JS runtimes (Deno, Node) for challenge execution
        for candidate in ("deno", "node", "nodejs", "bun"):
            if shutil.which(candidate):
                ydl_opts["js_runtimes"] = {candidate: {}}
                break

        # Check for optional cookies file if specified in settings or env
        cookies_file = None
        if job_context and job_context.settings.cookies_file:
            cookies_file = job_context.settings.cookies_file
        elif os.environ.get("AUTOCLIP_COOKIES_FILE"):
            cookies_file = os.environ.get("AUTOCLIP_COOKIES_FILE")

        if cookies_file and Path(cookies_file).exists():
            ydl_opts["cookiefile"] = str(cookies_file)

        # Resolve egress proxy (explicit setting, env var, or local WARP sidecar auto-detection)
        proxy = (
            (job_context.settings.proxy if job_context and getattr(job_context, "settings", None) and hasattr(job_context.settings, "proxy") else "")
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
                        log.info("Server-downloader auto-detected local WARP SOCKS5 sidecar at %s", proxy)
            except Exception:
                pass

        if proxy:
            if proxy.startswith("socks5://"):
                proxy = "socks5h://" + proxy[len("socks5://"):]
            ydl_opts["proxy"] = proxy
            ydl_opts["source_address"] = "0.0.0.0"
            log.info("Server-downloader configured with egress proxy: %s", proxy)

        is_youtube = any(h in domain for h in ("youtube.com", "youtu.be"))
        if is_youtube:
            strategies = [
                ("visionos", {"youtube": {"player_client": ["visionos"]}}),
                ("android", {"youtube": {"player_client": ["android"]}}),
                ("pot_web", {"youtube": {"player_client": ["web"], "fetch_pot": ["always"]}}),
                ("pot_mweb", {"youtube": {"player_client": ["mweb"], "fetch_pot": ["always"]}}),
                ("ios", {"youtube": {"player_client": ["ios"]}}),
                ("default", None),
            ]
        else:
            strategies = [("default", None)]

        # 4. Execute download with timeout guard in separate thread
        def _execute_ydl() -> dict[str, Any]:
            if yt_dlp is None:
                raise SourceAcquisitionError(
                    SourceErrorCode.ENGINE_FAILURE,
                    "yt-dlp is not installed in this environment",
                )
            last_exc = None
            for s_name, extractor_args in strategies:
                opts = dict(ydl_opts)
                if extractor_args:
                    opts["extractor_args"] = extractor_args
                log.info("Server-downloader attempting strategy '%s' for URL: %s", s_name, source_url)
                try:
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        res = ydl.extract_info(source_url, download=True)
                        log.info("Server-downloader strategy '%s' succeeded for URL: %s", s_name, source_url)
                        return res
                except Exception as exc:
                    last_exc = exc
                    log.warning("Server-downloader strategy '%s' failed: %s", s_name, exc)
                    exc_str = str(exc).lower()
                    if "not found" in exc_str or "does not exist" in exc_str:
                        raise exc
            if last_exc:
                raise last_exc
            raise RuntimeError("Download produced no result")

        info_dict: dict[str, Any] | None = None
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_execute_ydl)
            try:
                info_dict = future.result(timeout=self.timeout_s)
            except concurrent.futures.TimeoutError as exc:
                telemetry.end_time = time.time()
                telemetry.duration_s = telemetry.end_time - start_time
                telemetry.final_status = "FAILED"
                telemetry.failure_code = SourceErrorCode.SOURCE_PROVIDER_TIMEOUT
                raise SourceAcquisitionError(
                    f"Server downloader timed out after {self.timeout_s}s for URL: {source_url}",
                    code=SourceErrorCode.SOURCE_PROVIDER_TIMEOUT,
                    provider_name="server-downloader",
                    hint="The source media host took too long to respond.",
                ) from exc
            except SourceAcquisitionError as exc:
                telemetry.end_time = time.time()
                telemetry.duration_s = telemetry.end_time - start_time
                telemetry.final_status = "FAILED"
                telemetry.failure_code = exc.code
                raise exc
            except Exception as exc:
                telemetry.end_time = time.time()
                telemetry.duration_s = telemetry.end_time - start_time
                telemetry.final_status = "FAILED"
                mapped_error = self._classify_exception(exc, source_url)
                telemetry.failure_code = mapped_error.code
                raise mapped_error from exc

        # 5. Locate the downloaded file
        candidates = list(target_dir.glob("acquired_source*"))
        if not candidates:
            # Check for any newly created media files in target_dir
            candidates = [p for p in target_dir.iterdir() if p.is_file() and p.suffix.lower() in (".mp4", ".mkv", ".webm")]

        if not candidates:
            telemetry.end_time = time.time()
            telemetry.duration_s = telemetry.end_time - start_time
            telemetry.final_status = "FAILED"
            telemetry.failure_code = SourceErrorCode.SOURCE_MEDIA_INVALID
            raise SourceAcquisitionError(
                "Server downloader completed without leaving a media file on disk.",
                code=SourceErrorCode.SOURCE_MEDIA_INVALID,
                provider_name="server-downloader",
                hint="The media could not be written to server storage.",
            )

        # Use the largest candidate if multiple exist
        downloaded_file = max(candidates, key=lambda p: p.stat().st_size)

        # Standardize target filename to target_dir / "source.mp4"
        final_media_path = target_dir / "source.mp4"
        if downloaded_file.resolve() != final_media_path.resolve():
            shutil.move(str(downloaded_file), str(final_media_path))

        # 6. Pass through the unified media validation gate
        media_info, sha256_hash = validate_media_gate(
            final_media_path,
            expected_dir=target_dir,
            max_size_bytes=self.max_size_bytes,
        )

        end_time = time.time()
        duration_s = end_time - start_time
        file_size = final_media_path.stat().st_size

        telemetry.end_time = end_time
        telemetry.duration_s = duration_s
        telemetry.bytes_acquired = file_size
        telemetry.final_status = "SUCCESS"
        telemetry.sha256 = sha256_hash
        telemetry.media_duration = media_info.duration_s

        if on_progress:
            try:
                on_progress(1.0)
            except Exception:
                pass

        result = AcquisitionResult(
            success=True,
            local_media_path=final_media_path,
            provider_name="server-downloader",
            source_url=source_url,
            detected_media_type="video",
            duration=media_info.duration_s,
            file_size=file_size,
            sha256=sha256_hash,
            acquisition_attempts=1,
            diagnostic_code="SUCCESS",
            diagnostic_message="Acquired via ServerDownloaderEngine",
            media_info=media_info,
            provider_metadata={
                "engine": "yt-dlp-server-open-source",
                "telemetry": {
                    "provider": "server-downloader",
                    "source_domain": domain,
                    "duration_s": round(duration_s, 2),
                    "bytes": file_size,
                    "sha256": sha256_hash,
                    "media_duration": media_info.duration_s,
                },
            },
        )
        return result, telemetry

    def _classify_exception(self, exc: Exception, url: str) -> SourceAcquisitionError:
        """Classify downloader exception into canonical SourceAcquisitionError."""
        msg = str(exc)
        low = msg.lower()

        if "sign in to confirm" in low or "bot" in low or "automated queries" in low:
            return SourceAcquisitionError(
                "YouTube anti-bot verification requested for this source URL.",
                code=SourceErrorCode.SOURCE_ACCESS_BLOCKED,
                provider_name="server-downloader",
                hint=(
                    "YouTube is restricting automated server retrieval for this video. "
                    "Please upload the video file directly to proceed."
                ),
            )

        if "private video" in low or "members-only" in low or "premieres in" in low:
            return SourceAcquisitionError(
                "This video is private, restricted, or unplayable.",
                code=SourceErrorCode.SOURCE_AUTH_REQUIRED,
                provider_name="server-downloader",
                hint="Please ensure the video is publicly accessible or upload the file directly.",
            )

        if "unavailable" in low or "not available" in low or "does not exist" in low or "not found" in low:
            return SourceAcquisitionError(
                "The requested video is unavailable or has been deleted.",
                code=SourceErrorCode.SOURCE_UNAVAILABLE,
                provider_name="server-downloader",
                hint="Please check the video URL.",
            )


        if "urlopen error" in low or "connection refused" in low or "network is unreachable" in low:
            return SourceAcquisitionError(
                f"Network communication failure while accessing URL: {exc}",
                code=SourceErrorCode.SOURCE_NETWORK_ERROR,
                provider_name="server-downloader",
                hint="Could not connect to the remote host. Check network connectivity.",
            )

        if "unsupported url" in low or "is not a valid url" in low:
            return SourceAcquisitionError(
                f"Unsupported or malformed media URL: {url}",
                code=SourceErrorCode.SOURCE_INVALID_URL,
                provider_name="server-downloader",
                hint="Please provide a valid YouTube watch URL, Shorts URL, youtu.be, or direct video link.",
            )

        return SourceAcquisitionError(
            f"Server downloader failed to extract media: {exc}",
            code=SourceErrorCode.SOURCE_ALL_PROVIDERS_FAILED,
            provider_name="server-downloader",
            hint="An error occurred during server-side media extraction.",
        )
