"""Source Acquisition Subsystem.

Provides an isolated, multi-provider abstraction layer for acquiring remote media bytes
with deterministic fallback, unified media validation, SSRF security guarantees,
and forensic provenance tracking.
"""

from .base import (
    AcquisitionResult,
    IngestError,
    JobContext,
    SourceAcquisitionError,
    SourceAcquisitionProvider,
    SourceErrorCode,
)
from .providers.http_api_provider import HttpApiAcquisitionProvider
from .providers.ytdlp_provider import YtDlpAcquisitionProvider
from .registry import SourceAcquisitionRegistry, get_default_registry
from .security import safe_target_path, validate_remote_url
from .validation import validate_media_gate

__all__ = [
    "AcquisitionResult",
    "IngestError",
    "JobContext",
    "SourceAcquisitionError",
    "SourceAcquisitionProvider",
    "SourceErrorCode",
    "YtDlpAcquisitionProvider",
    "HttpApiAcquisitionProvider",
    "SourceAcquisitionRegistry",
    "get_default_registry",
    "validate_remote_url",
    "safe_target_path",
    "validate_media_gate",
]
