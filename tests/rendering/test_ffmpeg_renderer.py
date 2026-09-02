"""Unit tests for FFmpeg subprocess execution and security."""

import os
import pytest
from clipping.rendering.ffmpeg import FFmpegRenderer
from clipping.rendering.exceptions import FFmpegExecutionError


def test_ffmpeg_binary_discovery():
    renderer = FFmpegRenderer()
    assert renderer.ffmpeg_path is not None
    assert len(renderer.ffmpeg_path) > 0


@pytest.mark.asyncio
async def test_ffmpeg_missing_source_file():
    renderer = FFmpegRenderer()
    with pytest.raises(FileNotFoundError):
        await renderer.render_clip(
            source_video_path="/non/existent/master.mp4",
            filtergraph="scale=1080:1920",
            output_path="/tmp/output.mp4",
            clip_start=0.0,
            clip_end=5.0,
        )


@pytest.mark.asyncio
async def test_ffmpeg_invalid_duration():
    renderer = FFmpegRenderer()
    with pytest.raises(ValueError):
        await renderer.render_clip(
            source_video_path=__file__,  # Existing file
            filtergraph="scale=1080:1920",
            output_path="/tmp/output.mp4",
            clip_start=10.0,
            clip_end=5.0,  # Invalid
        )
