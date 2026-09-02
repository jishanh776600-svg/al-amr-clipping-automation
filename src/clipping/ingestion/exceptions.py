"""Exceptions for Video Ingestion subsystem."""


class IngestionError(Exception):
    """Base exception for video ingestion errors."""
    pass


class InvalidSourceError(IngestionError):
    """Raised when the source URI or reference format is invalid."""
    pass


class IngestionNetworkError(IngestionError):
    """Raised when network download or connection fails."""
    pass


class UnsupportedMediaError(IngestionError):
    """Raised when media format or container is unsupported."""
    pass


class ChecksumMismatchError(IngestionError):
    """Raised when downloaded file integrity verification fails."""
    pass
