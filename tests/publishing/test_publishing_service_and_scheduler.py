"""Unit tests for PublishingService, Idempotency, Batch Processing, and Scheduler."""

from datetime import datetime, timedelta, timezone
import pytest
from clipping.approval.models import ApprovalRequest, ApprovalStatus
from clipping.approval.repository import ApprovalRepository
from clipping.contracts.qa import QAReport, QACheckStatus
from clipping.publishing.client import MockYouTubeClient
from clipping.publishing.gates import PublishingGateEnforcer
from clipping.publishing.metadata import YouTubeMetadataBuilder
from clipping.publishing.models import PublishStatus
from clipping.publishing.repository import PublishingRepository
from clipping.publishing.service import PublishingService
from clipping.publishing.scheduler import PublishingScheduler
from clipping.storage.local import LocalStorageDriver


@pytest.fixture
def publishing_env(temp_vault_dir):
    storage = LocalStorageDriver(root_dir=temp_vault_dir)
    app_repo = ApprovalRepository(storage_driver=storage)
    pub_repo = PublishingRepository(storage_driver=storage)
    gates = PublishingGateEnforcer(approval_repository=app_repo, storage_driver=storage)
    client = MockYouTubeClient(expected_channel_id="UC_CORRECT_CHANNEL_01")
    service = PublishingService(
        repository=pub_repo,
        client=client,
        gate_enforcer=gates,
        storage_driver=storage,
    )
    scheduler = PublishingScheduler(repository=pub_repo, publishing_service=service)

    return {
        "storage": storage,
        "app_repo": app_repo,
        "pub_repo": pub_repo,
        "gates": gates,
        "client": client,
        "service": service,
        "scheduler": scheduler,
    }


@pytest.mark.asyncio
async def test_publish_clip_lifecycle_and_idempotency(publishing_env):
    storage = publishing_env["storage"]
    app_repo = publishing_env["app_repo"]
    pub_repo = publishing_env["pub_repo"]
    client = publishing_env["client"]
    service = publishing_env["service"]

    job_id = "job_pub_01"
    clip_id = "clip_pub_01"
    app_id = "req_pub_01"
    video_key = f"clips/{clip_id}/final.mp4"

    # 1. Seed Google Drive state: Approved request, QA Report, Video bytes
    req = ApprovalRequest(
        approval_request_id=app_id,
        job_id=job_id,
        source_video_id="src_01",
        clip_id=clip_id,
        clip_index=1,
        title="Automated Video Shorts",
        start_time=0.0,
        end_time=30.0,
        duration=30.0,
        score=95.0,
        video_storage_key=video_key,
        status=ApprovalStatus.APPROVED,
    )
    await app_repo.save_request(req)

    qa_report = QAReport(
        clip_id=clip_id,
        source_video_id="src_01",
        overall_status=QACheckStatus.PASS,
        can_publish=True,
    )
    await storage.upload_bytes(qa_report.model_dump_json().encode("utf-8"), f"clips/{clip_id}/qa_report.json")
    await storage.upload_bytes(b"MOCK_MP4_BYTES_DATA", video_key)

    metadata = YouTubeMetadataBuilder.build(title="Automated Video Shorts", description="AI clipping workflow")

    # 2. First Publish: Must succeed
    result = await service.publish_clip(
        job_id=job_id,
        clip_id=clip_id,
        approval_request_id=app_id,
        video_storage_key=video_key,
        metadata=metadata,
        expected_channel_id="UC_CORRECT_CHANNEL_01",
    )

    assert result.status == PublishStatus.PUBLISHED
    assert result.youtube_video_id is not None
    assert result.youtube_url == f"https://youtube.com/shorts/{result.youtube_video_id}"
    assert len(client.uploaded_videos) == 1

    # Verify audit record
    audits = await pub_repo.list_audits_for_job(job_id)
    assert len(audits) >= 2  # Ready->Publishing, Publishing->Published

    # 3. Second Publish (Idempotency): Must NOT call client.upload_video again
    re_result = await service.publish_clip(
        job_id=job_id,
        clip_id=clip_id,
        approval_request_id=app_id,
        video_storage_key=video_key,
        metadata=metadata,
        expected_channel_id="UC_CORRECT_CHANNEL_01",
    )

    assert re_result.status == PublishStatus.PUBLISHED
    assert re_result.youtube_video_id == result.youtube_video_id
    assert len(client.uploaded_videos) == 1  # Still 1 upload!


@pytest.mark.asyncio
async def test_publish_channel_identity_mismatch(publishing_env):
    storage = publishing_env["storage"]
    app_repo = publishing_env["app_repo"]
    service = publishing_env["service"]

    job_id = "job_pub_mismatch"
    clip_id = "clip_pub_mismatch"
    app_id = "req_pub_mismatch"
    video_key = f"clips/{clip_id}/final.mp4"

    # Seed state
    req = ApprovalRequest(
        approval_request_id=app_id,
        job_id=job_id,
        source_video_id="src_01",
        clip_id=clip_id,
        clip_index=1,
        title="Title",
        start_time=0.0,
        end_time=30.0,
        duration=30.0,
        score=90.0,
        video_storage_key=video_key,
        status=ApprovalStatus.APPROVED,
    )
    await app_repo.save_request(req)
    await storage.upload_bytes(b"{}", f"clips/{clip_id}/qa_report.json")
    qa_report = QAReport(clip_id=clip_id, source_video_id="src_01", overall_status=QACheckStatus.PASS, can_publish=True)
    await storage.upload_bytes(qa_report.model_dump_json().encode("utf-8"), f"clips/{clip_id}/qa_report.json")
    await storage.upload_bytes(b"DATA", video_key)

    metadata = YouTubeMetadataBuilder.build(title="Test")

    # Call with mismatched expected channel
    result = await service.publish_clip(
        job_id=job_id,
        clip_id=clip_id,
        approval_request_id=app_id,
        video_storage_key=video_key,
        metadata=metadata,
        expected_channel_id="UC_WRONG_CHANNEL_ID",
    )

    assert result.status == PublishStatus.FAILED
    assert "channel" in result.failure_reason.lower()


@pytest.mark.asyncio
async def test_scheduler_future_and_due_catchup(publishing_env):
    storage = publishing_env["storage"]
    app_repo = publishing_env["app_repo"]
    pub_repo = publishing_env["pub_repo"]
    service = publishing_env["service"]
    scheduler = publishing_env["scheduler"]

    job_id = "job_sched_01"
    clip_id = "clip_sched_01"
    app_id = "req_sched_01"
    video_key = f"clips/{clip_id}/final.mp4"

    # Seed state
    req = ApprovalRequest(
        approval_request_id=app_id,
        job_id=job_id,
        source_video_id="src_01",
        clip_id=clip_id,
        clip_index=1,
        title="Scheduled Short",
        start_time=0.0,
        end_time=30.0,
        duration=30.0,
        score=92.0,
        video_storage_key=video_key,
        status=ApprovalStatus.APPROVED,
    )
    await app_repo.save_request(req)
    qa = QAReport(clip_id=clip_id, source_video_id="src_01", overall_status=QACheckStatus.PASS, can_publish=True)
    await storage.upload_bytes(qa.model_dump_json().encode("utf-8"), f"clips/{clip_id}/qa_report.json")
    await storage.upload_bytes(b"DATA", video_key)

    now = datetime.now(timezone.utc)
    future_time = now + timedelta(hours=2)

    meta = YouTubeMetadataBuilder.build(title="Scheduled Short", scheduled_publish_at=future_time)

    # 1. Publish with future release time -> DEFERRED
    result = await service.publish_clip(
        job_id=job_id,
        clip_id=clip_id,
        approval_request_id=app_id,
        video_storage_key=video_key,
        metadata=meta,
        expected_channel_id="UC_CORRECT_CHANNEL_01",
        scheduled_publish_at=future_time,
    )
    assert result.status == PublishStatus.DEFERRED

    # 2. Scheduler runs now (before due time) -> 0 clips published
    due_now = await scheduler.process_due_scheduled_clips(current_time=now, expected_channel_id="UC_CORRECT_CHANNEL_01")
    assert len(due_now) == 0

    # 3. Scheduler runs at future_time + 5 mins (catch up) -> 1 clip published
    eval_time = future_time + timedelta(minutes=5)
    due_future = await scheduler.process_due_scheduled_clips(current_time=eval_time, expected_channel_id="UC_CORRECT_CHANNEL_01")
    assert len(due_future) == 1
    assert due_future[0].status == PublishStatus.PUBLISHED

    # 4. Check that scheduled index was cleaned up
    remaining = await pub_repo.list_due_scheduled_records(current_time=eval_time + timedelta(hours=1))
    assert len(remaining) == 0


def test_parse_youtube_api_error_granular():
    from clipping.publishing.client import parse_youtube_api_error
    from clipping.publishing.models import FailureClassification

    # A. Quota Exceeded
    raw_quota = '{"error": {"code": 403, "message": "Quota exceeded", "errors": [{"reason": "quotaExceeded"}]}}'
    reason, msg, failure_type = parse_youtube_api_error(403, raw_quota)
    assert reason == "quotaExceeded"
    assert failure_type == FailureClassification.RETRYABLE

    # B. Channel-level Daily Upload Limit Exceeded
    raw_upload_limit = '{"error": {"code": 403, "message": "The user has exceeded the number of videos they may upload.", "errors": [{"reason": "uploadLimitExceeded"}]}}'
    reason, msg, failure_type = parse_youtube_api_error(403, raw_upload_limit)
    assert reason == "uploadLimitExceeded"
    assert failure_type == FailureClassification.RETRYABLE

    # C. Rate limit exceeded
    raw_rate = '{"error": {"code": 429, "message": "Too Many Requests", "errors": [{"reason": "rateLimitExceeded"}]}}'
    reason, msg, failure_type = parse_youtube_api_error(429, raw_rate)
    assert reason == "rateLimitExceeded"
    assert failure_type == FailureClassification.RETRYABLE

    # D. Permanent Permission Denied / Access not configured
    raw_perm = '{"error": {"code": 403, "message": "Access Not Configured", "errors": [{"reason": "accessNotConfigured"}]}}'
    reason, msg, failure_type = parse_youtube_api_error(403, raw_perm)
    assert reason == "accessNotConfigured"
    assert failure_type == FailureClassification.NON_RETRYABLE

    # E. Bad Request / Malformed Metadata
    raw_bad = '{"error": {"code": 400, "message": "Invalid title", "errors": [{"reason": "invalidTitle"}]}}'
    reason, msg, failure_type = parse_youtube_api_error(400, raw_bad)
    assert reason == "invalidTitle"
    assert failure_type == FailureClassification.NON_RETRYABLE


@pytest.mark.asyncio
async def test_publish_quota_exceeded_deferred_not_skipped(publishing_env):
    storage = publishing_env["storage"]
    app_repo = publishing_env["app_repo"]
    pub_repo = publishing_env["pub_repo"]
    client = publishing_env["client"]
    service = publishing_env["service"]

    job_id = "job_quota_01"
    clip_id = "clip_quota_01"
    app_id = "req_quota_01"
    video_key = f"clips/{clip_id}/final.mp4"

    # Seed state
    req = ApprovalRequest(
        approval_request_id=app_id,
        job_id=job_id,
        source_video_id="src_01",
        clip_id=clip_id,
        clip_index=1,
        title="Quota Test Short",
        start_time=0.0,
        end_time=30.0,
        duration=30.0,
        score=95.0,
        video_storage_key=video_key,
        status=ApprovalStatus.APPROVED,
    )
    await app_repo.save_request(req)
    qa = QAReport(clip_id=clip_id, source_video_id="src_01", overall_status=QACheckStatus.PASS, can_publish=True)
    await storage.upload_bytes(qa.model_dump_json().encode("utf-8"), f"clips/{clip_id}/qa_report.json")
    await storage.upload_bytes(b"DATA", video_key)

    meta = YouTubeMetadataBuilder.build(title="Quota Test Short")

    # Simulate API quotaExceeded
    client.simulate_quota_error = True

    result = await service.publish_clip(
        job_id=job_id,
        clip_id=clip_id,
        approval_request_id=app_id,
        video_storage_key=video_key,
        metadata=meta,
        expected_channel_id="UC_CORRECT_CHANNEL_01",
    )

    # Must be DEFERRED for future retry, NOT SKIPPED or permanently failed
    assert result.status == PublishStatus.DEFERRED
    assert result.failure_type.value == "retryable"
    assert result.scheduled_publish_at is not None

    # Scheduled index must contain this clip
    eval_time = result.scheduled_publish_at + timedelta(minutes=1)
    due = await pub_repo.list_due_scheduled_records(current_time=eval_time)
    assert len(due) == 1
    assert due[0].clip_id == clip_id


@pytest.mark.asyncio
async def test_publish_channel_upload_limit_deferred(publishing_env):
    storage = publishing_env["storage"]
    app_repo = publishing_env["app_repo"]
    client = publishing_env["client"]
    service = publishing_env["service"]

    job_id = "job_limit_01"
    clip_id = "clip_limit_01"
    app_id = "req_limit_01"
    video_key = f"clips/{clip_id}/final.mp4"

    req = ApprovalRequest(
        approval_request_id=app_id,
        job_id=job_id,
        source_video_id="src_01",
        clip_id=clip_id,
        clip_index=1,
        title="Channel Limit Short",
        start_time=0.0,
        end_time=30.0,
        duration=30.0,
        score=95.0,
        video_storage_key=video_key,
        status=ApprovalStatus.APPROVED,
    )
    await app_repo.save_request(req)
    qa = QAReport(clip_id=clip_id, source_video_id="src_01", overall_status=QACheckStatus.PASS, can_publish=True)
    await storage.upload_bytes(qa.model_dump_json().encode("utf-8"), f"clips/{clip_id}/qa_report.json")
    await storage.upload_bytes(b"DATA", video_key)

    meta = YouTubeMetadataBuilder.build(title="Channel Limit Short")

    # Simulate channel uploadLimitExceeded
    client.simulate_upload_limit_error = True

    result = await service.publish_clip(
        job_id=job_id,
        clip_id=clip_id,
        approval_request_id=app_id,
        video_storage_key=video_key,
        metadata=meta,
        expected_channel_id="UC_CORRECT_CHANNEL_01",
    )

    assert result.status == PublishStatus.DEFERRED
    assert result.failure_type.value == "retryable"
    assert "upload limit" in result.failure_reason.lower()


@pytest.mark.asyncio
async def test_publish_permission_denied_permanent_failure(publishing_env):
    storage = publishing_env["storage"]
    app_repo = publishing_env["app_repo"]
    client = publishing_env["client"]
    service = publishing_env["service"]

    job_id = "job_perm_01"
    clip_id = "clip_perm_01"
    app_id = "req_perm_01"
    video_key = f"clips/{clip_id}/final.mp4"

    req = ApprovalRequest(
        approval_request_id=app_id,
        job_id=job_id,
        source_video_id="src_01",
        clip_id=clip_id,
        clip_index=1,
        title="Permission Test Short",
        start_time=0.0,
        end_time=30.0,
        duration=30.0,
        score=95.0,
        video_storage_key=video_key,
        status=ApprovalStatus.APPROVED,
    )
    await app_repo.save_request(req)
    qa = QAReport(clip_id=clip_id, source_video_id="src_01", overall_status=QACheckStatus.PASS, can_publish=True)
    await storage.upload_bytes(qa.model_dump_json().encode("utf-8"), f"clips/{clip_id}/qa_report.json")
    await storage.upload_bytes(b"DATA", video_key)

    meta = YouTubeMetadataBuilder.build(title="Permission Test Short")

    client.simulate_permission_error = True

    result = await service.publish_clip(
        job_id=job_id,
        clip_id=clip_id,
        approval_request_id=app_id,
        video_storage_key=video_key,
        metadata=meta,
        expected_channel_id="UC_CORRECT_CHANNEL_01",
    )

    assert result.status == PublishStatus.FAILED
    assert result.failure_type.value == "non_retryable"


@pytest.mark.asyncio
async def test_batch_publish_independent_isolation(publishing_env):
    storage = publishing_env["storage"]
    app_repo = publishing_env["app_repo"]
    service = publishing_env["service"]

    job_id = "job_batch_iso"

    # Seed 2 clips: Clip 1 (Approved -> will publish), Clip 2 (Rejected -> will skip)
    for cid, app_status in [("clip_iso_1", ApprovalStatus.APPROVED), ("clip_iso_2", ApprovalStatus.REJECTED)]:
        req = ApprovalRequest(
            approval_request_id=f"req_{cid}",
            job_id=job_id,
            source_video_id="src_01",
            clip_id=cid,
            clip_index=1,
            title=f"Title {cid}",
            start_time=0.0,
            end_time=30.0,
            duration=30.0,
            score=95.0,
            video_storage_key=f"clips/{cid}/final.mp4",
            status=app_status,
        )
        await app_repo.save_request(req)
        qa = QAReport(clip_id=cid, source_video_id="src_01", overall_status=QACheckStatus.PASS, can_publish=True)
        await storage.upload_bytes(qa.model_dump_json().encode("utf-8"), f"clips/{cid}/qa_report.json")
        await storage.upload_bytes(b"DATA", f"clips/{cid}/final.mp4")

    items = [
        {
            "clip_id": "clip_iso_1",
            "approval_request_id": "req_clip_iso_1",
            "video_storage_key": "clips/clip_iso_1/final.mp4",
            "metadata": YouTubeMetadataBuilder.build(title="Clip 1"),
        },
        {
            "clip_id": "clip_iso_2",
            "approval_request_id": "req_clip_iso_2",
            "video_storage_key": "clips/clip_iso_2/final.mp4",
            "metadata": YouTubeMetadataBuilder.build(title="Clip 2"),
        },
    ]

    summary = await service.batch_publish_approved_clips(job_id=job_id, items=items, expected_channel_id="UC_CORRECT_CHANNEL_01")
    assert summary.published_count == 1
    assert summary.skipped_count == 1
    assert summary.all_processed is True
