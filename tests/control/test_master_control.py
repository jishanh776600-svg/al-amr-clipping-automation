"""Unit and integration tests for Master Control, Emergency State, and Operator Authorization."""

import pytest
from httpx import AsyncClient, ASGITransport
from pydantic import SecretStr

from clipping.approval.models import ApprovalRequest, ApprovalStatus
from clipping.approval.repository import ApprovalRepository
from clipping.config.settings import Settings
from clipping.control.models import SystemControlState, SystemOperatingMode
from clipping.control.repository import ControlRepository
from clipping.control.service import MasterControlService
from clipping.core.constants import CANONICAL_PIPELINE_STAGES, PIPELINE_STAGE_COUNT
from clipping.publishing.gates import PublishingGateEnforcer
from clipping.publishing.models import PublishStatus
from clipping.state.models import JobState, PipelineStage
from clipping.state.remote import RemoteStorageStateRepository
from clipping.storage.local import LocalStorageDriver
import clipping.ui.server as ui_server
from clipping.ui.server import app


@pytest.fixture
def control_test_env(temp_vault_dir, monkeypatch):
    storage = LocalStorageDriver(root_dir=temp_vault_dir)
    control_repo = ControlRepository(storage_driver=storage)
    state_repo = RemoteStorageStateRepository(storage_driver=storage)
    app_repo = ApprovalRepository(storage_driver=storage)
    ctrl_service = MasterControlService(
        control_repository=control_repo,
        state_repository=state_repo,
        storage_driver=storage,
    )
    monkeypatch.setattr(ui_server, "get_storage_driver", lambda: storage)
    return {
        "storage": storage,
        "control_repo": control_repo,
        "state_repo": state_repo,
        "app_repo": app_repo,
        "ctrl_service": ctrl_service,
    }


def test_pipeline_stages_count_is_nine():
    """Validates that the pipeline stage count is strictly 9 and matches canonical names."""
    assert PIPELINE_STAGE_COUNT == 9
    assert len(CANONICAL_PIPELINE_STAGES) == 9
    expected = [
        "01_INGESTION",
        "02_TRANSCRIPTION",
        "03_UNDERSTANDING",
        "04_DISCOVERY",
        "05_REFRAME",
        "06_RENDER",
        "07_QA",
        "08_APPROVAL",
        "09_PUBLISH",
    ]
    assert CANONICAL_PIPELINE_STAGES == expected


@pytest.mark.asyncio
async def test_control_state_persistence_and_emergency_stop(control_test_env):
    """Verifies that Emergency Stop sets durable state in Google Drive and logs audit trail."""
    ctrl_service = control_test_env["ctrl_service"]
    control_repo = control_test_env["control_repo"]

    # Initial state is default OPERATIONAL
    init_state = await control_repo.get_state()
    assert init_state.mode == SystemOperatingMode.OPERATIONAL
    assert init_state.can_start_new_jobs() is True
    assert init_state.can_publish() is True

    # 1. Trigger EMERGENCY STOP
    stopped_state = await ctrl_service.emergency_stop(
        operator="Director",
        reason="Abnormal audio drift detected on batch 12",
    )
    assert stopped_state.mode == SystemOperatingMode.EMERGENCY_STOPPED
    assert stopped_state.emergency_stopped is True
    assert stopped_state.automation_paused is True
    assert stopped_state.publishing_locked is True
    assert stopped_state.can_start_new_jobs() is False
    assert stopped_state.can_publish() is False
    assert stopped_state.version == 2

    # 2. Check persistence directly from storage
    persisted = await control_repo.get_state()
    assert persisted.emergency_stopped is True
    assert persisted.last_changed_by == "Director"

    # 3. Verify audit record
    audits = await control_repo.list_audits()
    assert len(audits) >= 1
    assert audits[0].action == "EMERGENCY_STOP"
    assert audits[0].operator == "Director"

    # 4. Resume automation
    resumed = await ctrl_service.resume_automation(
        operator="Director",
        reason="Audio drift resolved; resuming normal ops",
    )
    assert resumed.mode == SystemOperatingMode.OPERATIONAL
    assert resumed.emergency_stopped is False
    assert resumed.automation_paused is False
    assert resumed.publishing_locked is False
    assert resumed.version == 3


@pytest.mark.asyncio
async def test_publishing_lock_and_pause_states(control_test_env):
    """Verifies independent publishing lock and automation pause without emergency stop."""
    ctrl_service = control_test_env["ctrl_service"]
    control_repo = control_test_env["control_repo"]

    # 1. Pause automation
    paused = await ctrl_service.pause_automation(operator="Ops", reason="Routine maintenance")
    assert paused.automation_paused is True
    assert paused.emergency_stopped is False
    assert paused.mode == SystemOperatingMode.AUTOMATION_PAUSED
    assert paused.can_start_new_jobs() is False

    # 2. Lock publishing
    locked = await ctrl_service.set_publishing_lock(locked=True, operator="Ops", reason="Holding uploads")
    assert locked.publishing_locked is True
    assert locked.can_publish() is False

    # Unlock publishing
    unlocked = await ctrl_service.set_publishing_lock(locked=False, operator="Ops", reason="Releasing uploads")
    assert unlocked.publishing_locked is False


@pytest.mark.asyncio
async def test_publishing_gate_enforces_master_control(control_test_env):
    """Proves that PublishingGateEnforcer defers uploads when Emergency Stop or Publish Lock is active."""
    app_repo = control_test_env["app_repo"]
    storage = control_test_env["storage"]
    control_repo = control_test_env["control_repo"]
    ctrl_service = control_test_env["ctrl_service"]

    gates = PublishingGateEnforcer(
        approval_repository=app_repo,
        storage_driver=storage,
        control_repository=control_repo,
    )

    # Initially OK
    ok, status, _ = await gates.verify_control_gate()
    assert ok is True
    assert status == PublishStatus.READY

    # Trigger Emergency Stop
    await ctrl_service.emergency_stop(operator="SafetyOfficer", reason="Safety incident")
    ok, status, reason = await gates.verify_control_gate()
    assert ok is False
    assert status == PublishStatus.DEFERRED
    assert "EMERGENCY STOP" in reason

    # Resume but lock publishing
    await ctrl_service.resume_automation(operator="SafetyOfficer")
    await ctrl_service.set_publishing_lock(locked=True, operator="SafetyOfficer")
    ok, status, reason = await gates.verify_control_gate()
    assert ok is False
    assert status == PublishStatus.DEFERRED
    assert "Publishing Lock" in reason


@pytest.mark.asyncio
async def test_job_cancel_and_retry_operations(control_test_env):
    """Verifies cooperative job cancellation and requeue retry operations."""
    state_repo = control_test_env["state_repo"]
    ctrl_service = control_test_env["ctrl_service"]

    job_id = "job_ctrl_test_01"
    await state_repo.create_job(
        job_id=job_id,
        campaign_id="cmp_01",
        source_video_id="src_01",
        idempotency_key="idemp_01",
    )

    # Cancel job
    await ctrl_service.cancel_job(job_id=job_id, operator="Director", reason="Operator aborted run")
    cancelled_job = await state_repo.get_job(job_id)
    assert cancelled_job.current_state == JobState.FAILED

    # Retry / requeue job
    await ctrl_service.retry_job(job_id=job_id, operator="Director", reason="Re-running failed job")
    requeued_job = await state_repo.get_job(job_id)
    assert requeued_job.current_state == JobState.CREATED


@pytest.mark.asyncio
async def test_operator_token_authorization(monkeypatch, control_test_env):
    """
    Verifies that mutating endpoints reject unauthorized callers with HTTP 401
    and permit callers providing the valid Operator Token.
    """
    secret = "secret-token-xyz-123"
    monkeypatch.setenv("OPERATOR_TOKEN", secret)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Read operations remain accessible without token
        status_resp = await client.get("/api/system/status")
        assert status_resp.status_code == 200
        data = status_resp.json()
        assert data["pipeline"]["stage_count"] == 9

        ctrl_resp = await client.get("/api/control/state")
        assert ctrl_resp.status_code == 200

        # 2. Mutating operation without token -> 401 Unauthorized
        stop_fail = await client.post(
            "/api/control/emergency-stop",
            json={"reason": "Testing unauthorized stop"},
        )
        assert stop_fail.status_code == 401

        # 3. Mutating operation with incorrect token -> 401 Unauthorized
        stop_bad = await client.post(
            "/api/control/emergency-stop",
            headers={"X-Operator-Token": "wrong-token"},
            json={"reason": "Testing bad token"},
        )
        assert stop_bad.status_code == 401

        # 4. Mutating operation with valid token -> 200 OK
        stop_ok = await client.post(
            "/api/control/emergency-stop",
            headers={"X-Operator-Token": secret},
            json={"reason": "Authorized emergency stop test"},
        )
        assert stop_ok.status_code == 200
        assert stop_ok.json()["status"] == "success"
        assert stop_ok.json()["control_state"]["emergency_stopped"] is True

        # 5. Resume with Bearer header -> 200 OK
        resume_ok = await client.post(
            "/api/control/resume",
            headers={"Authorization": f"Bearer {secret}"},
            json={"reason": "Authorized resume test"},
        )
        assert resume_ok.status_code == 200
        assert resume_ok.json()["control_state"]["emergency_stopped"] is False
