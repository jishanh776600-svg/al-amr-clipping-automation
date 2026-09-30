"""Google Drive acquisition provider using gdown for reliable server-side downloads.

gdown is specifically designed to download Google Drive files from server
environments where yt-dlp's Drive extractor fails due to:
  - IP-based blocks from cloud datacenter IPs
  - HTML virus-scan confirmation pages for large files
  - Session/cookie requirements that yt-dlp cannot satisfy headlessly

This provider is registered with highest priority for Drive URLs.
"""

from __future__ import annotations

import logging
import re
import shutil
from collections.abc import Callable
from pathlib import Path

from ..base import (
    AcquisitionResult,
    JobContext,
    SourceAcquisitionError,
    SourceAcquisitionProvider,
    SourceErrorCode,
)
from ..validation import compute_sha256, validate_media_gate

log = logging.getLogger(__name__)

# Regex to extract Drive file ID from various URL formats
_DRIVE_ID_RE = re.compile(
    r"(?:"
    r"drive\.google\.com/file/d/([A-Za-z0-9_-]+)"         # /file/d/<ID>
    r"|drive\.google\.com/open\?.*?id=([A-Za-z0-9_-]+)"   # /open?id=<ID>
    r"|drive\.google\.com/uc\?.*?id=([A-Za-z0-9_-]+)"     # /uc?id=<ID>
    r"|drive\.usercontent\.google\.com/download\?.*?id=([A-Za-z0-9_-]+)"
    r")"
)


def _extract_drive_id(url: str) -> str | None:
    """Extract Google Drive file ID from any Drive URL format."""
    m = _DRIVE_ID_RE.search(url)
    if m:
        return next(g for g in m.groups() if g)
    return None


def is_drive_url(url: str) -> bool:
    """Return True if the URL is a Google Drive file link."""
    return "drive.google.com" in url or "drive.usercontent.google.com" in url


class GoogleDriveAcquisitionProvider(SourceAcquisitionProvider):
    """Acquisition provider for Google Drive files using gdown.

    Handles 'Anyone with link' files without authentication.
    Automatically resolves virus-scan warning pages for large files.
    """

    @property
    def provider_name(self) -> str:
        return "gdrive-gdown"

    def is_configured(self) -> bool:
        """Always configured — gdown is a pure-Python library with no credentials needed."""
        try:
            import gdown  # noqa: F401
            return True
        except ImportError:
            log.warning("gdown is not installed; Google Drive provider unavailable. Run: pip install gdown")
            return False

    def acquire(
        self,
        source_url: str,
        target_dir: Path,
        job_context: JobContext | None = None,
        on_progress: Callable[[float], None] | None = None,
    ) -> AcquisitionResult:
        """Download a Google Drive file using gdown."""
        if not is_drive_url(source_url):
            raise SourceAcquisitionError(
                f"URL is not a Google Drive link: {source_url}",
                code=SourceErrorCode.SOURCE_PROVIDER_UNAVAILABLE,
                provider_name=self.provider_name,
            )


        file_id = _extract_drive_id(source_url)
        if not file_id:
            raise SourceAcquisitionError(
                f"Could not extract file ID from Drive URL: {source_url}",
                code=SourceErrorCode.SOURCE_INVALID_URL,
                hint="Please provide a valid Google Drive share link (drive.google.com/file/d/...).",
                provider_name=self.provider_name,
            )

        try:
            import gdown
        except ImportError as exc:
            raise SourceAcquisitionError(
                "gdown library not installed.",
                code=SourceErrorCode.SOURCE_PROVIDER_UNAVAILABLE,
                hint="Install gdown: pip install gdown",
                provider_name=self.provider_name,
            ) from exc

        target_dir = Path(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)

        # gdown downloads to the target path — use a placeholder name first
        output_path = target_dir / "source.mp4"

        log.info("GoogleDriveProvider: downloading file_id=%s via gdown", file_id)

        try:
            # fuzzy=True handles /file/d/ID/view URLs and warning pages automatically
            result = gdown.download(
                id=file_id,
                output=str(output_path),
                quiet=False,
                fuzzy=True,
                resume=False,
            )
        except Exception as exc:
            shutil.rmtree(target_dir, ignore_errors=True)
            err_msg = str(exc).lower()
            if "permission" in err_msg or "cannot be downloaded" in err_msg or "access" in err_msg:
                raise SourceAcquisitionError(
                    f"Google Drive file is not publicly accessible: {exc}",
                    code=SourceErrorCode.SOURCE_ACCESS_BLOCKED,
                    hint=(
                        "Ensure the file sharing is set to 'Anyone with the link' → 'Viewer'. "
                        "Open Google Drive → right-click file → Share → change to Anyone with link."
                    ),
                    provider_name=self.provider_name,
                ) from exc
            raise SourceAcquisitionError(
                f"gdown failed to download Drive file: {exc}",
                code=SourceErrorCode.SOURCE_ALL_PROVIDERS_FAILED,
                hint="Ensure the Drive link is publicly shared ('Anyone with link' → Viewer).",
                provider_name=self.provider_name,
            ) from exc

        if result is None or not Path(result).exists():
            shutil.rmtree(target_dir, ignore_errors=True)
            raise SourceAcquisitionError(
                f"gdown returned no file for Drive ID {file_id}. "
                "The file may be restricted or deleted.",
                code=SourceErrorCode.SOURCE_UNAVAILABLE,
                hint=(
                    "Check that the file still exists on Drive and sharing is set to "
                    "'Anyone with the link' → 'Viewer'."
                ),
                provider_name=self.provider_name,
            )

        downloaded_path = Path(result)

        # If gdown saved with a different name/extension, use it
        if downloaded_path != output_path and downloaded_path.exists():
            output_path = downloaded_path

        if output_path.stat().st_size == 0:
            shutil.rmtree(target_dir, ignore_errors=True)
            raise SourceAcquisitionError(
                "gdown downloaded an empty file from Drive.",
                code=SourceErrorCode.SOURCE_MEDIA_INVALID,
                provider_name=self.provider_name,
            )

        log.info(
            "GoogleDriveProvider: downloaded %s (%.1f MB)",
            output_path.name,
            output_path.stat().st_size / (1024 * 1024),
        )

        media_info, sha256_hash = validate_media_gate(output_path)
        file_size = output_path.stat().st_size
        duration = getattr(media_info, "duration_s", 0.0) or 0.0

        return AcquisitionResult(
            success=True,
            local_media_path=output_path,
            provider_name=self.provider_name,
            source_url=source_url,
            detected_media_type="video",
            duration=duration,
            file_size=file_size,
            sha256=sha256_hash,
            acquisition_attempts=1,
            diagnostic_code="SUCCESS",
            diagnostic_message=f"Drive file downloaded via gdown (file_id={file_id}).",
            media_info=media_info,
            provider_metadata={
                "drive_file_id": file_id,
                "title": output_path.stem,
                "channel": "Google Drive",
            },
        )
