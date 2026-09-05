"""Targeted Validation for Phase 5 Step 1: Autonomous Campaign Intelligence & Whop Integration."""

import pytest
import pytest_asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from clipping.agent.account.capability import AccountManagementCapability
from clipping.agent.bridge.campaign_clipping_bridge import CampaignClippingBridge
from clipping.agent.browser.driver import MockBrowserDriver
from clipping.agent.campaign.decision import CampaignDecisionEngine
from clipping.agent.campaign.discovery import CampaignDiscoveryCapability
from clipping.agent.campaign.evaluator import CampaignEvaluator, OpportunityTier
from clipping.agent.campaign.intelligence import CampaignIntelligenceEngine
from clipping.agent.campaign.models import (
    AccountRequirements,
    CampaignDuration,
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
from clipping.agent.campaign.sources.registry import CampaignSourceRegistry
from clipping.agent.campaign.sources.whop import WhopCampaignSource
from clipping.agent.capabilities.base import CapabilityContext
from clipping.agent.cloud.queue import CloudTaskQueue
from clipping.agent.loop import AutonomousOperationsLoop
from clipping.agent.policy import PolicyDecisionType, PolicyEngine
from clipping.agent.repository import TaskRepository
from clipping.agent.vault.models import AccountMetadata, AccountPlatform, AccountStatus
from clipping.agent.vault.vault import EncryptedCredentialVault
from clipping.storage.local import LocalStorageDriver


@pytest_asyncio.fixture
async def phase5_env(tmp_path: Path):
    storage = LocalStorageDriver(root_dir=str(tmp_path / "storage"))
    vault = EncryptedCredentialVault(storage_driver=storage, master_key="test_master_key_12345")
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
        "policy": policy,
        "campaign_repo": campaign_repo,
        "mock_browser": mock_browser,
        "evaluator": evaluator,
        "whop_source": whop_source,
        "source_registry": source_registry,
        "discovery_cap": discovery_cap,
        "decision_engine": decision_engine,
        "bridge": bridge,
        "loop": loop,
    }


# 1. Whop Campaign Source Normalization & Extensibility
@pytest.mark.asyncio
async def test_01_whop_source_normalization(phase5_env):
    """1. Whop source extracts and normalizes payout, CPM, footage URLs, and content terms."""
    whop = phase5_env["whop_source"]
    raw_whop_data = [
        {
            "id": "whop_creator_99",
            "title": "FinTech Viral Clips Challenge",
            "community": "Alpha FinTech Hub",
            "cpm_rate": 2.50,
            "total_budget": 10000.0,
            "source_video_uris": ["https://youtube.com/watch?v=real_long_form_01"],
            "hashtags": ["fintech", "money"],
            "allowed_platforms": ["youtube_shorts"],
            "daily_creator_limit": 5,
        }
    ]

    discovered = await whop.discover(metadata={"raw_campaigns": raw_whop_data})
    assert len(discovered) == 1
    brief = discovered[0]

    assert brief["campaign_id"] == "whop_creator_99"
    assert brief["name"] == "FinTech Viral Clips Challenge"
    assert brief["payout_terms"]["cpm_rate"] == 2.50
    assert brief["payout_terms"]["total_budget"] == 10000.0
    assert "https://youtube.com/watch?v=real_long_form_01" in brief["discovered_source_uris"]
    assert "#fintech" in brief["posting_requirements"]["required_hashtags"]
    assert brief["quotas"]["daily_creator_limit"] == 5


# 2. CPM Sweet Spot & Holistic Evaluation
@pytest.mark.asyncio
async def test_02_campaign_evaluator_cpm_and_ranking(phase5_env):
    """2. Evaluator prioritizes $2 CPM target, fairly assesses $1-$5, and evaluates exceptional tiers."""
    evaluator: CampaignEvaluator = phase5_env["evaluator"]

    # Campaign A: $2.00 CPM (Ideal sweet spot)
    camp_ideal = CampaignRecord(
        campaign_id="camp_eval_ideal",
        name="Ideal $2 CPM Campaign",
        source="https://whop.com",
        payout_terms=PayoutTerms(cpm_rate=2.0, total_budget=5000.0, remaining_budget=4000.0),
        source_material=SourceMaterial(video_urls=["https://youtube.com/watch?v=v1", "https://youtube.com/watch?v=v2"]),
        discovered_source_uris=["https://youtube.com/watch?v=v1", "https://youtube.com/watch?v=v2"],
    )

    # Campaign B: $0.50 CPM (Sub-target)
    camp_low = CampaignRecord(
        campaign_id="camp_eval_low",
        name="Low CPM Campaign",
        source="https://whop.com",
        payout_terms=PayoutTerms(cpm_rate=0.50, total_budget=1000.0),
        discovered_source_uris=["https://youtube.com/watch?v=v3"],
    )

    # Campaign C: $8.00 CPM (Exceptional tier)
    camp_high = CampaignRecord(
        campaign_id="camp_eval_high",
        name="High CPM Campaign",
        source="https://whop.com",
        payout_terms=PayoutTerms(cpm_rate=8.0, total_budget=2000.0),
        discovered_source_uris=["https://youtube.com/watch?v=v4"],
    )

    score_ideal = evaluator.evaluate(camp_ideal)
    score_low = evaluator.evaluate(camp_low)
    score_high = evaluator.evaluate(camp_high)

    # Ideal $2 CPM achieves top tier score
    assert score_ideal.tier == OpportunityTier.STRONG_PURSUE
    assert score_ideal.overall_score >= 85.0
    assert score_ideal.cpm_score >= 95.0

    # Low CPM is penalized but viable if volume warrants
    assert score_low.overall_score < score_ideal.overall_score
    assert score_low.cpm_score < 60.0

    # High $8 CPM is recognized as viable opportunity with earning potential
    assert score_high.is_worth_pursuing() is True
    assert score_high.estimated_earning_potential > score_low.estimated_earning_potential

    # Ranking order: Ideal and High lead Low
    ranked = evaluator.rank([camp_low, camp_ideal, camp_high])
    assert ranked[0][0].campaign_id in ("camp_eval_ideal", "camp_eval_high")
    assert ranked[-1][0].campaign_id == "camp_eval_low"


# 3. Duplicate Detection and Term Drift Auditing
@pytest.mark.asyncio
async def test_03_duplicate_and_term_change_intelligence(phase5_env):
    """3. Intelligence engine detects duplicate campaigns and records changes in terms."""
    now = datetime.now(timezone.utc)
    camp_v1 = CampaignRecord(
        campaign_id="camp_whop_orig",
        name="Crypto Insights Daily",
        source="https://whop.com/hub/crypto",
        payout_terms=PayoutTerms(cpm_rate=2.00, remaining_budget=5000.0),
        posting_requirements=PostingRequirements(required_hashtags=["#crypto"]),
    )

    # Duplicate candidate with updated terms (CPM changed from $2.00 to $2.50)
    camp_v2 = CampaignRecord(
        campaign_id="camp_whop_orig",
        name="Crypto Insights Daily",
        source="https://whop.com/hub/crypto",
        payout_terms=PayoutTerms(cpm_rate=2.50, remaining_budget=3500.0),
        posting_requirements=PostingRequirements(required_hashtags=["#crypto", "#bitcoin"]),
    )

    # Duplicate detection matches by campaign_id and source
    dup = CampaignIntelligenceEngine.detect_duplicate(camp_v2, [camp_v1])
    assert dup is not None
    assert dup.campaign_id == "camp_whop_orig"

    # Term change detection captures rate increase, budget drop, and new hashtag
    changes = CampaignIntelligenceEngine.detect_term_changes(camp_v1, camp_v2, now=now)
    assert len(changes) >= 3
    field_names = [c.field_name for c in changes]
    assert "payout_terms.cpm_rate" in field_names
    assert "payout_terms.remaining_budget" in field_names
    assert "posting_requirements.required_hashtags" in field_names

    # Merged record retains full audit history
    merged = CampaignIntelligenceEngine.audit_and_merge(camp_v1, camp_v2, now=now)
    assert merged.payout_terms.cpm_rate == 2.50
    assert len(merged.term_changes) >= 3


# 4. End-to-End Whop Discovery -> Intelligent Evaluation -> Production Pipeline Bridge
@pytest.mark.asyncio
async def test_04_autonomous_whop_campaign_to_clipping_pipeline(phase5_env):
    """4. Whop campaign is discovered, evaluated, matched with vault account, and bridged to CloudTaskQueue."""
    vault = phase5_env["vault"]
    loop = phase5_env["loop"]
    queue = phase5_env["queue"]
    task_repo = phase5_env["task_repo"]

    # Register an approved YouTube Shorts account in credential vault
    await vault.save_account(
        AccountMetadata(
            platform=AccountPlatform.YOUTUBE,
            account_id="yt_channel_prod",
            username="AlAmrShorts",
            status=AccountStatus.ACTIVE,
            reuse_eligibility=True,
        ),
        sensitive_credentials={"oauth_refresh_token": "SENSITIVE_SECRET_MASKED"},
    )

    # Feed discovered Whop campaign into autonomous loop
    whop_raw_feed = [
        {
            "id": "whop_autoloop_01",
            "title": "Cloud Scale AI Shorts",
            "source_url": "https://whop.com/campaigns/cloud_ai",
            "cpm_rate": 2.20,
            "total_budget": 8000.0,
            "source_video_uris": ["https://youtube.com/watch?v=cloud_ai_real_vod"],
            "hashtags": ["ai", "automation"],
            "allowed_platforms": ["youtube_shorts"],
            "daily_creator_limit": 3,
        }
    ]

    cycle_res = await loop.run_discovery_and_dispatch_cycle(
        raw_campaigns=whop_raw_feed,
    )

    assert cycle_res["cycle_status"] == "completed"
    assert cycle_res["campaigns_discovered"] == 1
    assert len(cycle_res["tasks_enqueued"]) == 1

    # Verify task in CloudTaskQueue for cloud worker processing
    enqueued_task_id = cycle_res["tasks_enqueued"][0]
    task_record = await task_repo.get_task(enqueued_task_id)
    assert task_record is not None
    assert task_record.task_type.value == "media_clipping"
    assert task_record.inputs["source_uri"] == "https://youtube.com/watch?v=cloud_ai_real_vod"
    assert task_record.inputs["account_id"] == "yt_channel_prod"
    assert "#ai" in task_record.inputs["hashtags"]

    # Verify cloud queue item
    q_item = await queue.get_item(enqueued_task_id)
    assert q_item is not None
    assert q_item.status.value == "pending"


# 5. Contradiction and Disqualification Handling
@pytest.mark.asyncio
async def test_05_contradiction_and_disqualification(phase5_env):
    """5. Contradictory rules escalate to operator; expired or budget-exhausted campaigns reject cleanly."""
    evaluator: CampaignEvaluator = phase5_env["evaluator"]

    # Contradictory duration
    camp_contradictory = CampaignRecord(
        campaign_id="camp_bad_rules",
        name="Impossible Campaign",
        source="https://whop.com",
        posting_requirements=PostingRequirements(min_duration_seconds=75.0, max_duration_seconds=45.0),
    )
    score_contra = evaluator.evaluate(camp_contradictory)
    assert score_contra.tier == OpportunityTier.REJECT
    assert "Contradictory" in score_contra.recommendation_notes[0]

    # Exhausted budget
    camp_exhausted = CampaignRecord(
        campaign_id="camp_dead_pool",
        name="Exhausted Pool Campaign",
        source="https://whop.com",
        payout_terms=PayoutTerms(cpm_rate=2.0, total_budget=5000.0, remaining_budget=0.0, budget_exhausted=True),
    )
    score_exhausted = evaluator.evaluate(camp_exhausted)
    assert score_exhausted.tier == OpportunityTier.REJECT
    assert score_exhausted.is_worth_pursuing() is False
