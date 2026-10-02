"""Unit tests for Whop Campaign State Machine and Persistent Ledger (Step 3)."""

import json
from pathlib import Path
import pytest

from whop.catalog import DiscoveredCampaign, build_discovered_campaign
from whop.models import (
    CampaignEvent,
    CampaignRecord,
    CampaignState,
    InvalidStateTransitionError,
    validate_transition,
)
from whop.ledger import CampaignLedger


@pytest.fixture
def temp_ledger(tmp_path: Path) -> CampaignLedger:
    """Fixture providing an isolated CampaignLedger SQLite instance."""
    db_file = tmp_path / "test_whop_ledger.db"
    return CampaignLedger(db_path=db_file)


@pytest.fixture
def sample_eligible_campaign() -> DiscoveredCampaign:
    """Returns a realistic eligible campaign."""
    return build_discovered_campaign(
        campaign_id="camp-eligible-1",
        title="AI SaaS Growth Secrets",
        campaign_url="https://whop.com/discover/camp-eligible-1",
        payout_raw="$1.50 CPM",
        platforms_raw="YouTube, TikTok",
        source_urls=["https://youtube.com/watch?v=12345678901"],
        guideline_urls=["https://whop.com/guidelines/1"],
    )


@pytest.fixture
def sample_rejected_campaign() -> DiscoveredCampaign:
    """Returns a realistic rejected campaign (low CPM)."""
    return build_discovered_campaign(
        campaign_id="camp-rejected-1",
        title="Budget Fitness Tips",
        campaign_url="https://whop.com/discover/camp-rejected-1",
        payout_raw="$0.50 CPM",
        platforms_raw="TikTok",
        source_urls=["https://tiktok.com/@user/video/123"],
    )


# ---------------------------------------------------------------------------
# Test 1: New campaign lifecycle creation
# ---------------------------------------------------------------------------

def test_new_eligible_campaign_ingestion(temp_ledger: CampaignLedger, sample_eligible_campaign: DiscoveredCampaign):
    rec, is_new = temp_ledger.ingest_discovered_campaign(sample_eligible_campaign)

    assert is_new is True
    assert rec.campaign_id == "camp-eligible-1"
    assert rec.current_state == CampaignState.ELIGIBLE
    assert rec.cpm == 1.50
    assert rec.recommended_account == "ACCOUNT_1_FINANCE_BUSINESS"

    # Verify audit trail events
    events = temp_ledger.list_events("camp-eligible-1")
    assert len(events) == 3
    assert events[0].new_state == CampaignState.DISCOVERED.value
    assert events[1].previous_state == CampaignState.DISCOVERED.value
    assert events[1].new_state == CampaignState.VALIDATING.value
    assert events[2].previous_state == CampaignState.VALIDATING.value
    assert events[2].new_state == CampaignState.ELIGIBLE.value


def test_new_rejected_campaign_ingestion(temp_ledger: CampaignLedger, sample_rejected_campaign: DiscoveredCampaign):
    rec, is_new = temp_ledger.ingest_discovered_campaign(sample_rejected_campaign)

    assert is_new is True
    assert rec.campaign_id == "camp-rejected-1"
    assert rec.current_state == CampaignState.REJECTED
    assert rec.cpm == 0.50

    events = temp_ledger.list_events("camp-rejected-1")
    assert len(events) == 3
    assert events[0].new_state == CampaignState.DISCOVERED.value
    assert events[1].new_state == CampaignState.VALIDATING.value
    assert events[2].new_state == CampaignState.REJECTED.value
    assert "REJECTED_PAYOUT" in events[2].reason


# ---------------------------------------------------------------------------
# Test 2 & 3: Idempotent Rediscovery (Once, Twice, 10 Times)
# ---------------------------------------------------------------------------

def test_idempotent_rediscovery_single_duplicate(temp_ledger: CampaignLedger, sample_eligible_campaign: DiscoveredCampaign):
    # First discovery
    rec1, is_new1 = temp_ledger.ingest_discovered_campaign(sample_eligible_campaign)
    assert is_new1 is True

    # Second discovery (identical payload)
    rec2, is_new2 = temp_ledger.ingest_discovered_campaign(sample_eligible_campaign)
    assert is_new2 is False
    assert rec2.campaign_id == rec1.campaign_id
    assert rec2.current_state == CampaignState.ELIGIBLE

    # Ensure exactly 1 row exists in ledger
    all_campaigns = temp_ledger.list_campaigns()
    assert len(all_campaigns) == 1
    assert all_campaigns[0].campaign_id == "camp-eligible-1"

    # Audit events count should remain 3 (no duplicate events created if unchanged)
    events = temp_ledger.list_events("camp-eligible-1")
    assert len(events) == 3


def test_idempotent_rediscovery_10_times(temp_ledger: CampaignLedger, sample_eligible_campaign: DiscoveredCampaign):
    for i in range(10):
        rec, is_new = temp_ledger.ingest_discovered_campaign(sample_eligible_campaign)
        if i == 0:
            assert is_new is True
        else:
            assert is_new is False

    # Still exactly 1 record in database
    all_campaigns = temp_ledger.list_campaigns()
    assert len(all_campaigns) == 1
    events = temp_ledger.list_events("camp-eligible-1")
    assert len(events) == 3


# ---------------------------------------------------------------------------
# Test 4: Metadata Updates & Diff Logging
# ---------------------------------------------------------------------------

def test_metadata_update_logging(temp_ledger: CampaignLedger):
    c1 = build_discovered_campaign(
        campaign_id="camp-meta-1",
        title="Initial Title",
        campaign_url="https://whop.com/discover/camp-meta-1",
        payout_raw="$1.20 CPM",
        platforms_raw="YouTube",
        source_urls=["https://youtube.com/watch?v=11111111111"],
    )
    rec1, is_new1 = temp_ledger.ingest_discovered_campaign(c1)
    assert is_new1 is True
    assert rec1.cpm == 1.20

    # Rediscover with updated CPM ($2.00) and new title
    c2 = build_discovered_campaign(
        campaign_id="camp-meta-1",
        title="Updated Title V2",
        campaign_url="https://whop.com/discover/camp-meta-1",
        payout_raw="$2.00 CPM",
        platforms_raw="YouTube",
        source_urls=["https://youtube.com/watch?v=11111111111"],
    )
    rec2, is_new2 = temp_ledger.ingest_discovered_campaign(c2)
    assert is_new2 is False
    assert rec2.title == "Updated Title V2"
    assert rec2.cpm == 2.00

    # Verify METADATA_UPDATED event
    events = temp_ledger.list_events("camp-meta-1")
    update_events = [e for e in events if e.reason == "METADATA_UPDATED"]
    assert len(update_events) == 1

    diff = json.loads(update_events[0].metadata_json)
    assert diff["cpm"]["old"] == 1.20
    assert diff["cpm"]["new"] == 2.00
    assert diff["title"]["old"] == "Initial Title"
    assert diff["title"]["new"] == "Updated Title V2"


# ---------------------------------------------------------------------------
# Test 5: Re-evaluation of Rejected Campaign on Rediscovery
# ---------------------------------------------------------------------------

def test_rejected_campaign_becomes_eligible_on_rediscovery(temp_ledger: CampaignLedger):
    # Initial discovery: CPM too low ($0.50) -> REJECTED
    c_low = build_discovered_campaign(
        campaign_id="camp-promo-1",
        title="Finance Newsletter",
        campaign_url="https://whop.com/discover/camp-promo-1",
        payout_raw="$0.50 CPM",
        platforms_raw="YouTube",
        source_urls=["https://youtube.com/watch?v=22222222222"],
    )
    rec1, _ = temp_ledger.ingest_discovered_campaign(c_low)
    assert rec1.current_state == CampaignState.REJECTED

    # Rediscovery with higher payout ($1.50) -> becomes ELIGIBLE
    c_high = build_discovered_campaign(
        campaign_id="camp-promo-1",
        title="Finance Newsletter",
        campaign_url="https://whop.com/discover/camp-promo-1",
        payout_raw="$1.50 CPM",
        platforms_raw="YouTube",
        source_urls=["https://youtube.com/watch?v=22222222222"],
    )
    rec2, _ = temp_ledger.ingest_discovered_campaign(c_high)
    assert rec2.current_state == CampaignState.ELIGIBLE
    assert rec2.cpm == 1.50

    # Events should reflect re-evaluation
    events = temp_ledger.list_events("camp-promo-1")
    reeval_events = [e for e in events if "Eligibility re-evaluated" in e.reason]
    assert len(reeval_events) == 1
    assert reeval_events[0].new_state == CampaignState.ELIGIBLE.value


# ---------------------------------------------------------------------------
# Test 6: Legal State Transitions
# ---------------------------------------------------------------------------

def test_legal_lifecycle_transitions(temp_ledger: CampaignLedger, sample_eligible_campaign: DiscoveredCampaign):
    rec, _ = temp_ledger.ingest_discovered_campaign(sample_eligible_campaign)
    assert rec.current_state == CampaignState.ELIGIBLE

    # ELIGIBLE -> CLAIMING
    r1 = temp_ledger.transition_state(rec.campaign_id, CampaignState.CLAIMING, reason="Starting claim")
    assert r1.current_state == CampaignState.CLAIMING

    # CLAIMING -> CLAIMED
    r2 = temp_ledger.transition_state(rec.campaign_id, CampaignState.CLAIMED, reason="Claim verified")
    assert r2.current_state == CampaignState.CLAIMED

    # CLAIMED -> INGESTED
    r3 = temp_ledger.transition_state(rec.campaign_id, CampaignState.INGESTED, reason="Media downloaded")
    assert r3.current_state == CampaignState.INGESTED

    # INGESTED -> RENDERING
    r4 = temp_ledger.transition_state(rec.campaign_id, CampaignState.RENDERING, reason="AutoClip render initiated")
    assert r4.current_state == CampaignState.RENDERING

    # RENDERING -> RENDER_READY
    r5 = temp_ledger.transition_state(rec.campaign_id, CampaignState.RENDER_READY, reason="Clips rendered")
    assert r5.current_state == CampaignState.RENDER_READY

    # RENDER_READY -> AWAITING_APPROVAL
    r6 = temp_ledger.transition_state(rec.campaign_id, CampaignState.AWAITING_APPROVAL, reason="Sent to Telegram")
    assert r6.current_state == CampaignState.AWAITING_APPROVAL

    # AWAITING_APPROVAL -> APPROVED
    r7 = temp_ledger.transition_state(rec.campaign_id, CampaignState.APPROVED, reason="Approved by admin")
    assert r7.current_state == CampaignState.APPROVED

    # APPROVED -> SUBMITTING
    r8 = temp_ledger.transition_state(rec.campaign_id, CampaignState.SUBMITTING, reason="Posting to Whop")
    assert r8.current_state == CampaignState.SUBMITTING

    # SUBMITTING -> SUBMITTED (Terminal)
    r9 = temp_ledger.transition_state(rec.campaign_id, CampaignState.SUBMITTED, reason="Submission successful")
    assert r9.current_state == CampaignState.SUBMITTED

    # Idempotent self-transition does not raise
    r_same = temp_ledger.transition_state(rec.campaign_id, CampaignState.SUBMITTED)
    assert r_same.current_state == CampaignState.SUBMITTED


# ---------------------------------------------------------------------------
# Test 7: Illegal State Transitions
# ---------------------------------------------------------------------------

def test_illegal_state_transitions_raise_error(temp_ledger: CampaignLedger, sample_eligible_campaign: DiscoveredCampaign):
    rec, _ = temp_ledger.ingest_discovered_campaign(sample_eligible_campaign)
    assert rec.current_state == CampaignState.ELIGIBLE

    # ELIGIBLE cannot jump directly to SUBMITTED
    with pytest.raises(InvalidStateTransitionError) as excinfo:
        temp_ledger.transition_state(rec.campaign_id, CampaignState.SUBMITTED)
    assert "Illegal state machine transition" in str(excinfo.value)

    # Transition to CLAIMED without CLAIMING is illegal
    with pytest.raises(InvalidStateTransitionError):
        temp_ledger.transition_state(rec.campaign_id, CampaignState.CLAIMED)

    # Non-existent campaign raises KeyError
    with pytest.raises(KeyError):
        temp_ledger.transition_state("non-existent-campaign-id", CampaignState.CLAIMING)


# ---------------------------------------------------------------------------
# Test 8: Retry and Error Diagnostics
# ---------------------------------------------------------------------------

def test_record_retry_attempt(temp_ledger: CampaignLedger, sample_eligible_campaign: DiscoveredCampaign):
    rec, _ = temp_ledger.ingest_discovered_campaign(sample_eligible_campaign)
    assert rec.retry_count == 0
    assert rec.last_error is None

    # First failure attempt
    r1 = temp_ledger.record_retry_attempt(rec.campaign_id, "Network timeout connecting to host")
    assert r1.retry_count == 1
    assert "Network timeout" in r1.last_error
    assert r1.last_attempt_at is not None

    # Second failure attempt
    r2 = temp_ledger.record_retry_attempt(rec.campaign_id, "Rate limit exceeded (429)")
    assert r2.retry_count == 2
    assert "Rate limit" in r2.last_error


# ---------------------------------------------------------------------------
# Test 9: Read-Only Stale-State Inspection
# ---------------------------------------------------------------------------

def test_stale_campaign_inspection_is_read_only(temp_ledger: CampaignLedger, sample_eligible_campaign: DiscoveredCampaign):
    rec, _ = temp_ledger.ingest_discovered_campaign(sample_eligible_campaign)
    temp_ledger.transition_state(rec.campaign_id, CampaignState.CLAIMING)

    # Check stale inspector with 0 threshold (everything in transient state is older than 0s)
    stale = temp_ledger.inspect_stale_campaigns(max_age_seconds=0)
    assert len(stale) == 1
    assert stale[0]["campaign_id"] == rec.campaign_id
    assert stale[0]["current_state"] == CampaignState.CLAIMING.value
    assert stale[0]["possible_stale"] is True

    # Verify that the database was NOT modified (read-only verification)
    refreshed = temp_ledger.get_campaign(rec.campaign_id)
    assert refreshed.current_state == CampaignState.CLAIMING
    assert refreshed.retry_count == 0


# ---------------------------------------------------------------------------
# Test 10: Real Step 2 JSON Artifact Ingestion Test
# ---------------------------------------------------------------------------

def test_real_step2_json_ingestion(temp_ledger: CampaignLedger):
    artifact_path = Path("scratch/step2_live_cr_download/whop-campaign-discovery/whop_campaign_discovery.json")
    if not artifact_path.exists():
        pytest.skip("Step 2 real discovery JSON artifact not present locally.")

    with open(artifact_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    raw_campaigns = data.get("campaigns", [])
    assert len(raw_campaigns) == 8

    # Ingest all 8 campaigns
    for c_dict in raw_campaigns:
        camp = DiscoveredCampaign(
            campaign_id=c_dict["campaign_id"],
            title=c_dict["title"],
            campaign_url=c_dict["campaign_url"],
            payout_raw=c_dict["payout_raw"],
            cpm=c_dict["cpm"],
            platforms=c_dict["platforms"],
            source_urls=c_dict["source_urls"],
            guideline_urls=c_dict["guideline_urls"],
            eligible=c_dict["eligible"],
            eligibility_reasons=c_dict["eligibility_reasons"],
            recommended_account=c_dict["recommended_account"],
            discovered_at=c_dict["discovered_at"],
        )
        temp_ledger.ingest_discovered_campaign(camp)

    # Verify database counts
    all_campaigns = temp_ledger.list_campaigns(limit=100)
    assert len(all_campaigns) == 8

    eligible_campaigns = temp_ledger.list_campaigns(state=CampaignState.ELIGIBLE)
    rejected_campaigns = temp_ledger.list_campaigns(state=CampaignState.REJECTED)

    assert len(eligible_campaigns) == 3
    assert len(rejected_campaigns) == 5

    # Rediscover all 8 campaigns again -> verify idempotency
    for c_dict in raw_campaigns:
        camp = DiscoveredCampaign(
            campaign_id=c_dict["campaign_id"],
            title=c_dict["title"],
            campaign_url=c_dict["campaign_url"],
            payout_raw=c_dict["payout_raw"],
            cpm=c_dict["cpm"],
            platforms=c_dict["platforms"],
            source_urls=c_dict["source_urls"],
            guideline_urls=c_dict["guideline_urls"],
            eligible=c_dict["eligible"],
            eligibility_reasons=c_dict["eligibility_reasons"],
            recommended_account=c_dict["recommended_account"],
            discovered_at=c_dict["discovered_at"],
        )
        _, is_new = temp_ledger.ingest_discovered_campaign(camp)
        assert is_new is False

    # Campaign count is strictly 8
    assert len(temp_ledger.list_campaigns(limit=100)) == 8


def test_ingest_from_raw_dict(temp_ledger: CampaignLedger):
    raw = {
        "campaign_id": "camp-raw-dict-1",
        "title": "Raw Dict Campaign",
        "campaign_url": "https://whop.com/discover/camp-raw-dict-1",
        "payout_raw": "$2.00 CPM",
        "cpm": 2.0,
        "platforms": ["youtube"],
        "source_urls": ["https://youtube.com/watch?v=999"],
        "guideline_urls": [],
        "eligible": True,
        "eligibility_reasons": ["ELIGIBLE_CRITERIA_MET"],
        "recommended_account": "ACCOUNT_1_FINANCE_BUSINESS",
    }
    rec, is_new = temp_ledger.ingest_discovered_campaign(raw)
    assert is_new is True
    assert rec.campaign_id == "camp-raw-dict-1"
    assert rec.current_state == CampaignState.ELIGIBLE

