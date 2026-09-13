"""ServerDownloaderProvider implementation for AL AMR Source Acquisition.

Priority 1 provider wrapping server-side downloader engine with optional remote internal API mode.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from ..base import (
    AcquisitionResult,
    JobContext,
    SourceAcquisitionError,
    SourceAcquisitionProvider,
    SourceErrorCode,
)
from ..security import safe_target_path, validate_remote_url
from ..validation import validate_media_gate
from .engine import ServerDownloaderEngine

log = logging.getLogger(__name__)


class ServerDownloaderProvider(SourceAcquisitionProvider):
    """Primary server-side downloader provider.

    Executes either directly via ServerDownloaderEngine in the local/worker process,
    or delegates to an internal authenticated API endpoint if configured.
    """

    def __init__(
        self,
        *,
        remote_endpoint: str | None = None,
        internal_token: str | None = None,
        timeout_s: float = 180.0,
        engine: ServerDownloaderEngine | None = None,
    ) -> None:
        self._remote_endpoint = (
            remote_endpoint
            or os.environ.get("AUTOCLIP_SERVER_DOWNLOADER_URL", "").strip()
        )
        self._internal_token = (
            internal_token
            or os.environ.get("AL_AMR_MASTER_KEY")
            or os.environ.get("WORKER_CALLBACK_SECRET")
            or os.environ.get("AUTOCLIP_API_KEY")
            or os.environ.get("OPERATOR_TOKEN")
            or ""
        ).strip()
        self._timeout_s = timeout_s
        self._engine = engine or ServerDownloaderEngine(timeout_s=timeout_s)

    @property
    def provider_name(self) -> str:
        return "server-downloader"

    def is_configured(self) -> bool:
        """Always available either as an in-process engine or remote microservice."""
        return True

    def acquire(
        self,
        source_url: str,
        target_dir: Path,
        job_context: JobContext | None = None,
        on_progress: Callable[[float], None] | None = None,
    ) -> AcquisitionResult:
        """Acquire source media using the server downloader.

        Tries remote internal endpoint if configured, otherwise executes in-process engine.
        """
        # SSRF security boundary check
        validate_remote_url(source_url)
        target_dir = Path(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)

        if self._remote_endpoint:
            log.info(
                "Delegating acquisition to remote internal downloader service: %s",
                self._remote_endpoint,
            )
            return self._acquire_remote(source_url, target_dir, job_context, on_progress)

        # In-process server downloader execution
        log.info("Executing in-process server downloader engine for URL: %s", source_url)
        result, telemetry = self._engine.download(
            source_url,
            target_dir,
            job_context=job_context,
            on_progress=on_progress,
        )
        return result

    def _acquire_remote(
        self,
        source_url: str,
        target_dir: Path,
        job_context: JobContext | None,
        on_progress: Callable[[float], None] | None,
    ) -> AcquisitionResult:
        """Call internal HTTP acquisition endpoint and stream the media bytes."""
        validate_remote_url(self._remote_endpoint, require_https=False)

        job_id = job_context.job_id if job_context else "direct"
        dest_file = target_dir / "source.mp4"
        start_time = time.time()

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "ALAMR-CloudWorker/0.1.0",
        }
        if self._internal_token:
            headers["Authorization"] = f"Bearer {self._internal_token}"
            headers["X-API-Key"] = self._internal_token

        payload = {
            "url": source_url,
            "job_id": job_id,
        }

        try:
            with httpx.Client(timeout=self._timeout_s, follow_redirects=True) as client:
                acquire_url = self._remote_endpoint.rstrip("/")
                if not acquire_url.endswith("/acquire"):
                    acquire_url = f"{acquire_url}/internal/acquire"

                resp = client.post(acquire_url, json=payload, headers=headers)
                if resp.status_code == 401 or resp.status_code == 403:
                    raise SourceAcquisitionError(
                        "Internal acquisition endpoint authentication failed.",
                        code=SourceErrorCode.SOURCE_AUTH_REQUIRED,
                        provider_name=self.provider_name,
                        hint="Check internal master key configuration.",
                    )
                if resp.status_code != 200:
                    try:
                        err_detail = resp.json().get("detail", resp.text)
                    except Exception:
                        err_detail = resp.text
                    raise SourceAcquisitionError(
                        f"Remote downloader service returned status {resp.status_code}: {err_detail}",
                        code=SourceErrorCode.SOURCE_PROVIDER_UNAVAILABLE,
                        provider_name=self.provider_name,
                        hint=str(err_detail),
                    )

                data = resp.json()
                stream_url = data.get("stream_url") or data.get("media_url")
                if not stream_url:
                    raise SourceAcquisitionError(
                        "Remote downloader did not return a stream URL.",
                        code=SourceErrorCode.SOURCE_MEDIA_INVALID,
                        provider_name=self.provider_name,
                    )

                # Stream the media bytes
                with client.stream("GET", stream_url, headers=headers) as stream_resp:
                    if stream_resp.status_code != 200:
                        raise SourceAcquisitionError(
                            f"Failed to stream media from remote endpoint: status {stream_resp.status_code}",
                            code=SourceErrorCode.SOURCE_PROVIDER_UNAVAILABLE,
                            provider_name=self.provider_name,
                        )

                    total_size = int(stream_resp.headers.get("Content-Length", 0))
                    downloaded = 0
                    with dest_file.open("wb") as f:
                        for chunk in stream_resp.iter_bytes(chunk_size=65536):
                            f.write(chunk)
                            downloaded += len(chunk)
                            if on_progress and total_size > 0:
                                on_progress(min(0.95, downloaded / total_size))

            # Validate the streamed media
            media_info, sha256_hash = validate_media_gate(
                dest_file,
                expected_dir=target_dir,
            )

            duration_s = time.time() - start_time
            file_size = dest_file.stat().st_size

            return AcquisitionResult(
                success=True,
                local_media_path=dest_file,
                provider_name=self.provider_name,
                source_url=source_url,
                detected_media_type="video",
                duration=media_info.duration_s,
                file_size=file_size,
                sha256=sha256_hash,
                acquisition_attempts=1,
                diagnostic_code="SUCCESS",
                diagnostic_message="Acquired via remote ServerDownloaderProvider",
                media_info=media_info,
                provider_metadata={
                    "engine": "remote-server-downloader",
                    "telemetry": {
                        "provider": self.provider_name,
                        "duration_s": round(duration_s, 2),
                        "bytes": file_size,
                        "sha256": sha256_hash,
                        "media_duration": media_info.duration_s,
                    },
                },
            )

        except SourceAcquisitionError:
            raise
        except Exception as exc:
            raise SourceAcquisitionError(
                f"Communication failure with server downloader: {exc}",
                code=SourceErrorCode.SOURCE_NETWORK_ERROR,
                provider_name=self.provider_name,
            ) from exc
