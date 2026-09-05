"""Targeted Validation for Phase 3: Autonomous Browser + Campaign Operations."""

import pytest
import pytest_asyncio
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from clipping.agent.account.capability import AccountManagementCapability
from clipping.agent.bridge.campaign_clipping_bridge import CampaignClippingBridge
from clipping.agent.browser.capability import BrowserAutomationCapability
from clipping.agent.browser.driver import MockBrowserDriver
from clipping.agent.browser.models import BrowserAction, BrowserActionType
from clipping.agent.campaign.decision import CampaignDecisionEngine
from clipping.agent.campaign.discovery import CampaignDiscoveryCapability
from clipping.agent.campaign.models import (
    CampaignPlatform,
    CampaignRecord,
    CampaignStatus,
    PostingRequirements,
)
from clipping.agent.campaign.repository import CampaignRepository
from clipping.agent.capabilities.base import CapabilityContext
from clipping.agent.capabilities.registry import CapabilityRegistry
from clipping.agent.cloud.queue import CloudTaskQueue
from clipping.agent.escalation import EscalationReason
from clipping.agent.loop import AutonomousOperationsLoop
from clipping.agent.models import AgentTask, TaskPriority, TaskType
from clipping.agent.policy import PolicyDecisionType, PolicyEngine, PolicyRule
from clipping.agent.repository import TaskRepository
from clipping.agent.vault.models import AccountMetadata, AccountPlatform, AccountStatus
from clipping.agent.vault.vault import EncryptedCredentialVault
from clipping.control.models import SystemControlState, SystemOperatingMode
from clipping.control.repository import ControlRepository
from clipping.storage.local import LocalStorageDriver
from clipping.ui.server import app, get_storage_driver


@pytest_asyncio.fixture
async def phase3_env(tmp_path: Path):
    storage = LocalStorageDriver(root_dir=str(tmp_path / "storage"))
    vault = EncryptedCredentialVault(storage_driver=storage, master_key="test_master_key_12345")
    task_repo = TaskRepository(storage_driver=storage)
    queue = CloudTaskQueue(storage_driver=storage)
    control_repo = ControlRepository(storage_driver=storage)
    await control_repo.save_state(SystemControlState(mode=SystemOperatingMode.OPERATIONAL))

    policy = PolicyEngine(
        default_decision=PolicyDecisionType.ALLOW,
        rules=[
            PolicyRule(
                rule_id="CONFIRM_ACCOUNT_CREATION",
                description="Require confirmation to create new external channel",
                capability_pattern="account_management",
                action_pattern="create_channel",
                decision=PolicyDecisionType.REQUIRE_CONFIRMATION,
                requires_human_confirmation=True,
            )
        ],
    )

    campaign_repo = CampaignRepository(storage_driver=storage)
    mock_browser = MockBrowserDriver()
    browser_cap = BrowserAutomationCapability(driver=mock_browser)
    discovery_cap = CampaignDiscoveryCapability(repository=campaign_repo, browser_driver=mock_browser)
    decision_engine = CampaignDecisionEngine(vault=vault, policy_engine=policy)
    bridge = CampaignClippingBridge(queue=queue, task_repository=task_repo)

    registry = CapabilityRegistry()
    registry.register(browser_cap)
    registry.register(discovery_cap)
    account_cap = AccountManagementCapability(vault=vault)
    registry.register(account_cap)

    loop = AutonomousOperationsLoop(
        discovery_capability=discovery_cap,
        campaign_repository=campaign_repo,
        decision_engine=decision_engine,
        clipping_bridge=bridge,
        task_repository=task_repo,
        storage_driver=storage,
    )

    return {
        "storage": storage,
        "vault": vault,
        "task_repo": task_repo,
        "queue": queue,
        "control_repo": control_repo,
        "policy": policy,
        "campaign_repo": campaign_repo,
        "mock_browser": mock_browser,
        "browser_cap": browser_cap,
        "discovery_cap": discovery_cap,
        "decision_engine": decision_engine,
        "bridge": bridge,
        "account_cap": account_cap,
        "registry": registry,
        "loop": loop,
    }


# 1. Browser capability registration and execution
@pytest.mark.asyncio
async def test_01_browser_capability_execution(phase3_env):
    """1. Browser capability executes navigation, extracting text content."""
    env = phase3_env
    browser = env["browser_cap"]
    mock = env["mock_browser"]
    mock.register_mock_page(
        "https://example.com/campaigns",
        "Example Campaigns",
        "Active campaigns for creators: Tech, Gaming, Lifestyle",
    )

    context = CapabilityContext(
        task_id="t_browser_01",
        inputs={
            "actions": [
                {"action_type": "navigate", "url": "https://example.com/campaigns"},
            ]
        },
        storage_driver=env["storage"],
    )

    result = await browser.execute(context)
    assert result.success is True
    assert result.outputs["current_url"] == "https://example.com/campaigns"
    assert "Tech, Gaming" in result.outputs["text_content"]


# 2. Browser challenge escalation
@pytest.mark.asyncio
async def test_02_browser_challenge_escalation(phase3_env):
    """2. Browser encounters CAPTCHA challenge and halts with immediate escalation."""
    env = phase3_env
    browser = env["browser_cap"]
    mock = env["mock_browser"]
    mock.simulate_captcha = True

    context = CapabilityContext(
        task_id="t_browser_challenge",
        inputs={
            "actions": [
                {"action_type": "navigate", "url": "https://protected-site.com/login"},
            ]
        },
        storage_driver=env["storage"],
    )

    result = await browser.execute(context)
    assert result.success is False
    assert result.escalation_required is True
    assert result.escalation_context.reason == EscalationReason.CAPTCHA_CHALLENGE


# 3. Encrypted Credential Vault
@pytest.mark.asyncio
async def test_03_encrypted_credential_vault(phase3_env):
    """3. Vault stores public metadata safely while encrypting sensitive secrets with Fernet."""
    vault = phase3_env["vault"]

    meta = AccountMetadata(
        platform=AccountPlatform.YOUTUBE,
        account_id="yt_channel_alpha",
        username="AlphaCreator",
        display_name="Alpha Tech Channel",
        status=AccountStatus.ACTIVE,
        reuse_eligibility=True,
    )
    secrets = {"oauth_refresh_token": "secret_token_xyz_987", "client_secret": "sensitive_pwd"}

    await vault.save_account(meta, sensitive_credentials=secrets)

    # 1. Public metadata returns zero sensitive keys
    loaded_meta = await vault.get_account_metadata(AccountPlatform.YOUTUBE, "yt_channel_alpha")
    assert loaded_meta is not None
    assert loaded_meta.username == "AlphaCreator"
    safe_dict = loaded_meta.to_safe_dict()
    assert "oauth_refresh_token" not in safe_dict
    assert "sensitive_pwd" not in safe_dict

    # 2. Encrypted secrets can be retrieved in memory by authorized callers
    loaded_secrets = await vault.get_account_credentials(AccountPlatform.YOUTUBE, "yt_channel_alpha")
    assert loaded_secrets["oauth_refresh_token"] == "secret_token_xyz_987"

    # 3. Raw file on storage driver is encrypted ciphertext
    raw_enc = await phase3_env["storage"].download_bytes("vault/accounts/youtube/yt_channel_alpha/secret.enc")
    assert b"secret_token_xyz_987" not in raw_enc  # Ciphertext verification


# 4. Campaign Discovery and Deduplication
@pytest.mark.asyncio
async def test_04_campaign_discovery_and_deduplication(phase3_env):
    """4. Campaign discovery normalizes records and handles updates without duplication."""
    discovery = phase3_env["discovery_cap"]
    repo = phase3_env["campaign_repo"]

    raw_campaign = {
        "campaign_id": "camp_ai_news_01",
        "name": "AI Daily News Highlights",
        "required_platforms": ["youtube_shorts"],
        "posting_requirements": {"min_duration_seconds": 30.0, "max_duration_seconds": 60.0},
        "discovered_source_uris": ["https://www.youtube.com/watch?v=real_source_123"],
    }

    context = CapabilityContext(
        task_id="t_disc_01",
        inputs={"campaigns": [raw_campaign]},
        storage_driver=phase3_env["storage"],
    )

    res1 = await discovery.execute(context)
    assert res1.success is True
    assert "camp_ai_news_01" in res1.outputs["campaign_ids"]

    # Re-run same discovery: should update rather than corrupt index
    res2 = await discovery.execute(context)
    assert res2.success is True

    campaigns = await repo.list_campaigns()
    assert len(campaigns) == 1
    assert campaigns[0].campaign_id == "camp_ai_news_01"


# 5. Campaign Contradiction Escalation
@pytest.mark.asyncio
async def test_05_campaign_contradiction_escalation(phase3_env):
    """5. Contradictory rules (e.g. min duration > max duration) trigger operator escalation."""
    discovery = phase3_env["discovery_cap"]

    bad_campaign = {
        "campaign_id": "camp_broken_01",
        "name": "Broken Campaign",
        "posting_requirements": {"min_duration_seconds": 90.0, "max_duration_seconds": 30.0},  # Impossible
    }

    context = CapabilityContext(
        task_id="t_disc_broken",
        inputs={"campaigns": [bad_campaign]},
        storage_driver=phase3_env["storage"],
    )

    result = await discovery.execute(context)
    assert result.success is False
    assert result.escalation_required is True
    assert result.escalation_context.reason == EscalationReason.CONTRADICTORY_INSTRUCTIONS


# 6. Campaign Decision Engine: Routine Approval
@pytest.mark.asyncio
async def test_06_campaign_decision_engine_routine_approval(phase3_env):
    """6. Active campaign with eligible vault account is automatically approved."""
    decision_engine = phase3_env["decision_engine"]
    vault = phase3_env["vault"]

    # Seed eligible account in vault
    await vault.save_account(
        AccountMetadata(
            platform=AccountPlatform.YOUTUBE,
            account_id="yt_active_01",
            username="ActiveTechChannel",
            status=AccountStatus.ACTIVE,
            reuse_eligibility=True,
        )
    )

    campaign = CampaignRecord(
        campaign_id="camp_approved_01",
        name="Auto Approved Campaign",
        source="https://campaigns.org/item1",
        status=CampaignStatus.ACTIVE,
        required_platforms=[CampaignPlatform.YOUTUBE_SHORTS],
        discovered_source_uris=["https://www.youtube.com/watch?v=valid_source_1"],
    )

    decision = await decision_engine.evaluate_campaign_for_execution(campaign)
    assert decision.is_approved is True
    assert decision.selected_account_id == "yt_active_01"
    assert decision.selected_source_uri == "https://www.youtube.com/watch?v=valid_source_1"
    assert decision.escalation_required is False


# 7. Campaign Decision Engine: Exception Escalation
@pytest.mark.asyncio
async def test_07_campaign_decision_engine_exception_escalation(phase3_env):
    """7. Missing eligible account when creation requires confirmation triggers escalation."""
    decision_engine = phase3_env["decision_engine"]

    # Empty vault -> no eligible account
    campaign = CampaignRecord(
        campaign_id="camp_need_account",
        name="Needs Account Campaign",
        source="https://campaigns.org/item2",
        status=CampaignStatus.ACTIVE,
        required_platforms=[CampaignPlatform.YOUTUBE_SHORTS],
    )

    decision = await decision_engine.evaluate_campaign_for_execution(campaign)
    assert decision.is_approved is False
    assert decision.escalation_required is True
    assert decision.escalation_context.reason == EscalationReason.POLICY_VIOLATION


# 8. Account Operations Capability
@pytest.mark.asyncio
async def test_08_account_management_capability(phase3_env):
    """8. Account management configures channel profile and enforces bounds."""
    account_cap = phase3_env["account_cap"]
    vault = phase3_env["vault"]

    # 1. Create channel record
    ctx_create = CapabilityContext(
        task_id="t_acc_01",
        inputs={
            "action": "create_channel_record",
            "platform": "youtube",
            "username": "AI_Clips_Official",
            "campaign_id": "camp_ai_news_01",
        },
        storage_driver=phase3_env["storage"],
    )
    res_create = await account_cap.execute(ctx_create)
    assert res_create.success is True

    # 2. Update profile
    ctx_update = CapabilityContext(
        task_id="t_acc_02",
        inputs={
            "action": "configure_profile",
            "platform": "youtube",
            "account_id": "acc_youtube_ai_clips_official",
            "display_name": "AI Clips Daily",
            "tags": ["ai", "tech", "shorts"],
        },
        storage_driver=phase3_env["storage"],
    )
    res_update = await account_cap.execute(ctx_update)
    assert res_update.success is True
    assert res_update.outputs["account"]["display_name"] == "AI Clips Daily"


# 9. Browser -> Clipping Bridge
@pytest.mark.asyncio
async def test_09_campaign_to_clipping_bridge(phase3_env):
    """9. Bridge builds a genuine MEDIA_CLIPPING task for production pipeline."""
    bridge = phase3_env["bridge"]
    queue = phase3_env["queue"]
    task_repo = phase3_env["task_repo"]

    campaign = CampaignRecord(
        campaign_id="camp_bridge_test",
        name="Bridge Test Campaign",
        source="https://example.com",
        required_platforms=[CampaignPlatform.YOUTUBE_SHORTS],
    )

    task = await bridge.create_and_enqueue_clipping_job(
        campaign=campaign,
        source_uri="https://www.youtube.com/watch?v=sample_video_real",
        account_id="acc_yt_01",
    )

    assert task.task_type == TaskType.MEDIA_CLIPPING
    assert task.inputs["capability"] == "media_clipping"
    assert task.inputs["source_uri"] == "https://www.youtube.com/watch?v=sample_video_real"
    assert task.inputs["campaign_id"] == "camp_bridge_test"

    # Verify task is queued in CloudTaskQueue
    item = await queue.get_item(task.task_id)
    assert item is not None
    assert item.status.value == "pending"


# 10. Autonomous Operations Loop
@pytest.mark.asyncio
async def test_10_autonomous_operations_loop(phase3_env):
    """10. Full loop discovers campaigns, evaluates eligibility, and dispatches clipping tasks."""
    loop = phase3_env["loop"]
    vault = phase3_env["vault"]

    # Seed an account
    await vault.save_account(
        AccountMetadata(
            platform=AccountPlatform.YOUTUBE,
            account_id="yt_channel_loop",
            username="LoopChannel",
            status=AccountStatus.ACTIVE,
            reuse_eligibility=True,
        )
    )

    raw_campaigns = [
        {
            "campaign_id": "camp_loop_01",
            "name": "Loop Discovered Campaign",
            "required_platforms": ["youtube_shorts"],
            "discovered_source_uris": ["https://www.youtube.com/watch?v=loop_source_video"],
        }
    ]

    cycle_res = await loop.run_discovery_and_dispatch_cycle(raw_campaigns=raw_campaigns)
    assert cycle_res["cycle_status"] == "completed"
    assert cycle_res["campaigns_discovered"] == 1
    assert len(cycle_res["tasks_enqueued"]) == 1


# 11. Mission Control Backend Endpoints
@pytest.mark.asyncio
async def test_11_dashboard_backend_endpoints(phase3_env):
    """11. UI backend exposes mission control data without exposing sensitive secrets."""
    storage = phase3_env["storage"]
    app.dependency_overrides[get_storage_driver] = lambda: storage

    # Add dummy account and campaign into storage
    vault = phase3_env["vault"]
    await vault.save_account(
        AccountMetadata(
            platform=AccountPlatform.YOUTUBE,
            account_id="yt_console_01",
            username="ConsoleUser",
        ),
        sensitive_credentials={"password": "TOP_SECRET_NEVER_EXPOSE"},
    )
    camp_repo = phase3_env["campaign_repo"]
    await camp_repo.save_campaign(
        CampaignRecord(
            campaign_id="camp_console_01",
            name="Console Campaign",
            source="https://console.org",
        )
    )

    client = TestClient(app)

    # 1. Agent Status
    r_status = client.get("/api/agent/status")
    assert r_status.status_code == 200
    assert r_status.json()["status"] == "operational"

    # 2. Campaigns
    r_camps = client.get("/api/campaigns")
    assert r_camps.status_code == 200
    assert len(r_camps.json()) >= 1
    assert r_camps.json()[0]["campaign_id"] == "camp_console_01"

    # 3. Accounts (Confirm zero secret leakage)
    r_accs = client.get("/api/accounts")
    assert r_accs.status_code == 200
    assert len(r_accs.json()) >= 1
    assert r_accs.json()[0]["username"] == "ConsoleUser"
    assert "TOP_SECRET" not in r_accs.text

    # 4. Mission Control Overview
    r_overview = client.get("/api/mission-control/overview")
    assert r_overview.status_code == 200
    data = r_overview.json()
    assert data["campaigns_count"] >= 1
    assert data["accounts_count"] >= 1
    app.dependency_overrides.clear()


# 12. Cloud Worker Execution of Browser Capability (Step 12 Cloud Validation)
@pytest.mark.asyncio
async def test_12_cloud_worker_executes_browser_capability(phase3_env):
    """12. Headless CloudAgentWorker claims and executes browser task through durable queue."""
    from clipping.agent.cloud.worker import CloudAgentWorker
    from clipping.agent.cloud.lease import WorkerLeaseEngine
    from clipping.agent.cloud.telemetry import CloudTelemetryEngine
    from clipping.agent.cloud.limits import CloudResourceLimits
    from clipping.agent.events import AgentEventSystem

    storage = phase3_env["storage"]
    queue = phase3_env["queue"]
    task_repo = phase3_env["task_repo"]
    registry = phase3_env["registry"]
    policy = phase3_env["policy"]
    control_repo = phase3_env["control_repo"]
    lease_engine = WorkerLeaseEngine(storage_driver=storage)
    telemetry = CloudTelemetryEngine(storage_driver=storage)
    event_system = AgentEventSystem(storage_driver=storage)
    limits = CloudResourceLimits()

    mock = phase3_env["mock_browser"]
    mock.register_mock_page(
        "https://cloud-campaign-source.internal/list",
        "Cloud Campaigns",
        "Headless Cloud Worker Discovery Payload",
    )

    worker = CloudAgentWorker(
        worker_id="cloud_worker_headless_01",
        task_repository=task_repo,
        queue=queue,
        capabilities=registry,
        policy_engine=policy,
        event_system=event_system,
        control_repository=control_repo,
        lease_engine=lease_engine,
        telemetry=telemetry,
        storage_driver=storage,
        limits=limits,
        heartbeat_interval_seconds=0.1,
        lease_ttl_seconds=5,
    )

    # Enqueue a browser operation task into the Cloud Task Queue
    task = AgentTask(
        task_id="t_cloud_browser_exec",
        task_type=TaskType.BROWSER_OPERATION,
        objective="Extract campaign list in headless cloud worker",
        inputs={
            "capability": "browser_operation",
            "actions": [
                {"action_type": "navigate", "url": "https://cloud-campaign-source.internal/list"},
            ],
        },
        priority=TaskPriority.HIGH,
    )
    await task_repo.save_task(task)
    await queue.enqueue(task.task_id, priority=int(TaskPriority.HIGH))

    # Worker claims and executes task in isolated cloud workspace
    completed_task = await worker.run_next_task()
    assert completed_task is not None
    assert completed_task.task_id == "t_cloud_browser_exec"
    assert completed_task.status.value == "succeeded"
    assert completed_task.outputs["current_url"] == "https://cloud-campaign-source.internal/list"
    assert "Headless Cloud Worker Discovery" in completed_task.outputs["text_content"]

    # Verify queue marked completed
    item = await queue.get_item("t_cloud_browser_exec")
    assert item.status.value == "completed"
