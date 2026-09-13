"""Deterministic Source Acquisition Registry with Multi-Provider Fallback."""

from __future__ import annotations

import logging
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ...config import IngestSettings
from .base import (
    AcquisitionResult,
    JobContext,
    SourceAcquisitionError,
    SourceAcquisitionProvider,
    SourceErrorCode,
)
from .providers.cobalt_provider import CobaltAcquisitionProvider
from .providers.http_api_provider import HttpApiAcquisitionProvider
from .providers.invidious_provider import InvidiousAcquisitionProvider
from .providers.piped_provider import PipedAcquisitionProvider
from .providers.ytdlp_provider import YtDlpAcquisitionProvider
from .security import safe_target_path, validate_remote_url
from .server_downloader import ServerDownloaderProvider

log = logging.getLogger(__name__)


class SourceAcquisitionRegistry:
    """Orchestrates source acquisition attempts across registered providers in deterministic order."""

    def __init__(self, providers: list[SourceAcquisitionProvider] | None = None) -> None:
        if providers is not None:
            self._providers = list(providers)
        else:
            # Deterministic priority: Cobalt -> Piped -> Invidious -> Server Downloader -> yt-dlp -> Secondary HTTP API
            self._providers = [
                CobaltAcquisitionProvider(),
                PipedAcquisitionProvider(),
                InvidiousAcquisitionProvider(),
                ServerDownloaderProvider(),
                YtDlpAcquisitionProvider(),
                HttpApiAcquisitionProvider(),
            ]


    @property
    def providers(self) -> list[SourceAcquisitionProvider]:
        """Return the current list of registered providers in execution priority order."""
        return list(self._providers)

    def register_provider(
        self,
        provider: SourceAcquisitionProvider,
        *,
        prepend: bool = False,
    ) -> None:
        """Register a new provider."""
        if prepend:
            self._providers.insert(0, provider)
        else:
            self._providers.append(provider)

    def acquire(
        self,
        source_url: str,
        target_dir: Path,
        job_context: JobContext | None = None,
        on_progress: Callable[[float], None] | None = None,
    ) -> AcquisitionResult:
        """Execute source acquisition trying providers in priority order until one succeeds.

        Raises:
            SourceAcquisitionError: If all configured providers fail or security checks fail.
        """
        # Security perimeter check
        validate_remote_url(source_url)
        target_dir = Path(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)

        start_time = time.time()
        domain = urlparse(source_url).netloc.lower()

        job_id = job_context.job_id if job_context else ""
        from ...jobs.events import acquisition_event, broker

        def _notify_event(phase: str, status: str = "active", **kwargs: Any) -> None:
            if not job_id:
                return
            try:
                event = acquisition_event(job_id=job_id, phase=phase, status=status, **kwargs)
                broker.publish(event)
            except Exception as e:
                log.debug("Failed to publish acquisition event: %s", e)

        _notify_event(
            phase="VALIDATING_URL",
            status="active",
            message="Validating source URL and accessibility...",
        )

        attempts_history: list[dict[str, Any]] = []
        last_error: SourceAcquisitionError | None = None

        configured_providers = [p for p in self._providers if p.is_configured()]
        if not configured_providers:
            _notify_event(
                phase="FAILED",
                status="failed",
                message="No source acquisition providers are configured.",
            )
            raise SourceAcquisitionError(
                "No source acquisition providers are configured.",
                code=SourceErrorCode.SOURCE_PROVIDER_UNAVAILABLE,
                hint="Please ensure yt-dlp or an acquisition endpoint is configured.",
            )

        for attempt_index, provider in enumerate(configured_providers, start=1):
            log.info(
                "Source acquisition attempt %d/%d using provider '%s' for URL: %s",
                attempt_index,
                len(configured_providers),
                provider.provider_name,
                source_url,
            )

            _notify_event(
                phase="TRYING_PROVIDER",
                status="active",
                provider=provider.provider_name,
                attempt=attempt_index,
                total_attempts=len(configured_providers),
                message=f"Attempting acquisition via {provider.provider_name} (provider {attempt_index} of {len(configured_providers)})...",
                telemetry={"fallback_history": attempts_history},
            )

            # Isolated subdirectory per provider attempt to avoid file collisions
            provider_work_dir = target_dir / f"_attempt_{provider.provider_name}"
            safe_target_path(target_dir, f"_attempt_{provider.provider_name}")
            shutil.rmtree(provider_work_dir, ignore_errors=True)
            provider_work_dir.mkdir(parents=True, exist_ok=True)

            def _wrapped_progress(pct: float, **kw: Any) -> None:
                if on_progress:
                    try:
                        on_progress(pct)
                    except Exception:
                        pass
                _notify_event(
                    phase="DOWNLOADING",
                    status="active",
                    provider=provider.provider_name,
                    progress_percent=round(pct, 1),
                    bytes_downloaded=kw.get("bytes_downloaded"),
                    total_bytes=kw.get("total_bytes"),
                    download_speed=kw.get("download_speed"),
                    eta_seconds=kw.get("eta_seconds"),
                    attempt=attempt_index,
                    total_attempts=len(configured_providers),
                    message=f"Downloading media stream via {provider.provider_name} ({pct:.1f}%)...",
                )

            try:
                result = provider.acquire(
                    source_url=source_url,
                    target_dir=provider_work_dir,
                    job_context=job_context,
                    on_progress=_wrapped_progress,
                )

                _notify_event(
                    phase="VALIDATING_MEDIA",
                    status="active",
                    provider=provider.provider_name,
                    attempt=attempt_index,
                    total_attempts=len(configured_providers),
                    message=f"Verifying media stream integrity and container from {provider.provider_name}...",
                )

                # Move downloaded media to root target_dir / source.<ext>
                src_media = result.local_media_path
                dst_media = target_dir / src_media.name
                if src_media.resolve() != dst_media.resolve():
                    try:
                        if provider_work_dir.resolve() in src_media.resolve().parents:
                            shutil.move(str(src_media), str(dst_media))
                        else:
                            shutil.copy2(str(src_media), str(dst_media))
                    except Exception:
                        shutil.copy2(str(src_media), str(dst_media))

                # Clean up attempt folder
                shutil.rmtree(provider_work_dir, ignore_errors=True)

                log.info(
                    "Source acquisition successful with provider '%s': %s (size=%d bytes, duration=%.2fs, sha256=%s)",
                    provider.provider_name,
                    dst_media.name,
                    result.file_size,
                    result.duration,
                    result.sha256[:16],
                )

                elapsed_s = time.time() - start_time
                provenance = {
                    "provider": provider.provider_name,
                    "attempts": attempt_index,
                    "acquisition_duration": round(elapsed_s, 2),
                    "bytes": result.file_size,
                    "sha256": result.sha256,
                    "media_duration": result.duration,
                    "final_status": "SUCCESS",
                    "failure_code": None,
                    "fallback_history": attempts_history,
                    "attempts_history": attempts_history,
                    "source_domain": domain,
                    "job_id": job_id or None,
                    "file_size": result.file_size,
                    "duration_s": result.duration,
                }
                combined_meta = dict(result.provider_metadata)
                combined_meta["provenance"] = provenance

                _notify_event(
                    phase="SOURCE_ACQUIRED",
                    status="completed",
                    provider=provider.provider_name,
                    progress_percent=100.0,
                    attempt=attempt_index,
                    total_attempts=len(configured_providers),
                    message=f"Source media acquired successfully via {provider.provider_name} ({result.file_size / (1024 * 1024):.1f} MB).",
                    telemetry=provenance,
                )

                return AcquisitionResult(
                    success=True,
                    local_media_path=dst_media,
                    provider_name=provider.provider_name,
                    source_url=source_url,
                    detected_media_type=result.detected_media_type,
                    duration=result.duration,
                    file_size=result.file_size,
                    sha256=result.sha256,
                    acquisition_attempts=attempt_index,
                    diagnostic_code="SUCCESS",
                    diagnostic_message=f"Acquired via {provider.provider_name}",
                    media_info=result.media_info,
                    provider_metadata=combined_meta,
                )

            except SourceAcquisitionError as exc:
                last_error = exc
                shutil.rmtree(provider_work_dir, ignore_errors=True)
                attempts_history.append({
                    "provider": provider.provider_name,
                    "code": exc.code,
                    "message": exc.message,
                })
                log.warning(
                    "Provider '%s' failed to acquire source: [%s] %s",
                    provider.provider_name,
                    exc.code,
                    exc.message,
                )
                # If URL is fundamentally malformed, stop immediately
                if exc.code == SourceErrorCode.SOURCE_INVALID_URL:
                    _notify_event(
                        phase="FAILED",
                        status="failed",
                        provider=provider.provider_name,
                        message=exc.message,
                        telemetry={"fallback_history": attempts_history},
                    )
                    raise exc

            except Exception as exc:
                shutil.rmtree(provider_work_dir, ignore_errors=True)
                mapped_err = SourceAcquisitionError(
                    f"Unexpected failure in provider {provider.provider_name}: {exc}",
                    code=SourceErrorCode.SOURCE_ALL_PROVIDERS_FAILED,
                    provider_name=provider.provider_name,
                )
                last_error = mapped_err
                attempts_history.append({
                    "provider": provider.provider_name,
                    "code": mapped_err.code,
                    "message": str(exc),
                })
                log.warning("Provider '%s' encountered unexpected error: %s", provider.provider_name, exc)

        # All providers failed
        shutil.rmtree(target_dir, ignore_errors=True)
        summary_code = last_error.code if last_error else SourceErrorCode.SOURCE_ALL_PROVIDERS_FAILED
        log.error(
            "All source acquisition providers failed for URL: %s. History: %r",
            source_url,
            attempts_history,
        )

        _notify_event(
            phase="FAILED",
            status="failed",
            message="AL AMR could not acquire source media automatically from this URL across all configured providers.",
            telemetry={"fallback_history": attempts_history},
        )

        raise SourceAcquisitionError(
            "AL AMR could not acquire source media automatically from this URL across all configured providers.",
            code=summary_code,
            hint=(
                "AL AMR could not acquire source media automatically from this link across all configured acquisition providers. "
                "The source may be restricted, blocked, or unavailable. "
                "Please upload the video file directly to proceed."
            ),
            attempts=len(attempts_history),
        )


_DEFAULT_REGISTRY: SourceAcquisitionRegistry | None = None


def get_default_registry(settings: IngestSettings | None = None) -> SourceAcquisitionRegistry:
    """Get or create the singleton default acquisition registry."""
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = SourceAcquisitionRegistry(
            providers=[
                PipedAcquisitionProvider(),
                InvidiousAcquisitionProvider(),
                ServerDownloaderProvider(),
                YtDlpAcquisitionProvider(settings=settings),
                HttpApiAcquisitionProvider(),
            ]
        )
    return _DEFAULT_REGISTRY

