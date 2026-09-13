"""Source Acquisition Provider Interface & Core Contracts.

Defines the normalized contract for acquiring source media bytes from remote or local
locations without coupling downstream pipeline processing to specific download engines.
"""

from __future__ import annotations

import abc
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...config import IngestSettings
from ..ffmpeg import MediaInfo


class IngestError(RuntimeError):
    """Base error for all source ingestion failures."""

    def __init__(self, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:
        if self.hint:
            return f"{self.message}\n\n{self.hint}"
        return self.message


class SourceErrorCode:
    """Canonical error classifications for remote source acquisition."""

    SOURCE_INVALID_URL = "SOURCE_INVALID_URL"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    SOURCE_ACCESS_BLOCKED = "SOURCE_ACCESS_BLOCKED"
    SOURCE_AUTH_REQUIRED = "SOURCE_AUTH_REQUIRED"
    SOURCE_NETWORK_ERROR = "SOURCE_NETWORK_ERROR"
    SOURCE_PROVIDER_TIMEOUT = "SOURCE_PROVIDER_TIMEOUT"
    SOURCE_PROVIDER_UNAVAILABLE = "SOURCE_PROVIDER_UNAVAILABLE"
    SOURCE_FORMAT_ERROR = "SOURCE_FORMAT_ERROR"
    SOURCE_MEDIA_INVALID = "SOURCE_MEDIA_INVALID"
    SOURCE_ALL_PROVIDERS_FAILED = "SOURCE_ALL_PROVIDERS_FAILED"


class SourceAcquisitionError(IngestError):
    """Normalized structured source acquisition exception."""

    def __init__(
        self,
        message: str,
        *,
        code: str = SourceErrorCode.SOURCE_ALL_PROVIDERS_FAILED,
        hint: str = "",
        provider_name: str | None = None,
        attempts: int = 1,
    ) -> None:
        super().__init__(message, hint=hint)
        self.code = code
        self.provider_name = provider_name
        self.attempts = attempts

    def __str__(self) -> str:
        base = super().__str__()
        prov = f" ({self.provider_name})" if self.provider_name else ""
        return f"[{self.code}]{prov} {base}"


@dataclass(frozen=True)
class JobContext:
    """Execution context passed to acquisition providers."""

    job_id: str = ""
    source_id: str = ""
    settings: IngestSettings = field(default_factory=IngestSettings)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AcquisitionResult:
    """Normalized artifact produced by a successful source acquisition provider."""

    success: bool
    local_media_path: Path
    provider_name: str
    source_url: str
    detected_media_type: str = "video"
    duration: float = 0.0
    file_size: int = 0
    sha256: str = ""
    acquisition_attempts: int = 1
    diagnostic_code: str = "SUCCESS"
    diagnostic_message: str = "Source acquired and validated successfully."
    media_info: MediaInfo | None = None
    provider_metadata: dict[str, Any] = field(default_factory=dict)


class SourceAcquisitionProvider(abc.ABC):
    """Abstract interface that all source acquisition providers must implement."""

    @property
    @abc.abstractmethod
    def provider_name(self) -> str:
        """Unique identifying name for this acquisition provider."""
        ...

    @abc.abstractmethod
    def is_configured(self) -> bool:
        """Return True if this provider is configured and available for execution."""
        ...

    @abc.abstractmethod
    def acquire(
        self,
        source_url: str,
        target_dir: Path,
        job_context: JobContext | None = None,
        on_progress: Callable[[float], None] | None = None,
    ) -> AcquisitionResult:
        """Attempt to acquire source media bytes for source_url into target_dir.

        Raises SourceAcquisitionError if acquisition fails or media is invalid.
        """
        ...
