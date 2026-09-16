"""Comprehensive regression test suite for strict duration constraints enforcement.
Tests:
1. Configured 20–30 sec -> final accepted MP4 must be 20–30 sec.
2. A 15 sec candidate cannot pass a 20 sec minimum.
3. A 19 sec candidate cannot pass a 20 sec minimum.
4. A 20 sec candidate can pass.
5. A 30 sec candidate can pass.
6. A >30 sec final render is rejected.
7. If no >=20 sec candidate exists, the system fails clearly instead of uploading a short clip.
8. Ad-hoc gallery upload with campaign_spec=None still obeys 20–30 sec.
9. The worker receives the configured duration values rather than silently using defaults.
10. Final validation checks the actual rendered MP4 duration.
"""

from unittest.mock import MagicMock, patch
from pathlib import Path

from autoclip.campaign.duration import resolve_duration_limits, validate_duration_bounds
from autoclip.campaign.candidate_discovery import CandidateDiscoveryEngine
from autoclip.campaign.clip_assembly import PreRenderQualityGate, SmartBoundaryEngine
from autoclip.pipeline.retention.quality_gate import FinalPreRenderQualityGate
from autoclip.pipeline.retention.pacing import DynamicPacingEngine
from autoclip.pipeline.final_render.quality_gate import FinalRenderQualityGate
from autoclip.pipeline.validator import validate_media_output
from autoclip.pipeline.transcript import Transcript, Word
from autoclip.db import models


def test_1_and_8_duration_resolution_adhoc():
    """Verify ad-hoc jobs (campaign_spec=None) resolve configured 20-30s duration."""
    job_settings = {
        "min_duration_s": 20.0,
        "max_duration_s": 30.0,
        "clips": {"min_duration_s": 20.0, "max_duration_s": 30.0},
    }
    min_dur, max_dur = resolve_duration_limits(job_settings=job_settings, campaign_spec=None, campaign_brief=None)
    assert min_dur == 20.0
    assert max_dur == 30.0

    # Also test resolution when only nested in clips
    job_settings_nested = {
        "clips": {"min_duration_s": 25.0, "max_duration_s": 35.0},
    }
    min_dur2, max_dur2 = resolve_duration_limits(job_settings=job_settings_nested, campaign_spec=None)
    assert min_dur2 == 25.0
    assert max_dur2 == 35.0


def test_2_3_4_5_candidate_and_pre_render_duration_bounds():
    """Verify 15s and 19s candidates fail a 20s minimum, while 20s and 30s pass."""
    gate = PreRenderQualityGate(job_settings={"min_duration_s": 20.0, "max_duration_s": 30.0})

    words = [Word(start=i * 1.0, end=i * 1.0 + 0.8, text=f"word{i}") for i in range(35)]
    transcript = Transcript(words=words, language="en")

    # 15s candidate
    opt_15 = MagicMock()
    opt_15.duration_s = 15.0
    opt_15.start_word = 0
    opt_15.end_word = 14
    opt_15.hook_type = "question"
    opt_15.hook_score = 9.0
    opt_15.climax_start_s = None
    opt_15.cta_type = ""
    res_15 = gate.evaluate(opt_15, transcript)
    assert res_15.is_approved is False
    assert any("duration_under_min" in r for r in res_15.rejection_reasons)

    # 19s candidate
    opt_19 = MagicMock()
    opt_19.duration_s = 19.0
    opt_19.start_word = 0
    opt_19.end_word = 18
    opt_19.hook_type = "question"
    opt_19.hook_score = 9.0
    opt_19.climax_start_s = None
    opt_19.cta_type = ""
    res_19 = gate.evaluate(opt_19, transcript)
    assert res_19.is_approved is False
    assert any("duration_under_min" in r for r in res_19.rejection_reasons)

    # 20s candidate
    opt_20 = MagicMock()
    opt_20.duration_s = 20.0
    opt_20.start_word = 0
    opt_20.end_word = 19
    opt_20.hook_type = "question"
    opt_20.hook_score = 9.0
    opt_20.climax_start_s = None
    opt_20.cta_type = ""
    res_20 = gate.evaluate(opt_20, transcript)
    assert not any("duration_under_min" in r for r in res_20.rejection_reasons)

    # 30s candidate
    opt_30 = MagicMock()
    opt_30.duration_s = 30.0
    opt_30.start_word = 0
    opt_30.end_word = 29
    opt_30.hook_type = "question"
    opt_30.hook_score = 9.0
    opt_30.climax_start_s = None
    opt_30.cta_type = ""
    res_30 = gate.evaluate(opt_30, transcript)
    assert not any("duration_over_max" in r for r in res_30.rejection_reasons)

    # 31s candidate (> 30s)
    opt_31 = MagicMock()
    opt_31.duration_s = 31.0
    opt_31.start_word = 0
    opt_31.end_word = 30
    opt_31.hook_type = "question"
    opt_31.hook_score = 9.0
    opt_31.climax_start_s = None
    opt_31.cta_type = ""
    res_31 = gate.evaluate(opt_31, transcript)
    assert res_31.is_approved is False
    assert any("duration_over_max" in r for r in res_31.rejection_reasons)


def test_6_and_10_final_render_quality_gate_checks_actual_mp4_duration():
    """Verify FinalRenderQualityGate probes actual MP4 duration and rejects <20s or >30s."""
    gate = FinalRenderQualityGate()

    # Mock probe_media returning actual 15.0s video
    mock_probe_15 = {
        "format": {"format_name": "mp4", "duration": "15.0"},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1080,
                "height": 1920,
                "r_frame_rate": "30/1",
                "duration": "15.0",
            },
            {
                "codec_type": "audio",
                "codec_name": "aac",
                "channels": 2,
                "sample_rate": 48000,
                "duration": "15.0",
            },
        ],
    }

    with patch.object(gate, "probe_media", return_value=mock_probe_15), \
         patch.object(gate, "decode_check", return_value=(True, "")), \
         patch.object(gate, "measure_audio_levels", return_value=(-14.0, -1.5)), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.is_file", return_value=True), \
         patch("pathlib.Path.stat") as mock_stat:

        mock_stat.return_value.st_size = 5000000

        res_15 = gate.evaluate(
            output_path=Path("/tmp/clip15.mp4"),
            expected_duration_s=15.0,
            min_duration_s=20.0,
            max_duration_s=30.0,
        )
        assert res_15.is_approved is False
        assert any("below configured minimum 20.00s" in r for r in res_15.rejection_reasons)

    # Mock probe_media returning actual 35.0s video (>30s)
    mock_probe_35 = {
        "format": {"format_name": "mp4", "duration": "35.0"},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1080,
                "height": 1920,
                "r_frame_rate": "30/1",
                "duration": "35.0",
            },
            {
                "codec_type": "audio",
                "codec_name": "aac",
                "channels": 2,
                "sample_rate": 48000,
                "duration": "35.0",
            },
        ],
    }

    with patch.object(gate, "probe_media", return_value=mock_probe_35), \
         patch.object(gate, "decode_check", return_value=(True, "")), \
         patch.object(gate, "measure_audio_levels", return_value=(-14.0, -1.5)), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.is_file", return_value=True), \
         patch("pathlib.Path.stat") as mock_stat:

        mock_stat.return_value.st_size = 5000000

        res_35 = gate.evaluate(
            output_path=Path("/tmp/clip35.mp4"),
            expected_duration_s=35.0,
            min_duration_s=20.0,
            max_duration_s=30.0,
        )
        assert res_35.is_approved is False
        assert any("above configured maximum 30.00s" in r for r in res_35.rejection_reasons)

    # Mock probe_media returning valid 25.0s video (20-30s)
    mock_probe_25 = {
        "format": {"format_name": "mp4", "duration": "25.0"},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1080,
                "height": 1920,
                "r_frame_rate": "30/1",
                "duration": "25.0",
            },
            {
                "codec_type": "audio",
                "codec_name": "aac",
                "channels": 2,
                "sample_rate": 48000,
                "duration": "25.0",
            },
        ],
    }

    with patch.object(gate, "probe_media", return_value=mock_probe_25), \
         patch.object(gate, "decode_check", return_value=(True, "")), \
         patch.object(gate, "measure_audio_levels", return_value=(-14.0, -1.5)), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.is_file", return_value=True), \
         patch("pathlib.Path.stat") as mock_stat:

        mock_stat.return_value.st_size = 5000000

        res_25 = gate.evaluate(
            output_path=Path("/tmp/clip25.mp4"),
            expected_duration_s=25.0,
            min_duration_s=20.0,
            max_duration_s=30.0,
        )
        assert res_25.is_approved is True


def test_7_candidate_discovery_no_valid_candidates_fails_cleanly():
    """Verify that if source duration < min_duration_s, discovery returns empty rather than short clips."""
    # Source is only 15 seconds long, but min_duration_s is 20s
    words = [Word(start=i * 1.0, end=i * 1.0 + 0.9, text=f"short{i}") for i in range(15)]
    transcript = Transcript(words=words, language="en")

    discovery = CandidateDiscoveryEngine(
        job_settings={"min_duration_s": 20.0, "max_duration_s": 30.0}
    )
    windows = discovery.discover_windows(transcript)
    assert len(windows) == 0  # Must NOT produce windows


def test_9_pacing_engine_preserves_min_duration():
    """Verify DynamicPacingEngine does not compress a 20s clip below 20s."""
    pacing = DynamicPacingEngine()
    # 20 words spanning 20.0s
    words = [Word(start=0.5 + i * 0.95, end=0.5 + i * 0.95 + 0.8, text=f"w{i}") for i in range(20)]
    clip_start = 0.0
    clip_end = 20.2  # 20.2s raw

    res = pacing.analyze_and_tighten(
        words=words,
        clip_start_s=clip_start,
        clip_end_s=clip_end,
        min_duration_s=20.0,
        max_duration_s=30.0,
    )
    dur = res.tightened_end_s - res.tightened_start_s
    assert dur >= 20.0


def test_10_validate_media_output_with_duration_bounds():
    """Verify validate_media_output strictly fails when actual duration is out of bounds."""
    info_15 = MagicMock(duration_s=15.2, width=1080, height=1920, has_video=True, has_audio=True, video_codec="h264", audio_codec="aac")
    with patch("autoclip.pipeline.ffmpeg.probe", return_value=info_15), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.is_file", return_value=True), \
         patch("pathlib.Path.stat") as mock_stat:

        mock_stat.return_value.st_size = 5000000
        val = validate_media_output(
            Path("/tmp/clip.mp4"),
            min_duration_s=20.0,
            max_duration_s=30.0,
            decode_check=False,
        )
        assert val.is_valid is False
        assert any("strictly below configured minimum 20.00s" in err for err in val.errors)


def test_11_worker_runner_argparse_handles_empty_and_valid_values():
    """Verify worker_runner parses float or empty string duration arguments and job_settings safely."""
    import sys
    from autoclip.jobs.worker_runner import parse_args

    orig_argv = sys.argv
    try:
        # Case 1: valid numbers and json
        sys.argv = [
            "worker_runner.py",
            "--job-id", "job-123",
            "--source-url", "https://example.com/video.mp4",
            "--min-duration", "20",
            "--max-duration", "30",
            "--job-settings", '{"clips": {"min_duration_s": 20.0}}',
        ]
        args1 = parse_args()
        assert args1.min_duration == 20.0
        assert args1.max_duration == 30.0
        assert 'min_duration_s' in args1.job_settings

        # Case 2: empty strings (e.g. from empty env or input)
        sys.argv = [
            "worker_runner.py",
            "--job-id", "job-123",
            "--source-url", "https://example.com/video.mp4",
            "--min-duration", "",
            "--max-duration", "",
            "--job-settings", "",
        ]
        args2 = parse_args()
        assert args2.min_duration is None
        assert args2.max_duration is None
        assert args2.job_settings == ""
    finally:
        sys.argv = orig_argv


if __name__ == "__main__":
    print("Running test_1_and_8_duration_resolution_adhoc...")
    test_1_and_8_duration_resolution_adhoc()
    print("Running test_2_3_4_5_candidate_and_pre_render_duration_bounds...")
    test_2_3_4_5_candidate_and_pre_render_duration_bounds()
    print("Running test_6_and_10_final_render_quality_gate_checks_actual_mp4_duration...")
    test_6_and_10_final_render_quality_gate_checks_actual_mp4_duration()
    print("Running test_7_candidate_discovery_no_valid_candidates_fails_cleanly...")
    test_7_candidate_discovery_no_valid_candidates_fails_cleanly()
    print("Running test_9_pacing_engine_preserves_min_duration...")
    test_9_pacing_engine_preserves_min_duration()
    print("Running test_10_validate_media_output_with_duration_bounds...")
    test_10_validate_media_output_with_duration_bounds()
    print("Running test_11_worker_runner_argparse_handles_empty_and_valid_values...")
    test_11_worker_runner_argparse_handles_empty_and_valid_values()
    print("ALL DURATION ENFORCEMENT & WORKER ARGPARSE TESTS PASSED SUCCESSFULLY!")
