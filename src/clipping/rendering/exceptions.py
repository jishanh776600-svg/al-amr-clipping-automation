"""Exceptions for Subtitle Generation and Video Rendering."""


class RenderingError(Exception):
    """Base exception for all rendering and subtitle errors."""
    pass


class SubtitleGenerationError(RenderingError):
    """Raised when subtitle extraction, timing normalization, or formatting fails."""
    pass


class FFmpegExecutionError(RenderingError):
    """Raised when FFmpeg process execution fails or returns a non-zero exit code."""
    pass


class FiltergraphError(RenderingError):
    """Raised when virtual camera filtergraph construction fails."""
    pass
