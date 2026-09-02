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

__all__ = [
    "SourceType",
    "SourceReference",
    "VideoIngestor",
    "RemoteVideoIngestor",
    "IngestionError",
    "InvalidSourceError",
    "IngestionNetworkError",
    "UnsupportedMediaError",
    "ChecksumMismatchError",
]
