"""Tests for Viral Streamer editing style, presets, filters, and compositions."""

import pytest
from pathlib import Path
from autoclip.pipeline import captions
from autoclip.pipeline.captions import (
    CaptionGroup,
    get_style,
    resolve_style,
    build_ass,
    VIRAL_STREAMER_KINETIC,
    VIRAL_NEON_GREEN,
    VIRAL_ONE_WORD,
)
from autoclip.pipeline import filters
from autoclip.pipeline.transcript import Word


def test_viral_streamer_preset_registration():
    """Verify that viral streamer presets are registered and accessible via aliases."""
    s1 = get_style("viral_streamer_kinetic")
    assert s1.key == "viral_streamer_kinetic"
    assert s1.font == "Anton"
    assert s1.all_caps is True
    assert s1.max_words == 2
    assert s1.animation == "viral_pop"
    assert s1.accent == "#FFE600"
    assert s1.outline_width >= 4.0

    # Test aliases
    assert get_style("viral").key == "viral_streamer_kinetic"
    assert get_style("speed").key == "viral_streamer_kinetic"
    assert get_style("ishowspeed").key == "viral_streamer_kinetic"
    assert get_style("streamer").key == "viral_streamer_kinetic"
    assert get_style("streamer_kinetic").key == "viral_streamer_kinetic"
    assert get_style("all_in_one_viral").key == "viral_streamer_kinetic"
    assert get_style("all in one viral").key == "viral_streamer_kinetic"


    # Test neon green preset
    s2 = get_style("viral_neon_green")
    assert s2.accent == "#00FF66"
    assert get_style(
        "neon_green"
    ).key == "viral_neon_green"

    # Test one word preset
    s3 = get_style("viral_one_word")
    assert s3.max_words == 1
    assert get_style("one_word").key == "viral_one_word"


def test_viral_pop_ass_generation():
    """Verify that build_ass generates pop events with scale tags and color accents."""
    words = [
        Word(text="Look", start=0.0, end=0.4),
        Word(text="at", start=0.4, end=0.6),
        Word(text="this", start=0.6, end=1.0),
        Word(text="jump", start=1.0, end=1.5),
    ]

    style = get_style("viral_streamer_kinetic")
    ass = build_ass(words, style, width=1080, height=1920)

    assert len(ass.events) >= 4
    event_texts = [e.text for e in ass.events]
    assert any("\\fscx120\\fscy120" in t for t in event_texts)
    assert any("LOOK" in t for t in event_texts)
    assert any("JUMP" in t for t in event_texts)


def test_viral_vibrant_filter():
    """Verify that viral_vibrant and streamer_hdr filters are registered and valid."""
    assert filters.is_valid_filter("viral_vibrant")
    assert filters.is_valid_filter("streamer_hdr")
    assert filters.is_valid_filter("viral")
    assert filters.is_valid_filter("streamer")

    f1 = filters.get_filter("viral_vibrant")
    assert "saturation=1.28" in f1.ffmpeg_expr
    assert "unsharp" in f1.ffmpeg_expr

    f2 = filters.get_filter("streamer")
    assert f2.id == "viral_vibrant"


def test_split_screen_divider_in_export():
    """Verify that export filtergraph for split screen includes horizontal divider."""
    from autoclip.pipeline.export import _split_chain
    from autoclip.pipeline.reframe.croppath import CropSegment, CropKeyframe, Strategy

    seg = CropSegment(
        start_s=0.0,
        end_s=10.0,
        width=540,
        height=960,
        keyframes=[CropKeyframe(t=0.0, x=0, y=0)],
        secondary_keyframes=[CropKeyframe(t=0.0, x=540, y=0)],
        strategy=Strategy.SPLIT,
    )

    chain = _split_chain("[0:v]", seg, index=0, out_w=1080, out_h=1920)
    full_str = " ".join(chain)
    assert "drawbox=" in full_str
    assert "vstack=" in full_str


def test_sfx_engine_planning_and_rendering(tmp_path):
    """Verify that SFXEngine plans and renders sound effects for words and cuts."""
    from autoclip.pipeline.sfx import SFXEngine
    from autoclip.pipeline.reframe.croppath import CropPath, CropSegment, CropKeyframe, Strategy

    engine = SFXEngine()
    words = [
        Word(text="Look", start=0.5, end=0.8),
        Word(text="at", start=0.8, end=1.0),
        Word(text="this", start=1.0, end=1.2),
        Word(text="win", start=2.5, end=3.0),
        Word(text="insane", start=4.0, end=4.5),
    ]

    crop_path = CropPath(
        source_width=1920,
        source_height=1080,
        segments=[
            CropSegment(start_s=0.0, end_s=3.0, width=540, height=960, keyframes=[CropKeyframe(0.0, 0, 0)]),
            CropSegment(start_s=3.0, end_s=6.0, width=540, height=960, keyframes=[CropKeyframe(3.0, 540, 0)]),
        ]
    )

    events = engine.plan_sfx_events(
        words=words,
        crop_path=crop_path,
        duration_s=6.0,
        climax_window=(4.0, 5.5),
    )

    assert len(events) >= 3
    event_types = [e.sound_type for e in events]
    # Check that cut whoosh, hook pop, and boom are present
    assert "whoosh" in event_types or "pop" in event_types
    assert "boom" in event_types or "ding" in event_types

    out_sfx = tmp_path / "test_sfx.m4a"
    engine.render_sfx_track(events, duration_s=6.0, output_path=out_sfx)
    assert out_sfx.is_file()
    assert out_sfx.stat().st_size > 1000

