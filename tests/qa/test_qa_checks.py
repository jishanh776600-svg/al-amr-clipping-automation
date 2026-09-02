"""Unit tests for Layered QA Checks."""

import pytest
from clipping.contracts.qa import (
    MediaValidationResult,
    QACheckStatus,
    QASeverity,
)
from clipping.contracts.director import ReframePlan, ReframeCropKeyframe
from clipping.contracts.clip import RankedCandidate, ClipCandidate, ClipScore
from clipping.contracts.rendering import RenderOutput
from clipping.qa.checks import (
    check_media_integrity,
    check_subtitle_integrity,
    check_reframe_plan_integrity,
    check_artifact_consistency,
)


def test_media_integrity_pass():
    media = MediaValidationResult(
        is_valid=True,
        width=1080,
        height=1920,
        duration_seconds=30.2,
        fps=30.0,
        video_codec="h264",
        audio_codec="aac",
        file_size_bytes=5_000_000,
    )
    checks = check_media_integrity(media, expected_duration=30.0)

    for c in checks:
        assert c.status == QACheckStatus.PASS


def test_media_integrity_wrong_resolution_fail():
    media = MediaValidationResult(
        is_valid=True,
        width=1920,
        height=1080,  # Invalid: Landscape instead of 1080x1920 portrait
        duration_seconds=30.0,
        fps=30.0,
        video_codec="h264",
        file_size_bytes=5_000_000,
    )
    checks = check_media_integrity(media, expected_duration=30.0)

    res_check = next(c for c in checks if c.check_id == "media_resolution_1080x1920")
    assert res_check.status == QACheckStatus.FAIL
    assert res_check.severity == QASeverity.CRITICAL


def test_subtitle_integrity_valid_ass():
    ass_script = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,68,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4.5,2.5,2,60,60,420,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:03.50,Default,,0,0,0,,{\\c&H0000D7FF}Autonomous{\\c&H00FFFFFF} clipping engine
"""
    checks = check_subtitle_integrity(ass_script, clip_duration=5.0)

    for c in checks:
        assert c.status == QACheckStatus.PASS


def test_subtitle_integrity_negative_timestamps_fail():
    malformed_ass = """[Script Info]
[V4+ Styles]
[Events]
Dialogue: 0,0:00:05.00,0:00:02.00,Default,,0,0,0,,Backwards in time
"""
    checks = check_subtitle_integrity(malformed_ass, clip_duration=10.0)
    time_check = next(c for c in checks if c.check_id == "subtitle_timestamps_non_negative")
    assert time_check.status == QACheckStatus.FAIL


def test_reframe_plan_out_of_bounds_fail():
    plan = ReframePlan(
        clip_id="CLIP_OOB",
        source_width=1920,
        source_height=1080,
        target_width=1080,
        target_height=1920,
        keyframes=[
            ReframeCropKeyframe(timestamp=0.0, crop_x=1500, crop_y=0, crop_w=608, crop_h=1080)  # 1500 + 608 = 2108 > 1920!
        ],
    )
    checks = check_reframe_plan_integrity(plan, source_w=1920, source_h=1080)
    bounds_check = next(c for c in checks if c.check_id == "reframe_crop_bounds_safe")
    assert bounds_check.status == QACheckStatus.FAIL
    assert bounds_check.severity == QASeverity.CRITICAL


def test_artifact_id_mismatch_fail():
    cand = RankedCandidate(
        candidate=ClipCandidate(
            candidate_id="CLIP_EXPECTED",
            source_video_id="VID_01",
            start_time=0.0,
            end_time=30.0,
            duration=30.0,
            transcript_text="Transcript text",
            hook_sentence="Hook",
        ),
        score=ClipScore(candidate_id="CLIP_EXPECTED", hook_strength=80.0, narrative_completeness=80.0, curiosity_factor=80.0, overall_virality_score=80.0),
        rank=1,
    )
    render_out = RenderOutput(
        clip_id="CLIP_MISMATCHED",  # Mismatch!
        output_storage_key="clips/CLIP_MISMATCHED/final_1080x1920.mp4",
        duration_seconds=30.0,
        file_size_bytes=1000,
        render_time_seconds=1.0,
    )

    checks = check_artifact_consistency("CLIP_EXPECTED", "VID_01", selected_clip=cand, render_output=render_out)
    id_check = next(c for c in checks if c.check_id == "artifact_id_alignment")
    assert id_check.status == QACheckStatus.FAIL
    assert id_check.severity == QASeverity.CRITICAL
