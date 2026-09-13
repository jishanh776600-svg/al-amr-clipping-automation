"""yt-dlp acquisition provider wrapping YouTubeSourceAcquirer."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from ....config import IngestSettings
from ..base import (
    AcquisitionResult,
    JobContext,
    SourceAcquisitionError,
    SourceAcquisitionProvider,
    SourceErrorCode,
)
from ..security import validate_remote_url
from ..validation import compute_sha256, validate_media_gate
from ...youtube_acquirer import (
    YouTubeErrorCode,
    YouTubeIngestError,
    YouTubeSourceAcquirer,
)

log = logging.getLogger(__name__)

# Map internal YouTubeErrorCode to normalized canonical SourceErrorCode
_CODE_MAP = {
    YouTubeErrorCode.INVALID_URL: SourceErrorCode.SOURCE_INVALID_URL,
    YouTubeErrorCode.VIDEO_UNAVAILABLE: SourceErrorCode.SOURCE_UNAVAILABLE,
    YouTubeErrorCode.AUTH_REQUIRED: SourceErrorCode.SOURCE_AUTH_REQUIRED,
    YouTubeErrorCode.EXTRACTION_BLOCKED: SourceErrorCode.SOURCE_ACCESS_BLOCKED,
    YouTubeErrorCode.NETWORK_ERROR: SourceErrorCode.SOURCE_NETWORK_ERROR,
    YouTubeErrorCode.FORMAT_ERROR: SourceErrorCode.SOURCE_FORMAT_ERROR,
    YouTubeErrorCode.DOWNLOAD_FAILED: SourceErrorCode.SOURCE_ALL_PROVIDERS_FAILED,
    YouTubeErrorCode.MEDIA_INVALID: SourceErrorCode.SOURCE_MEDIA_INVALID,
}


class YtDlpAcquisitionProvider(SourceAcquisitionProvider):
    """Primary acquisition provider using upstream yt-dlp with multi-strategy InnerTube clients."""

    def __init__(self, settings: IngestSettings | None = None) -> None:
        self._settings = settings or IngestSettings()

    @property
    def provider_name(self) -> str:
        return "yt-dlp"

    def is_configured(self) -> bool:
        """yt-dlp is bundled as a primary dependency and always configured."""
        return True

    def acquire(
        self,
        source_url: str,
        target_dir: Path,
        job_context: JobContext | None = None,
        on_progress: Callable[[float], None] | None = None,
    ) -> AcquisitionResult:
        """Acquire source media using yt-dlp."""
        # Enforce security perimeter (SSRF prevention)
        validate_remote_url(source_url)

        settings = (job_context.settings if job_context else None) or self._settings
        acquirer = YouTubeSourceAcquirer(settings)

        try:
            yt_res = acquirer.acquire(source_url, target_dir, on_progress=on_progress)
        except YouTubeIngestError as exc:
            mapped_code = _CODE_MAP.get(exc.code, SourceErrorCode.SOURCE_ALL_PROVIDERS_FAILED)
            raise SourceAcquisitionError(
                exc.message,
                code=mapped_code,
                hint=exc.hint,
                provider_name=self.provider_name,
            ) from exc
        except SourceAcquisitionError:
            raise
        except Exception as exc:
            log.warning("yt-dlp acquisition unhandled failure: %s", exc)
            raise SourceAcquisitionError(
                f"yt-dlp acquisition failed: {exc}",
                code=SourceErrorCode.SOURCE_ALL_PROVIDERS_FAILED,
                hint="Please upload the video file directly.",
                provider_name=self.provider_name,
            ) from exc

        media_path = yt_res.media_path
        file_size = media_path.stat().st_size if media_path.exists() else 0
        sha256_hash = compute_sha256(media_path) if media_path.exists() else ""
        media_info = yt_res.media_info

        metadata = yt_res.metadata or {}
        title = metadata.get("title") or media_path.stem
        channel = metadata.get("uploader") or metadata.get("channel") or ""
        duration = getattr(media_info, "duration_s", 0.0) or float(metadata.get("duration") or 0.0)

        return AcquisitionResult(
            success=True,
            local_media_path=media_path,
            provider_name=self.provider_name,
            source_url=source_url,
            detected_media_type="video",
            duration=duration,
            file_size=file_size,
            sha256=sha256_hash,
            acquisition_attempts=1,
            diagnostic_code="SUCCESS",
            diagnostic_message="Source acquired and validated successfully via yt-dlp.",
            media_info=media_info,
            provider_metadata={
                "strategy": yt_res.method,
                "title": title,
                "channel": channel,
            },
        )
