"""Targeted Validation for Phase 6: Autonomous Orchestration Engine.

Validates:
1. Whop-first discovery, economic evaluation ($2 preferred CPM), and opportunity ranking/selection.
2. Crash recovery, state machine checkpoints, and idempotent resumption.
3. Master Control safety gates: emergency stop immediate abort and automation pause deferral.
4. End-to-end autonomous campaign lifecycle: discovery -> account resolution -> clipping dispatch -> QA -> submission/publishing -> reconciliation -> finalization.
5. Dashboard orchestration REST API endpoints.
"""

import pytest
import pytest_asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from httpx import AsyncClient, ASGITransport

from clipping.agent.account.lifecycle import AccountLifecycleService
from clipping.agent.bridge.campaign_clipping_bridge import CampaignClippingBridge
from clipping.agent.campaign.discovery import CampaignDiscoveryCapability
from clipping.agent.campaign.evaluator import CampaignEvaluator
from clipping.agent.campaign.models import (
    AccountRequirements,
    CampaignDuration,
    CampaignLifecycleState,
    CampaignPlatform,
    CampaignRecord,
    CampaignStatus,
    PayoutModel,
    PayoutTerms,
    PostingRequirements,
    QuotasAndCaps,
    SourceMaterial,
)
from clipping.agent.campaign.repository import CampaignRepository
from clipping.agent.campaign.sources.base import CampaignSource
from clipping.agent.campaign.sources.registry import CampaignSourceRegistry
from clipping.agent.capabilities.base import CapabilityContext
from clipping.agent.cloud.queue import CloudTaskQueue
from clipping.agent.models import AgentTask
from clipping.agent.orchestration.engine import AutonomousOrchestrationEngine
from clipping.agent.orchestration.models import (
    CampaignOrchestrationRecord,
    OrchestrationCycleSummary,
    OrchestrationStage,
)
from clipping.agent.orchestration.repository import OrchestrationRepository
from clipping.agent.policy import PolicyEngine
from clipping.agent.publishing.adapters.base import PlatformPublishingAdapter, PlatformPublishResult, PlatformStatusResult
from clipping.agent.publishing.capability import PublishingCapability
from clipping.agent.publishing.models import CampaignSubmissionRecord, SubmissionStatus
from clipping.agent.publishing.reconciliation import PublishingReconciliationService
from clipping.agent.publishing.repository import CampaignSubmissionRepository
from clipping.agent.repository import AgentTaskRepository
from clipping.agent.state import TaskState
from clipping.agent.vault.models import AccountMetadata, AccountPlatform, AccountStatus
from clipping.agent.vault.vault import EncryptedCredentialVault
from clipping.control.models import SystemControlState, SystemOperatingMode
from clipping.control.repository import ControlRepository
from clipping.storage.local import LocalStorageDriver
from clipping.ui.server import app


class MockWhopSource(CampaignSource):
    """Mock Whop source returning high-scoring ($2 CPM) campaigns."""
    @property
    def source_id(self) -> str:
        return "whop"

    @property
    def name(self) -> str:
        return "Whop Campaign Marketplace"

    @property
    def is_primary(self) -> bool:
        return True

    async def discover(
        self,
        query: Optional[str] = None,
        platform: Optional[str] = None,
        limit: int = 50,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        return [
            {
                "campaign_id": "whop_camp_sweetspot",
                "name": "Fintech App Virality Clips",
                "description": "Short punchy clips explaining fintech concepts",
                "cpm_rate": 2.0,  # Preferred $2 CPM
                "payout_model": "cpm",
                "total_budget": 10000.0,
                "remaining_budget": 7500.0,
                "allowed_platforms": ["youtube_shorts"],
                "source_video_uris": ["https://youtube.com/watch?v=real_fintech_source_101"],
                "daily_creator_limit": 3,
                "campaign_total_clip_cap": 50,
                "min_duration_seconds": 20.0,
                "max_duration_seconds": 60.0,
                "hashtags": ["#fintech", "#wealth"],
                "mentions": ["@FintechApp"],
                "status": "active",
            },
            {
                "campaign_id": "whop_camp_subcpm",
                "name": "Low Yield Campaign",
                "description": "Low reward campaign",
                "cpm_rate": 0.20,  # Sub-optimal <$1 CPM
                "payout_model": "cpm",
                "total_budget": 500.0,
                "remaining_budget": 50.0,
                "allowed_platforms": ["youtube_shorts"],
                "source_video_uris": ["https://youtube.com/watch?v=low_yield_source"],
                "min_duration_seconds": 20.0,
                "max_duration_seconds": 60.0,
                "status": "active",
            },
        ]

    async def fetch_campaign_detail(self, campaign_ref: str) -> Optional[Dict[str, Any]]:
        return None


class MockYouTubePublishingAdapter(PlatformPublishingAdapter):
    @property
    def platform(self) -> AccountPlatform:
        return AccountPlatform.YOUTUBE

    async def publish(self, submission: CampaignSubmissionRecord, media_path: str, credentials: Dict[str, Any]) -> PlatformPublishResult:
        return PlatformPublishResult(
            success=True,
            status=SubmissionStatus.PUBLISHED,
            platform_post_id="yt_short_mock_999",
            platform_url="https://youtube.com/shorts/yt_short_mock_999",
            raw_response={"status": "uploaded", "view_count": 0},
        )

    async def reconcile_status(self, platform_post_id: str, credentials: Dict[str, Any]) -> PlatformStatusResult:
        return PlatformStatusResult(
            post_id=platform_post_id,
            exists_on_platform=True,
            platform_status=SubmissionStatus.PUBLISHED,
            view_count=1250,
            raw_details={"view_count": 1250, "privacy": "public"},
        )


@pytest_asyncio.fixture
async def orch_env(tmp_path: Path):
    storage_dir = tmp_path / "storage"
    media_dir = tmp_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    storage = LocalStorageDriver(root_dir=str(storage_dir))

    vault = EncryptedCredentialVault(storage_driver=storage, master_key="test_master_key_orch_789")
    campaign_repo = CampaignRepository(storage_driver=storage)
    submission_repo = CampaignSubmissionRepository(storage_driver=storage)
    control_repo = ControlRepository(storage_driver=storage)
    task_repo = AgentTaskRepository(storage_driver=storage)
    orch_repo = OrchestrationRepository(storage_driver=storage)
    queue = CloudTaskQueue(storage_driver=storage)
    policy = PolicyEngine()
    evaluator = CampaignEvaluator(preferred_cpm=2.0, min_viable_cpm=1.0, max_target_cpm=5.0)

    # Pre-register YouTube channel account in vault
    test_acc = AccountMetadata(
        account_id="acc_yt_primary",
        platform=AccountPlatform.YOUTUBE,
        username="al_amr_clips_yt",
        display_name="AL AMR Clips Official",
        status=AccountStatus.ACTIVE,
        is_dedicated_to_campaign=False,
    )
    await vault.save_account(test_acc)

    # Setup source registry with MockWhopSource
    source_reg = CampaignSourceRegistry()
    mock_whop = MockWhopSource()
    source_reg.register(mock_whop)

    discovery_cap = CampaignDiscoveryCapability(
        repository=campaign_repo,
        source_registry=source_reg,
        evaluator=evaluator,
    )

    account_service = AccountLifecycleService(vault=vault, policy=policy)
    clipping_bridge = CampaignClippingBridge(queue=queue, task_repository=task_repo)

    yt_adapter = MockYouTubePublishingAdapter()
    adapters = {AccountPlatform.YOUTUBE: yt_adapter}

    pub_cap = PublishingCapability(
        submission_repository=submission_repo,
        campaign_repository=campaign_repo,
        vault=vault,
        control_repository=control_repo,
        policy_engine=policy,
        adapters=adapters,
    )

    reconciler = PublishingReconciliationService(
        repository=submission_repo,
        adapters=adapters,
        vault=vault,
    )

    engine = AutonomousOrchestrationEngine(
        storage_driver=storage,
        control_repository=control_repo,
        campaign_repository=campaign_repo,
        task_repository=task_repo,
        orchestration_repository=orch_repo,
        source_registry=source_reg,
        discovery_capability=discovery_cap,
        evaluator=evaluator,
        account_service=account_service,
        clipping_bridge=clipping_bridge,
        publishing_capability=pub_cap,
        reconciler=reconciler,
        policy_engine=policy,
        credential_vault=vault,
        task_queue=queue,
    )

    return {
        "storage": storage,
        "media_dir": media_dir,
        "engine": engine,
        "campaign_repo": campaign_repo,
        "submission_repo": submission_repo,
        "control_repo": control_repo,
        "task_repo": task_repo,
        "orch_repo": orch_repo,
        "vault": vault,
        "queue": queue,
    }


@pytest.mark.asyncio
async def test_orchestration_whop_discovery_and_economic_selection(orch_env):
    """Verifies Whop-first discovery, $2 preferred CPM scoring, and opportunity selection."""
    engine: AutonomousOrchestrationEngine = orch_env["engine"]
    campaign_repo: CampaignRepository = orch_env["campaign_repo"]
    orch_repo: OrchestrationRepository = orch_env["orch_repo"]

    # Run single orchestration cycle
    summary: OrchestrationCycleSummary = await engine.run_orchestration_cycle(
        source_name="whop",
        max_campaigns_to_process=2,
    )

    assert summary.status == "completed"
    assert summary.campaigns_discovered >= 2
    assert summary.campaigns_evaluated >= 2
    assert summary.opportunities_selected >= 1

    # Verify that the $2 CPM sweet spot campaign was ranked highest and selected
    sweetspot_record = await orch_repo.get_record("whop_camp_sweetspot")
    assert sweetspot_record is not None
    assert sweetspot_record.opportunity_score > 80.0
    assert sweetspot_record.current_stage in (
        OrchestrationStage.OPPORTUNITY_SELECTED,
        OrchestrationStage.ACCOUNT_ASSIGNED,
        OrchestrationStage.SOURCE_ACQUISITION,
        OrchestrationStage.PRODUCTION_DISPATCHED,
    )


@pytest.mark.asyncio
async def test_orchestration_crash_recovery_and_checkpoint_resumption(orch_env):
    """Verifies that an interrupted or crashed orchestration safely resumes from checkpoints."""
    engine: AutonomousOrchestrationEngine = orch_env["engine"]
    orch_repo: OrchestrationRepository = orch_env["orch_repo"]
    campaign_repo: CampaignRepository = orch_env["campaign_repo"]
    task_repo: AgentTaskRepository = orch_env["task_repo"]

    # Create a pre-existing campaign in repository
    campaign = CampaignRecord(
        campaign_id="camp_resume_test_1",
        name="Crash Recovery Campaign",
        source="https://whop.com/campaigns/resume1",
        required_platforms=[CampaignPlatform.YOUTUBE_SHORTS],
        payout_terms=PayoutTerms(cpm_rate=2.0, model=PayoutModel.CPM, total_budget=5000.0, remaining_budget=4000.0),
        duration_terms=CampaignDuration(start_date=datetime.now(timezone.utc), deadline=datetime.now(timezone.utc) + timedelta(days=10)),
        posting_requirements=PostingRequirements(min_duration_seconds=20.0, max_duration_seconds=60.0),
        quotas=QuotasAndCaps(daily_creator_limit=3, campaign_total_clip_cap=20),
        source_material=SourceMaterial(video_urls=["https://youtube.com/watch?v=resume_source_1"]),
        status=CampaignStatus.ACTIVE,
    )
    await campaign_repo.save_campaign(campaign)

    # Simulate a prior run that crashed after ACCOUNT_ASSIGNED
    record = CampaignOrchestrationRecord(
        orchestration_id="orch_camp_resume_test_1",
        campaign_id=campaign.campaign_id,
        account_id="acc_yt_primary",
        platform="youtube",
        current_stage=OrchestrationStage.ACCOUNT_ASSIGNED,
        opportunity_score=88.5,
    )
    record = record.record_stage(OrchestrationStage.ACCOUNT_ASSIGNED, {"account_id": "acc_yt_primary"})
    await orch_repo.save_record(record)

    # Resume orchestration using engine
    resumed = await engine.resume_orchestration(campaign.campaign_id)
    assert resumed is not None
    assert resumed.campaign_id == campaign.campaign_id
    # Must have advanced past ACCOUNT_ASSIGNED to SOURCE_ACQUISITION and PRODUCTION_DISPATCHED
    assert resumed.current_stage in (
        OrchestrationStage.SOURCE_ACQUISITION,
        OrchestrationStage.PRODUCTION_DISPATCHED,
    )
    assert resumed.production_task_id is not None

    # Verify task was actually created in task repository
    task = await task_repo.get_task(resumed.production_task_id)
    assert task is not None
    assert task.inputs["source_uri"] == "https://youtube.com/watch?v=resume_source_1"


@pytest.mark.asyncio
async def test_orchestration_safety_gates_emergency_stop_and_pause(orch_env):
    """Verifies Master Control Emergency Stop immediately halts orchestration and pause defers."""
    engine: AutonomousOrchestrationEngine = orch_env["engine"]
    control_repo: ControlRepository = orch_env["control_repo"]

    # 1. Test Emergency Stop
    stop_state = SystemControlState(
        mode=SystemOperatingMode.EMERGENCY_STOPPED,
        emergency_stopped=True,
        automation_paused=True,
        publishing_locked=True,
        reason="Operator invoked emergency stop",
    )
    await control_repo.save_state(stop_state)

    summary = await engine.run_orchestration_cycle()
    assert summary.status == "emergency_stopped"
    assert "Emergency Stop" in summary.errors[0]

    # 2. Test Automation Pause
    pause_state = SystemControlState(
        mode=SystemOperatingMode.AUTOMATION_PAUSED,
        emergency_stopped=False,
        automation_paused=True,
        publishing_locked=False,
        reason="Scheduled maintenance pause",
    )
    await control_repo.save_state(pause_state)

    summary_pause = await engine.run_orchestration_cycle()
    assert summary_pause.status == "safety_paused"
    assert "Paused" in summary_pause.errors[0]


@pytest.mark.asyncio
async def test_orchestration_full_lifecycle_and_publishing_reconciliation(orch_env):
    """
    Simulates complete autonomous campaign flow:
    discovery -> account resolution -> clipping dispatch -> QA verified ->
    submission & publishing -> platform reconciliation -> finalization.
    """
    engine: AutonomousOrchestrationEngine = orch_env["engine"]
    campaign_repo: CampaignRepository = orch_env["campaign_repo"]
    orch_repo: OrchestrationRepository = orch_env["orch_repo"]
    task_repo: AgentTaskRepository = orch_env["task_repo"]
    media_dir: Path = orch_env["media_dir"]
    storage: LocalStorageDriver = orch_env["storage"]

    # 1. Setup target campaign with daily limit of 1 (to test finalization trigger)
    campaign = CampaignRecord(
        campaign_id="camp_e2e_full_flow",
        name="Fintech Alpha Viral Campaign",
        source="https://whop.com/campaigns/alpha1",
        required_platforms=[CampaignPlatform.YOUTUBE_SHORTS],
        payout_terms=PayoutTerms(cpm_rate=2.5, model=PayoutModel.CPM, total_budget=1000.0, remaining_budget=500.0),
        duration_terms=CampaignDuration(start_date=datetime.now(timezone.utc), deadline=datetime.now(timezone.utc) + timedelta(days=15)),
        posting_requirements=PostingRequirements(min_duration_seconds=20.0, max_duration_seconds=60.0),
        quotas=QuotasAndCaps(daily_creator_limit=1, campaign_total_clip_cap=5),
        source_material=SourceMaterial(video_urls=["https://youtube.com/watch?v=alpha_fintech_full"]),
        status=CampaignStatus.ACTIVE,
    )
    await campaign_repo.save_campaign(campaign)

    # 2. Run initial cycle -> dispatches production clipping task
    summary1 = await engine.run_orchestration_cycle(target_campaign_id=campaign.campaign_id)
    assert summary1.status == "completed"
    assert summary1.production_tasks_dispatched == 1

    record = await orch_repo.get_record(campaign.campaign_id)
    assert record.current_stage == OrchestrationStage.PRODUCTION_DISPATCHED
    task_id = record.production_task_id

    # 3. Simulate production worker completing the 9-stage clipping pipeline with verified candidate clip
    fake_clip_file = media_dir / "clip_alpha_99.mp4"
    fake_clip_file.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"A" * 5000)
    uploaded_media_path = "rendered_clips/clip_alpha_99.mp4"
    await storage.upload(str(fake_clip_file), uploaded_media_path)

    task = await task_repo.get_task(task_id)
    completed_task = task.model_copy(
        update={
            "status": TaskState.SUCCEEDED,
            "outputs": {
                "clip_id": "clip_alpha_99",
                "media_path": uploaded_media_path,
                "duration_seconds": 35.0,
            },
        }
    )
    await task_repo.save_task(completed_task)

    # 4. Run second cycle -> advances through QA -> submission -> publishing -> reconciliation -> finalization
    summary2 = await engine.run_orchestration_cycle(target_campaign_id=campaign.campaign_id)
    assert summary2.status == "completed"
    assert summary2.submissions_processed == 1
    assert summary2.reconciliations_run >= 1
    assert summary2.campaigns_finalized == 1

    final_record = await orch_repo.get_record(campaign.campaign_id)
    assert final_record.current_stage == OrchestrationStage.FINALIZED

    # Verify campaign was marked COMPLETED with post-campaign rules applied
    final_camp = await campaign_repo.get_campaign(campaign.campaign_id)
    assert final_camp.status == CampaignStatus.COMPLETED


@pytest.mark.asyncio
async def test_orchestration_dashboard_endpoints(orch_env):
    """Verifies that FastAPI dashboard exposes orchestration status, records, cycle, and history."""
    engine: AutonomousOrchestrationEngine = orch_env["engine"]
    storage: LocalStorageDriver = orch_env["storage"]

    # Trigger a cycle so records exist
    await engine.run_orchestration_cycle(source_name="whop", max_campaigns_to_process=1)

    from clipping.ui.server import get_storage_driver
    app.dependency_overrides[get_storage_driver] = lambda: storage

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1. GET /api/orchestration/status
            res_status = await client.get("/api/orchestration/status")
            assert res_status.status_code == 200
            status_data = res_status.json()
            assert "engine_state" in status_data
            assert "active_orchestrations_count" in status_data
            assert "latest_cycle" in status_data

            # 2. GET /api/orchestration/records
            res_records = await client.get("/api/orchestration/records")
            assert res_records.status_code == 200
            records_data = res_records.json()
            assert isinstance(records_data, list)
            assert len(records_data) >= 1

            target_cid = records_data[0]["campaign_id"]

            # 3. GET /api/orchestration/records/{campaign_id}
            res_record = await client.get(f"/api/orchestration/records/{target_cid}")
            assert res_record.status_code == 200
            rec_data = res_record.json()
            assert rec_data["campaign_id"] == target_cid
            assert "checkpoints" in rec_data

            # 4. GET /api/orchestration/history
            res_history = await client.get("/api/orchestration/history")
            assert res_history.status_code == 200
            hist_data = res_history.json()
            assert isinstance(hist_data, list)
            assert len(hist_data) >= 1
    finally:
        app.dependency_overrides.clear()
