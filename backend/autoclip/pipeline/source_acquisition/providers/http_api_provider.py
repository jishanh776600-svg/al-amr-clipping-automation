"""HTTP microservice acquisition provider for remote source media fetching."""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from ..base import (
    AcquisitionResult,
    JobContext,
    SourceAcquisitionError,
    SourceAcquisitionProvider,
    SourceErrorCode,
)
from ..security import validate_remote_url
from ..validation import validate_media_gate

log = logging.getLogger(__name__)


class HttpApiAcquisitionProvider(SourceAcquisitionProvider):
    """Secondary provider for legitimate media fetching via an external HTTP API microservice."""

    def __init__(
        self,
        endpoint: str | None = None,
        api_key: str | None = None,
        timeout_s: float = 120.0,
    ) -> None:
        self._endpoint = endpoint or os.environ.get("AUTOCLIP_ACQUISITION_ENDPOINT", "").strip()
        self._api_key = api_key or os.environ.get("AUTOCLIP_ACQUISITION_KEY", "").strip()
        try:
            self._timeout_s = float(os.environ.get("AUTOCLIP_ACQUISITION_TIMEOUT", str(timeout_s)))
        except (ValueError, TypeError):
            self._timeout_s = timeout_s

    @property
    def provider_name(self) -> str:
        return "http-api"

    def is_configured(self) -> bool:
        """Configured only when an endpoint URL is provided."""
        return bool(self._endpoint)

    def acquire(
        self,
        source_url: str,
        target_dir: Path,
        job_context: JobContext | None = None,
        on_progress: Callable[[float], None] | None = None,
    ) -> AcquisitionResult:
        """Stream media from remote microservice endpoint into target_dir."""
        if not self.is_configured():
            raise SourceAcquisitionError(
                "HTTP API acquisition provider is not configured.",
                code=SourceErrorCode.SOURCE_PROVIDER_UNAVAILABLE,
                hint="AUTOCLIP_ACQUISITION_ENDPOINT is not configured in the environment.",
                provider_name=self.provider_name,
            )

        # Validate security perimeter on both input URL and endpoint
        validate_remote_url(source_url)
        validate_remote_url(self._endpoint)

        target_file = target_dir / "source.mp4"
        headers: dict[str, str] = {
            "User-Agent": "AL-AMR-Clipping-Automation/1.0",
            "Accept": "video/mp4,video/*,*/*",
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
            headers["X-API-Key"] = self._api_key

        payload = {
            "url": source_url,
            "job_id": job_context.job_id if job_context else "",
        }

        log.info(
            "Attempting source acquisition via HTTP API provider: endpoint=%s",
            self._endpoint,
        )

        try:
            with httpx.Client(follow_redirects=True, timeout=self._timeout_s) as client:
                with client.stream("POST", self._endpoint, json=payload, headers=headers) as response:
                    if response.status_code == 404:
                        raise SourceAcquisitionError(
                            f"Remote source or acquisition endpoint returned 404 Not Found: {source_url}",
                            code=SourceErrorCode.SOURCE_UNAVAILABLE,
                            hint="The source video or acquisition endpoint does not exist.",
                            provider_name=self.provider_name,
                        )
                    if response.status_code in (401, 403):
                        raise SourceAcquisitionError(
                            f"Acquisition endpoint authentication/authorization failed: HTTP {response.status_code}",
                            code=SourceErrorCode.SOURCE_ACCESS_BLOCKED,
                            hint="Access was refused or authentication is required.",
                            provider_name=self.provider_name,
                        )
                    if response.status_code >= 400:
                        raise SourceAcquisitionError(
                            f"Acquisition endpoint failed with HTTP status {response.status_code}",
                            code=SourceErrorCode.SOURCE_PROVIDER_UNAVAILABLE,
                            hint="The external acquisition provider encountered an error.",
                            provider_name=self.provider_name,
                        )

                    total_bytes = None
                    content_len = response.headers.get("content-length")
                    if content_len and content_len.isdigit():
                        total_bytes = int(content_len)

                    downloaded = 0
                    with target_file.open("wb") as f:
                        for chunk in response.iter_bytes(chunk_size=65536):
                            f.write(chunk)
                            downloaded += len(chunk)
                            if on_progress and total_bytes and total_bytes > 0:
                                on_progress(min(1.0, downloaded / total_bytes))

        except httpx.TimeoutException as exc:
            shutil.rmtree(target_dir, ignore_errors=True)
            raise SourceAcquisitionError(
                f"HTTP acquisition provider timed out after {self._timeout_s}s",
                code=SourceErrorCode.SOURCE_PROVIDER_TIMEOUT,
                hint="The remote source acquisition service timed out.",
                provider_name=self.provider_name,
            ) from exc
        except httpx.NetworkError as exc:
            shutil.rmtree(target_dir, ignore_errors=True)
            raise SourceAcquisitionError(
                f"Network communication failure connecting to HTTP acquisition provider: {exc}",
                code=SourceErrorCode.SOURCE_NETWORK_ERROR,
                hint="Network connection could not be established to the acquisition service.",
                provider_name=self.provider_name,
            ) from exc
        except SourceAcquisitionError:
            raise
        except Exception as exc:
            shutil.rmtree(target_dir, ignore_errors=True)
            raise SourceAcquisitionError(
                f"HTTP API acquisition failure: {exc}",
                code=SourceErrorCode.SOURCE_ALL_PROVIDERS_FAILED,
                hint="Please upload the video file directly.",
                provider_name=self.provider_name,
            ) from exc

        # Strict validation gate
        media_info, sha256_hash = validate_media_gate(target_file)
        file_size = target_file.stat().st_size

        return AcquisitionResult(
            success=True,
            local_media_path=target_file,
            provider_name=self.provider_name,
            source_url=source_url,
            detected_media_type="video",
            duration=media_info.duration_s,
            file_size=file_size,
            sha256=sha256_hash,
            acquisition_attempts=1,
            diagnostic_code="SUCCESS",
            diagnostic_message="Source acquired and validated successfully via HTTP API.",
            media_info=media_info,
            provider_metadata={
                "endpoint": self._endpoint,
            },
        )
