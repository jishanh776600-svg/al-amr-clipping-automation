"""Video ingestion package exports."""

from clipping.ingestion.source import SourceType, SourceReference
from clipping.ingestion.base import VideoIngestor
from clipping.ingestion.remote import RemoteVideoIngestor
from clipping.ingestion.exceptions import (
    IngestionError,
    InvalidSourceError,
    IngestionNetworkError,
    UnsupportedMediaError,
    ChecksumMismatchError,
)

from clipping.ingestion.robust_downloader import RobustMediaDownloader
from clipping.ingestion.source_resolver import SourceResolutionEngine

__all__ = [
    "SourceType",
    "SourceReference",
    "VideoIngestor",
    "RemoteVideoIngestor",
    "RobustMediaDownloader",
    "SourceResolutionEngine",
    "IngestionError",
    "InvalidSourceError",
    "IngestionNetworkError",
    "UnsupportedMediaError",
    "ChecksumMismatchError",
]

