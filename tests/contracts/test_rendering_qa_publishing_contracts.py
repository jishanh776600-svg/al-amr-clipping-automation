"""Unit tests for Rendering, QA, and Publishing contracts."""

import pytest
from pydantic import ValidationError
from clipping.contracts.rendering import SubtitleEvent, RenderJob, RenderOutput
from clipping.contracts.qa import (
    QAPassStatus,
    StructuralQAResult,
    VisualQAResult,
    AudioQAResult,
    QAResult,
    DuplicateFingerprint,
)
from clipping.contracts.publishing import (
    ApprovalStatus,
    ApprovalRequest,
    PublishingStatus,
    PublishingJob,
    PublishingResult,
)
from clipping.contracts.director import ReframePlan, ReframeCropKeyframe, SpeakerLayout


def test_subtitle_event_and_render_job():
    sub = SubtitleEvent(start_time=0.0, end_time=1.5, text="Welcome to the show!", pos_x=540, pos_y=1450)
    assert sub.style_preset == "kinetic_gold"

    plan = ReframePlan(
        clip_id="CLIP_01",
        source_width=1920,
        source_height=1080,
        keyframes=[ReframeCropKeyframe(timestamp=0.0, crop_x=0, crop_y=0, crop_w=1080, crop_h=1080, layout_mode=SpeakerLayout.SOLO)],
    )

    job = RenderJob(
        job_id="RENDER_JOB_01",
        clip_id="CLIP_01",
        source_video_storage_key="sources/VID_01/master.mp4",
        reframe_plan=plan,
        output_storage_key="clips/CLIP_01/final_1080x1920.mp4",
    )
    assert job.target_width == 1080
    assert job.target_height == 1920


def test_qa_result_aggregation():
    struct = StructuralQAResult(width=1080, height=1920, fps=30.0, has_audio_stream=True, duration_seconds=42.0)
    assert struct.is_valid is True

    visual = VisualQAResult(black_segments_count=0, freeze_segments_count=0, safezone_violations_count=0)
    assert visual.is_valid is True

    audio = AudioQAResult(integrated_loudness_lufs=-14.2, true_peak_dbfs=-1.2, loudness_range_lra=6.5)
    assert audio.is_valid is True

    qa = QAResult(
        clip_id="CLIP_01",
        overall_status=QAPassStatus.PASSED,
        structural=struct,
        visual=visual,
        audio=audio,
        compliance_passed=True,
        duplicate_hash="a1b2c3d4e5f60718",
    )
    assert qa.overall_status == QAPassStatus.PASSED


def test_approval_and_publishing():
    req = ApprovalRequest(
        clip_id="CLIP_01",
        campaign_id="CAMP_01",
        video_storage_key="clips/CLIP_01/final_1080x1920.mp4",
        thumbnail_storage_key="clips/CLIP_01/thumbnail.jpg",
        title_suggestion="How AI Automation Works in 2026",
        virality_score=94.5,
    )
    assert req.approval_status == ApprovalStatus.PENDING

    pub_job = PublishingJob(
        job_id="PUB_01",
        clip_id="CLIP_01",
        channel_id="UC_TEST_CHANNEL",
        video_storage_key="clips/CLIP_01/final_1080x1920.mp4",
        title="How AI Automation Works in 2026 #Shorts",
    )
    assert pub_job.publishing_status == PublishingStatus.QUEUED
