"""Focused tests for Step 22: Final Render, Packaging & Output Quality Gate."""

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest
from fastapi import HTTPException

from autoclip.db import schema, store
from autoclip.db.models import Clip, FinalRenderRecord, Job, Source, new_id, utcnow
from autoclip.pipeline.final_render import (
    FinalRenderConfig,
    FinalRenderEngine,
    FinalRenderGateResult,
    FinalRenderMetadata,
    FinalRenderQualityGate,
    OutputPackager,
)


@pytest.fixture
def synth_media_fixture(tmp_path: Path):
    """Generates synthetic 1080x1920 video and non-conforming videos for quality gate testing."""
    valid_mp4 = tmp_path / "valid_vertical.mp4"
    invalid_res_mp4 = tmp_path / "invalid_landscape.mp4"
    audio_only_mp4 = tmp_path / "audio_only.mp4"
    video_only_mp4 = tmp_path / "video_only.mp4"

    # 1. Conforming 1080x1920 H.264 + AAC 3-second video
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=size=1080x1920:rate=30:duration=3",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            str(valid_mp4),
        ],
        capture_output=True,
        check=True,
    )

    # 2. Non-conforming 1920x1080 landscape video
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=30:duration=3",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            str(invalid_res_mp4),
        ],
        capture_output=True,
        check=True,
    )

    # 3. Audio only (no video stream)
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
            "-c:a", "aac",
            str(audio_only_mp4),
        ],
        capture_output=True,
        check=True,
    )

    # 4. Video only (no audio stream)
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=size=1080x1920:rate=30:duration=3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(video_only_mp4),
        ],
        capture_output=True,
        check=True,
    )

    return valid_mp4, invalid_res_mp4, audio_only_mp4, video_only_mp4


def test_final_render_config_defaults():
    """Validates default final render configuration meets 9:16 vertical broadcast requirements."""
    cfg = FinalRenderConfig()
    assert cfg.width == 1080
    assert cfg.height == 1920
    assert cfg.ratio == "9:16"
    assert cfg.video_codec == "libx264"
    assert cfg.audio_codec == "aac"
    assert cfg.audio_sample_rate == 48000
    assert cfg.pixel_format == "yuv420p"

    d = cfg.to_dict()
    assert d["width"] == 1080
    assert d["height"] == 1920
    assert d["ratio"] == "9:16"


def test_quality_gate_valid_output(synth_media_fixture):
    """Conforming 1080x1920 H.264/AAC output passes quality gate with high score."""
    valid_mp4, _, _, _ = synth_media_fixture
    gate = FinalRenderQualityGate()

    result = gate.evaluate(valid_mp4, expected_duration_s=3.0, expected_caption_style="classic_professional")
    assert result.status in ("RENDER_PASS", "RENDER_WARN")
    assert result.is_approved is True
    assert result.quality_score >= 90.0
    assert result.video_metrics["width"] == 1080
    assert result.video_metrics["height"] == 1920
    assert result.video_metrics["decode_ok"] is True
    assert result.audio_metrics["audio_codec"] == "aac"


def test_quality_gate_invalid_resolution(synth_media_fixture):
    """Quality gate rejects videos that do not match the exact 1080x1920 specification."""
    _, invalid_res_mp4, _, _ = synth_media_fixture
    gate = FinalRenderQualityGate()

    result = gate.evaluate(invalid_res_mp4, expected_duration_s=3.0)
    assert result.status == "RENDER_REJECT"
    assert result.is_approved is False
    assert result.quality_score == 0.0
    assert any("Resolution mismatch" in r for r in result.rejection_reasons)


def test_quality_gate_missing_video(synth_media_fixture):
    """Quality gate rejects output with missing video stream."""
    _, _, audio_only_mp4, _ = synth_media_fixture
    gate = FinalRenderQualityGate()

    result = gate.evaluate(audio_only_mp4, expected_duration_s=3.0)
    assert result.status == "RENDER_REJECT"
    assert any("No video stream found" in r for r in result.rejection_reasons)


def test_quality_gate_missing_audio(synth_media_fixture):
    """Quality gate rejects output when audio is required but missing."""
    _, _, _, video_only_mp4 = synth_media_fixture
    gate = FinalRenderQualityGate()

    result = gate.evaluate(video_only_mp4, expected_duration_s=3.0, require_audio=True)
    assert result.status == "RENDER_REJECT"
    assert any("Audio stream missing" in r for r in result.rejection_reasons)


def test_quality_gate_duration_mismatch(synth_media_fixture):
    """Quality gate flags significant duration discrepancies against expected clip duration."""
    valid_mp4, _, _, _ = synth_media_fixture
    gate = FinalRenderQualityGate()

    # Valid MP4 is 3.0s, but we assert expected is 15.0s
    result = gate.evaluate(valid_mp4, expected_duration_s=15.0)
    assert result.status == "RENDER_REJECT"
    assert any("deviates significantly" in r for r in result.rejection_reasons)


def test_safe_filenaming_and_packaging(tmp_path: Path):
    """OutputPackager sanitizes unsafe names, prevents path traversal, and packages atomically."""
    unsafe_job_id = "../../malicious_job/../../etc"
    safe_job = OutputPackager.slugify_name(unsafe_job_id)
    assert ".." not in safe_job
    assert "/" not in safe_job
    assert "\\" not in safe_job

    exports_dir = tmp_path / "exports"
    pkg_dir = OutputPackager.get_package_dir(
        exports_dir,
        job_id="job_clean_123",
        rank=1,
        clip_id="clip_abc_456",
        title="Epic Moments: Part 1? (Uncut!)",
    )
    assert "clip_001_epic_moments_part_1_uncut" in pkg_dir.name

    # Create dummy temp video
    temp_mp4 = tmp_path / "temp_render.mp4"
    temp_mp4.write_bytes(b"dummy video data for test")

    metadata = FinalRenderMetadata(
        job_id="job_clean_123",
        clip_id="clip_abc_456",
        source_id="src_1",
        final_rank=1,
        title="Epic Moments",
        duration_s=5.0,
        width=1080,
        height=1920,
        fps=30.0,
        video_codec="h264",
        audio_codec="aac",
        caption_style="classic_professional",
        bgm_asset_id="bgm_1",
        bgm_asset_name="Track 1",
        quality_score=98.0,
        quality_status="RENDER_PASS",
        render_timestamp=utcnow(),
    )

    quality_res = FinalRenderGateResult(
        status="RENDER_PASS",
        quality_score=98.0,
        video_metrics={"width": 1080, "height": 1920},
    )

    final_mp4 = OutputPackager.package_clip(pkg_dir, temp_mp4, metadata, quality_res)

    assert final_mp4.is_file()
    assert final_mp4.name == "final.mp4"
    assert (pkg_dir / "metadata.json").is_file()
    assert (pkg_dir / "quality.json").is_file()

    meta_content = json.loads((pkg_dir / "metadata.json").read_text(encoding="utf-8"))
    assert meta_content["clip_id"] == "clip_abc_456"
    assert meta_content["resolution"] == "1080x1920"


def test_idempotent_retry(synth_media_fixture, tmp_path: Path):
    """FinalRenderEngine reuses existing valid final output on retry without re-rendering."""
    valid_mp4, _, _, _ = synth_media_fixture
    exports_dir = tmp_path / "exports"
    work_dir = tmp_path / "work"

    clip = Clip(
        id="clip-idempotent-1",
        job_id="job-retry-1",
        rank=1,
        start_s=0.0,
        end_s=3.0,
        title="Retry Clip",
        status="candidate",
    )

    # Pre-populate package with valid output
    pkg_dir = OutputPackager.get_package_dir(exports_dir, job_id=clip.job_id, rank=clip.rank, clip_id=clip.id, title=clip.title)
    pkg_dir.mkdir(parents=True, exist_ok=True)
    existing_final = pkg_dir / "final.mp4"
    import shutil
    shutil.copy2(valid_mp4, existing_final)

    engine = FinalRenderEngine()
    final_path, record = engine.render_and_package(
        clip=clip,
        source_media_path=valid_mp4,
        crop_path=None,  # Not needed on idempotent reuse!
        caption_style_key="classic_professional",
        ass_path=None,
        audio_path=None,
        bgm_asset_id=None,
        bgm_asset_name="",
        exports_base_dir=exports_dir,
        render_work_dir=work_dir,
    )

    assert final_path == existing_final
    assert record.is_approved is True
    assert record.telemetry.get("idempotent_reuse") is True


@pytest.mark.asyncio
async def test_publishing_blocked_for_rejected_renders(initialised_db):
    """Publishing is rejected with HTTP 400 if final render failed quality gate."""
    from autoclip.api.publishing import publish_export_endpoint
    from autoclip.api.schemas import PublishRequestIn
    from autoclip.db.models import Export

    # Create dummy source, job, clip, export, and rejected final render
    store.create_source(Source(id="s1", type="upload", path="p"))
    store.create_job(Job(id="job-pub-1", source_id="s1", status="running"))
    store.create_clip(Clip(id="clip-pub-1", job_id="job-pub-1", rank=1, start_s=0.0, end_s=5.0, status="candidate", score=85))

    export_rec = store.create_export(
        Export(
            id="exp-pub-1",
            clip_id="clip-pub-1",
            path="/tmp/final.mp4",
            ratio="9:16",
            style="classic_professional",
            size_bytes=5000,
        )
    )

    # Store rejected final render record
    store.replace_final_renders(
        "job-pub-1",
        [
            FinalRenderRecord(
                id="render-pub-1",
                job_id="job-pub-1",
                clip_id="clip-pub-1",
                output_path="/tmp/final.mp4",
                package_dir="/tmp",
                quality_score=0.0,
                quality_status="RENDER_REJECT",
                render_status="failed",
                error_details=["Resolution mismatch: expected 1080x1920, got 720x1280"],
            )
        ],
    )

    # Publishing must be blocked with HTTP 400
    with pytest.raises(HTTPException) as exc_info:
        await publish_export_endpoint(
            export_id="exp-pub-1",
            request=PublishRequestIn(platforms=["telegram"]),
        )

    assert exc_info.value.status_code == 400
    assert "Resolution mismatch" in str(exc_info.value.detail)


def test_db_store_final_renders(tmp_path: Path, monkeypatch):
    """Verifies SQLite schema V17 migration and CRUD operations for FinalRenderRecord."""
    db_file = tmp_path / "test_store_v17.db"
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    v = schema.migrate(conn)
    assert v >= 17

    monkeypatch.setattr(store, "connection", lambda: conn)

    store.create_source(Source(id="s1", type="upload", path="p"))
    store.create_job(Job(id="job-v17-1", source_id="s1", status="running"))
    store.create_clip(Clip(id="clip-v17-1", job_id="job-v17-1", rank=1, start_s=0.0, end_s=5.0, status="candidate", score=85))

    record = FinalRenderRecord(
        id="fr-1",
        job_id="job-v17-1",
        clip_id="clip-v17-1",
        output_path="/exports/job-v17-1/clip_001/final.mp4",
        package_dir="/exports/job-v17-1/clip_001",
        duration=5.0,
        width=1080,
        height=1920,
        fps=30.0,
        video_codec="h264",
        audio_codec="aac",
        caption_style="rich_dynamic",
        bgm_asset_id="bgm-v17-1",
        quality_score=95.0,
        quality_status="RENDER_PASS",
        render_status="completed",
        render_attempt=1,
        error_details=[],
        telemetry={"bitrate": "2.5M"},
    )

    # 1. Replace
    stored = store.replace_final_renders("job-v17-1", [record])
    assert len(stored) == 1

    # 2. List
    results = store.list_final_renders("job-v17-1")
    assert len(results) == 1
    assert results[0].is_approved is True
    assert results[0].width == 1080
    assert results[0].height == 1920

    # 3. Get single
    clip_render = store.get_final_render("clip-v17-1")
    assert clip_render is not None
    assert clip_render.id == "fr-1"
    assert clip_render.caption_style == "rich_dynamic"
