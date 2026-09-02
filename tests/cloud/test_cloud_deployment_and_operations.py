"""Phase 12 Cloud Deployment, Autonomous Operation, Lease Locking, and Recovery Tests."""

import pytest
from datetime import datetime, timedelta, timezone
from httpx import AsyncClient, ASGITransport
from pydantic import SecretStr

from clipping.approval.models import ApprovalRequest, ApprovalStatus
from clipping.approval.repository import ApprovalRepository
from clipping.cli.pipeline_runner import run_pipeline
from clipping.config.settings import Settings
from clipping.control.github import GitHubWorkflowDispatcher
from clipping.control.models import SystemControlState, SystemOperatingMode
from clipping.control.repository import ControlRepository
from clipping.control.service import MasterControlService
from clipping.publishing.models import PublishStatus
from clipping.publishing.repository import PublishingRepository
from clipping.state.lease import JobLease, JobLeaseRepository
from clipping.state.models import JobState, PipelineStage
from clipping.state.remote import RemoteStorageStateRepository
from clipping.storage.local import LocalStorageDriver
import clipping.ui.server as ui_server
from clipping.ui.server import app


@pytest.fixture
def cloud_env(temp_vault_dir, monkeypatch):
    storage = LocalStorageDriver(root_dir=temp_vault_dir)
    control_repo = ControlRepository(storage_driver=storage)
    state_repo = RemoteStorageStateRepository(storage_driver=storage)
    lease_repo = JobLeaseRepository(storage_driver=storage)
    app_repo = ApprovalRepository(storage_driver=storage)
    ctrl_service = MasterControlService(
        control_repository=control_repo,
        state_repository=state_repo,
        storage_driver=storage,
    )

    app.dependency_overrides[ui_server.get_storage_driver] = lambda: storage
    monkeypatch.setattr("clipping.cli.pipeline_runner.get_storage_driver", lambda s: storage)

    yield {
        "storage": storage,
        "control_repo": control_repo,
        "state_repo": state_repo,
        "lease_repo": lease_repo,
        "app_repo": app_repo,
        "ctrl_service": ctrl_service,
    }
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_job_lease_locking_and_collision_prevention(cloud_env):
    """Proves that JobLeaseRepository prevents concurrent duplicate execution across workers."""
    lease_repo = cloud_env["lease_repo"]
    job_id = "job_lease_test_01"

    # Worker 1 claims job
    ok, reason = await lease_repo.acquire_lease(job_id, worker_id="gha_runner_01", ttl_seconds=60)
    assert ok is True
    assert reason is None

    # Worker 2 attempts concurrent claim on same job -> blocked
    ok2, collision_reason = await lease_repo.acquire_lease(job_id, worker_id="gha_runner_02", ttl_seconds=60)
    assert ok2 is False
    assert "already locked" in collision_reason

    # Worker 1 releases lease
    released = await lease_repo.release_lease(job_id, worker_id="gha_runner_01")
    assert released is True

    # Worker 2 can now acquire lease
    ok3, reason3 = await lease_repo.acquire_lease(job_id, worker_id="gha_runner_02", ttl_seconds=60)
    assert ok3 is True


@pytest.mark.asyncio
async def test_job_lease_stale_worker_reclamation(cloud_env):
    """Proves that an expired lease from an interrupted runner is safely reclaimed."""
    storage = cloud_env["storage"]
    lease_repo = cloud_env["lease_repo"]
    job_id = "job_stale_test"

    # Inject an expired lease (expired 5 minutes ago)
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    stale_lease = JobLease(
        job_id=job_id,
        worker_id="crashed_runner_99",
        claimed_at=past - timedelta(minutes=30),
        lease_expires_at=past,
        status="active",
    )
    await storage.upload_bytes(
        stale_lease.model_dump_json(indent=2).encode("utf-8"),
        f"jobs/{job_id}/lease.json",
    )

    # Next worker reclaims stale lease
    ok, reason = await lease_repo.acquire_lease(job_id, worker_id="new_runner_100", ttl_seconds=1800)
    assert ok is True
    assert reason is None


@pytest.mark.asyncio
async def test_pipeline_runner_emergency_stop_cooperative_halt(cloud_env):
    """Proves that pipeline_runner halts immediately when Emergency Stop is active."""
    ctrl_service = cloud_env["ctrl_service"]
    job_id = "job_halt_test"

    # Activate Emergency Stop
    await ctrl_service.emergency_stop(operator="SafetyOfficer", reason="Critical drift incident")

    # Runner attempts to run
    exit_code = await run_pipeline(
        source_uri="https://www.youtube.com/watch?v=sample",
        job_id=job_id,
        worker_id="runner_halt_01",
    )
    assert exit_code == 1


@pytest.mark.asyncio
async def test_pipeline_runner_automation_pause_graceful_skip(cloud_env):
    """Proves that pipeline_runner skips gracefully when automation is paused."""
    ctrl_service = cloud_env["ctrl_service"]
    job_id = "job_pause_test"

    # Pause automation
    await ctrl_service.pause_automation(operator="Ops", reason="Scheduled maintenance")

    # Runner attempts to run -> returns 0 (gracefully skipped)
    exit_code = await run_pipeline(
        source_uri="https://www.youtube.com/watch?v=sample",
        job_id=job_id,
        worker_id="runner_pause_01",
    )
    assert exit_code == 0


@pytest.mark.asyncio
async def test_healthz_and_readiness_probe(cloud_env):
    """Verifies that /healthz responds with operational readiness telemetry."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/healthz")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["liveness"] is True
        assert data["readiness"] is True
        assert data["emergency_stopped"] is False


@pytest.mark.asyncio
async def test_run_now_endpoint_lifecycle(cloud_env, monkeypatch):
    """Verifies that POST /api/control/run-now creates a durable job record and dispatches workflow."""
    ctrl_service = cloud_env["ctrl_service"]
    state_repo = cloud_env["state_repo"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Successful run now
        resp = await client.post(
            "/api/control/run-now",
            json={"source_uri": "https://www.youtube.com/watch?v=test", "campaign_id": "test_campaign"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        job_id = data["job_id"]

        # Check job in canonical storage
        job = await state_repo.get_job(job_id)
        assert job is not None
        assert job.campaign_id == "test_campaign"

        # 2. Blocked run now when Emergency Stopped
        await ctrl_service.emergency_stop(operator="Director", reason="Testing stop lock")
        block_resp = await client.post(
            "/api/control/run-now",
            json={"source_uri": "https://www.youtube.com/watch?v=test"},
        )
        assert block_resp.status_code == 409


@pytest.mark.asyncio
async def test_render_statelessness_reboot_recovery(cloud_env):
    """
    Simulates complete Render container restart by tearing down and reconstructing
    app state from the underlying Google Drive canonical storage driver.
    """
    storage = cloud_env["storage"]
    ctrl_service = cloud_env["ctrl_service"]
    app_repo = cloud_env["app_repo"]

    # 1. Seed state in storage
    await ctrl_service.set_publishing_lock(locked=True, operator="Operator_01", reason="Maintenance window")
    req = ApprovalRequest(
        approval_request_id="req_render_reboot_01",
        job_id="job_reboot_01",
        source_video_id="src_01",
        clip_id="clip_01",
        clip_index=1,
        title="Statelessness Test Clip",
        start_time=0.0,
        end_time=30.0,
        duration=30.0,
        score=92.0,
        video_storage_key="clips/clip_01/final.mp4",
        status=ApprovalStatus.APPROVED,
    )
    await app_repo.save_request(req)

    # 2. Simulate container reboot: new repositories initialized over same storage
    new_control_repo = ControlRepository(storage_driver=storage)
    new_app_repo = ApprovalRepository(storage_driver=storage)

    recovered_state = await new_control_repo.get_state()
    assert recovered_state.publishing_locked is True
    assert recovered_state.last_changed_by == "Operator_01"

    recovered_req = await new_app_repo.get_request_by_id("req_render_reboot_01")
    assert recovered_req is not None
    assert recovered_req.status == ApprovalStatus.APPROVED


@pytest.mark.asyncio
async def test_ephemeral_runner_interruption_resumption(cloud_env):
    """
    Proves that when an ephemeral GitHub Actions worker terminates at an intermediate checkpoint,
    a subsequent worker picks up from canonical storage and completes the pipeline.
    """
    state_repo = cloud_env["state_repo"]
    job_id = "job_interrupted_01"

    # Initial job created by worker 1 advancing to TRANSCRIBING
    await state_repo.create_job(
        job_id=job_id,
        campaign_id="cmp_01",
        source_video_id="src_01",
        idempotency_key=f"idemp_{job_id}",
    )
    await state_repo.update_job_state(
        job_id=job_id,
        new_state=JobState.TRANSCRIBING,
        new_stage=PipelineStage.PERCEPTION,
        reason="Worker 1 executed transcription before being terminated",
    )

    # Worker 2 picks up job and runs to completion
    exit_code = await run_pipeline(
        source_uri="https://www.youtube.com/watch?v=sample",
        job_id=job_id,
        worker_id="runner_resumed_02",
    )
    assert exit_code == 0

    final_job = await state_repo.get_job(job_id)
    assert final_job.current_state == JobState.AWAITING_APPROVAL
    assert final_job.current_stage == PipelineStage.APPROVAL
