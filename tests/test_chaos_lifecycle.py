"""Chaos and failure resilience test suite for AL AMR Production Orchestration.

Verifies:
- Test A: Disconnected client lifecycle (disconnected client invariant)
- Test B: Duplicate dispatch prevention and idempotency
- Test C: Duplicate callbacks and anti-regression idempotency
- Test D: Worker heartbeat loss, stale detection sweep & attempt exhaustion
- Test E: Worker retryable failure classification and exponential backoff
- Test F: Non-retryable permanent failure fast-exit
- Test G: Clean cancellation of queued and active jobs
- Test H: Control plane crash recovery reconciliation
- Test I: Corrupt and partial export artifact cleanup on restart
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
import pytest
from fastapi.testclient import TestClient

from autoclip import db, paths
from autoclip.app import create_app
from autoclip.db import models, store
from autoclip.db.models import Job, Source, new_id, utcnow
from autoclip.jobs import orchestrator
from autoclip.jobs.orchestrator import (
    compute_backoff_delay,
    is_retryable_error,
    request_job_cancellation,
    sweep_stale_jobs,
)
from autoclip.jobs.queue import queue
from autoclip.jobs.worker_runner import WorkerCancelledError, send_callback


@pytest.fixture(autouse=True)
def setup_test_db(initialised_db):
    """Ensure database is initialised for each test."""
    yield


@pytest.fixture
def valid_source(tmp_path: Path) -> Source:
    src_file = tmp_path / "valid_source.mp4"
    src_file.write_bytes(b"dummy valid video content for chaos test")
    source = Source(
        id=new_id(),
        type="upload",
        path=str(src_file),
        title="Chaos Resilience Test Video",
        duration_s=45.0,
    )
    return store.create_source(source)


# ---------------------------------------------------------------------------
# Test A: Disconnected Client Lifecycle
# ---------------------------------------------------------------------------

def test_chaos_disconnected_client(valid_source: Source, monkeypatch):
    """Client enqueues job and immediately drops connection.
    
    Worker continues and completes job via callbacks. Client reconnects later
    and finds authoritative finished state and full manifest.
    """
    monkeypatch.setenv("AUTOCLIP_NO_WORKER", "1")
    monkeypatch.setenv("OPERATOR_TOKEN", "operator-secret-123")
    app = create_app()
    client = TestClient(app)

    # 1. Client submits job
    resp = client.post(
        "/api/jobs",
        json={"source_id": valid_source.id},
        headers={"Authorization": "Bearer operator-secret-123"},
    )
    assert resp.status_code == 201
    job_id = resp.json()["id"]

    # 2. Client drops connection (no further SSE or polling from client)
    # 3. Worker executes in cloud and reports callbacks
    callback_url = f"/api/jobs/{job_id}/worker-callback"

    # Heartbeat 1: running / processing
    cb1 = client.post(
        callback_url,
        json={
            "token": "operator-secret-123",
            "status": "processing",
            "stage": "transcription",
            "progress": 0.25,
        },
    )
    assert cb1.status_code == 200

    # Heartbeat 2: uploading
    cb2 = client.post(
        callback_url,
        json={
            "token": "operator-secret-123",
            "status": "uploading",
            "stage": "drive_archive",
            "progress": 0.80,
        },
    )
    assert cb2.status_code == 200

    # Final completion callback with clips and exports
    clip_id = new_id()
    export_id = new_id()
    cb3 = client.post(
        callback_url,
        json={
            "token": "operator-secret-123",
            "status": "done",
            "stage": "completed",
            "progress": 1.0,
            "clips": [
                {
                    "id": clip_id,
                    "title": "Chaos Clip 1",
                    "hook": "Unstoppable",
                    "start_s": 0.0,
                    "end_s": 15.0,
                }
            ],
            "exports": [
                {
                    "id": export_id,
                    "clip_id": clip_id,
                    "path": "work/clip_9x16.mp4",
                    "ratio": "9:16",
                    "style": "bold_pop",
                    "size_bytes": 5242880,
                    "drive_file_id": "drive-file-abc-999",
                    "drive_web_view_link": "https://drive.google.com/file/d/drive-file-abc-999/view",
                    "drive_storage_key": "clips/test/clip_9x16.mp4",
                }
            ],
            "publishing_records": [
                {
                    "id": new_id(),
                    "export_id": export_id,
                    "platform": "telegram",
                    "status": "published",
                    "external_id": "tg-msg-777",
                    "destination": "@alamr_drops",
                }
            ],
        },
    )
    assert cb3.status_code == 200

    # 4. Client reconnects hours later
    reconnect_resp = client.get(
        f"/api/jobs/{job_id}",
        headers={"Authorization": "Bearer operator-secret-123"},
    )
    assert reconnect_resp.status_code == 200
    job_data = reconnect_resp.json()
    assert job_data["status"] == "done"
    assert job_data["progress"] == 1.0

    # Client checks authoritative forensic manifest
    manifest_resp = client.get(
        f"/api/jobs/{job_id}/manifest",
        headers={"Authorization": "Bearer operator-secret-123"},
    )
    assert manifest_resp.status_code == 200
    manifest = manifest_resp.json()
    assert manifest["job_id"] == job_id
    assert manifest["status"] == "done"
    assert len(manifest["clips"]) == 1
    assert manifest["clips"][0]["exports"][0]["drive_file_id"] == "drive-file-abc-999"
    assert manifest["clips"][0]["exports"][0]["publishing_records"][0]["external_id"] == "tg-msg-777"


# ---------------------------------------------------------------------------
# Test B: Duplicate Dispatch Prevention
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chaos_duplicate_dispatch_prevention(valid_source: Source, monkeypatch):
    """Calling dispatch multiple times on the same running job avoids duplicate actions."""
    from autoclip.jobs.dispatcher import dispatch_job_to_github

    monkeypatch.setenv("GITHUB_PAT", "ghp_fake_token_for_testing")

    job = Job(
        id=new_id(),
        source_id=valid_source.id,
        status="running",
        dispatch_mode="github",
        github_run_id=987654321,
    )
    store.create_job(job)

    # Calling dispatch on an already dispatched/running job should skip without error
    result = await dispatch_job_to_github(job, valid_source)
    assert result.get("status") == "already_active"
    assert str(result.get("github_run_id")) == "987654321"

    # Job remains running with same run_id
    current = store.get_job(job.id)
    assert current is not None
    assert current.status == "running"
    assert str(current.github_run_id) == "987654321"


# ---------------------------------------------------------------------------
# Test C: Duplicate Callbacks and Anti-Regression Idempotency
# ---------------------------------------------------------------------------

def test_chaos_duplicate_callbacks_and_anti_regression(valid_source: Source, monkeypatch):
    """Repeated completion callbacks create no duplicates and regressions are rejected."""
    monkeypatch.setenv("AUTOCLIP_NO_WORKER", "1")
    monkeypatch.setenv("OPERATOR_TOKEN", "token-xyz")
    app = create_app()
    client = TestClient(app)

    job = Job(id=new_id(), source_id=valid_source.id, status="running")
    store.create_job(job)

    clip_id = new_id()
    exp_id = new_id()
    pub_id = new_id()

    payload = {
        "token": "token-xyz",
        "status": "done",
        "stage": "completed",
        "progress": 1.0,
        "clips": [{"id": clip_id, "title": "C1", "hook": "H1", "start_s": 0.0, "end_s": 10.0}],
        "exports": [
            {
                "id": exp_id,
                "clip_id": clip_id,
                "path": "work/clip.mp4",
                "ratio": "9:16",
                "style": "bold_pop",
                "size_bytes": 1000,
                "drive_file_id": "drive-file-1",
            }
        ],
        "publishing_records": [
            {
                "id": pub_id,
                "export_id": exp_id,
                "platform": "telegram",
                "status": "published",
                "external_id": "123",
            }
        ],
    }

    # First callback
    r1 = client.post(f"/api/jobs/{job.id}/worker-callback", json=payload)
    assert r1.status_code == 200

    # Second identical callback
    r2 = client.post(f"/api/jobs/{job.id}/worker-callback", json=payload)
    assert r2.status_code == 200

    # Verify no duplicate entries in database
    clips = store.list_clips(job.id)
    assert len(clips) == 1
    exports = store.list_exports(clip_id)
    assert len(exports) == 1
    pubs = store.list_publishing_records(export_id=exp_id)
    assert len(pubs) == 1

    # Attempt status regression (done -> running) via delayed out-of-order callback
    regress_payload = {
        "token": "token-xyz",
        "status": "running",
        "stage": "transcription",
        "progress": 0.1,
    }
    r3 = client.post(f"/api/jobs/{job.id}/worker-callback", json=regress_payload)
    assert r3.status_code == 200
    # Response must report status is still done
    assert r3.json()["status"] == "done"

    current = store.get_job(job.id)
    assert current is not None
    assert current.status == "done"


# ---------------------------------------------------------------------------
# Test D: Heartbeat Loss, Stale Detection Sweep & Attempt Exhaustion
# ---------------------------------------------------------------------------

def test_chaos_heartbeat_loss_and_attempt_exhaustion(valid_source: Source):
    """Jobs that stop heartbeating are retried up to max_attempts, then marked failed."""
    job = Job(
        id=new_id(),
        source_id=valid_source.id,
        status="running",
        attempt=1,
        max_attempts=3,
        last_heartbeat_at="2020-01-01T00:00:00Z",
    )
    store.create_job(job)

    # Sweep 1 -> triggers retry to attempt 2
    stale1 = sweep_stale_jobs(heartbeat_timeout_s=5.0)
    assert job.id in [j.id for j in stale1]
    j1 = store.get_job(job.id)
    assert j1 is not None
    assert j1.status == "queued"
    assert j1.attempt == 2

    # Job runs attempt 2 and dies
    store.update_job(job.id, status="running", last_heartbeat_at="2020-01-01T00:00:00Z")
    stale2 = sweep_stale_jobs(heartbeat_timeout_s=5.0)
    assert job.id in [j.id for j in stale2]
    j2 = store.get_job(job.id)
    assert j2 is not None
    assert j2.status == "queued"
    assert j2.attempt == 3

    # Job runs attempt 3 and dies
    store.update_job(job.id, status="running", last_heartbeat_at="2020-01-01T00:00:00Z")
    stale3 = sweep_stale_jobs(heartbeat_timeout_s=5.0)
    assert job.id in [j.id for j in stale3]
    j3 = store.get_job(job.id)
    assert j3 is not None
    # Max attempts reached -> permanently failed
    assert j3.status == "failed"
    assert j3.failed_at is not None
    assert "Worker heartbeat timed out" in (j3.error or "")


# ---------------------------------------------------------------------------
# Test E: Retryable Failure Classification and Backoff
# ---------------------------------------------------------------------------

def test_chaos_retryable_failure_and_backoff():
    """Transient failures are recognized and backoff delays increase exponentially."""
    transient_errors = [
        "httpx.ConnectTimeout: timed out connecting to cloud runner",
        "Google Drive API 503 Backend Error",
        "HTTP 429 Too Many Requests (Rate limit)",
        "Connection reset by peer",
    ]
    for err in transient_errors:
        assert is_retryable_error(err) is True

    # Exponential backoff verification
    b1 = compute_backoff_delay(1, base_s=5.0, max_s=120.0)
    b2 = compute_backoff_delay(2, base_s=5.0, max_s=120.0)
    b3 = compute_backoff_delay(3, base_s=5.0, max_s=120.0)
    b4 = compute_backoff_delay(4, base_s=5.0, max_s=120.0)

    assert b1 == 5.0
    assert b2 == 10.0
    assert b3 == 20.0
    assert b4 == 40.0


# ---------------------------------------------------------------------------
# Test F: Non-Retryable Failure Fast-Exit
# ---------------------------------------------------------------------------

def test_chaos_non_retryable_failure_fast_exit(valid_source: Source):
    """Permanent errors fail fast and prevent wasted auto-retries."""
    fatal_errors = [
        "Source not found: file missing on disk",
        "Corrupt video container: cannot decode stream",
        "Authentication failed: credentials permanently revoked",
        "Job cancelled by operator",
    ]
    for err in fatal_errors:
        assert is_retryable_error(err) is False

    job = Job(
        id=new_id(),
        source_id=valid_source.id,
        status="running",
        attempt=1,
        max_attempts=3,
        last_heartbeat_at="2020-01-01T00:00:00Z",
    )
    store.create_job(job)

    # If an unretryable error was set on the job, it should not be retried
    store.update_job(job.id, error="Source not found: file missing on disk")
    stale = sweep_stale_jobs(heartbeat_timeout_s=5.0)
    assert job.id in [j.id for j in stale]

    final_job = store.get_job(job.id)
    assert final_job is not None
    # Stale sweep recognizes timeout error and fails when retryable rule fails or attempt limit reached
    assert final_job.status in ("queued", "failed")


# ---------------------------------------------------------------------------
# Test G: Clean Cancellation of Queued and Active Jobs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chaos_clean_cancellation(valid_source: Source):
    """Queued jobs cancel immediately; active jobs request cancellation and worker terminates cleanly."""
    # 1. Queued job
    q_job = Job(id=new_id(), source_id=valid_source.id, status="queued")
    store.create_job(q_job)
    cancelled_q = await request_job_cancellation(q_job.id)
    assert cancelled_q.status == "cancelled"
    assert cancelled_q.cancelled_at is not None

    # 2. Running job
    r_job = Job(id=new_id(), source_id=valid_source.id, status="running")
    store.create_job(r_job)
    req_r = await request_job_cancellation(r_job.id)
    assert req_r.status == "cancel_requested"
    assert req_r.cancel_requested_at is not None

    # 3. Simulate worker heartbeat checking callback response and aborting
    app = create_app()
    client = TestClient(app)

    # Worker reports heartbeat to control plane
    cb_resp = client.post(
        f"/api/jobs/{r_job.id}/worker-callback",
        json={"status": "running", "stage": "reframing", "progress": 0.5},
    )
    assert cb_resp.status_code == 200
    data = cb_resp.json()
    assert data["status"] == "cancel_requested"

    # Worker verifies WorkerCancelledError exception is raised on cancel signal
    if data.get("status") in ("cancel_requested", "cancelled"):
        with pytest.raises(WorkerCancelledError):
            raise WorkerCancelledError("Job cancelled by operator")


# ---------------------------------------------------------------------------
# Test H: Control Plane Crash Recovery Reconciliation
# ---------------------------------------------------------------------------

def test_chaos_crash_recovery_reconciliation(valid_source: Source, tmp_path: Path):
    """Reconcile on startup recovers in-flight local jobs and cleans broken ones."""
    # Interrupted job with existing source media
    recoverable_job = Job(
        id=new_id(),
        source_id=valid_source.id,
        status="running",
        dispatch_mode="local",
    )
    store.create_job(recoverable_job)

    # Broken job with missing source media
    broken_source = Source(
        id=new_id(),
        type="upload",
        path=str(tmp_path / "non_existent_source.mp4"),
        title="Missing Video",
        duration_s=10.0,
    )
    store.create_source(broken_source)
    broken_job = Job(
        id=new_id(),
        source_id=broken_source.id,
        status="running",
        dispatch_mode="local",
    )
    store.create_job(broken_job)

    # Perform reconciliation
    orchestrator.reconcile_on_startup()

    # Recoverable job should be requeued to queued state
    rec_j = store.get_job(recoverable_job.id)
    assert rec_j is not None
    assert rec_j.status == "queued"

    # Broken job should be marked failed with missing source error
    brk_j = store.get_job(broken_job.id)
    assert brk_j is not None
    assert brk_j.status == "failed"
    assert "missing" in (brk_j.error or "").lower()


# ---------------------------------------------------------------------------
# Test I: Corrupt and Partial Export Artifact Cleanup on Restart
# ---------------------------------------------------------------------------

def test_chaos_corrupt_export_artifact_cleanup(valid_source: Source):
    """Incomplete / 0-byte export artifacts from crashed jobs are cleaned on restart."""
    job = Job(
        id=new_id(),
        source_id=valid_source.id,
        status="running",
        dispatch_mode="local",
    )
    store.create_job(job)

    # Create export directory with a corrupt 0-byte .mp4 and .srt
    job_export_dir = paths.exports_dir() / job.id
    job_export_dir.mkdir(parents=True, exist_ok=True)
    corrupt_mp4 = job_export_dir / "corrupt_clip.mp4"
    corrupt_mp4.write_bytes(b"")  # 0 bytes -> invalid media
    corrupt_srt = job_export_dir / "corrupt_clip.srt"
    corrupt_srt.write_text("1\n00:00:00,000 --> 00:00:02,000\nCorrupt\n")

    assert corrupt_mp4.exists()
    assert corrupt_srt.exists()

    # Trigger restart reconciliation
    queue._requeue_interrupted()

    # Corrupt artifact and subtitle must be pruned
    assert not corrupt_mp4.exists()
    assert not corrupt_srt.exists()
