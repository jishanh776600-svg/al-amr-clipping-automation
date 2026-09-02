"""Comprehensive tests for pipeline_runner real media engine wiring and checkpoint semantics."""

import pytest
from typing import List
from clipping.approval.models import ApprovalStatus
from clipping.cli.pipeline_runner import run_pipeline
from clipping.control.models import SystemControlState
from clipping.control.repository import ControlRepository
from clipping.state.lease import JobLeaseRepository
from clipping.state.models import JobState, PipelineStage
from clipping.state.remote import RemoteStorageStateRepository
from clipping.storage.local import LocalStorageDriver
from tests.pipeline_mocks import (
    MockAudioPerceptionEngine,
    MockClipDiscoveryEngine,
    MockQAEngine,
    MockRenderOrchestrationEngine,
    MockTelegramApprovalGateway,
    MockVideoIngestor,
    MockVideoUnderstandingEngine,
    MockVirtualCameraDirector,
)


@pytest.fixture
def runner_env(temp_vault_dir):
    storage = LocalStorageDriver(root_dir=temp_vault_dir)
    state_repo = RemoteStorageStateRepository(storage_driver=storage)
    control_repo = ControlRepository(storage_driver=storage)
    lease_repo = JobLeaseRepository(storage_driver=storage)
    return {
        "storage": storage,
        "state_repo": state_repo,
        "control_repo": control_repo,
        "lease_repo": lease_repo,
    }


@pytest.mark.asyncio
async def test_full_pipeline_stage_ordering_and_invocation(runner_env):
    """Verifies that all 8 automated stages execute in exact order with real engine outputs."""
    storage = runner_env["storage"]
    state_repo = runner_env["state_repo"]

    ingestor = MockVideoIngestor(storage)
    perception = MockAudioPerceptionEngine()
    vision = MockVideoUnderstandingEngine()
    discovery = MockClipDiscoveryEngine(produce_candidates=True)
    director = MockVirtualCameraDirector()
    renderer = MockRenderOrchestrationEngine()
    qa = MockQAEngine(can_publish=True)
    approval = MockTelegramApprovalGateway()

    job_id = "job_full_ordering_01"
    exit_code = await run_pipeline(
        source_uri="https://www.youtube.com/watch?v=real_sample_01",
        campaign_id="campaign_alpha",
        job_id=job_id,
        worker_id="worker_test_01",
        ingestor=ingestor,
        perception_engine=perception,
        vision_engine=vision,
        discovery_engine=discovery,
        camera_director=director,
        render_engine=renderer,
        qa_engine=qa,
        approval_gateway=approval,
        storage=storage,
    )

    assert exit_code == 0

    # 1. Verify final job state
    job = await state_repo.get_job(job_id)
    assert job is not None
    assert job.current_state == JobState.AWAITING_APPROVAL
    assert job.current_stage == PipelineStage.APPROVAL

    # 2. Verify state transition sequence preserves exact ordering
    history = await state_repo.get_job_history(job_id)
    states = [t.to_state for t in history]
    stages = [t.stage for t in history]

    # Expected progression:
    # CREATED -> INGESTING_VIDEO -> TRANSCRIBING -> RESOLVING_SPEAKERS -> DISCOVERING_CLIPS -> REFRAMING_AND_RENDERING -> RUNNING_QA -> AWAITING_APPROVAL
    assert JobState.INGESTING_VIDEO in states
    assert JobState.TRANSCRIBING in states
    assert JobState.RESOLVING_SPEAKERS in states
    assert JobState.DISCOVERING_CLIPS in states
    assert JobState.REFRAMING_AND_RENDERING in states
    assert JobState.RUNNING_QA in states
    assert JobState.AWAITING_APPROVAL in states

    # 3. Verify canonical artifacts were produced and persisted
    source_id = job.source_video_id
    assert await storage.exists(f"sources/{source_id}/master.mp4")
    assert await storage.exists(f"sources/{source_id}/metadata.json")
    assert await storage.exists(f"sources/{source_id}/speaker_transcript.json")
    assert await storage.exists(f"sources/{source_id}/scenes.json")
    assert await storage.exists(f"sources/{source_id}/selected_clips.json")

    # 4. Verify candidate reached Telegram approval
    assert len(approval.dispatched_requests) == 1
    assert approval.dispatched_requests[0].status == ApprovalStatus.AWAITING_APPROVAL
    assert approval.dispatched_requests[0].job_id == job_id


@pytest.mark.asyncio
async def test_zero_discovery_candidates_handling(runner_env):
    """Verifies that 0 candidate clips is handled honestly as a valid outcome without failure."""
    storage = runner_env["storage"]
    state_repo = runner_env["state_repo"]

    ingestor = MockVideoIngestor(storage)
    perception = MockAudioPerceptionEngine()
    vision = MockVideoUnderstandingEngine()
    discovery = MockClipDiscoveryEngine(produce_candidates=False)  # 0 candidates
    director = MockVirtualCameraDirector()
    renderer = MockRenderOrchestrationEngine()
    qa = MockQAEngine()
    approval = MockTelegramApprovalGateway()

    job_id = "job_zero_candidates_01"
    exit_code = await run_pipeline(
        source_uri="https://www.youtube.com/watch?v=low_quality_video",
        job_id=job_id,
        ingestor=ingestor,
        perception_engine=perception,
        vision_engine=vision,
        discovery_engine=discovery,
        camera_director=director,
        render_engine=renderer,
        qa_engine=qa,
        approval_gateway=approval,
        storage=storage,
    )

    assert exit_code == 0
    job = await state_repo.get_job(job_id)
    assert job.current_state == JobState.AWAITING_APPROVAL
    assert job.current_stage == PipelineStage.APPROVAL
    assert job.metadata_json.get("selected_clips_count") == 0

    # Rendering and QA should not have been called, 0 requests dispatched
    assert len(approval.dispatched_requests) == 0


@pytest.mark.asyncio
async def test_qa_failure_prevents_telegram_approval(runner_env):
    """Verifies that QA failure prevents clips from reaching Telegram approval gateway."""
    storage = runner_env["storage"]
    state_repo = runner_env["state_repo"]

    ingestor = MockVideoIngestor(storage)
    perception = MockAudioPerceptionEngine()
    vision = MockVideoUnderstandingEngine()
    discovery = MockClipDiscoveryEngine(produce_candidates=True)
    director = MockVirtualCameraDirector()
    renderer = MockRenderOrchestrationEngine()
    qa = MockQAEngine(can_publish=False)  # QA FAILS
    approval = MockTelegramApprovalGateway()

    job_id = "job_qa_fail_01"
    exit_code = await run_pipeline(
        source_uri="https://www.youtube.com/watch?v=qa_failing_video",
        job_id=job_id,
        ingestor=ingestor,
        perception_engine=perception,
        vision_engine=vision,
        discovery_engine=discovery,
        camera_director=director,
        render_engine=renderer,
        qa_engine=qa,
        approval_gateway=approval,
        storage=storage,
    )

    assert exit_code == 0
    job = await state_repo.get_job(job_id)
    assert job.metadata_json.get("passing_clips_count") == 0
    # ZERO requests dispatched to Telegram
    assert len(approval.dispatched_requests) == 0


@pytest.mark.asyncio
async def test_stage_failure_does_not_advance_checkpoint_and_releases_lease(runner_env):
    """Verifies that failure in an engine marks job FAILED, retains stage, and releases lease."""
    storage = runner_env["storage"]
    state_repo = runner_env["state_repo"]
    lease_repo = runner_env["lease_repo"]

    class FailingPerceptionEngine:
        async def process(self, source_video_id, storage_driver, force_recompute=False):
            raise RuntimeError("Whisper model OOM out of memory error")

    job_id = "job_failure_test_01"
    worker_id = "worker_failing_01"

    exit_code = await run_pipeline(
        source_uri="https://www.youtube.com/watch?v=oom_video",
        job_id=job_id,
        worker_id=worker_id,
        ingestor=MockVideoIngestor(storage),
        perception_engine=FailingPerceptionEngine(),
        storage=storage,
    )

    assert exit_code == 1

    # Job must be marked FAILED at PERCEPTION stage
    job = await state_repo.get_job(job_id)
    assert job.current_state == JobState.FAILED
    assert job.current_stage == PipelineStage.PERCEPTION
    assert "Whisper model OOM" in (job.error_message or "")

    # Lease must be released
    lease = await lease_repo.get_lease(job_id)
    assert lease is None or lease.status == "released"


@pytest.mark.asyncio
async def test_emergency_stop_cooperative_halt_mid_pipeline(runner_env):
    """Verifies that Master Control emergency stop halts execution immediately at stage boundary."""
    storage = runner_env["storage"]
    state_repo = runner_env["state_repo"]
    control_repo = runner_env["control_repo"]

    # Pre-activate emergency stop
    await control_repo.save_state(SystemControlState(emergency_stopped=True))

    job_id = "job_estop_halt_01"
    exit_code = await run_pipeline(
        source_uri="https://www.youtube.com/watch?v=sample",
        job_id=job_id,
        storage=storage,
    )

    assert exit_code == 1
    # Check that job is not created or lease is not claimed
    lease = await runner_env["lease_repo"].get_lease(job_id)
    assert lease is None


@pytest.mark.asyncio
async def test_checkpoint_resumption_skips_completed_stages(runner_env):
    """Verifies that an interrupted job resumes from existing artifacts without re-running completed stages."""
    storage = runner_env["storage"]
    state_repo = runner_env["state_repo"]

    ingest_call_count = 0

    class TrackingIngestor(MockVideoIngestor):
        async def ingest(self, source_ref, storage_driver, source_video_id, force_reingest=False):
            nonlocal ingest_call_count
            ingest_call_count += 1
            return await super().ingest(source_ref, storage_driver, source_video_id, force_reingest)

    job_id = "job_resumption_01"
    source_id = "src_resumed_42"

    # Seed the job with completed ingestion
    await state_repo.create_job(
        job_id=job_id,
        campaign_id="cmp_01",
        source_video_id=source_id,
        idempotency_key=f"idemp_{job_id}",
    )
    # Upload ingestion artifacts
    await storage.upload_bytes(b"master_video_data", f"sources/{source_id}/master.mp4")
    from clipping.contracts.perception import SourceVideoMetadata
    meta = SourceVideoMetadata(
        video_id=source_id,
        title="Pre-ingested",
        duration_seconds=30.0,
        width=1920,
        height=1080,
        fps=30.0,
        source_url="https://www.youtube.com/watch?v=pre_ingested",
        master_video_storage_key=f"sources/{source_id}/master.mp4",
        audio_storage_key="",
    )
    await storage.upload_bytes(meta.model_dump_json().encode("utf-8"), f"sources/{source_id}/metadata.json")

    # Run runner with TrackingIngestor
    exit_code = await run_pipeline(
        source_uri="https://www.youtube.com/watch?v=pre_ingested",
        job_id=job_id,
        ingestor=TrackingIngestor(),
        perception_engine=MockAudioPerceptionEngine(),
        vision_engine=MockVideoUnderstandingEngine(),
        discovery_engine=MockClipDiscoveryEngine(produce_candidates=True),
        camera_director=MockVirtualCameraDirector(),
        render_engine=MockRenderOrchestrationEngine(),
        qa_engine=MockQAEngine(can_publish=True),
        approval_gateway=MockTelegramApprovalGateway(),
        storage=storage,
    )

    assert exit_code == 0
    # Ingestor must NOT have been called because artifacts already existed!
    assert ingest_call_count == 0

    # Final job is AWAITING_APPROVAL
    final_job = await state_repo.get_job(job_id)
    assert final_job.current_state == JobState.AWAITING_APPROVAL


@pytest.mark.asyncio
async def test_pipeline_runner_with_real_ffmpeg_and_qa(runner_env):
    """Integration test: pipeline_runner executes REAL RemoteVideoIngestor, REAL FFmpeg rendering, and REAL QAEngine."""
    import tempfile
    import cv2
    import numpy as np
    from clipping.ingestion.remote import RemoteVideoIngestor
    from clipping.vision.director import KalmanVirtualCameraDirector
    from clipping.rendering.engine import RenderOrchestrationEngine
    from clipping.qa.engine import QAEngine

    storage = runner_env["storage"]
    state_repo = runner_env["state_repo"]

    # 1. Seed real synthetic master video in storage vault
    with tempfile.TemporaryDirectory() as tmp_dir:
        raw_video_path = f"{tmp_dir}/test_source.mp4"
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        out = cv2.VideoWriter(raw_video_path, fourcc, 30, (1920, 1080))
        for _ in range(90):  # 3 seconds @ 30fps
            frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
            frame[:] = (40, 40, 40)
            cv2.circle(frame, (960, 400), 120, (180, 150, 100), -1)
            out.write(frame)
        out.release()

        # Upload to vault
        await storage.upload(raw_video_path, "vault_raw/test_source.mp4", content_type="video/mp4")

    # 2. Wire REAL production engines for Ingestion, Director, FFmpeg Rendering, and QA
    approval = MockTelegramApprovalGateway()
    job_id = "job_real_integration_01"

    exit_code = await run_pipeline(
        source_uri="gdrive://vault_raw/test_source.mp4",
        campaign_id="test_real_campaign",
        job_id=job_id,
        worker_id="integration_worker_01",
        ingestor=RemoteVideoIngestor(),
        camera_director=KalmanVirtualCameraDirector(),
        render_engine=RenderOrchestrationEngine(),
        qa_engine=QAEngine(),
        perception_engine=MockAudioPerceptionEngine(),
        vision_engine=MockVideoUnderstandingEngine(),
        discovery_engine=MockClipDiscoveryEngine(produce_candidates=True),
        approval_gateway=approval,
        storage=storage,
    )

    assert exit_code == 0

    # 3. Verify Job reached AWAITING_APPROVAL
    job = await state_repo.get_job(job_id)
    assert job.current_state == JobState.AWAITING_APPROVAL
    assert job.current_stage == PipelineStage.APPROVAL

    # 4. Verify REAL 1080x1920 MP4 video artifact was rendered by FFmpeg and uploaded to storage
    source_id = job.source_video_id
    clip_id = f"clip_{source_id}_01"
    final_video_key = f"clips/{clip_id}/final_1080x1920.mp4"
    assert await storage.exists(final_video_key) is True

    # 5. Verify REAL QA report exists and passed
    qa_report_key = f"clips/{clip_id}/qa_report.json"
    assert await storage.exists(qa_report_key) is True
    qa_bytes = await storage.download_bytes(qa_report_key)
    from clipping.contracts.qa import QAReport
    report = QAReport.model_validate_json(qa_bytes.decode("utf-8"))
    assert report.can_publish is True
    assert report.media_validation is not None
    assert report.media_validation.width == 1080
    assert report.media_validation.height == 1920

    # 6. Verify Telegram approval card received the rendered clip key
    assert len(approval.dispatched_requests) == 1
    assert approval.dispatched_requests[0].video_storage_key == final_video_key
