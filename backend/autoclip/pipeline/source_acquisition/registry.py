"""Deterministic Source Acquisition Registry with Multi-Provider Fallback."""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ...config import IngestSettings
from .base import (
    AcquisitionResult,
    JobContext,
    SourceAcquisitionError,
    SourceAcquisitionProvider,
    SourceErrorCode,
)
from .providers.http_api_provider import HttpApiAcquisitionProvider
from .providers.ytdlp_provider import YtDlpAcquisitionProvider
from .security import safe_target_path, validate_remote_url

log = logging.getLogger(__name__)


class SourceAcquisitionRegistry:
    """Orchestrates source acquisition attempts across registered providers in deterministic order."""

    def __init__(self, providers: list[SourceAcquisitionProvider] | None = None) -> None:
        if providers is not None:
            self._providers = list(providers)
        else:
            # Default deterministic order: Primary yt-dlp -> Secondary HTTP API
            self._providers = [
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

        attempts_history: list[dict[str, Any]] = []
        last_error: SourceAcquisitionError | None = None

        configured_providers = [p for p in self._providers if p.is_configured()]
        if not configured_providers:
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

            # Isolated subdirectory per provider attempt to avoid file collisions
            provider_work_dir = target_dir / f"_attempt_{provider.provider_name}"
            safe_target_path(target_dir, f"_attempt_{provider.provider_name}")
            shutil.rmtree(provider_work_dir, ignore_errors=True)
            provider_work_dir.mkdir(parents=True, exist_ok=True)

            try:
                result = provider.acquire(
                    source_url=source_url,
                    target_dir=provider_work_dir,
                    job_context=job_context,
                    on_progress=on_progress,
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

                provenance = {
                    "provider": provider.provider_name,
                    "attempts": attempt_index,
                    "attempts_history": attempts_history,
                    "sha256": result.sha256,
                    "file_size": result.file_size,
                    "duration_s": result.duration,
                }
                combined_meta = dict(result.provider_metadata)
                combined_meta["provenance"] = provenance

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
                YtDlpAcquisitionProvider(settings=settings),
                HttpApiAcquisitionProvider(),
            ]
        )
    return _DEFAULT_REGISTRY
