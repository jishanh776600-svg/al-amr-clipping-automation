"""Comprehensive End-to-End Autonomous Lifecycle Integration Test Suite.

Verifies the unified, production-grade autonomous workflow of AL AMR CLIPPING:
1. Whop Campaign Discovery (real source architecture, $1-$5 CPM sweet spot, $2 preferred)
2. Campaign Evaluation, Ranking, and Opportunity Selection
3. Autonomous Account Assignment / Provisioning with Deterministic Vault IDs
4. Source Material Acquisition & Rule Contradiction Validation
5. Autonomous Clipping Dispatch to Cloud Task Queue
6. Ephemeral Cloud Compute Execution via CloudAgentWorker & MediaClippingCapability
7. Generation of ProductionClipArtifact (1080x1920 9:16 vertical, subtitles, safe zones)
8. Autonomous QA Verification Gate
9. Pre-Submission Rule Validation (SubmissionRuleEngine) & Safety Gate (PublishingSafetyGate, PolicyEngine)
10. Social Media Publishing / Submission Adapter Execution (YouTube/Instagram)
11. Platform Result Reconciliation (PublishingReconciliationService)
12. Campaign Finalization & Account Release upon Quota / Budget Fulfillment
13. Boundary & Safety Controls:
    - Master Control Emergency Stop & Automation Pause
    - Publishing Lock
    - QA Rejection Escalation (zero corrupted clips published)
    - Campaign Brief Contradiction Escalation
    - Security Challenge (CAPTCHA / 2FA) Human Escalation
    - Idempotency & Duplicate Submission Prevention
    - Crash Recovery and Intermediate Checkpoint Resumption
"""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
import pytest
import pytest_asyncio

from clipping.agent.account.lifecycle import AccountLifecycleService
from clipping.agent.bridge.campaign_clipping_bridge import CampaignClippingBridge
from clipping.agent.campaign.discovery import CampaignDiscoveryCapability
from clipping.agent.campaign.evaluator import CampaignEvaluator
from clipping.agent.campaign.models import (
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
from clipping.agent.capabilities.clipping_adapter import MediaClippingCapability
from clipping.agent.capabilities.registry import CapabilityRegistry
from clipping.agent.cloud.lease import WorkerLeaseEngine
from clipping.agent.cloud.limits import CloudResourceLimits
from clipping.agent.cloud.queue import CloudTaskQueue
from clipping.agent.cloud.telemetry import CloudTelemetryEngine
from clipping.agent.cloud.worker import CloudAgentWorker
from clipping.agent.escalation import EscalationContext, EscalationReason, EscalationSeverity
from clipping.agent.events import AgentEventSystem
from clipping.agent.models import AgentTask
from clipping.agent.orchestration.engine import AutonomousOrchestrationEngine
from clipping.agent.orchestration.models import CampaignOrchestrationRecord, OrchestrationStage
from clipping.agent.orchestration.repository import OrchestrationRepository
from clipping.agent.policy import PolicyDecisionType, PolicyEngine
from clipping.agent.publishing.adapters.base import PlatformPublishingAdapter, PlatformPublishResult, PlatformStatusResult
from clipping.agent.publishing.adapters.youtube import YouTubePublishingAdapter
from clipping.agent.publishing.capability import PublishingCapability
from clipping.agent.publishing.models import (
    CampaignSubmissionRecord,
    PublishingMode,
    SubmissionStatus,
)
from clipping.agent.publishing.reconciliation import PublishingReconciliationService
from clipping.agent.publishing.repository import CampaignSubmissionRepository
from clipping.agent.repository import AgentTaskRepository
from clipping.agent.state import TaskState
from clipping.agent.vault.models import AccountMetadata, AccountPlatform, AccountStatus
from clipping.agent.vault.vault import EncryptedCredentialVault
from clipping.contracts.rendering import ProductionClipArtifact
from clipping.state.models import JobState, PipelineStage
from clipping.control.models import SystemControlState, SystemOperatingMode
from clipping.control.repository import ControlRepository
from clipping.publishing.client import MockYouTubeClient
from clipping.state.remote import RemoteStorageStateRepository
from clipping.storage.local import LocalStorageDriver


class MockWhopMarketplaceSource(CampaignSource):
    """Real source architecture simulation yielding competitive and sub-optimal campaigns."""

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
                "campaign_id": "whop_fintech_top",
                "name": "Fintech Alpha Viral Clips",
                "description": "High-payout campaign targeting short educational finance clips",
                "cpm_rate": 2.50,  # Within the preferred $1 - $5 CPM sweet spot ($2.0 preferred)
                "payout_model": "cpm",
                "total_budget": 5000.0,
                "remaining_budget": 4500.0,
                "allowed_platforms": ["youtube_shorts"],
                "source_video_uris": ["https://youtube.com/watch?v=alpha_finance_master"],
                "daily_creator_limit": 1,
                "campaign_total_clip_cap": 25,
                "min_duration_seconds": 15.0,
                "max_duration_seconds": 60.0,
                "hashtags": ["#finance", "#wealth", "#investing"],
                "mentions": ["@FintechAlpha"],
                "status": "active",
            },
            {
                "campaign_id": "whop_low_cpm_suboptimal",
                "name": "Low Reward Campaign",
                "description": "Underpaying campaign below viability threshold",
                "cpm_rate": 0.35,  # Sub-optimal <$1.0 CPM
                "payout_model": "cpm",
                "total_budget": 300.0,
                "remaining_budget": 30.0,
                "allowed_platforms": ["youtube_shorts"],
                "source_video_uris": ["https://youtube.com/watch?v=low_cpm_source"],
                "min_duration_seconds": 15.0,
                "max_duration_seconds": 60.0,
                "status": "active",
            },
        ]

    async def fetch_campaign_detail(self, campaign_ref: str) -> Optional[Dict[str, Any]]:
        for camp in await self.discover():
            if camp["campaign_id"] == campaign_ref:
                return camp
        return None


class MockAdaptivePublishingAdapter(PlatformPublishingAdapter):
    """Adapter allowing controlled simulation of normal publishing, CAPTCHA security challenges, and crashes."""

    def __init__(self, platform: AccountPlatform = AccountPlatform.YOUTUBE):
        self._platform = platform
        self.should_challenge_security = False
        self.published_submissions: List[CampaignSubmissionRecord] = []
        self.view_counter = 1250

    @property
    def platform(self) -> AccountPlatform:
        return self._platform

    async def publish(
        self,
        submission: CampaignSubmissionRecord,
        media_path: str,
        credentials: Dict[str, Any],
    ) -> PlatformPublishResult:
        if self.should_challenge_security:
            return PlatformPublishResult(
                success=False,
                status=SubmissionStatus.ESCALATED,
                escalation_required=True,
                escalation_context=EscalationContext(
                    what_happened="Platform presented automated CAPTCHA challenge during upload",
                    why_it_happened="Platform anti-bot heuristic triggered",
                    decision_required="Human operator must complete CAPTCHA in designated secure session",
                    available_options=["solve_captcha", "rotate_proxy", "abort_submission"],
                    reason=EscalationReason.CAPTCHA_CHALLENGE,
                    severity=EscalationSeverity.HIGH,
                ),
                error_message="CAPTCHA challenge encountered",
            )

        post_id = f"yt_{submission.campaign_id[:6]}_{submission.clip_id[:8]}"
        self.published_submissions.append(submission)
        return PlatformPublishResult(
            success=True,
            platform_post_id=post_id,
            platform_url=f"https://youtube.com/shorts/{post_id}",
            status=SubmissionStatus.PUBLISHED,
        )

    async def reconcile_status(
        self,
        platform_post_id: str,
        credentials: Dict[str, Any],
    ) -> PlatformStatusResult:
        return PlatformStatusResult(
            post_id=platform_post_id,
            exists_on_platform=True,
            platform_status=SubmissionStatus.PUBLISHED,
            privacy_status="public",
            view_count=self.view_counter,
        )


@pytest_asyncio.fixture
async def e2e_env(tmp_path: Path):
    storage_dir = tmp_path / "storage"
    storage = LocalStorageDriver(root_dir=str(storage_dir))

    vault = EncryptedCredentialVault(storage_driver=storage, master_key="e2e_vault_master_key_999")
    campaign_repo = CampaignRepository(storage_driver=storage)
    submission_repo = CampaignSubmissionRepository(storage_driver=storage)
    control_repo = ControlRepository(storage_driver=storage)
    task_repo = AgentTaskRepository(storage_driver=storage)
    orch_repo = OrchestrationRepository(storage_driver=storage)
    queue = CloudTaskQueue(storage_driver=storage)
    policy = PolicyEngine(default_decision=PolicyDecisionType.ALLOW)
    telemetry = CloudTelemetryEngine(storage_driver=storage)
    events = AgentEventSystem()

    # Pre-set Master Control to OPERATIONAL
    await control_repo.save_state(SystemControlState(mode=SystemOperatingMode.OPERATIONAL))

    # Seed primary creator account in vault
    test_account = AccountMetadata(
        account_id="acc_yt_creator_01",
        platform=AccountPlatform.YOUTUBE,
        username="alamr_finance_clips",
        display_name="AL AMR Finance Clips",
        status=AccountStatus.ACTIVE,
        is_dedicated_to_campaign=False,
    )
    await vault.save_account(test_account)

    # Register Whop discovery source
    source_reg = CampaignSourceRegistry()
    whop_source = MockWhopMarketplaceSource()
    source_reg.register(whop_source)

    evaluator = CampaignEvaluator(preferred_cpm=2.0, min_viable_cpm=1.0, max_target_cpm=5.0)
    discovery_cap = CampaignDiscoveryCapability(
        repository=campaign_repo,
        source_registry=source_reg,
        evaluator=evaluator,
    )

    account_service = AccountLifecycleService(vault=vault, policy=policy)
    clipping_bridge = CampaignClippingBridge(queue=queue, task_repository=task_repo)

    # Set up publishing adapters & reconciler
    yt_adapter = MockAdaptivePublishingAdapter(AccountPlatform.YOUTUBE)
    adapters = {AccountPlatform.YOUTUBE: yt_adapter}

    reconciler = PublishingReconciliationService(
        repository=submission_repo,
        adapters=adapters,
        vault=vault,
    )

    pub_capability = PublishingCapability(
        submission_repository=submission_repo,
        campaign_repository=campaign_repo,
        vault=vault,
        control_repository=control_repo,
        policy_engine=policy,
        adapters=adapters,
        telemetry_engine=telemetry,
    )

    # Capability Registry & Worker setup
    cap_registry = CapabilityRegistry()

    # Realistic runner producing valid 1080x1920 MP4 & ProductionClipArtifact
    async def production_pipeline_runner(
        source_uri: str,
        campaign_id: str,
        job_id: str,
        storage: Any,
        candidate_specs: Optional[Dict[str, Any]] = None,
    ) -> int:
        clip_id = (candidate_specs or {}).get("clip_id") or f"clip_{job_id[:8]}"
        final_video_key = f"clips/{clip_id}/final_1080x1920.mp4"

        # Generate realistic vertical MP4 bytes (>1KB)
        valid_mp4_bytes = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 4096
        await storage.upload_bytes(valid_mp4_bytes, final_video_key)

        duration = float((candidate_specs or {}).get("end_time", 35.0) - (candidate_specs or {}).get("start_time", 0.0))
        if duration <= 0:
            duration = 35.0

        artifact = ProductionClipArtifact(
            clip_id=clip_id,
            source_video_id="alpha_finance_master",
            campaign_id=campaign_id,
            start_time=(candidate_specs or {}).get("start_time", 0.0),
            end_time=(candidate_specs or {}).get("start_time", 0.0) + duration,
            duration_seconds=duration,
            media_path=final_video_key,
            aspect_ratio="9:16",
            width=1080,
            height=1920,
            fps=30.0,
            file_size_bytes=len(valid_mp4_bytes),
            qa_status="passed",
            qa_report_key=f"clips/{clip_id}/qa_report.json",
            reframe_plan_key=f"clips/{clip_id}/reframe_plan.json",
        )
        await storage.upload_bytes(artifact.model_dump_json().encode("utf-8"), f"clips/{clip_id}/production_artifact.json")

        state_repo = RemoteStorageStateRepository(storage_driver=storage)
        await state_repo.create_job(job_id=job_id, campaign_id=campaign_id, source_video_id="alpha_finance_master", idempotency_key=f"idemp_{job_id}")
        await state_repo.update_job_state(
            job_id=job_id,
            new_state=JobState.AWAITING_APPROVAL,
            new_stage=PipelineStage.APPROVAL,
            reason="QA passed",
            metadata={
                "passing_clips_count": 1,
                "primary_clip_id": clip_id,
                "primary_media_path": final_video_key,
                "primary_duration": duration,
                "resolution": "1080x1920",
                "aspect_ratio": "9:16",
                "qa_status": "passed",
                "artifacts": [artifact.model_dump(mode="json")],
            },
        )
        return 0

    media_clipping_cap = MediaClippingCapability(runner_fn=production_pipeline_runner)
    cap_registry.register(media_clipping_cap)

    lease_engine = WorkerLeaseEngine(storage_driver=storage)
    worker = CloudAgentWorker(
        worker_id="cloud_worker_e2e_01",
        task_repository=task_repo,
        queue=queue,
        capabilities=cap_registry,
        policy_engine=policy,
        event_system=events,
        control_repository=control_repo,
        lease_engine=lease_engine,
        telemetry=telemetry,
        storage_driver=storage,
    )

    # Master Orchestration Engine wired with inline worker
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
        publishing_capability=pub_capability,
        submission_repository=submission_repo,
        reconciler=reconciler,
        policy_engine=policy,
        credential_vault=vault,
        task_queue=queue,
        worker=worker,
        telemetry_engine=telemetry,
        event_system=events,
    )

    return {
        "storage": storage,
        "vault": vault,
        "campaign_repo": campaign_repo,
        "submission_repo": submission_repo,
        "control_repo": control_repo,
        "task_repo": task_repo,
        "orch_repo": orch_repo,
        "queue": queue,
        "policy": policy,
        "worker": worker,
        "yt_adapter": yt_adapter,
        "reconciler": reconciler,
        "pub_capability": pub_capability,
        "engine": engine,
        "production_pipeline_runner": production_pipeline_runner,
        "media_clipping_cap": media_clipping_cap,
    }


@pytest.mark.asyncio
async def test_end_to_end_autonomous_lifecycle_whop_to_finalization(e2e_env):
    """
    Test 1: Full end-to-end autonomous lifecycle.
    Whop Discovery -> CPM Selection ($2.50 vs $0.35) -> Account Assignment ->
    Source Acquisition -> Task Enqueue -> CloudWorker Execution -> ProductionClipArtifact (1080x1920) ->
    QA Verification -> SubmissionRuleEngine -> Publishing Adapter -> Reconciliation -> Finalization.
    """
    engine: AutonomousOrchestrationEngine = e2e_env["engine"]
    orch_repo: OrchestrationRepository = e2e_env["orch_repo"]
    campaign_repo: CampaignRepository = e2e_env["campaign_repo"]
    submission_repo: CampaignSubmissionRepository = e2e_env["submission_repo"]
    yt_adapter: MockAdaptivePublishingAdapter = e2e_env["yt_adapter"]

    # Execute end-to-end orchestration cycle
    summary = await engine.run_orchestration_cycle(source_name="whop", max_campaigns_to_process=1)

    assert summary.status == "completed"
    assert summary.campaigns_discovered == 2
    assert summary.campaigns_evaluated == 2
    assert summary.production_tasks_dispatched == 1
    assert summary.submissions_processed == 1
    assert summary.reconciliations_run == 1
    assert summary.campaigns_finalized == 1
    assert len(summary.errors) == 0

    # 1. Verify that the $2.50 CPM campaign was prioritized over the sub-optimal $0.35 CPM campaign
    top_record = await orch_repo.get_record("whop_fintech_top")
    assert top_record is not None
    assert top_record.opportunity_score > 70.0
    assert top_record.current_stage == OrchestrationStage.FINALIZED

    # The low CPM campaign should not have been selected
    low_cpm_record = await orch_repo.get_record("whop_low_cpm_suboptimal")
    assert low_cpm_record is None or low_cpm_record.current_stage == OrchestrationStage.OPPORTUNITY_SELECTED

    # 2. Verify complete audit checkpoints in the orchestration record
    stage_names = [cp.stage.value for cp in top_record.checkpoints]
    expected_stages = [
        OrchestrationStage.OPPORTUNITY_SELECTED.value,
        OrchestrationStage.ACCOUNT_ASSIGNED.value,
        OrchestrationStage.SOURCE_ACQUISITION.value,
        OrchestrationStage.PRODUCTION_DISPATCHED.value,
        OrchestrationStage.PRODUCTION_COMPLETED.value,
        OrchestrationStage.QA_VERIFIED.value,
        OrchestrationStage.SUBMISSION_PENDING.value,
        OrchestrationStage.PUBLISHED.value,
        OrchestrationStage.RECONCILED.value,
        OrchestrationStage.FINALIZED.value,
    ]
    for exp in expected_stages:
        assert exp in stage_names, f"Missing stage in audit trail: {exp}"

    # 3. Verify real submission was recorded with platform post ID and watch URL
    assert top_record.submission_id is not None
    sub = await submission_repo.get_submission(top_record.campaign_id, top_record.submission_id)
    assert sub is not None
    assert sub.platform == AccountPlatform.YOUTUBE
    assert sub.platform_post_id is not None
    assert "https://youtube.com/shorts/" in (sub.platform_url or "")
    assert sub.current_status == SubmissionStatus.PUBLISHED

    # 4. Verify campaign was marked COMPLETED with lifecycle state
    camp = await campaign_repo.get_campaign(top_record.campaign_id)
    assert camp.status == CampaignStatus.COMPLETED

    # 5. Verify adapter received the published submission
    assert len(yt_adapter.published_submissions) == 1
    assert yt_adapter.published_submissions[0].campaign_id == "whop_fintech_top"


@pytest.mark.asyncio
async def test_idempotent_caching_and_no_duplicate_production(e2e_env):
    """
    Test 2: Idempotency & Duplicate Prevention.
    Verifies that re-executing clipping for an already produced clip hits storage cache,
    and attempting to re-publish an already submitted clip triggers idempotency hits without duplicate uploads.
    """
    engine: AutonomousOrchestrationEngine = e2e_env["engine"]
    storage: LocalStorageDriver = e2e_env["storage"]
    pub_cap: PublishingCapability = e2e_env["pub_capability"]
    yt_adapter: MockAdaptivePublishingAdapter = e2e_env["yt_adapter"]

    # 1. Run initial lifecycle to establish artifacts
    await engine.run_orchestration_cycle(source_name="whop", max_campaigns_to_process=1)
    initial_uploads = len(yt_adapter.published_submissions)
    assert initial_uploads == 1

    # 2. Test MediaClippingCapability idempotency cache hit
    media_cap: MediaClippingCapability = e2e_env["media_clipping_cap"]
    clip_id = yt_adapter.published_submissions[0].clip_id
    ctx = CapabilityContext(
        task_id="task_reexecution_test",
        inputs={
            "source_uri": "https://youtube.com/watch?v=alpha_finance_master",
            "campaign_id": "whop_fintech_top",
            "job_id": "job_reexec_01",
            "clip_id": clip_id,
        },
        storage_driver=storage,
    )
    cache_res = await media_cap.execute(ctx)
    assert cache_res.success is True
    assert cache_res.outputs.get("cached") is True
    assert cache_res.outputs.get("clip_id") == clip_id

    # 3. Test PublishingCapability duplicate submission idempotency
    pub_ctx = CapabilityContext(
        task_id="task_duplicate_pub_attempt",
        inputs={
            "campaign_id": "whop_fintech_top",
            "clip_id": clip_id,
            "account_id": "acc_yt_creator_01",
            "media_path": f"clips/{clip_id}/final_1080x1920.mp4",
            "platform": "youtube",
            "publishing_mode": "draft",
        },
        storage_driver=storage,
    )
    dup_res = await pub_cap.execute(pub_ctx)
    assert dup_res.success is True
    assert dup_res.outputs.get("idempotent_hit") is True
    # Ensure no additional duplicate upload occurred on the adapter
    assert len(yt_adapter.published_submissions) == initial_uploads


@pytest.mark.asyncio
async def test_crash_recovery_and_checkpoint_resumption(e2e_env):
    """
    Test 3: Crash Recovery & Checkpoint Resumption.
    Simulates a worker/orchestrator crash after task dispatch, and proves resume_orchestration
    picks up from PRODUCTION_DISPATCHED to finish QA, publishing, and finalization.
    """
    engine: AutonomousOrchestrationEngine = e2e_env["engine"]
    orch_repo: OrchestrationRepository = e2e_env["orch_repo"]
    campaign_repo: CampaignRepository = e2e_env["campaign_repo"]
    storage: LocalStorageDriver = e2e_env["storage"]
    task_repo: AgentTaskRepository = e2e_env["task_repo"]
    worker: CloudAgentWorker = e2e_env["worker"]

    cid = "whop_crash_recovery_camp"
    camp = CampaignRecord(
        campaign_id=cid,
        name="Resilience Crash Test Campaign",
        source="https://whop.com/campaigns/resilience",
        required_platforms=[CampaignPlatform.YOUTUBE_SHORTS],
        payout_terms=PayoutTerms(cpm_rate=2.2, model=PayoutModel.CPM, total_budget=2000.0, remaining_budget=1000.0),
        duration_terms=CampaignDuration(start_date=datetime.now(timezone.utc), deadline=datetime.now(timezone.utc) + timedelta(days=10)),
        posting_requirements=PostingRequirements(min_duration_seconds=15.0, max_duration_seconds=60.0),
        quotas=QuotasAndCaps(daily_creator_limit=1),
        source_material=SourceMaterial(video_urls=["https://youtube.com/watch?v=resilience_source"]),
        status=CampaignStatus.ACTIVE,
    )
    await campaign_repo.save_campaign(camp)

    # Disable inline worker temporarily to freeze state at PRODUCTION_DISPATCHED
    engine.worker = None
    summary1 = await engine.run_orchestration_cycle(target_campaign_id=cid)
    assert summary1.production_tasks_dispatched == 1

    record_before = await orch_repo.get_record(cid)
    assert record_before.current_stage == OrchestrationStage.PRODUCTION_DISPATCHED
    task_id = record_before.production_task_id

    # Simulate cloud worker picking up task from queue asynchronously
    task = await worker.run_next_task()
    assert task is not None
    assert task.status == TaskState.SUCCEEDED

    # Restore worker and call resume_orchestration
    engine.worker = worker
    resumed_record = await engine.resume_orchestration(cid)

    assert resumed_record is not None
    assert resumed_record.current_stage == OrchestrationStage.FINALIZED
    assert resumed_record.submission_id is not None


@pytest.mark.asyncio
async def test_safety_gates_emergency_stop_and_publishing_lock(e2e_env):
    """
    Test 4: Master Control Safety Gates.
    - Global Emergency Stop immediately aborts cycle.
    - Publishing Lock allows production and QA, but halts before platform upload.
    """
    engine: AutonomousOrchestrationEngine = e2e_env["engine"]
    control_repo: ControlRepository = e2e_env["control_repo"]
    orch_repo: OrchestrationRepository = e2e_env["orch_repo"]
    campaign_repo: CampaignRepository = e2e_env["campaign_repo"]

    # 1. Test Global Emergency Stop
    await control_repo.save_state(
        SystemControlState(
            mode=SystemOperatingMode.EMERGENCY_STOPPED,
            emergency_stopped=True,
            reason="Operator triggered manual emergency stop",
        )
    )
    summary_stopped = await engine.run_orchestration_cycle()
    assert summary_stopped.status == "emergency_stopped"
    assert "Emergency Stop" in summary_stopped.errors[0]

    # 2. Reset to OPERATIONAL but engage PUBLISHING LOCK
    await control_repo.save_state(
        SystemControlState(
            mode=SystemOperatingMode.OPERATIONAL,
            emergency_stopped=False,
            automation_paused=False,
            publishing_locked=True,
            reason="Operator locked publishing channel",
        )
    )

    cid = "whop_pub_locked_camp"
    camp = CampaignRecord(
        campaign_id=cid,
        name="Publishing Locked Campaign",
        source="https://whop.com/campaigns/publock",
        required_platforms=[CampaignPlatform.YOUTUBE_SHORTS],
        payout_terms=PayoutTerms(cpm_rate=2.0, model=PayoutModel.CPM, total_budget=1000.0, remaining_budget=500.0),
        duration_terms=CampaignDuration(start_date=datetime.now(timezone.utc), deadline=datetime.now(timezone.utc) + timedelta(days=10)),
        posting_requirements=PostingRequirements(min_duration_seconds=15.0, max_duration_seconds=60.0),
        quotas=QuotasAndCaps(daily_creator_limit=1),
        source_material=SourceMaterial(video_urls=["https://youtube.com/watch?v=publock_source"]),
        status=CampaignStatus.ACTIVE,
    )
    await campaign_repo.save_campaign(camp)

    summary_locked = await engine.run_orchestration_cycle(target_campaign_id=cid)
    assert summary_locked.status == "completed"
    assert summary_locked.production_tasks_dispatched == 1
    assert summary_locked.submissions_processed == 0

    record_locked = await orch_repo.get_record(cid)
    # Reached QA_VERIFIED or BLOCKED at submission due to publishing lock
    assert record_locked.current_stage in (OrchestrationStage.QA_VERIFIED, OrchestrationStage.BLOCKED)
    assert "Publishing Locked" in (record_locked.blocking_reason or "")


@pytest.mark.asyncio
async def test_qa_rejection_escalation(e2e_env):
    """
    Test 5: QA Gating Failure Escalation.
    Verifies that if a rendered clip fails QA standards (zero passing clips),
    the system creates an explicit human escalation (EscalationReason.QUALITY_ASSURANCE_FAILURE)
    and refuses to submit or publish corrupted content.
    """
    engine: AutonomousOrchestrationEngine = e2e_env["engine"]
    orch_repo: OrchestrationRepository = e2e_env["orch_repo"]
    campaign_repo: CampaignRepository = e2e_env["campaign_repo"]
    task_repo: AgentTaskRepository = e2e_env["task_repo"]
    storage: LocalStorageDriver = e2e_env["storage"]
    worker: CloudAgentWorker = e2e_env["worker"]

    cid = "whop_qa_fail_camp"
    camp = CampaignRecord(
        campaign_id=cid,
        name="QA Strict Failure Campaign",
        source="https://whop.com/campaigns/qafail",
        required_platforms=[CampaignPlatform.YOUTUBE_SHORTS],
        payout_terms=PayoutTerms(cpm_rate=2.5, model=PayoutModel.CPM, total_budget=1000.0, remaining_budget=500.0),
        duration_terms=CampaignDuration(start_date=datetime.now(timezone.utc), deadline=datetime.now(timezone.utc) + timedelta(days=10)),
        posting_requirements=PostingRequirements(min_duration_seconds=15.0, max_duration_seconds=60.0),
        quotas=QuotasAndCaps(daily_creator_limit=1),
        source_material=SourceMaterial(video_urls=["https://youtube.com/watch?v=qa_fail_source"]),
        status=CampaignStatus.ACTIVE,
    )
    await campaign_repo.save_campaign(camp)

    # Configure runner to report 0 passing clips
    async def failing_qa_runner(source_uri, campaign_id, job_id, storage, candidate_specs=None):
        state_repo = RemoteStorageStateRepository(storage_driver=storage)
        await state_repo.create_job(job_id=job_id, campaign_id=campaign_id, source_video_id="qa_fail_source", idempotency_key=f"idemp_{job_id}")
        await state_repo.update_job_state(
            job_id=job_id,
            new_state=JobState.FAILED,
            new_stage=PipelineStage.QA,
            reason="QA Rejected: Audio silence detected across entire duration",
            metadata={"passing_clips_count": 0, "qa_status": "failed"},
        )
        return 0

    failing_cap = MediaClippingCapability(runner_fn=failing_qa_runner)
    worker.capabilities.register(failing_cap, override=True)

    summary = await engine.run_orchestration_cycle(target_campaign_id=cid)
    assert summary.status == "completed"
    assert summary.escalations_raised == 1

    record = await orch_repo.get_record(cid)
    assert record.current_stage == OrchestrationStage.ESCALATED
    assert record.escalation_id is not None

    esc = await task_repo.get_escalation(record.escalation_id)
    assert esc is not None
    assert esc.reason == EscalationReason.QUALITY_ASSURANCE_FAILURE
    assert "0 clips passed QA" in (esc.context.why_it_happened or "")


@pytest.mark.asyncio
async def test_campaign_brief_contradiction_escalation(e2e_env):
    """
    Test 6: Campaign Rule Contradiction Escalation.
    Verifies that campaigns with contradictory briefs (e.g. min_duration > max_duration)
    raise an explicit EscalationReason.CONTRADICTORY_INSTRUCTIONS and halt safely.
    """
    engine: AutonomousOrchestrationEngine = e2e_env["engine"]
    orch_repo: OrchestrationRepository = e2e_env["orch_repo"]
    campaign_repo: CampaignRepository = e2e_env["campaign_repo"]
    task_repo: AgentTaskRepository = e2e_env["task_repo"]

    cid = "whop_contradictory_camp"
    camp = CampaignRecord(
        campaign_id=cid,
        name="Contradictory Brief Campaign",
        source="https://whop.com/campaigns/contradictory",
        required_platforms=[CampaignPlatform.YOUTUBE_SHORTS],
        payout_terms=PayoutTerms(cpm_rate=2.0, model=PayoutModel.CPM, total_budget=1000.0, remaining_budget=500.0),
        duration_terms=CampaignDuration(start_date=datetime.now(timezone.utc), deadline=datetime.now(timezone.utc) + timedelta(days=10)),
        posting_requirements=PostingRequirements(
            min_duration_seconds=90.0,  # Contradicts max duration!
            max_duration_seconds=30.0,
        ),
        quotas=QuotasAndCaps(daily_creator_limit=1),
        source_material=SourceMaterial(video_urls=["https://youtube.com/watch?v=contradictory_source"]),
        status=CampaignStatus.ACTIVE,
    )
    await campaign_repo.save_campaign(camp)

    summary = await engine.run_orchestration_cycle(target_campaign_id=cid)
    assert summary.status == "completed"
    assert summary.escalations_raised == 1

    record = await orch_repo.get_record(cid)
    assert record.current_stage == OrchestrationStage.ESCALATED
    assert record.escalation_id is not None

    esc = await task_repo.get_escalation(record.escalation_id)
    assert esc is not None
    assert esc.reason == EscalationReason.CONTRADICTORY_INSTRUCTIONS
    assert "contradictory" in esc.context.what_happened.lower()


@pytest.mark.asyncio
async def test_security_challenge_escalation(e2e_env):
    """
    Test 7: Security Challenge (CAPTCHA / 2FA) Human Escalation.
    Verifies that when a publishing adapter encounters a platform security challenge,
    the workflow transitions to ESCALATED and records a human intervention request.
    """
    engine: AutonomousOrchestrationEngine = e2e_env["engine"]
    orch_repo: OrchestrationRepository = e2e_env["orch_repo"]
    campaign_repo: CampaignRepository = e2e_env["campaign_repo"]
    task_repo: AgentTaskRepository = e2e_env["task_repo"]
    yt_adapter: MockAdaptivePublishingAdapter = e2e_env["yt_adapter"]

    # Trigger CAPTCHA challenge on the adapter
    yt_adapter.should_challenge_security = True

    cid = "whop_captcha_challenge_camp"
    camp = CampaignRecord(
        campaign_id=cid,
        name="Security Challenge Test Campaign",
        source="https://whop.com/campaigns/security",
        required_platforms=[CampaignPlatform.YOUTUBE_SHORTS],
        payout_terms=PayoutTerms(cpm_rate=2.0, model=PayoutModel.CPM, total_budget=1000.0, remaining_budget=500.0),
        duration_terms=CampaignDuration(start_date=datetime.now(timezone.utc), deadline=datetime.now(timezone.utc) + timedelta(days=10)),
        posting_requirements=PostingRequirements(min_duration_seconds=15.0, max_duration_seconds=60.0),
        quotas=QuotasAndCaps(daily_creator_limit=1),
        source_material=SourceMaterial(video_urls=["https://youtube.com/watch?v=security_source"]),
        status=CampaignStatus.ACTIVE,
    )
    await campaign_repo.save_campaign(camp)

    summary = await engine.run_orchestration_cycle(target_campaign_id=cid)
    assert summary.status == "completed"
    assert summary.escalations_raised == 1

    record = await orch_repo.get_record(cid)
    assert record.current_stage == OrchestrationStage.ESCALATED
    assert record.escalation_id is not None

    esc = await task_repo.get_escalation(record.escalation_id)
    assert esc is not None
    assert esc.reason == EscalationReason.CAPTCHA_CHALLENGE
    assert "CAPTCHA" in esc.context.what_happened
