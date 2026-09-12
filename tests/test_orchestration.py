"""Tests for Step 9 Autonomous Production Orchestration & Job Lifecycle."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
import pytest
from fastapi.testclient import TestClient

from autoclip import db
from autoclip.app import create_app
from autoclip.db import models, store
from autoclip.db.models import Job, Source, new_id, utcnow
from autoclip.jobs import orchestrator
from autoclip.jobs.orchestrator import (
    InvalidStateTransitionError,
    can_transition,
    compute_backoff_delay,
    is_retryable_error,
    request_job_cancellation,
    sweep_stale_jobs,
    validate_transition,
)


@pytest.fixture(autouse=True)
def setup_test_db(initialised_db):
    """Ensure database is initialised for each test."""
    yield


@pytest.fixture
def test_source(tmp_path: Path) -> Source:
    src_file = tmp_path / "video.mp4"
    src_file.write_bytes(b"dummy video data")
    source = Source(
        id=new_id(),
        type="upload",
        path=str(src_file),
        title="Orchestration Test Video",
        duration_s=60.0,
    )
    return store.create_source(source)


def test_state_machine_valid_and_invalid_transitions():
    """Verify state machine allows valid transitions and rejects invalid ones."""
    # Valid transitions
    assert can_transition("queued", "dispatching")
    assert can_transition("queued", "running")
    assert can_transition("queued", "cancelled")
    assert can_transition("dispatching", "running")
    assert can_transition("running", "processing")
    assert can_transition("processing", "uploading")
    assert can_transition("uploading", "publishing")
    assert can_transition("publishing", "done")
    assert can_transition("running", "cancel_requested")
    assert can_transition("cancel_requested", "cancelled")
    assert can_transition("failed", "queued")

    # Invalid transitions
    assert not can_transition("done", "running")
    assert not can_transition("done", "queued")
    assert not can_transition("done", "dispatching")

    # validate_transition helper raises on invalid
    validate_transition("running", "done")
    with pytest.raises(InvalidStateTransitionError):
        validate_transition("done", "running")


def test_retry_policy_and_backoff():
    """Verify classification of retryable vs fatal errors and backoff calculation."""
    # Transient / retryable errors
    assert is_retryable_error("HTTPConnectionPool timeout reading from github.com")
    assert is_retryable_error("Temporary 503 Service Unavailable")
    assert is_retryable_error("Google Drive rate limit exceeded: quota")
    assert is_retryable_error(None)

    # Permanent / fatal errors
    assert not is_retryable_error("Source not found: file missing on disk")
    assert not is_retryable_error("Unsupported media format")
    assert not is_retryable_error("Job cancelled by operator")
    assert not is_retryable_error("Authentication permanently invalid")

    # Exponential backoff calculations
    delay1 = compute_backoff_delay(1, base_s=5.0, max_s=60.0)
    delay2 = compute_backoff_delay(2, base_s=5.0, max_s=60.0)
    delay3 = compute_backoff_delay(3, base_s=5.0, max_s=60.0)
    delay10 = compute_backoff_delay(10, base_s=5.0, max_s=60.0)

    assert delay1 == 5.0
    assert delay2 == 10.0
    assert delay3 == 20.0
    assert delay10 == 60.0  # Capped at max_s


def test_stale_job_detection_and_auto_retry(test_source: Source):
    """Verify stale jobs without heartbeat are swept and retried if attempts remain."""
    # Create job with past heartbeat
    job = Job(
        id=new_id(),
        source_id=test_source.id,
        status="running",
        attempt=1,
        max_attempts=3,
        last_heartbeat_at="2020-01-01T00:00:00Z",
    )
    store.create_job(job)

    # Sweep stale jobs with a small threshold
    stale_jobs = sweep_stale_jobs(heartbeat_timeout_s=10.0)
    stale_ids = [j.id for j in stale_jobs]
    assert job.id in stale_ids

    # Job had attempt 1 < 3, so it should be auto-requeued for attempt 2
    updated = store.get_job(job.id)
    assert updated is not None
    assert updated.status == "queued"
    assert updated.attempt == 2
    assert updated.stale_at is not None


def test_stale_job_fails_when_max_attempts_exceeded(test_source: Source):
    """Verify stale job fails permanently once max_attempts is reached."""
    job = Job(
        id=new_id(),
        source_id=test_source.id,
        status="running",
        attempt=3,
        max_attempts=3,
        last_heartbeat_at="2020-01-01T00:00:00Z",
    )
    store.create_job(job)

    stale_jobs = sweep_stale_jobs(heartbeat_timeout_s=10.0)
    stale_ids = [j.id for j in stale_jobs]
    assert job.id in stale_ids

    updated = store.get_job(job.id)
    assert updated is not None
    assert updated.status == "failed"
    assert updated.failed_at is not None
    assert "Worker heartbeat timed out" in (updated.error or "")


@pytest.mark.asyncio
async def test_job_cancellation_lifecycle(test_source: Source):
    """Verify cancellation marks queued jobs cancelled and running jobs cancel_requested."""
    # 1. Queued job
    queued_job = Job(id=new_id(), source_id=test_source.id, status="queued")
    store.create_job(queued_job)

    res = await request_job_cancellation(queued_job.id)
    assert res.status == "cancelled"
    assert res.cancelled_at is not None

    # 2. Running job
    running_job = Job(id=new_id(), source_id=test_source.id, status="running")
    store.create_job(running_job)

    res2 = await request_job_cancellation(running_job.id)
    assert res2.status == "cancel_requested"
    assert res2.cancel_requested_at is not None


def test_callback_duplicate_idempotency_and_regressions(test_source: Source, monkeypatch):
    """Verify callback is idempotent on duplicate done and rejects status regression."""
    monkeypatch.setenv("AUTOCLIP_NO_WORKER", "1")
    monkeypatch.setenv("OPERATOR_TOKEN", "test-auth-token")
    app = create_app()
    client = TestClient(app)

    job = Job(id=new_id(), source_id=test_source.id, status="running")
    store.create_job(job)

    # First completion callback
    resp1 = client.post(
        f"/api/jobs/{job.id}/worker-callback",
        json={
            "token": "test-auth-token",
            "status": "done",
            "stage": "completed",
            "progress": 1.0,
        },
    )
    assert resp1.status_code == 200
    assert resp1.json()["status"] == "done"

    # Duplicate completion callback must remain done
    resp2 = client.post(
        f"/api/jobs/{job.id}/worker-callback",
        json={
            "token": "test-auth-token",
            "status": "done",
            "stage": "completed",
            "progress": 1.0,
        },
    )
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "done"

    # Backward regression (done -> running) must be safely ignored by callback
    resp3 = client.post(
        f"/api/jobs/{job.id}/worker-callback",
        json={
            "token": "test-auth-token",
            "status": "running",
            "stage": "download",
            "progress": 0.1,
        },
    )
    assert resp3.status_code == 200
    # State remains done
    assert resp3.json()["status"] == "done"


def test_job_manifest_endpoint(test_source: Source, monkeypatch):
    """Verify GET /api/jobs/{id}/manifest aggregates complete clip, drive, and publish data."""
    monkeypatch.setenv("AUTOCLIP_NO_WORKER", "1")
    monkeypatch.setenv("OPERATOR_TOKEN", "test-auth-token")
    app = create_app()
    client = TestClient(app)

    job = Job(id=new_id(), source_id=test_source.id, status="done", current_stage="completed", progress=1.0)
    store.create_job(job)

    clip = models.Clip(
        id=new_id(),
        job_id=job.id,
        title="Highlight 1",
        hook="Amazing intro",
        start_s=0.0,
        end_s=15.0,
        status="candidate",
    )
    store.create_clip(clip)

    exp = models.Export(
        id=new_id(),
        clip_id=clip.id,
        path="work/test.mp4",
        ratio="9:16",
        style="bold_pop",
        size_bytes=102400,
        drive_file_id="drive-12345",
        drive_web_view_link="https://drive.google.com/file/d/drive-12345/view",
        drive_storage_key="clips/test.mp4",
    )
    store.create_export(exp)

    pub = models.PublishingRecord(
        id=new_id(),
        export_id=exp.id,
        job_id=job.id,
        platform="telegram",
        status="published",
        external_id="msg-999",
        destination="@alamr_drops",
    )
    store.create_or_update_publishing_record(pub)

    resp = client.get(
        f"/api/jobs/{job.id}/manifest",
        headers={"Authorization": "Bearer test-auth-token"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["job_id"] == job.id
    assert data["status"] == "done"
    assert len(data["clips"]) == 1
    c_manifest = data["clips"][0]
    assert c_manifest["clip_id"] == clip.id
    assert len(c_manifest["exports"]) == 1
    e_manifest = c_manifest["exports"][0]
    assert e_manifest["drive_file_id"] == "drive-12345"
    assert len(e_manifest["publishing_records"]) == 1
    assert e_manifest["publishing_records"][0]["external_id"] == "msg-999"


def test_operator_retry_endpoint(test_source: Source, monkeypatch):
    """Verify POST /api/jobs/{id}/retry enforces attempt limits and requeues."""
    monkeypatch.setenv("AUTOCLIP_NO_WORKER", "1")
    monkeypatch.setenv("OPERATOR_TOKEN", "test-auth-token")
    app = create_app()
    client = TestClient(app)

    # 1. Successful retry
    job = Job(id=new_id(), source_id=test_source.id, status="failed", attempt=1, max_attempts=3, error="Transient failure")
    store.create_job(job)

    resp = client.post(
        f"/api/jobs/{job.id}/retry",
        headers={"Authorization": "Bearer test-auth-token"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "queued"
    assert data["attempt"] == 2
    assert data["error"] is None

    # 2. Exceeded max attempts
    exhausted_job = Job(id=new_id(), source_id=test_source.id, status="failed", attempt=3, max_attempts=3, error="Exhausted")
    store.create_job(exhausted_job)

    resp_exhausted = client.post(
        f"/api/jobs/{exhausted_job.id}/retry",
        headers={"Authorization": "Bearer test-auth-token"},
    )
    assert resp_exhausted.status_code == 400
    assert "exceeded maximum allowed attempts" in resp_exhausted.json()["detail"]


def test_cancel_terminal_job_rejected(test_source: Source, monkeypatch):
    """Verify attempting to cancel an already finished job returns 409."""
    monkeypatch.setenv("AUTOCLIP_NO_WORKER", "1")
    monkeypatch.setenv("OPERATOR_TOKEN", "test-auth-token")
    app = create_app()
    client = TestClient(app)

    done_job = Job(id=new_id(), source_id=test_source.id, status="done")
    store.create_job(done_job)

    resp = client.post(
        f"/api/jobs/{done_job.id}/cancel",
        headers={"Authorization": "Bearer test-auth-token"},
    )
    assert resp.status_code == 409


def test_crash_recovery_reconciliation(test_source: Source):
    """Verify reconcile_on_startup cleans incomplete artifacts and requeues interrupted jobs."""
    # Create an interrupted local job
    interrupted_job = Job(
        id=new_id(),
        source_id=test_source.id,
        status="running",
        dispatch_mode="local",
    )
    store.create_job(interrupted_job)

    orchestrator.reconcile_on_startup()

    recovered = store.get_job(interrupted_job.id)
    assert recovered is not None
    assert recovered.status == "queued"

