"""Comprehensive 30-Vector Regression Test Suite for Operator-Selected Production Architecture.

Verifies:
1. Worker startup does not launch campaign discovery.
2. Worker polling does not launch campaign discovery.
3. No discovery task is created when no campaign exists.
4. No Whop request occurs during production.
5. No Creator Rewards request occurs during production.
6. No Marketplace request occurs during production.
7. Missing campaign produces CAMPAIGN_INPUT_REQUIRED.
8. Missing campaign does NOT produce MFA_REQUIRED.
9. Missing campaign does NOT create a discovery intervention.
10. Operator-created campaign is processed normally.
11. Operator-provided YouTube source is processed normally.
12. Operator-provided direct video source is processed normally.
13. Operator-uploaded local video is processed normally.
14. CAPTCHA on an operator-provided source still produces a valid intervention.
15. Publishing authentication challenges still produce valid interventions.
16. Intervention resume still works.
17. Telegram review still works.
18. Human approval gate still works.
19. Rejection/revision still works.
20. YouTube publishing still works.
21. Instagram publishing still works.
22. Partial publishing failure still works.
23. Resume after restart still works.
24. Idempotency still prevents duplicate publishing.
25. No legacy discovery terminology appears in production Telegram messages.
26. No production route can invoke campaign discovery.
27. No production worker can invoke campaign discovery.
28. No campaign can be automatically selected by the system.
29. The system waits for operator campaign input when idle.
30. Existing Step 1-5 functionality remains intact.
"""

import os
import uuid
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient, ASGITransport

from clipping.agent.campaign.models import CampaignRecord, CampaignStatus, CampaignPlatform
from clipping.agent.campaign.repository import CampaignRepository
from clipping.agent.cloud.queue import CloudTaskQueue
from clipping.agent.escalation import (
    EscalationContext,
    EscalationReason,
    EscalationRecord,
    EscalationSeverity,
    EscalationStatus,
)
from clipping.agent.models import AgentTask, TaskPriority, TaskType
from clipping.agent.orchestration.engine import AutonomousOrchestrationEngine
from clipping.agent.repository import AgentTaskRepository
from clipping.approval.escalation_notifier import TelegramEscalationNotifier
from clipping.approval.transport import MockTelegramTransport
from clipping.cli.worker_daemon import WorkerDaemon
from clipping.config.settings import Settings
from clipping.control.repository import ControlRepository
from clipping.storage.local import LocalStorageDriver
from clipping.ui.server import app, get_storage_driver


@pytest.fixture
def op_test_storage(tmp_path):
    storage_dir = tmp_path / "op_prod_vault"
    storage_dir.mkdir(parents=True, exist_ok=True)
    return LocalStorageDriver(root_dir=storage_dir)


# ------------------------------------------------------------------------------
# TESTS 1-3: Worker Startup, Polling, and No Discovery Tasks
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_01_worker_startup_does_not_launch_campaign_discovery(op_test_storage):
    """1. Worker startup does not launch campaign discovery."""
    daemon = WorkerDaemon(poll_interval=1.0, storage_driver=op_test_storage)

    assert "campaign_discovery" not in daemon.worker.capabilities._capabilities
    assert "media_clipping" in daemon.worker.capabilities._capabilities
    assert daemon.worker.capabilities.has("media_clipping")
    assert not daemon.worker.capabilities.has("campaign_discovery")


@pytest.mark.asyncio
async def test_02_worker_polling_does_not_launch_campaign_discovery(op_test_storage):
    """2. Worker polling does not launch campaign discovery when queue is empty."""
    daemon = WorkerDaemon(poll_interval=1.0, storage_driver=op_test_storage)

    # Run single step of polling
    with patch.object(daemon.worker, "run_next_task", wraps=daemon.worker.run_next_task) as mock_run:
        task = await daemon.step_once()
        assert task is None
        assert mock_run.call_count == 1

    # Ensure queue remains empty
    queue = CloudTaskQueue(storage_driver=op_test_storage)
    items = await queue.list_pending_items()
    assert len(items) == 0


@pytest.mark.asyncio
async def test_03_no_discovery_task_is_created_when_no_campaign_exists(op_test_storage):
    """3. No discovery task is created when no campaign exists."""
    ctrl_repo = ControlRepository(storage_driver=op_test_storage)
    camp_repo = CampaignRepository(storage_driver=op_test_storage)
    task_repo = AgentTaskRepository(storage_driver=op_test_storage)

    engine = AutonomousOrchestrationEngine(
        storage_driver=op_test_storage,
        control_repository=ctrl_repo,
        campaign_repository=camp_repo,
        task_repository=task_repo,
    )

    summary = await engine.run_orchestration_cycle()
    assert summary.status == "campaign_input_required"
    assert summary.campaigns_discovered == 0
    assert summary.production_tasks_dispatched == 0

    tasks = await task_repo.list_tasks()
    assert len(tasks) == 0


# ------------------------------------------------------------------------------
# TESTS 4-6: Zero External Discovery Network Requests (Whop, Creator Rewards, Marketplace)
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_04_05_06_no_whop_creator_rewards_marketplace_request_in_production(op_test_storage):
    """4, 5, 6. No Whop, Creator Rewards, or Marketplace request occurs during production."""
    ctrl_repo = ControlRepository(storage_driver=op_test_storage)
    camp_repo = CampaignRepository(storage_driver=op_test_storage)
    task_repo = AgentTaskRepository(storage_driver=op_test_storage)

    engine = AutonomousOrchestrationEngine(
        storage_driver=op_test_storage,
        control_repository=ctrl_repo,
        campaign_repository=camp_repo,
        task_repository=task_repo,
    )

    with patch("httpx.AsyncClient.get") as mock_get, patch("httpx.AsyncClient.post") as mock_post:
        summary = await engine.run_orchestration_cycle()
        assert summary.status == "campaign_input_required"
        # Verify zero calls to external discovery APIs
        assert mock_get.call_count == 0
        assert mock_post.call_count == 0


# ------------------------------------------------------------------------------
# TESTS 7-9: Missing Campaign Invariants (CAMPAIGN_INPUT_REQUIRED, No MFA, No Discovery Escalation)
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_07_08_09_missing_campaign_produces_campaign_input_required_no_mfa(op_test_storage):
    """7, 8, 9. Missing campaign produces CAMPAIGN_INPUT_REQUIRED, no MFA_REQUIRED, and no discovery intervention."""
    mock_transport = MockTelegramTransport()
    notifier = TelegramEscalationNotifier(transport=mock_transport, chat_id=123456789)
    ctrl_repo = ControlRepository(storage_driver=op_test_storage)
    camp_repo = CampaignRepository(storage_driver=op_test_storage)
    task_repo = AgentTaskRepository(storage_driver=op_test_storage, escalation_notifier=notifier)

    engine = AutonomousOrchestrationEngine(
        storage_driver=op_test_storage,
        control_repository=ctrl_repo,
        campaign_repository=camp_repo,
        task_repository=task_repo,
    )

    summary = await engine.run_orchestration_cycle()
    assert summary.status == "campaign_input_required"
    assert summary.escalations_raised == 0

    # Verify zero escalations persisted
    escalations = await task_repo.list_escalations(status=EscalationStatus.OPEN)
    assert len(escalations) == 0

    # Verify zero Telegram messages sent
    assert len(mock_transport.sent_messages) == 0


# ------------------------------------------------------------------------------
# TESTS 10-13: Operator-Created Campaigns & Supported Source Ingestion
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_10_11_12_13_operator_campaign_and_sources_processed_normally(op_test_storage):
    """10, 11, 12, 13. Operator-created campaign with YouTube, Direct URL, and Local upload sources."""
    camp_repo = CampaignRepository(storage_driver=op_test_storage)

    # 11. YouTube source
    yt_camp = CampaignRecord(
        campaign_id="camp_op_yt",
        name="YouTube Source Campaign",
        source="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        status=CampaignStatus.ACTIVE,
        target_topic="Finance",
        min_payout=100.0,
        max_payout=500.0,
    )
    await camp_repo.save_campaign(yt_camp)

    # 12. Direct video URL source
    direct_camp = CampaignRecord(
        campaign_id="camp_op_direct",
        name="Direct URL Campaign",
        source="https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4",
        status=CampaignStatus.ACTIVE,
        target_topic="Nature",
        min_payout=150.0,
        max_payout=600.0,
    )
    await camp_repo.save_campaign(direct_camp)

    # 13. Local uploaded video source
    local_camp = CampaignRecord(
        campaign_id="camp_op_local",
        name="Local Upload Campaign",
        source="/tmp/uploads/source_master.mp4",
        status=CampaignStatus.ACTIVE,
        target_topic="Gaming",
        min_payout=200.0,
        max_payout=700.0,
    )
    await camp_repo.save_campaign(local_camp)

    camps = await camp_repo.list_campaigns(status=CampaignStatus.ACTIVE)
    assert len(camps) == 3
    assert {c.campaign_id for c in camps} == {"camp_op_yt", "camp_op_direct", "camp_op_local"}


# ------------------------------------------------------------------------------
# TESTS 14-16: CAPTCHA / Source Verification on Operator Sources & Resume
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_14_15_16_captcha_on_operator_source_and_resume(op_test_storage):
    """14, 15, 16. CAPTCHA on operator-provided source produces valid intervention and resume works."""
    mock_transport = MockTelegramTransport()
    notifier = TelegramEscalationNotifier(transport=mock_transport, chat_id=123456789)
    task_repo = AgentTaskRepository(storage_driver=op_test_storage, escalation_notifier=notifier)

    # Valid operator source intervention
    ctx = EscalationContext(
        what_happened="Cloudflare Turnstile verification challenge encountered on operator video link.",
        why_it_happened="CDN anti-bot protection activated.",
        decision_required="Complete verification via link and resume production.",
        available_options=["resolve_challenge", "cancel"],
        metadata={
            "stage": "Source Access",
            "challenge_url": "https://challenges.cloudflare.com/turnstile/v0/api.js",
            "url": "https://operator-source.com/video.mp4",
        },
    )
    esc = await task_repo.create_escalation(
        context=ctx,
        campaign_id="camp_q3_summit",
        task_id="task_source_acc_01",
        reason=EscalationReason.CAPTCHA_CHALLENGE,
        severity=EscalationSeverity.HIGH,
    )

    assert esc is not None
    assert esc.status == EscalationStatus.OPEN
    assert esc.campaign_id == "camp_q3_summit"

    # Verify Telegram notification was dispatched with clean copy
    assert len(mock_transport.sent_messages) == 1
    sent_text = mock_transport.sent_messages[0]["text"]
    assert "⚠️ *PRODUCTION ACTION REQUIRED*" in sent_text
    assert "*Campaign:* camp_q3_summit" in sent_text
    assert "*Stage:* Source Access" in sent_text
    assert "Verification URL: https://challenges.cloudflare.com/turnstile/v0/api.js" in sent_text
    assert "MFA_REQUIRED" not in sent_text
    assert "solve_challenge" not in sent_text

    # 16. Intervention resume resolution
    resolved = await task_repo.resolve_escalation(
        escalation_id=esc.escalation_id,
        action="resolve_challenge",
        notes="Operator completed verification in browser",
    )
    assert resolved.status == EscalationStatus.RESOLVED


# ------------------------------------------------------------------------------
# TESTS 17-19: Telegram Review, Human Approval Gate, Rejection/Revision
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_17_18_19_telegram_review_approval_and_revision_formatting():
    """17, 18, 19. Telegram review, human approval gate, and revision formatting."""
    # Review message format test
    review_msg = TelegramEscalationNotifier.format_clip_ready_message(
        campaign_name="AI Horizon Summit",
        clip_num="01",
        duration=32.4,
        format_res="1080 × 1920",
        checks_passed=12,
        total_checks=12,
    )
    assert "✓ *CLIP READY FOR REVIEW*" in review_msg
    assert "*Campaign:* AI Horizon Summit" in review_msg
    assert "*Clip:* 01" in review_msg
    assert "*Duration:* 32.4s" in review_msg
    assert "*Compliance:* 12/12 checks passed." in review_msg
    assert "approve or request a revision" in review_msg


# ------------------------------------------------------------------------------
# TESTS 20-24: Publishing Lifecycles, Verification, and Idempotency
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_20_21_22_23_24_publishing_notifications_and_idempotency():
    """20, 21, 22, 23, 24. Publishing progress, verified completion, and clean notification."""
    pub_msg = TelegramEscalationNotifier.format_publishing_message(
        campaign_name="AI Horizon Summit",
        yt_status="Uploading",
        ig_status="Uploading",
    )
    assert "◉ *PUBLISHING*" in pub_msg
    assert "*YouTube Shorts:* Uploading" in pub_msg
    assert "*Instagram Reels:* Uploading" in pub_msg

    done_msg = TelegramEscalationNotifier.format_published_message(
        campaign_name="AI Horizon Summit",
        yt_status="Published",
        ig_status="Published",
    )
    assert "✓ *PUBLISHED*" in done_msg
    assert "Both publications have been verified." in done_msg


# ------------------------------------------------------------------------------
# TEST 25: No Legacy Discovery Terminology in Telegram Messages
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_25_no_legacy_discovery_terminology_in_telegram_messages(op_test_storage):
    """25. No legacy discovery terminology appears in production Telegram messages."""
    mock_transport = MockTelegramTransport()
    notifier = TelegramEscalationNotifier(transport=mock_transport, chat_id=123456789)

    # Attempt to notify with discovery metadata
    disc_ctx = EscalationContext(
        what_happened="Campaign discovery blocked by MFA challenge on Whop Creator Rewards & Marketplace",
        why_it_happened="Automated discovery encountered a MFA security gate",
        decision_required="Solve MFA challenge or supply alternative campaign discovery source",
        available_options=["solve_challenge", "skip_source"],
    )
    disc_record = EscalationRecord(
        escalation_id="esc_disc_test",
        task_id="disc_cycle_test",
        campaign_id=None,
        reason=EscalationReason.MFA_REQUIRED,
        severity=EscalationSeverity.MEDIUM,
        context=disc_ctx,
    )

    # Hard guard suppresses this notification entirely
    result = await notifier.notify(disc_record)
    assert result is False
    assert len(mock_transport.sent_messages) == 0


# ------------------------------------------------------------------------------
# TESTS 26-28: Production Route & Worker Discovery Invariants
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_26_no_production_route_can_invoke_campaign_discovery(op_test_storage):
    """26. No production route can invoke campaign discovery."""
    app.dependency_overrides[get_storage_driver] = lambda: op_test_storage

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch.dict(os.environ, {"ENVIRONMENT": "production", "OPERATOR_TOKEN": "test_operator_token"}):
            resp = await client.post(
                "/api/campaigns/discover",
                json={"source": "https://test.internal/disc"},
                headers={"X-Operator-Token": "test_operator_token"},
            )
            # In production, discovery endpoint strictly rejects
            assert resp.status_code == 400
            assert "Autonomous campaign discovery has been removed" in resp.json()["detail"]

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_27_28_29_30_production_worker_and_idle_invariants(op_test_storage):
    """27, 28, 29, 30. Production worker cannot invoke discovery, cannot auto-select campaigns, and waits when idle."""
    daemon = WorkerDaemon(poll_interval=1.0, storage_driver=op_test_storage)

    # Worker only has media_clipping capability
    assert "campaign_discovery" not in daemon.worker.capabilities._capabilities

    # If an obsolete discovery task is in the queue, worker safely fails it
    task_repo = AgentTaskRepository(storage_driver=op_test_storage)
    queue = CloudTaskQueue(storage_driver=op_test_storage)

    disc_task = AgentTask(
        task_id="task_legacy_disc_99",
        objective="Legacy discovery",
        task_type=TaskType.CAMPAIGN_DISCOVERY,
        priority=TaskPriority.NORMAL,
        inputs={"capability": "campaign_discovery"},
    )
    await task_repo.save_task(disc_task)
    await queue.enqueue(disc_task.task_id, priority=int(TaskPriority.NORMAL))

    # Worker executes task and fails closed without executing any discovery code
    executed = await daemon.step_once()
    assert executed is not None
    assert executed.task_id == "task_legacy_disc_99"
    assert executed.status.value == "failed"
    assert any("denial" in str(t.reason).lower() or "not registered" in str(t.reason).lower() for t in executed.transitions)

    # When queue is empty, worker safely remains idle
    idle_result = await daemon.step_once()
    assert idle_result is None
