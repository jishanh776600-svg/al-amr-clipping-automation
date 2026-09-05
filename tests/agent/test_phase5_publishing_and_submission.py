"""Targeted Validation for Phase 5 Step 3: Real Campaign Submission + Publishing Operations."""

import pytest
import pytest_asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from clipping.agent.browser.driver import MockBrowserDriver
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
from clipping.agent.capabilities.base import CapabilityContext
from clipping.agent.escalation import EscalationReason
from clipping.agent.policy import ActionRiskTier, ActionScope, PolicyDecisionType, PolicyEngine, PolicyRule
from clipping.agent.publishing.adapters.base import PlatformStatusResult
from clipping.agent.publishing.adapters.instagram import InstagramPublishingAdapter
from clipping.agent.publishing.adapters.youtube import YouTubePublishingAdapter
from clipping.agent.publishing.capability import PublishingCapability
from clipping.agent.publishing.media_safety import MediaSafetyVerifier
from clipping.agent.publishing.models import (
    CampaignSubmissionRecord,
    PublishingContentMetadata,
    PublishingMode,
    SubmissionStatus,
)
from clipping.agent.publishing.reconciliation import PublishingReconciliationService
from clipping.agent.publishing.repository import CampaignSubmissionRepository
from clipping.agent.publishing.rule_engine import SubmissionRuleEngine
from clipping.agent.publishing.safety_gate import PublishingSafetyGate
from clipping.agent.vault.models import AccountMetadata, AccountPlatform, AccountStatus
from clipping.agent.vault.vault import EncryptedCredentialVault
from clipping.control.models import SystemControlState, SystemOperatingMode
from clipping.control.repository import ControlRepository
from clipping.publishing.client import MockYouTubeClient
from clipping.storage.local import LocalStorageDriver


@pytest_asyncio.fixture
async def pub_env(tmp_path: Path):
    storage_dir = tmp_path / "storage"
    media_dir = tmp_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    storage = LocalStorageDriver(root_dir=str(storage_dir))

    vault = EncryptedCredentialVault(storage_driver=storage, master_key="test_master_key_pub_456")
    campaign_repo = CampaignRepository(storage_driver=storage)
    submission_repo = CampaignSubmissionRepository(storage_driver=storage)
    control_repo = ControlRepository(storage_driver=storage)

    # Initialize default operational control state
    await control_repo.save_state(SystemControlState(mode=SystemOperatingMode.OPERATIONAL))

    policy = PolicyEngine(default_decision=PolicyDecisionType.ALLOW)

    # Create real mock-backed YouTube and Instagram adapters
    mock_yt_client = MockYouTubeClient()
    mock_browser_driver = MockBrowserDriver()
    yt_adapter = YouTubePublishingAdapter(client=mock_yt_client)
    ig_adapter = InstagramPublishingAdapter(browser_driver=mock_browser_driver)

    adapters = {
        AccountPlatform.YOUTUBE: yt_adapter,
        AccountPlatform.INSTAGRAM: ig_adapter,
    }

    reconciler = PublishingReconciliationService(
        repository=submission_repo,
        adapters=adapters,
        vault=vault,
    )

    capability = PublishingCapability(
        submission_repository=submission_repo,
        campaign_repository=campaign_repo,
        vault=vault,
        control_repository=control_repo,
        policy_engine=policy,
        adapters=adapters,
    )

    # Create a real test media file (>1KB)
    test_media_file = media_dir / "rendered_clip_001.mp4"
    test_media_file.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"A" * 4096)

    return {
        "storage": storage,
        "vault": vault,
        "campaign_repo": campaign_repo,
        "submission_repo": submission_repo,
        "control_repo": control_repo,
        "policy": policy,
        "mock_yt_client": mock_yt_client,
        "mock_browser_driver": mock_browser_driver,
        "yt_adapter": yt_adapter,
        "ig_adapter": ig_adapter,
        "reconciler": reconciler,
        "capability": capability,
        "test_media_path": str(test_media_file),
    }


# Test 1: Pre-Submission Rule Engine Validation
@pytest.mark.asyncio
async def test_01_submission_rule_engine_validation(pub_env):
    """Verifies pre-submission validation of campaign terms, duration, hashtags, quotas, and contradictions."""
    submission_repo: CampaignSubmissionRepository = pub_env["submission_repo"]
    rule_engine = SubmissionRuleEngine(repository=submission_repo)

    camp = CampaignRecord(
        campaign_id="camp_rule_101",
        name="AI Tech Insights Challenge",
        source="whop",
        posting_requirements=PostingRequirements(
            min_duration_seconds=30.0,
            max_duration_seconds=60.0,
            required_hashtags=["#AITech", "#FutureNow"],
            required_mentions=["@TechReview"],
        ),
        payout_terms=PayoutTerms(cpm_rate=2.0, remaining_budget=2000.0, budget_exhausted=False),
        account_requirements=AccountRequirements(allow_account_reuse=True),
        quotas=QuotasAndCaps(daily_creator_limit=2),
    )
    acc = AccountMetadata(
        platform=AccountPlatform.YOUTUBE,
        account_id="acc_yt_rule_101",
        username="techcreator",
        status=AccountStatus.ACTIVE,
    )

    valid_meta = PublishingContentMetadata(
        title="Top AI Innovations of 2026",
        description="Check out the newest AI models changing technology.",
        hashtags=["#AITech", "#FutureNow", "#Shorts"],
        mentions=["@TechReview"],
    )

    # 1. Valid submission should pass
    res_valid = await rule_engine.validate_submission(
        campaign=camp,
        account=acc,
        clip_id="clip_001",
        clip_duration_seconds=45.0,
        metadata=valid_meta,
        target_platform=AccountPlatform.YOUTUBE,
    )
    assert res_valid.is_valid is True
    assert len(res_valid.reasons) == 0

    # 2. Missing hashtag and invalid duration should fail with clear reasons
    invalid_meta = PublishingContentMetadata(
        title="AI Innovations",
        description="Cool tech clip.",
        hashtags=["#Shorts"],  # Missing required tags
    )
    res_invalid = await rule_engine.validate_submission(
        campaign=camp,
        account=acc,
        clip_id="clip_002",
        clip_duration_seconds=15.0,  # Below 30s min
        metadata=invalid_meta,
        target_platform=AccountPlatform.YOUTUBE,
    )
    assert res_invalid.is_valid is False
    assert any("duration" in r.lower() for r in res_invalid.reasons)
    assert any("missing required hashtag" in r.lower() for r in res_invalid.reasons)

    # 3. Contradictory campaign terms must trigger immediate escalation
    contradictory_camp = camp.model_copy(
        update={
            "posting_requirements": PostingRequirements(
                min_duration_seconds=90.0,
                max_duration_seconds=30.0,  # Contradiction: min > max
            )
        }
    )
    res_contra = await rule_engine.validate_submission(
        campaign=contradictory_camp,
        account=acc,
        clip_id="clip_003",
        clip_duration_seconds=45.0,
        metadata=valid_meta,
        target_platform=AccountPlatform.YOUTUBE,
    )
    assert res_contra.is_valid is False
    assert res_contra.escalation_required is True
    assert res_contra.escalation_context.reason == EscalationReason.CONTRADICTORY_INSTRUCTIONS


# Test 2: Publishing Safety Gate Integration (Master Control + PolicyEngine)
@pytest.mark.asyncio
async def test_02_publishing_safety_gate_integration(pub_env):
    """Verifies that publishing lock, emergency stop, automation pause, and policy rules gate operations."""
    control_repo: ControlRepository = pub_env["control_repo"]
    policy: PolicyEngine = pub_env["policy"]
    safety_gate = PublishingSafetyGate(control_repository=control_repo, policy_engine=policy)

    # 1. Normal operational mode passes
    res_open = await safety_gate.evaluate_safety("acc_01", AccountPlatform.YOUTUBE, PublishingMode.DRAFT)
    assert res_open.can_proceed is True

    # 2. Global publishing lock blocks publishing
    await control_repo.save_state(SystemControlState(mode=SystemOperatingMode.OPERATIONAL, publishing_locked=True))
    res_locked = await safety_gate.evaluate_safety("acc_01", AccountPlatform.YOUTUBE, PublishingMode.DRAFT)
    assert res_locked.can_proceed is False
    assert res_locked.is_globally_locked is True

    # 3. Emergency stop blocks publishing
    await control_repo.save_state(SystemControlState(mode=SystemOperatingMode.EMERGENCY_STOPPED, emergency_stopped=True))
    res_stopped = await safety_gate.evaluate_safety("acc_01", AccountPlatform.YOUTUBE, PublishingMode.DRAFT)
    assert res_stopped.can_proceed is False
    assert res_stopped.is_emergency_stopped is True

    # Restore operational control state
    await control_repo.save_state(SystemControlState(mode=SystemOperatingMode.OPERATIONAL))

    # 4. PolicyEngine requiring human confirmation for immediate public publishing
    strict_policy = PolicyEngine(
        rules=[
            PolicyRule(
                rule_id="RULE_CONFIRM_PUBLIC",
                description="Require confirmation before public publishing",
                capability_pattern="publishing",
                action_pattern="publish_*",
                decision=PolicyDecisionType.REQUIRE_CONFIRMATION,
                requires_human_confirmation=True,
                priority=200,
            )
        ]
    )

    strict_gate = PublishingSafetyGate(control_repository=control_repo, policy_engine=strict_policy)
    res_policy = await strict_gate.evaluate_safety("acc_01", AccountPlatform.YOUTUBE, PublishingMode.IMMEDIATE)
    assert res_policy.can_proceed is False
    assert res_policy.requires_human_confirmation is True


# Test 3: Idempotent Publishing & Duplicate Prevention
@pytest.mark.asyncio
async def test_03_idempotent_publishing_and_duplicate_prevention(pub_env):
    """Verifies that identical submissions are detected idempotently without duplicate uploads."""
    cap: PublishingCapability = pub_env["capability"]
    campaign_repo: CampaignRepository = pub_env["campaign_repo"]
    vault: EncryptedCredentialVault = pub_env["vault"]
    storage = pub_env["storage"]

    # Seed campaign and account
    camp = CampaignRecord(
        campaign_id="camp_idemp_201",
        name="Scalable Systems Shorts",
        source="whop",
        posting_requirements=PostingRequirements(
            min_duration_seconds=10.0,
            max_duration_seconds=90.0,
            required_hashtags=["#Scalable"],
        ),
        payout_terms=PayoutTerms(cpm_rate=2.0),
    )
    await campaign_repo.save_campaign(camp)

    acc = AccountMetadata(
        platform=AccountPlatform.YOUTUBE,
        account_id="acc_yt_idemp_201",
        username="scalesystems",
        status=AccountStatus.ACTIVE,
    )
    await vault.save_account(acc)

    context = CapabilityContext(
        task_id="task_idemp_run",
        inputs={
            "action": "submit_and_publish",
            "campaign_id": "camp_idemp_201",
            "clip_id": "clip_scale_001",
            "account_id": "acc_yt_idemp_201",
            "platform": "youtube",
            "publishing_mode": "draft",
            "media_path": pub_env["test_media_path"],
            "title": "Scalable Systems Highlights",
            "duration_seconds": 40.0,
        },
        storage_driver=storage,
    )

    # First execution succeeds and uploads video
    res1 = await cap.execute(context)
    assert res1.success is True
    sub1 = res1.outputs["submission"]
    assert sub1["platform_post_id"] is not None
    assert sub1["current_status"] == "submitted"

    # Second execution on identical clip + mode must hit idempotency cache
    res2 = await cap.execute(context)
    assert res2.success is True
    assert res2.outputs.get("idempotent_hit") is True
    assert res2.outputs["submission"]["submission_id"] == sub1["submission_id"]
    assert res2.outputs["submission"]["platform_post_id"] == sub1["platform_post_id"]


# Test 4: Platform Adapters (YouTube Real Service & Instagram Challenge Escalation)
@pytest.mark.asyncio
async def test_04_platform_adapters_and_security_escalation(pub_env):
    """Verifies YouTube publishing output and Instagram security challenge escalation."""
    yt_adapter: YouTubePublishingAdapter = pub_env["yt_adapter"]
    ig_adapter: InstagramPublishingAdapter = pub_env["ig_adapter"]
    mock_browser = pub_env["mock_browser_driver"]

    dummy_sub = CampaignSubmissionRecord(
        submission_id="sub_test_adapter_01",
        campaign_id="camp_test_01",
        account_id="acc_01",
        platform=AccountPlatform.YOUTUBE,
        clip_id="clip_001",
        publishing_mode=PublishingMode.SCHEDULED,
        content_metadata=PublishingContentMetadata(
            title="Automated High-Signal Clip",
            description="Testing real adapter contracts",
            hashtags=["#shorts", "#automation"],
            scheduled_publish_at=datetime.now(timezone.utc) + timedelta(days=1),
        ),
        idempotency_key="test_key_01",
    )

    # 1. YouTube scheduled publication via adapter
    yt_res = await yt_adapter.publish(dummy_sub, pub_env["test_media_path"], credentials={})
    assert yt_res.success is True
    assert yt_res.status == SubmissionStatus.SCHEDULED
    assert yt_res.platform_post_id.startswith("yt_mock_")

    # 2. Instagram browser workflow encountering CAPTCHA challenge
    mock_browser.simulate_captcha = True

    ig_sub = dummy_sub.model_copy(update={"platform": AccountPlatform.INSTAGRAM})
    ig_res = await ig_adapter.publish(ig_sub, pub_env["test_media_path"], credentials={})
    assert ig_res.success is False
    assert ig_res.status == SubmissionStatus.ESCALATED
    assert ig_res.escalation_required is True
    assert ig_res.escalation_context.reason == EscalationReason.CAPTCHA_CHALLENGE


# Test 5: Platform Result Reconciliation
@pytest.mark.asyncio
async def test_05_platform_result_reconciliation(pub_env):
    """Verifies comparing local submission state against platform state and correcting discrepancies."""
    reconciler: PublishingReconciliationService = pub_env["reconciler"]
    submission_repo: CampaignSubmissionRepository = pub_env["submission_repo"]
    mock_yt: MockYouTubeClient = pub_env["mock_yt_client"]

    # Register live video in mock client
    mock_yt.uploaded_videos["mock_yt_live_123"] = {
        "status": {"uploadStatus": "processed", "privacyStatus": "public"}
    }

    # 1. Scenario A: Video is live on platform -> matches local state
    sub_live = CampaignSubmissionRecord(
        submission_id="sub_recon_live_01",
        campaign_id="camp_recon_01",
        account_id="acc_yt_01",
        platform=AccountPlatform.YOUTUBE,
        clip_id="clip_live_01",
        platform_post_id="mock_yt_live_123",
        publishing_mode=PublishingMode.IMMEDIATE,
        current_status=SubmissionStatus.PUBLISHED,
        content_metadata=PublishingContentMetadata(title="Live Video", description="Reconcile test"),
        idempotency_key="key_live_01",
    )
    await submission_repo.save_submission(sub_live)


    recon_res_live = await reconciler.reconcile_submission("camp_recon_01", "sub_recon_live_01")
    assert recon_res_live.state_corrected is False
    assert recon_res_live.reconciled_status == SubmissionStatus.PUBLISHED

    # 2. Scenario B: Video was removed / rejected on platform -> local state corrected to REJECTED
    sub_deleted = CampaignSubmissionRecord(
        submission_id="sub_recon_del_02",
        campaign_id="camp_recon_01",
        account_id="acc_yt_01",
        platform=AccountPlatform.YOUTUBE,
        clip_id="clip_del_02",
        platform_post_id="non_existent_yt_id",
        publishing_mode=PublishingMode.IMMEDIATE,
        current_status=SubmissionStatus.PUBLISHED,
        content_metadata=PublishingContentMetadata(title="Deleted Video", description="Reconcile test"),
        idempotency_key="key_del_02",
    )
    await submission_repo.save_submission(sub_deleted)

    recon_res_del = await reconciler.reconcile_submission("camp_recon_01", "sub_recon_del_02")
    assert recon_res_del.state_corrected is True
    assert recon_res_del.reconciled_status == SubmissionStatus.REJECTED

    # Verify durable record was updated with state transition history
    saved_del = await submission_repo.get_submission("camp_recon_01", "sub_recon_del_02")
    assert saved_del.current_status == SubmissionStatus.REJECTED
    assert len(saved_del.state_history) >= 1
    assert "Platform reconciliation corrected" in saved_del.state_history[-1].reason


# Test 6: Media Safety Verifier & Campaign Lifecycle Progression
@pytest.mark.asyncio
async def test_06_media_safety_and_campaign_lifecycle(pub_env, tmp_path):
    """Verifies that corrupted/unverified media is rejected and successful submission advances campaign lifecycle."""
    cap: PublishingCapability = pub_env["capability"]
    campaign_repo: CampaignRepository = pub_env["campaign_repo"]
    vault: EncryptedCredentialVault = pub_env["vault"]
    storage = pub_env["storage"]

    # 1. Media Safety: Corrupted / empty file must be rejected
    empty_file = tmp_path / "empty_clip.mp4"
    empty_file.write_bytes(b"")  # 0 bytes

    verifier = MediaSafetyVerifier(storage_driver=storage)
    safety_eval = await verifier.verify_media(
        media_path=str(empty_file),
        campaign_id="camp_lifecycle_01",
        clip_id="clip_empty_01",
        expected_platform=AccountPlatform.YOUTUBE,
    )
    assert safety_eval.is_safe is False
    assert any("corrupted or incomplete" in r.lower() for r in safety_eval.reasons)

    # 2. End-to-end publish advances CampaignLifecycleState from CONTENT_PRODUCTION to CAMPAIGN_ACTIVE
    camp = CampaignRecord(
        campaign_id="camp_lifecycle_301",
        name="Autonomous Growth Sprint",
        source="whop",
        lifecycle_state=CampaignLifecycleState.CONTENT_PRODUCTION,
        posting_requirements=PostingRequirements(
            min_duration_seconds=10.0,
            max_duration_seconds=90.0,
            required_hashtags=["#Growth"],
        ),
        payout_terms=PayoutTerms(cpm_rate=2.0),
    )
    await campaign_repo.save_campaign(camp)

    acc = AccountMetadata(
        platform=AccountPlatform.YOUTUBE,
        account_id="acc_yt_growth_301",
        username="growthcreator",
        status=AccountStatus.ACTIVE,
    )
    await vault.save_account(acc)

    context = CapabilityContext(
        task_id="task_lifecycle_run",
        inputs={
            "action": "submit_and_publish",
            "campaign_id": "camp_lifecycle_301",
            "clip_id": "clip_growth_001",
            "account_id": "acc_yt_growth_301",
            "platform": "youtube",
            "publishing_mode": "draft",
            "media_path": pub_env["test_media_path"],
            "title": "Autonomous Growth Strategy",
            "duration_seconds": 35.0,
        },
        storage_driver=storage,
    )

    pub_result = await cap.execute(context)
    assert pub_result.success is True

    # Verify campaign record advanced to CAMPAIGN_ACTIVE
    updated_camp = await campaign_repo.get_campaign("camp_lifecycle_301")
    assert updated_camp.lifecycle_state == CampaignLifecycleState.CAMPAIGN_ACTIVE
