"""Unit tests for ASS Subtitle Generation, Timing Normalization & Styling."""

import pytest
from clipping.contracts.perception import WordTimestamp
from clipping.rendering.subtitles import AssSubtitleGenerator, format_ass_time, escape_ass_text
from clipping.rendering.styles import SubtitlePreset, SubtitleStyleConfig
from clipping.rendering.exceptions import SubtitleGenerationError


def test_format_ass_time():
    assert format_ass_time(0.0) == "0:00:00.00"
    assert format_ass_time(1.25) == "0:00:01.25"
    assert format_ass_time(65.5) == "0:01:05.50"
    assert format_ass_time(3661.08) == "1:01:01.08"


def test_escape_ass_text():
    assert escape_ass_text("Hello {World} \\ Test") == "Hello (World) \\\\ Test"


def test_subtitle_timestamp_normalization():
    # Words in global source space from 100.0s to 105.0s
    words = [
        WordTimestamp(word="This", start=99.0, end=100.4, probability=0.99),  # Starts before clip
        WordTimestamp(word="is", start=100.5, end=101.0, probability=0.98),
        WordTimestamp(word="an", start=101.1, end=101.4, probability=0.99),
        WordTimestamp(word="autonomous", start=101.5, end=102.2, probability=0.95),
        WordTimestamp(word="pipeline.", start=102.3, end=103.0, probability=0.97),
        WordTimestamp(word="Future", start=105.5, end=106.0, probability=0.90),  # Starts after clip
    ]

    clip_start = 100.0
    clip_end = 104.0

    generator = AssSubtitleGenerator()
    ass_script = generator.generate_subtitles(words, clip_start=clip_start, clip_end=clip_end)

    assert "[Script Info]" in ass_script
    assert "PlayResX: 1080" in ass_script
    assert "PlayResY: 1920" in ass_script
    assert "[Events]" in ass_script

    # "This" starts at 100.0 (normalized: 0.0s)
    # "Future" should be excluded
    assert "Future" not in ass_script
    assert "Dialogue:" in ass_script


def test_kinetic_karaoke_highlighting():
    words = [
        WordTimestamp(word="Bold", start=0.0, end=0.5, probability=0.99),
        WordTimestamp(word="Gold", start=0.6, end=1.0, probability=0.99),
        WordTimestamp(word="Text", start=1.1, end=1.5, probability=0.99),
    ]

    style = SubtitleStyleConfig.from_preset(SubtitlePreset.KINETIC_GOLD)
    generator = AssSubtitleGenerator()
    ass_script = generator.generate_subtitles(words, clip_start=0.0, clip_end=2.0, style=style)

    # Verify Gold highlight color is embedded in dialogue events
    assert style.highlight_color in ass_script
    assert "Bold" in ass_script
    assert "Gold" in ass_script
    assert "Text" in ass_script


def test_empty_words_handling():
    generator = AssSubtitleGenerator()
    ass_script = generator.generate_subtitles([], clip_start=0.0, clip_end=5.0)
    assert "[Script Info]" in ass_script
    assert "[Events]" in ass_script


def test_invalid_clip_interval():
    generator = AssSubtitleGenerator()
    with pytest.raises(SubtitleGenerationError):
        generator.generate_subtitles([], clip_start=10.0, clip_end=5.0)
