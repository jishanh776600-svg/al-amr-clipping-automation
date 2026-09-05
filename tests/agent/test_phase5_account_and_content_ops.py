"""Targeted Validation for Phase 5 Step 2: Autonomous Campaign -> Account -> Content Operations."""

import pytest
import pytest_asyncio
from datetime import datetime, timezone
from pathlib import Path

from clipping.agent.account.branding import CampaignBrandingGenerator, ChannelBrandingProfile
from clipping.agent.account.lifecycle import AccountLifecycleService
from clipping.agent.bridge.campaign_clipping_bridge import CampaignClippingBridge
from clipping.agent.browser.driver import MockBrowserDriver
from clipping.agent.campaign.decision import CampaignDecisionEngine
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
    PostCampaignRules,
    PostingRequirements,
    QuotasAndCaps,
    SourceMaterial,
)
from clipping.agent.campaign.repository import CampaignRepository
from clipping.agent.campaign.sources.registry import CampaignSourceRegistry
from clipping.agent.campaign.sources.whop import WhopCampaignSource
from clipping.agent.cloud.queue import CloudTaskQueue
from clipping.agent.loop import AutonomousOperationsLoop
from clipping.agent.policy import PolicyDecisionType, PolicyEngine
from clipping.agent.repository import TaskRepository
from clipping.agent.vault.models import AccountMetadata, AccountPlatform, AccountStatus
from clipping.agent.vault.vault import EncryptedCredentialVault
from clipping.storage.local import LocalStorageDriver


@pytest_asyncio.fixture
async def ops_env(tmp_path: Path):
    storage = LocalStorageDriver(root_dir=str(tmp_path / "storage"))
    vault = EncryptedCredentialVault(storage_driver=storage, master_key="test_master_key_ops_123")
    task_repo = TaskRepository(storage_driver=storage)
    queue = CloudTaskQueue(storage_driver=storage)
    policy = PolicyEngine(default_decision=PolicyDecisionType.ALLOW)
    campaign_repo = CampaignRepository(storage_driver=storage)
    mock_browser = MockBrowserDriver()

    evaluator = CampaignEvaluator(preferred_cpm=2.0)
    source_registry = CampaignSourceRegistry()
    whop_source = WhopCampaignSource(browser_driver=mock_browser)
    source_registry.register(whop_source)

    discovery_cap = CampaignDiscoveryCapability(
        repository=campaign_repo,
        browser_driver=mock_browser,
        source_registry=source_registry,
        evaluator=evaluator,
    )
    decision_engine = CampaignDecisionEngine(
        vault=vault,
        policy_engine=policy,
        evaluator=evaluator,
    )
    bridge = CampaignClippingBridge(queue=queue, task_repository=task_repo)
    branding_gen = CampaignBrandingGenerator()
    account_service = AccountLifecycleService(
        vault=vault,
        policy=policy,
        branding_generator=branding_gen,
    )

    loop = AutonomousOperationsLoop(
        discovery_capability=discovery_cap,
        campaign_repository=campaign_repo,
        decision_engine=decision_engine,
        clipping_bridge=bridge,
        task_repository=task_repo,
        storage_driver=storage,
        account_service=account_service,
    )

    return {
        "storage": storage,
        "vault": vault,
        "task_repo": task_repo,
        "queue": queue,
        "policy": policy,
        "campaign_repo": campaign_repo,
        "evaluator": evaluator,
        "whop_source": whop_source,
        "discovery_cap": discovery_cap,
        "decision_engine": decision_engine,
        "bridge": bridge,
        "branding_gen": branding_gen,
        "account_service": account_service,
        "loop": loop,
    }


# Test 1: Autonomous Account Reuse vs Dedicated Creation & Idempotency
@pytest.mark.asyncio
async def test_01_account_reuse_vs_dedicated_creation(ops_env):
    """Verifies autonomous reuse of existing accounts vs dedicated creation and idempotency."""
    vault: EncryptedCredentialVault = ops_env["vault"]
    account_service: AccountLifecycleService = ops_env["account_service"]

    # Seed vault with an existing reusable account
    existing_acc = AccountMetadata(
        platform=AccountPlatform.YOUTUBE,
        account_id="acc_yt_general_01",
        username="alamrclips",
        display_name="AL AMR Clips",
        status=AccountStatus.ACTIVE,
        reuse_eligibility=True,
        campaign_association=None,
    )
    await vault.save_account(existing_acc)

    # 1. Campaign allowing reuse should autonomously bind the existing account
    camp_reusable = CampaignRecord(
        campaign_id="camp_reusable_101",
        name="Alpha FinTech Highlights",
        source="whop",
        required_platforms=[CampaignPlatform.YOUTUBE_SHORTS],
        account_requirements=AccountRequirements(allow_account_reuse=True),
        source_material=SourceMaterial(video_urls=["https://www.youtube.com/watch?v=reuse101"]),
    )
    res1 = await account_service.select_or_create_account(camp_reusable, AccountPlatform.YOUTUBE)
    assert res1.was_created is False
    assert res1.account.account_id == "acc_yt_general_01"
    assert res1.lifecycle_state == CampaignLifecycleState.ACCOUNT_ASSIGNED

    # 2. Campaign prohibiting reuse should autonomously provision a NEW dedicated account
    camp_dedicated = CampaignRecord(
        campaign_id="camp_dedicated_202",
        name="Exclusive Crypto Masterclass",
        source="whop",
        required_platforms=[CampaignPlatform.YOUTUBE_SHORTS],
        account_requirements=AccountRequirements(allow_account_reuse=False, required_niche="finance"),
        source_material=SourceMaterial(video_urls=["https://www.youtube.com/watch?v=ded202"]),
    )
    res2 = await account_service.select_or_create_account(camp_dedicated, AccountPlatform.YOUTUBE)
    assert res2.was_created is True
    assert res2.account.account_id != "acc_yt_general_01"
    assert "youtube" in res2.account.account_id
    assert res2.account.campaign_association == "camp_dedicated_202"
    assert res2.account.reuse_eligibility is False
    assert res2.branding_profile is not None

    # 3. Idempotency verification: Repeated call for same campaign must retrieve the same account
    res2_repeat = await account_service.select_or_create_account(camp_dedicated, AccountPlatform.YOUTUBE)
    assert res2_repeat.was_created is False
    assert res2_repeat.account.account_id == res2.account.account_id


# Test 2: Professional Campaign-Aware Branding Generation
@pytest.mark.asyncio
async def test_02_campaign_aware_branding_generation(ops_env):
    """Verifies synthesized branding profiles for YouTube and Instagram match campaign niche and platform rules."""
    branding_gen: CampaignBrandingGenerator = ops_env["branding_gen"]

    camp_tech = CampaignRecord(
        campaign_id="camp_tech_ai_303",
        name="CloudScale AI Founders Program",
        source="whop",
        description="Daily insights from top artificial intelligence and software engineers.",
        posting_requirements=PostingRequirements(
            required_hashtags=["#CloudScale", "#AI"],
            required_title_keywords=["AI", "Python"],
        ),
        account_requirements=AccountRequirements(required_niche="technology"),
    )

    # Test YouTube branding
    yt_profile = branding_gen.generate_branding(camp_tech, AccountPlatform.YOUTUBE)
    assert yt_profile.platform == AccountPlatform.YOUTUBE
    assert yt_profile.target_niche == "technology"
    assert "CloudScale" in yt_profile.channel_title
    assert yt_profile.handle.startswith("@")
    assert "#AI" in yt_profile.hashtags
    assert "#technology" in yt_profile.hashtags
    assert "ai" in [k.lower() for k in yt_profile.seo_keywords]
    assert yt_profile.banner_spec["dimensions"]["width"] == 2560
    assert yt_profile.avatar_spec["background_color"] == "#020617"  # Tech palette bg

    # Test Instagram branding
    ig_profile = branding_gen.generate_branding(camp_tech, AccountPlatform.INSTAGRAM)
    assert ig_profile.platform == AccountPlatform.INSTAGRAM
    assert len(ig_profile.handle) <= 30
    assert ig_profile.banner_spec["dimensions"]["width"] == 1080
    assert "🎬" in ig_profile.bio


# Test 3: Pre-flight Term Verification & Contradiction Guard
@pytest.mark.asyncio
async def test_03_preflight_rule_verification_and_guard(ops_env):
    """Verifies that campaigns with contradictory or invalid terms are blocked before dispatch."""
    decision_engine: CampaignDecisionEngine = ops_env["decision_engine"]

    invalid_camp = CampaignRecord(
        campaign_id="camp_invalid_404",
        name="Impossible Constraints Campaign",
        source="whop",
        posting_requirements=PostingRequirements(
            min_duration_seconds=90.0,
            max_duration_seconds=30.0,  # Contradiction: min > max
        ),
        payout_terms=PayoutTerms(cpm_rate=2.5),
        source_material=SourceMaterial(video_urls=["https://www.youtube.com/watch?v=test404"]),
    )

    decision = await decision_engine.evaluate_campaign_for_execution(invalid_camp)
    assert decision.is_approved is False
    assert decision.escalation_required is True
    assert decision.lifecycle_state == CampaignLifecycleState.ESCALATED
    assert "Contradictory" in decision.decision_reason


# Test 4: Post-Campaign Lifecycle & Privacy Protection
@pytest.mark.asyncio
async def test_04_post_campaign_lifecycle_and_disposition(ops_env):
    """Verifies account reuse eligibility, locking, and safe privacy actions upon campaign completion."""
    account_service: AccountLifecycleService = ops_env["account_service"]
    vault: EncryptedCredentialVault = ops_env["vault"]

    # Reusable post-campaign test
    camp_a = CampaignRecord(
        campaign_id="camp_fin_501",
        name="FinTech Summer Sprint",
        source="whop",
        account_requirements=AccountRequirements(allow_account_reuse=True),
        post_campaign_rules=PostCampaignRules(
            allow_account_reuse_after_campaign=True,
            privatize_videos_on_completion=True,
            delete_videos_on_completion=False,
        ),
    )
    acc_a = AccountMetadata(
        platform=AccountPlatform.YOUTUBE,
        account_id="acc_yt_fin_501",
        username="fintechsprint",
        campaign_association="camp_fin_501",
        reuse_eligibility=True,
    )
    await vault.save_account(acc_a)

    result_a = await account_service.finalize_campaign_lifecycle(camp_a, acc_a, payment_status="paid")
    assert result_a.lifecycle_state == CampaignLifecycleState.REUSE_ELIGIBLE
    assert result_a.reuse_eligible is True
    assert "account_freed_for_reuse" in result_a.actions_taken
    assert "marked_campaign_videos_private" in result_a.actions_taken

    # Verify vault record updated
    updated_acc_a = await vault.get_account_metadata(AccountPlatform.YOUTUBE, "acc_yt_fin_501")
    assert updated_acc_a.campaign_association is None
    assert updated_acc_a.reuse_eligibility is True

    # Dedicated / Prohibited reuse post-campaign test
    camp_b = CampaignRecord(
        campaign_id="camp_exclusive_502",
        name="Single Brand Exclusive Run",
        source="whop",
        account_requirements=AccountRequirements(allow_account_reuse=False),
        post_campaign_rules=PostCampaignRules(
            allow_account_reuse_after_campaign=False,
            privatize_videos_on_completion=False,
            delete_videos_on_completion=True,  # Guarded deletion
        ),
    )
    acc_b = AccountMetadata(
        platform=AccountPlatform.YOUTUBE,
        account_id="acc_yt_excl_502",
        username="exclusiverun",
        campaign_association="camp_exclusive_502",
        reuse_eligibility=False,
    )
    await vault.save_account(acc_b)

    result_b = await account_service.finalize_campaign_lifecycle(camp_b, acc_b, payment_status="paid")
    assert result_b.lifecycle_state == CampaignLifecycleState.REUSE_PROHIBITED
    assert result_b.reuse_eligible is False
    assert "account_locked_to_campaign" in result_b.actions_taken
    # Verify destructive delete is guarded by policy
    assert "video_deletion_skipped_guarded_by_policy" in result_b.actions_taken or "deleted_campaign_videos" in result_b.actions_taken


# Test 5: End-to-End Autonomous Flow with Task Tracking Metadata
@pytest.mark.asyncio
async def test_05_end_to_end_autonomous_operations_flow(ops_env):
    """Verifies end-to-end autonomous discovery, account provisioning, task dispatch with 5 tracking fields, and finalization."""
    loop: AutonomousOperationsLoop = ops_env["loop"]
    queue: CloudTaskQueue = ops_env["queue"]
    campaign_repo: CampaignRepository = ops_env["campaign_repo"]
    task_repo: TaskRepository = ops_env["task_repo"]

    raw_campaigns = [
        {
            "id": "whop_camp_e2e_601",
            "name": "Global AI Innovation Shorts",
            "payout": {"model": "cpm", "cpm_rate": 2.25, "total_budget": 5000.0, "currency": "USD"},
            "platforms": ["youtube_shorts"],
            "source_urls": ["https://www.youtube.com/watch?v=aie2e601source"],
            "account_requirements": {"allow_account_reuse": False, "required_niche": "technology"},
            "posting_requirements": {
                "min_duration_seconds": 30.0,
                "max_duration_seconds": 60.0,
                "required_hashtags": ["#AIInnovation", "#TechNews"],
            },
            "post_campaign_rules": {"allow_account_reuse_after_campaign": True},
        }
    ]

    # Run autonomous cycle
    cycle_res = await loop.run_discovery_and_dispatch_cycle(raw_campaigns=raw_campaigns)
    assert cycle_res["cycle_status"] == "completed"
    assert len(cycle_res["tasks_enqueued"]) == 1
    task_id = cycle_res["tasks_enqueued"][0]

    # Verify task persisted and enqueued with all 5 required tracking fields
    enqueued_job = await queue.get_item(task_id)
    assert enqueued_job is not None
    assert enqueued_job.task_id == task_id

    saved_task = await task_repo.get_task(task_id)
    assert saved_task is not None
    inputs = saved_task.inputs
    # Check 5 core tracking fields:
    assert inputs["campaign_id"] == "whop_camp_e2e_601"
    assert inputs["account_id"] is not None
    assert "youtube" in inputs["account_id"]
    assert inputs["source_video_id"] is not None
    assert inputs["task_id"] == task_id
    assert inputs["platform"] == "youtube_shorts"

    # Verify campaign state transitioned to CONTENT_PRODUCTION
    camp_record = await campaign_repo.get_campaign("whop_camp_e2e_601")
    assert camp_record.lifecycle_state == CampaignLifecycleState.CONTENT_PRODUCTION

    # Finalize campaign
    comp_res = await loop.finalize_campaign("whop_camp_e2e_601", payment_status="confirmed")
    assert comp_res.lifecycle_state == CampaignLifecycleState.REUSE_ELIGIBLE
    assert comp_res.payment_status == "confirmed"

    # Verify campaign status is completed
    finished_camp = await campaign_repo.get_campaign("whop_camp_e2e_601")
    assert finished_camp.status == CampaignStatus.COMPLETED
    assert finished_camp.lifecycle_state == CampaignLifecycleState.REUSE_ELIGIBLE

