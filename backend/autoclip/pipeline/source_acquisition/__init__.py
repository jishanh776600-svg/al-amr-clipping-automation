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
from .providers.cobalt_provider import CobaltAcquisitionProvider
from .providers.http_api_provider import HttpApiAcquisitionProvider
from .providers.invidious_provider import InvidiousAcquisitionProvider
from .providers.piped_provider import PipedAcquisitionProvider
from .providers.ytdlp_provider import YtDlpAcquisitionProvider
from .registry import SourceAcquisitionRegistry, get_default_registry
from .security import safe_target_path, validate_remote_url
from .server_downloader import ServerDownloaderEngine, ServerDownloaderProvider
from .validation import validate_media_gate
from .youtube_utils import extract_youtube_id, is_youtube_url

__all__ = [
    "AcquisitionResult",
    "IngestError",
    "JobContext",
    "SourceAcquisitionError",
    "SourceAcquisitionProvider",
    "SourceErrorCode",
    "CobaltAcquisitionProvider",
    "PipedAcquisitionProvider",
    "InvidiousAcquisitionProvider",
    "ServerDownloaderEngine",
    "ServerDownloaderProvider",
    "YtDlpAcquisitionProvider",
    "HttpApiAcquisitionProvider",
    "SourceAcquisitionRegistry",
    "get_default_registry",
    "validate_remote_url",
    "safe_target_path",
    "validate_media_gate",
    "extract_youtube_id",
    "is_youtube_url",
]

