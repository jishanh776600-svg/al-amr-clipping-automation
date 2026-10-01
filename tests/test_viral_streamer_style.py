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
