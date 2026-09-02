"""Abstract interfaces for Subtitle Generation and Rendering."""

from abc import ABC, abstractmethod
from typing import List, Optional
from clipping.contracts.perception import WordTimestamp
from clipping.contracts.director import ReframePlan
from clipping.rendering.styles import SubtitleStyleConfig


class SubtitleGenerator(ABC):
    """Abstract interface for generating subtitle scripts from word timestamps."""

    @abstractmethod
    def generate_subtitles(
        self,
        words: List[WordTimestamp],
        clip_start: float,
        clip_end: float,
        style: Optional[SubtitleStyleConfig] = None,
    ) -> str:
        """Transforms word timestamps into normalized ASS subtitle script."""
        pass


class FiltergraphBuilder(ABC):
    """Abstract interface for compiling ReframePlan into FFmpeg filtergraphs."""

    @abstractmethod
    def build_filtergraph(
        self,
        reframe_plan: ReframePlan,
        subtitle_ass_path: Optional[str] = None,
        target_width: int = 1080,
        target_height: int = 1920,
    ) -> str:
        """Constructs an FFmpeg video filtergraph chain."""
        pass


class MediaRenderer(ABC):
    """Abstract interface for executing video composition and transcoding."""

    @abstractmethod
    async def render_clip(
        self,
        source_video_path: str,
        filtergraph: str,
        output_path: str,
        clip_start: float,
        clip_end: float,
    ) -> str:
        """Invokes rendering engine and produces final vertical video output."""
        pass
