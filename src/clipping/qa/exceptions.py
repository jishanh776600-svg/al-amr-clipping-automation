"""Exceptions for Quality Assurance & Validation."""


class QAError(Exception):
    """Base exception for QA evaluation failures."""
    pass


class MediaProbeError(QAError):
    """Raised when probing media container or streams fails."""
    pass


class ArtifactIntegrityError(QAError):
    """Raised when cross-artifact IDs, timestamps, or files are mismatched or missing."""
    pass


class QAGatingError(QAError):
    """Raised when a critical QA check blocks publication."""
    pass
