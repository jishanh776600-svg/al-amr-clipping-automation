"""Unit tests for QA Engine orchestration & gating policy."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from clipping.contracts.qa import (
    MediaValidationResult,
    QACheckStatus,
    QAReport,
)
from clipping.qa.engine import QAEngine
from clipping.storage.local import LocalStorageDriver


@pytest.mark.asyncio
async def test_qa_engine_lifecycle_pass(temp_vault_dir):
    storage = LocalStorageDriver(root_dir=temp_vault_dir)

    # 1. Seed rendered artifacts in storage
    clip_id = "CLIP_QA_PASS"
    source_id = "VID_QA_01"

    await storage.upload_bytes(b"MOCK_VIDEO_DATA", f"clips/{clip_id}/final_1080x1920.mp4", content_type="video/mp4")
    ass_data = "[Script Info]\n[V4+ Styles]\n[Events]\nDialogue: 0,0:00:00.00,0:00:05.00,Default,,0,0,0,,Valid\n"
    await storage.upload_bytes(ass_data.encode("utf-8"), f"clips/{clip_id}/subtitles.ass", content_type="text/x-ssa")

    # 2. Mock Media Prober returning 1080x1920 valid media
    mock_prober = MagicMock()
    mock_prober.probe_media = AsyncMock(
        return_value=MediaValidationResult(
            is_valid=True,
            width=1080,
            height=1920,
            duration_seconds=5.0,
            fps=30.0,
            video_codec="h264",
            audio_codec="aac",
            file_size_bytes=100_000,
        )
    )

    engine = QAEngine(media_prober=mock_prober)
    report = await engine.evaluate_rendered_clip(
        clip_id=clip_id,
        source_video_id=source_id,
        storage_driver=storage,
        expected_duration=5.0,
    )

    assert isinstance(report, QAReport)
    assert report.clip_id == clip_id
    assert report.overall_status == QACheckStatus.PASS
    assert report.can_publish is True
    assert await storage.exists(f"clips/{clip_id}/qa_report.json") is True


@pytest.mark.asyncio
async def test_qa_engine_missing_video_fail(temp_vault_dir):
    storage = LocalStorageDriver(root_dir=temp_vault_dir)
    engine = QAEngine()

    report = await engine.evaluate_rendered_clip(
        clip_id="CLIP_MISSING",
        source_video_id="VID_MISSING",
        storage_driver=storage,
        expected_duration=30.0,
    )

    assert report.overall_status == QACheckStatus.FAIL
    assert report.can_publish is False
    critical_fail = next(c for c in report.checks if c.check_id == "media_file_exists")
    assert critical_fail.status == QACheckStatus.FAIL
