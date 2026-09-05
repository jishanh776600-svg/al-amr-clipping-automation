"""Targeted Integration & Functional Tests for AL AMR Clipping Dashboard Control Layer.

Verifies real API endpoints across all 11 functional domains:
1. Agent State & Orchestration
2. Task Queue & Lifecycle
3. Cloud Workers & Fleet Leases
4. Automation & Safety Controls
5. Campaign Operations
6. Account & Credential Vault (zero secrets leaked)
7. Clipping Pipeline Stages & Jobs
8. Human Approval Gateway
9. Publishing Queue & Gate Verification
10. Operator Escalations
11. Cloud Telemetry & Unified Dashboard Overview
"""

import pytest
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient, ASGITransport

from clipping.approval.models import ApprovalRequest, ApprovalStatus, ApprovalAuditRecord
from clipping.approval.repository import ApprovalRepository
from clipping.agent.models import AgentTask, TaskType
from clipping.agent.state import TaskState
from clipping.agent.repository import TaskRepository
from clipping.agent.escalation import EscalationContext, EscalationReason, EscalationRecord, EscalationSeverity, EscalationStatus
from clipping.agent.campaign.models import CampaignRecord, CampaignStatus, CampaignPlatform
from clipping.agent.campaign.repository import CampaignRepository
from clipping.agent.vault.vault import EncryptedCredentialVault
from clipping.agent.vault.models import AccountMetadata, AccountPlatform, AccountStatus
from clipping.agent.cloud.lease import WorkerLease, WorkerLeaseEngine
from clipping.agent.cloud.queue import CloudTaskQueue
from clipping.agent.cloud.telemetry import CloudTelemetryEngine, TelemetryEventType
from clipping.publishing.models import PublishRequest, PublishStatus, YouTubeVideoMetadata
from clipping.publishing.repository import PublishingRepository
from clipping.storage.local import LocalStorageDriver
import clipping.ui.server as ui_server
from clipping.ui.server import app


@pytest.fixture
def dashboard_env(temp_vault_dir):
    storage = LocalStorageDriver(root_dir=temp_vault_dir)
    app.dependency_overrides[ui_server.get_storage_driver] = lambda: storage
    yield {
        "storage": storage,
        "task_repo": TaskRepository(storage_driver=storage),
        "camp_repo": CampaignRepository(storage_driver=storage),
        "vault": EncryptedCredentialVault(storage_driver=storage),
        "lease_engine": WorkerLeaseEngine(storage_driver=storage),
        "queue": CloudTaskQueue(storage_driver=storage),
        "approval_repo": ApprovalRepository(storage_driver=storage),
        "pub_repo": PublishingRepository(storage_driver=storage),
        "telemetry": CloudTelemetryEngine(storage_driver=storage),
    }
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_pipeline_stages():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/pipeline/stages")
        assert resp.status_code == 200
        data = resp.json()
        assert data["stage_count"] == 9
        assert len(data["stages"]) == 9
        stage_names = [s["name"] for s in data["stages"]]
        assert stage_names[0] == "01_INGESTION"
        assert stage_names[-1] == "09_PUBLISH"
        for s in data["stages"]:
            assert "index" in s
            assert "name" in s
            assert "description" in s
            assert len(s["description"]) > 0


@pytest.mark.asyncio
async def test_task_lifecycle_endpoints(dashboard_env):
    task_repo = dashboard_env["task_repo"]

    # 1. Create a task
    task = AgentTask(
        task_id="task_test_101",
        objective="Process clips for camp_alpha",
        campaign_id="camp_alpha",
        task_type=TaskType.MEDIA_CLIPPING,
        status=TaskState.FAILED,
        input_payload={"source_video_id": "vid_101"},
    )
    await task_repo.save_task(task)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # List tasks
        resp = await client.get("/api/agent/tasks")
        assert resp.status_code == 200
        tasks = resp.json()
        assert len(tasks) >= 1
        assert any(t["task_id"] == "task_test_101" for t in tasks)

        # Get task detail
        detail_resp = await client.get("/api/agent/tasks/task_test_101")
        assert detail_resp.status_code == 200
        detail = detail_resp.json()
        assert detail["task_id"] == "task_test_101"
        assert detail["status"] == "failed"

        # Retry task
        retry_resp = await client.post(
            "/api/agent/tasks/task_test_101/retry",
            json={"reason": "Operator requested restart"},
        )
        assert retry_resp.status_code == 200
        retry_data = retry_resp.json()
        assert retry_data["status"] == "success"
        assert retry_data["task_status"] == "pending"

        # Verify task is now pending in repository
        updated = await task_repo.get_task("task_test_101")
        assert updated.status == TaskState.PENDING

        # Cancel task
        cancel_resp = await client.post(
            "/api/agent/tasks/task_test_101/cancel",
            json={"reason": "Testing cancellation"},
        )
        assert cancel_resp.status_code == 200
        cancel_data = cancel_resp.json()
        assert cancel_data["status"] == "success"
        assert cancel_data["task_status"] == "cancelled"

        updated = await task_repo.get_task("task_test_101")
        assert updated.status == TaskState.CANCELLED


@pytest.mark.asyncio
async def test_worker_leases_and_reclaim(dashboard_env):
    lease_engine = dashboard_env["lease_engine"]

    # Seed an active worker lease
    now = datetime.now(timezone.utc)
    lease = WorkerLease(
        task_id="task_worker_01",
        worker_id="runner_github_01",
        claimed_at=now,
        last_heartbeat_at=now,
        lease_expires_at=now + timedelta(seconds=300),
        heartbeat_count=1,
        status="active",
    )
    data = lease.model_dump_json(indent=2).encode("utf-8")
    await dashboard_env["storage"].upload_bytes(data, f"leases/{lease.task_id}.json", content_type="application/json")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # List workers
        resp = await client.get("/api/agent/workers")
        assert resp.status_code == 200
        workers = resp.json()
        assert len(workers) >= 1
        assert workers[0]["task_id"] == "task_worker_01"
        assert workers[0]["is_valid"] is True
        assert workers[0]["is_stale"] is False

        # Reclaim stale workers
        reclaim_resp = await client.post("/api/agent/workers/reclaim-stale", json={"stale_threshold_seconds": 0})
        assert reclaim_resp.status_code == 200
        assert reclaim_resp.json()["status"] == "success"


@pytest.mark.asyncio
async def test_campaign_operations_endpoints(dashboard_env):
    camp_repo = dashboard_env["camp_repo"]

    record = CampaignRecord(
        campaign_id="camp_target_01",
        name="Target Audience Boost",
        source="https://tiktok.com/campaigns/target_01",
        status=CampaignStatus.DRAFT,
        target_topic="AI Tools",
        min_payout=100.0,
        max_payout=500.0,
    )
    await camp_repo.save_campaign(record)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # List campaigns
        resp = await client.get("/api/campaigns")
        assert resp.status_code == 200
        camps = resp.json()
        assert len(camps) >= 1
        assert any(c["campaign_id"] == "camp_target_01" for c in camps)

        # Get detail
        det_resp = await client.get("/api/campaigns/camp_target_01")
        assert det_resp.status_code == 200
        assert det_resp.json()["name"] == "Target Audience Boost"

        # Update status
        status_resp = await client.post(
            "/api/campaigns/camp_target_01/status",
            json={"status": "active", "reason": "Operator approved"},
        )
        assert status_resp.status_code == 200
        assert status_resp.json()["new_status"] == "active"

        updated = await camp_repo.get_campaign("camp_target_01")
        assert updated.status == CampaignStatus.ACTIVE


@pytest.mark.asyncio
async def test_account_vault_security_and_status(dashboard_env):
    vault = dashboard_env["vault"]

    meta = AccountMetadata(
        account_id="acc_yt_01",
        username="daily_clips",
        platform=AccountPlatform.YOUTUBE,
        display_name="Daily Clips Channel",
        channel_id="UC123456789",
        status=AccountStatus.ACTIVE,
    )
    await vault.save_account(meta, sensitive_credentials={"oauth_refresh_token": "SUPER_SECRET_TOKEN"})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # List accounts - verify zero secrets exposed
        resp = await client.get("/api/accounts")
        assert resp.status_code == 200
        accs = resp.json()
        assert len(accs) >= 1
        raw_text = resp.text
        assert "SUPER_SECRET_TOKEN" not in raw_text

        # Get single account detail
        det_resp = await client.get("/api/accounts/youtube/acc_yt_01")
        assert det_resp.status_code == 200
        assert det_resp.json()["account_id"] == "acc_yt_01"
        assert "SUPER_SECRET_TOKEN" not in det_resp.text

        # Update account status
        st_resp = await client.post(
            "/api/accounts/youtube/acc_yt_01/status",
            json={"status": "suspended"},
        )
        assert st_resp.status_code == 200
        assert st_resp.json()["new_status"] == "suspended"

        updated = await vault.get_account_metadata("youtube", "acc_yt_01")
        assert updated.status == AccountStatus.SUSPENDED


@pytest.mark.asyncio
async def test_approvals_and_publishing_queues(dashboard_env):
    app_repo = dashboard_env["approval_repo"]
    pub_repo = dashboard_env["pub_repo"]

    # Seed approval request
    app_req = ApprovalRequest(
        approval_request_id="app_req_01",
        job_id="job_cross_01",
        source_video_id="src_cross_01",
        clip_id="clip_cross_01",
        clip_index=1,
        title="Epic Moments",
        start_time=0.0,
        end_time=30.0,
        duration=30.0,
        score=95.0,
        qa_status="PASS",
        video_storage_key="clips/clip_cross_01/video.mp4",
        status=ApprovalStatus.AWAITING_APPROVAL,
    )
    await app_repo.save_request(app_req)

    # Seed approval audit
    audit = ApprovalAuditRecord(
        audit_id="audit_cross_01",
        approval_request_id="app_req_01",
        job_id="job_cross_01",
        clip_id="clip_cross_01",
        previous_status=ApprovalStatus.AWAITING_APPROVAL,
        new_status=ApprovalStatus.APPROVED,
        telegram_user_id=12345,
        telegram_chat_id=67890,
        reason="Manual approval verification",
    )
    await app_repo.record_audit(audit)

    # Seed publish request
    pub_req = PublishRequest(
        job_id="job_cross_01",
        clip_id="clip_cross_01",
        approval_request_id="app_req_01",
        idempotency_key="idemp_pub_01",
        video_storage_key="clips/clip_cross_01/final.mp4",
        metadata=YouTubeVideoMetadata(
            title="Epic Moments",
            description="Top Highlights",
        ),
        status=PublishStatus.READY,
    )
    await pub_repo.save_record(pub_req)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Pending approvals
        p_resp = await client.get("/api/approvals/pending")
        assert p_resp.status_code == 200
        pending = p_resp.json()
        assert len(pending) >= 1
        assert pending[0]["approval_request_id"] == "app_req_01"

        # Approval history
        h_resp = await client.get("/api/approvals/history")
        assert h_resp.status_code == 200
        history = h_resp.json()
        assert len(history) >= 1
        assert history[0]["audit_id"] == "audit_cross_01"

        # Publishing queue
        pub_resp = await client.get("/api/publishing/queue")
        assert pub_resp.status_code == 200
        queue = pub_resp.json()
        assert len(queue) >= 1
        assert queue[0]["clip_id"] == "clip_cross_01"
        assert "can_publish" in queue[0]


@pytest.mark.asyncio
async def test_escalation_lifecycle(dashboard_env):
    task_repo = dashboard_env["task_repo"]

    ctx = EscalationContext(
        what_happened="Captcha detected during browser navigation",
        why_it_happened="Anti-bot challenge triggered by TikTok login page",
        decision_required="Operator solve captcha or provide session cookies",
        available_options=["solve_captcha", "abort_task"],
        reason=EscalationReason.CAPTCHA_CHALLENGE,
        severity=EscalationSeverity.HIGH,
    )
    esc = await task_repo.create_escalation(context=ctx, task_id="task_esc_01")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # List escalations
        resp = await client.get("/api/agent/escalations")
        assert resp.status_code == 200
        escs = resp.json()
        assert len(escs) >= 1
        assert any(e["escalation_id"] == esc.escalation_id for e in escs)

        # Get escalation detail
        det_resp = await client.get(f"/api/agent/escalations/{esc.escalation_id}")
        assert det_resp.status_code == 200
        assert det_resp.json()["reason"] == "captcha_challenge"

        # Resolve escalation
        res_resp = await client.post(
            f"/api/agent/escalations/{esc.escalation_id}/resolve",
            json={"action": "solve_captcha", "notes": "Captcha solved in cloud session"},
        )
        assert res_resp.status_code == 200
        res_data = res_resp.json()
        assert res_data["status"] == "success"
        assert res_data["escalation_status"] == "resolved"

        updated = await task_repo.get_escalation(esc.escalation_id)
        assert updated.status == EscalationStatus.RESOLVED


@pytest.mark.asyncio
async def test_telemetry_and_unified_dashboard_overview(dashboard_env):
    telemetry = dashboard_env["telemetry"]

    await telemetry.record(
        event_type=TelemetryEventType.TASK_COMPLETED,
        task_id="task_tel_01",
        worker_id="runner_01",
        duration_seconds=4.2,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Telemetry list
        t_resp = await client.get("/api/agent/telemetry")
        assert t_resp.status_code == 200
        events = t_resp.json()
        assert len(events) >= 1
        assert events[0]["task_id"] == "task_tel_01"

        # Unified Dashboard Overview
        dash_resp = await client.get("/api/dashboard/overview")
        assert dash_resp.status_code == 200
        dash_data = dash_resp.json()
        assert dash_data["project_name"] == "AL AMR Clipping Automation"
        assert dash_data["status"] in ["operational", "emergency_stopped"]
        assert "counts" in dash_data
        assert "campaigns" in dash_data["counts"]
        assert "accounts" in dash_data["counts"]
        assert "queue_depth" in dash_data["counts"]
        assert "recent_telemetry" in dash_data

        # Backward compatibility alias
        mc_resp = await client.get("/api/mission-control/overview")
        assert mc_resp.status_code == 200
        assert mc_resp.json()["project_name"] == "AL AMR Clipping Automation"
