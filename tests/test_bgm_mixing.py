"""Focused tests for Step 21: Production-Grade BGM Mixing, Ducking, Looping & Audio Quality."""

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from autoclip.db import connection, schema, store
from autoclip.db.models import BGMAssetRecord, BGMMixRecord, Clip, new_id, utcnow
from autoclip.pipeline.audio_mix import (
    AudioGateResult,
    AudioQualityGate,
    BGMMixingEngine,
    DuckingConfig,
)
from autoclip.pipeline.captions import CaptionEngine, CaptionGateResult


@pytest.fixture
def synth_audio_fixture(tmp_path: Path):
    """Generates synthetic speech and BGM WAV files for deterministic audio mixing tests."""
    speech_path = tmp_path / "speech.wav"
    bgm_short_path = tmp_path / "bgm_short.mp3"
    bgm_long_path = tmp_path / "bgm_long.mp3"

    # 4-second speech audio (sine wave 400Hz)
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "sine=frequency=400:duration=4",
            "-c:a", "pcm_s16le",
            str(speech_path),
        ],
        capture_output=True,
        check=True,
    )

    # 2-second short BGM track (to test seamless looping when clip > bgm)
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "sine=frequency=220:duration=2",
            "-c:a", "libmp3lame", "-b:a", "128k",
            str(bgm_short_path),
        ],
        capture_output=True,
        check=True,
    )

    # 10-second long BGM track (to test trimming when clip <= bgm)
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "sine=frequency=220:duration=10",
            "-c:a", "libmp3lame", "-b:a", "128k",
            str(bgm_long_path),
        ],
        capture_output=True,
        check=True,
    )

    return speech_path, bgm_short_path, bgm_long_path


def test_ducking_config_defaults():
    """Validates default ducking parameters align with production-grade standards."""
    cfg = DuckingConfig()
    assert cfg.duck_attenuation_db == 16.0
    assert cfg.attack_ms == 150.0
    assert cfg.release_ms == 450.0
    assert cfg.target_lufs == -14.0
    assert cfg.true_peak_limit == -1.5
    assert cfg.limiter_limit == 0.95

    d = cfg.to_dict()
    assert d["duck_attenuation_db"] == 16.0
    assert d["attack_ms"] == 150.0
    assert d["target_lufs"] == -14.0


def test_audio_quality_gate_missing_file(tmp_path: Path):
    """Quality gate cleanly rejects non-existent files."""
    gate = AudioQualityGate()
    result = gate.evaluate(tmp_path / "non_existent.m4a", expected_duration_s=5.0)
    assert result.status == "MIX_REJECT"
    assert result.quality_score == 0.0
    assert len(result.rejection_reasons) > 0


def test_bgm_mixing_no_bgm_mode(synth_audio_fixture, tmp_path: Path):
    """When No BGM is selected, speech audio is extracted untouched with bgm_applied=False."""
    speech_path, _, _ = synth_audio_fixture
    engine = BGMMixingEngine()

    clip = Clip(
        id="clip-nobgm-1",
        job_id="job-1",
        rank=1,
        start_s=0.0,
        end_s=3.0,
        title="No BGM Clip",
        status="discovered",
    )

    out_audio = tmp_path / "nobgm_out.m4a"
    record = engine.mix_clip(
        clip=clip,
        speech_input_path=speech_path,
        speech_start_offset_s=0.0,
        duration_s=3.0,
        bgm_asset=None,
        output_path=out_audio,
    )

    assert out_audio.is_file()
    assert record.bgm_applied is False
    assert record.bgm_asset_id is None
    assert record.loop_trim_decision == "none"
    assert record.quality_status in ("MIX_PASS", "MIX_WARN")
    assert record.is_approved is True


def test_bgm_mixing_trim_mode(synth_audio_fixture, tmp_path: Path):
    """When clip duration <= BGM duration, trimming and ducking are applied."""
    speech_path, _, bgm_long_path = synth_audio_fixture
    engine = BGMMixingEngine()

    bgm_asset = BGMAssetRecord(
        id="bgm-long-1",
        name="Long Epic",
        file_path=str(bgm_long_path),
        duration_s=10.0,
    )

    clip = Clip(
        id="clip-trim-1",
        job_id="job-1",
        rank=1,
        start_s=0.0,
        end_s=3.5,
        title="Trim Test Clip",
        status="discovered",
    )

    out_audio = tmp_path / "trim_out.m4a"
    record = engine.mix_clip(
        clip=clip,
        speech_input_path=speech_path,
        speech_start_offset_s=0.0,
        duration_s=3.5,
        bgm_asset=bgm_asset,
        output_path=out_audio,
    )

    assert out_audio.is_file()
    assert record.bgm_applied is True
    assert record.bgm_asset_name == "Long Epic"
    assert record.loop_trim_decision == "trim"
    assert record.ducking_applied is True
    assert record.normalization_applied is True
    assert record.quality_status in ("MIX_PASS", "MIX_WARN")
    assert record.is_approved is True
    assert record.true_peak_db <= 0.0


def test_bgm_mixing_loop_mode(synth_audio_fixture, tmp_path: Path):
    """When clip duration > BGM duration, seamless looping is engaged."""
    speech_path, bgm_short_path, _ = synth_audio_fixture
    engine = BGMMixingEngine()

    # BGM is 2 seconds, clip is 3.5 seconds -> requires looping
    bgm_asset = BGMAssetRecord(
        id="bgm-short-1",
        name="Short Beat",
        file_path=str(bgm_short_path),
        duration_s=2.0,
    )

    clip = Clip(
        id="clip-loop-1",
        job_id="job-1",
        rank=1,
        start_s=0.0,
        end_s=3.5,
        title="Loop Test Clip",
        status="discovered",
    )

    out_audio = tmp_path / "loop_out.m4a"
    record = engine.mix_clip(
        clip=clip,
        speech_input_path=speech_path,
        speech_start_offset_s=0.0,
        duration_s=3.5,
        bgm_asset=bgm_asset,
        output_path=out_audio,
    )

    assert out_audio.is_file()
    assert record.bgm_applied is True
    assert record.loop_trim_decision == "loop"
    assert record.ducking_applied is True
    assert record.quality_status in ("MIX_PASS", "MIX_WARN")


def test_bgm_mixing_missing_asset_raises(synth_audio_fixture, tmp_path: Path):
    """When operator-selected BGM is missing on disk, fails cleanly without substitution."""
    speech_path, _, _ = synth_audio_fixture
    engine = BGMMixingEngine()

    missing_asset = BGMAssetRecord(
        id="bgm-missing-99",
        name="Ghost Track",
        file_path=str(tmp_path / "does_not_exist.mp3"),
        duration_s=10.0,
    )

    clip = Clip(
        id="clip-err-1",
        job_id="job-1",
        rank=1,
        start_s=0.0,
        end_s=3.0,
        title="Error Clip",
        status="discovered",
    )

    with pytest.raises(FileNotFoundError) as exc_info:
        engine.mix_clip(
            clip=clip,
            speech_input_path=speech_path,
            speech_start_offset_s=0.0,
            duration_s=3.0,
            bgm_asset=missing_asset,
            output_path=tmp_path / "should_not_exist.m4a",
        )
    assert "Ghost Track" in str(exc_info.value)


def test_caption_rejection_no_silent_fallback():
    """Ensures CaptionEngine does NOT substitute generic fallback on quality gate rejection or error."""
    engine = CaptionEngine()

    clip = Clip(
        id="clip-cap-1",
        job_id="job-cap-1",
        rank=1,
        start_s=0.0,
        end_s=5.0,
        title="Cap Test",
        status="discovered",
    )

    subs, rec = engine.generate_captions(
        clip=clip,
        words=[],
        style_key="classic_professional",
        width=1080,
        height=1920,
    )

    # Must NOT have fallback_used=True
    assert rec.fallback_used is False
    assert rec.quality_status in ("CAPTION_PASS", "CAPTION_REJECT")


def test_db_store_bgm_mixes(tmp_path: Path, monkeypatch):
    """Verifies SQLite schema V16 and store operations for BGMMixRecord."""
    db_file = tmp_path / "test_store.db"
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    schema.migrate(conn)

    monkeypatch.setattr(store, "connection", lambda: conn)

    from autoclip.db.models import Job, Source

    # Create dummy job and clip for foreign keys
    store.create_source(Source(id="s1", type="upload", path="p"))
    store.create_job(Job(id="job-store-1", source_id="s1", status="running"))
    store.create_clip(Clip(id="clip-store-1", job_id="job-store-1", rank=1, start_s=0.0, end_s=5.0, status="candidate", score=85))

    record = BGMMixRecord(
        id="mix-rec-1",
        clip_id="clip-store-1",
        job_id="job-store-1",
        bgm_asset_id="bgm-asset-1",
        bgm_asset_name="Corporate Upbeat",
        bgm_applied=True,
        clip_duration_s=5.0,
        bgm_duration_s=120.0,
        loop_trim_decision="trim",
        ducking_applied=True,
        ducking_parameters={"duck_attenuation_db": 16.0},
        normalization_applied=True,
        integrated_lufs=-14.2,
        true_peak_db=-1.5,
        quality_score=98.0,
        quality_status="MIX_PASS",
        warnings=[],
        rejection_reasons=[],
        processing_time_s=0.45,
        mixed_audio_path="/path/to/mix.m4a",
        telemetry={"engine": "ffmpeg"},
    )

    # 1. Replace
    stored = store.replace_bgm_mixes("job-store-1", [record])
    assert len(stored) == 1

    # 2. List
    results = store.list_bgm_mixes("job-store-1")
    assert len(results) == 1
    assert results[0].bgm_asset_name == "Corporate Upbeat"
    assert results[0].is_approved is True
    assert results[0].ducking_parameters["duck_attenuation_db"] == 16.0

    # 3. Get single
    clip_mix = store.get_bgm_mix("clip-store-1")
    assert clip_mix is not None
    assert clip_mix.id == "mix-rec-1"
    assert clip_mix.integrated_lufs == -14.2
