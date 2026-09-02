"""Unit tests for FFmpeg Filtergraph Construction."""

import pytest
from clipping.contracts.director import ReframePlan, ReframeCropKeyframe
from clipping.rendering.filters import FFmpegFiltergraphBuilder, escape_ffmpeg_filter_path
from clipping.rendering.exceptions import FiltergraphError


def test_escape_ffmpeg_filter_path():
    # Windows path with backslashes and colon
    win_path = r"C:\Users\worker\tmp\subtitles.ass"
    escaped = escape_ffmpeg_filter_path(win_path)
    assert "\\:" in escaped
    assert "\\" not in escaped.replace("\\:", "")  # All backslashes converted to /


def test_static_reframe_filtergraph():
    plan = ReframePlan(
        clip_id="CLIP_STATIC",
        source_width=1920,
        source_height=1080,
        target_width=1080,
        target_height=1920,
        keyframes=[
            ReframeCropKeyframe(timestamp=0.0, crop_x=656, crop_y=0, crop_w=608, crop_h=1080)
        ],
    )

    builder = FFmpegFiltergraphBuilder()
    fg = builder.build_filtergraph(plan, subtitle_ass_path=r"C:\tmp\subs.ass")

    assert "crop=608:1080:656:0" in fg
    assert "scale=1080:1920:flags=lanczos" in fg
    assert "ass=filename=" in fg


def test_dynamic_reframe_filtergraph():
    plan = ReframePlan(
        clip_id="CLIP_DYNAMIC",
        source_width=1920,
        source_height=1080,
        target_width=1080,
        target_height=1920,
        keyframes=[
            ReframeCropKeyframe(timestamp=0.0, crop_x=200, crop_y=0, crop_w=608, crop_h=1080),
            ReframeCropKeyframe(timestamp=1.5, crop_x=800, crop_y=0, crop_w=608, crop_h=1080),
        ],
    )

    builder = FFmpegFiltergraphBuilder()
    fg = builder.build_filtergraph(plan)

    assert "crop=w=608:h=1080:x='if(lt(t,1.500),200,800)':y=0" in fg
    assert "scale=1080:1920:flags=lanczos" in fg
