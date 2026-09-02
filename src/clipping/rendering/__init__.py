"""Rendering package exports."""

from clipping.rendering.base import (
    SubtitleGenerator,
    FiltergraphBuilder,
    MediaRenderer,
)
from clipping.rendering.styles import SubtitlePreset, SubtitleStyleConfig
from clipping.rendering.subtitles import AssSubtitleGenerator, format_ass_time, escape_ass_text
from clipping.rendering.filters import FFmpegFiltergraphBuilder, escape_ffmpeg_filter_path
from clipping.rendering.ffmpeg import FFmpegRenderer
from clipping.rendering.engine import RenderOrchestrationEngine
from clipping.rendering.exceptions import (
    RenderingError,
    SubtitleGenerationError,
    FFmpegExecutionError,
    FiltergraphError,
)

__all__ = [
    "SubtitleGenerator",
    "FiltergraphBuilder",
    "MediaRenderer",
    "SubtitlePreset",
    "SubtitleStyleConfig",
    "AssSubtitleGenerator",
    "format_ass_time",
    "escape_ass_text",
    "FFmpegFiltergraphBuilder",
    "escape_ffmpeg_filter_path",
    "FFmpegRenderer",
    "RenderOrchestrationEngine",
    "RenderingError",
    "SubtitleGenerationError",
    "FFmpegExecutionError",
    "FiltergraphError",
]
