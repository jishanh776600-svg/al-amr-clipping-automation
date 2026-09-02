"""Unit tests for Publishing Gates and OAuth Token Management."""

import pytest
from pydantic import SecretStr
from clipping.approval.models import ApprovalRequest, ApprovalStatus
from clipping.approval.repository import ApprovalRepository
from clipping.contracts.qa import QAReport, QACheckStatus
from clipping.publishing.gates import PublishingGateEnforcer
from clipping.publishing.models import PublishStatus
from clipping.publishing.oauth import OAuthCredentials, OAuthTokenManager
from clipping.storage.local import LocalStorageDriver


@pytest.fixture
def gate_setup(temp_vault_dir):
    storage = LocalStorageDriver(root_dir=temp_vault_dir)
    app_repo = ApprovalRepository(storage_driver=storage)
    gates = PublishingGateEnforcer(approval_repository=app_repo, storage_driver=storage)
    return {
        "storage": storage,
        "app_repo": app_repo,
        "gates": gates,
    }


@pytest.mark.asyncio
async def test_approval_gate_states(gate_setup):
    app_repo = gate_setup["app_repo"]
    gates = gate_setup["gates"]

    # 1. Seed APPROVED request
    req_app = ApprovalRequest(
        approval_request_id="req_app_01",
        job_id="job_01",
        source_video_id="src_01",
        clip_id="clip_01",
        clip_index=1,
        title="Approved Hook",
        start_time=0.0,
        end_time=30.0,
        duration=30.0,
        score=90.0,
        video_storage_key="clips/clip_01/final.mp4",
        status=ApprovalStatus.APPROVED,
    )
    await app_repo.save_request(req_app)

    ok, status, reason = await gates.verify_approval_gate("job_01", "req_app_01")
    assert ok is True
    assert status == PublishStatus.READY

    # 2. Seed REJECTED request
    req_rej = req_app.model_copy(update={"approval_request_id": "req_rej_01", "status": ApprovalStatus.REJECTED})
    await app_repo.save_request(req_rej)

    ok, status, reason = await gates.verify_approval_gate("job_01", "req_rej_01")
    assert ok is False
    assert status == PublishStatus.SKIPPED

    # 3. Seed AWAITING request
    req_wait = req_app.model_copy(update={"approval_request_id": "req_wait_01", "status": ApprovalStatus.AWAITING_APPROVAL})
    await app_repo.save_request(req_wait)

    ok, status, reason = await gates.verify_approval_gate("job_01", "req_wait_01")
    assert ok is False
    assert status == PublishStatus.DEFERRED


@pytest.mark.asyncio
async def test_qa_gate(gate_setup):
    storage = gate_setup["storage"]
    gates = gate_setup["gates"]

    clip_id = "clip_qa_test"

    # 1. Missing QA report -> False
    ok, reason = await gates.verify_qa_gate(clip_id)
    assert ok is False
    assert "Missing QA report" in reason

    # 2. Seed QA report with can_publish = True
    report_pass = QAReport(
        clip_id=clip_id,
        source_video_id="src_01",
        overall_status=QACheckStatus.PASS,
        can_publish=True,
    )
    await storage.upload_bytes(
        report_pass.model_dump_json().encode("utf-8"),
        f"clips/{clip_id}/qa_report.json",
        content_type="application/json",
    )

    ok, reason = await gates.verify_qa_gate(clip_id)
    assert ok is True
    assert "passed" in reason

    # 3. Seed QA report with can_publish = False
    report_fail = QAReport(
        clip_id=clip_id,
        source_video_id="src_01",
        overall_status=QACheckStatus.FAIL,
        can_publish=False,
    )
    await storage.upload_bytes(
        report_fail.model_dump_json().encode("utf-8"),
        f"clips/{clip_id}/qa_report.json",
        content_type="application/json",
    )

    ok, reason = await gates.verify_qa_gate(clip_id)
    assert ok is False
    assert "blocked" in reason


def test_oauth_credentials_security():
    creds = OAuthCredentials(
        client_id="test_client_id.apps.googleusercontent.com",
        client_secret=SecretStr("super_secret_client_secret"),
        refresh_token=SecretStr("1//04test_refresh_token"),
    )
    # Ensure SecretStr masks values in repr/str
    assert "super_secret_client_secret" not in str(creds)
    assert "1//04test_refresh_token" not in str(creds)
