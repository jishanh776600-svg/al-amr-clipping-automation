"""Exceptions for Video Understanding and Virtual Camera Director."""


class VisionError(Exception):
    """Base exception for all vision and reframing errors."""
    pass


class SceneDetectionError(VisionError):
    """Raised when scene detection fails."""
    pass


class TrackingError(VisionError):
    """Raised when face/person tracking fails."""
    pass


class ActiveSpeakerResolutionError(VisionError):
    """Raised when active speaker resolution fails."""
    pass


class ReframeError(VisionError):
    """Raised when reframe planning or geometry calculation fails."""
    pass
