"""Unit tests for Whop autonomous campaign joiner (Step 10)."""

import pytest
from unittest.mock import MagicMock, patch
from whop.catalog import DiscoveredCampaign
from whop.joiner import (
    WhopCampaignJoiner,
    WhopAlreadyJoinedError,
    WhopJoinError,
    WhopJoinTimeoutError,
)
from whop.ledger import CampaignLedger
from whop.models import CampaignState, WhopJoinRecord


@pytest.fixture
def temp_ledger(tmp_path):
    db_file = tmp_path / "test_ledger.db"
    return CampaignLedger(db_path=db_file)


@pytest.fixture
def sample_candidate():
    return DiscoveredCampaign(
        campaign_id="new_camp_12345",
        title="Exclusive SaaS Clipping Bounty",
        campaign_url="https://whop.com/discover/new_camp_12345",
        payout_raw="$2.50 CPM",
        cpm=2.5,
        platforms=["youtube", "instagram"],
        source_urls=["https://drive.google.com/file/d/test12345/view"],
        eligible=True,
    )


def test_is_candidate_eligible_and_unjoined_success(temp_ledger, sample_candidate):
    joiner = WhopCampaignJoiner(ledger=temp_ledger)
    joined_ids = {"existing_camp_999", "c10875452a89"}
    
    is_ok, reason = joiner.is_candidate_eligible_and_unjoined(sample_candidate, joined_ids)
    assert is_ok is True
    assert reason == "ELIGIBLE_AND_UNJOINED"


def test_is_candidate_eligible_and_unjoined_rejects_already_joined(temp_ledger, sample_candidate):
    joiner = WhopCampaignJoiner(ledger=temp_ledger)
    joined_ids = {"new_camp_12345", "c10875452a89"}
    
    is_ok, reason = joiner.is_candidate_eligible_and_unjoined(sample_candidate, joined_ids)
    assert is_ok is False
    assert "already in the joined/claimed set" in reason


def test_is_candidate_eligible_and_unjoined_rejects_low_cpm(temp_ledger, sample_candidate):
    joiner = WhopCampaignJoiner(ledger=temp_ledger)
    sample_candidate.cpm = 0.50
    is_ok, reason = joiner.is_candidate_eligible_and_unjoined(sample_candidate, set())
    assert is_ok is False
    assert "below the minimum threshold" in reason


def test_is_candidate_eligible_and_unjoined_rejects_no_sources(temp_ledger, sample_candidate):
    joiner = WhopCampaignJoiner(ledger=temp_ledger)
    sample_candidate.source_urls = []
    is_ok, reason = joiner.is_candidate_eligible_and_unjoined(sample_candidate, set())
    assert is_ok is False
    assert "no source media URLs" in reason


def test_arm_join_mutation(temp_ledger, sample_candidate):
    joiner = WhopCampaignJoiner(ledger=temp_ledger)
    # Register candidate in ledger first
    temp_ledger.ingest_discovered_campaign(sample_candidate)
    
    armed = joiner.arm_join_mutation(sample_candidate, "test_operator_account")
    assert armed["event"] == "JOIN_MUTATION_ARMED"
    assert armed["campaign_id"] == "new_camp_12345"
    assert armed["account_identity"] == "test_operator_account"
    assert "timestamp" in armed


def test_join_dry_run_armed(temp_ledger, sample_candidate):
    joiner = WhopCampaignJoiner(ledger=temp_ledger)
    temp_ledger.ingest_discovered_campaign(sample_candidate)
    
    mock_page = MagicMock()
    mock_page.url = sample_candidate.campaign_url
    mock_btn = MagicMock()
    mock_btn.count.return_value = 1
    mock_btn.first.is_visible.return_value = True
    mock_page.locator.return_value = mock_btn

    with patch.object(joiner, "verify_campaign_membership", return_value=False):
        record = joiner.execute_join_mutation(
            page=mock_page,
            campaign=sample_candidate,
            account_identity="test_operator",
            dry_run_override=True,
        )
        assert record.join_state == "DRY_RUN_ARMED"
        assert record.campaign_id == "new_camp_12345"


def test_join_skips_when_already_member(temp_ledger, sample_candidate):
    joiner = WhopCampaignJoiner(ledger=temp_ledger)
    temp_ledger.ingest_discovered_campaign(sample_candidate)
    
    mock_page = MagicMock()
    mock_page.url = sample_candidate.campaign_url

    with patch.object(joiner, "verify_campaign_membership", return_value=True):
        record = joiner.execute_join_mutation(
            page=mock_page,
            campaign=sample_candidate,
            account_identity="test_operator",
            dry_run_override=False,
        )
        assert record.join_state == "JOINED"
        assert record.membership_verified is True
        
        # Verify ledger updated
        saved = temp_ledger.get_join_record("new_camp_12345")
        assert saved is not None
        assert saved.membership_verified is True


def test_join_live_success(temp_ledger, sample_candidate):
    joiner = WhopCampaignJoiner(ledger=temp_ledger)
    temp_ledger.ingest_discovered_campaign(sample_candidate)
    
    mock_page = MagicMock()
    mock_page.url = sample_candidate.campaign_url
    mock_btn = MagicMock()
    mock_btn.count.return_value = 1
    mock_btn.first.is_visible.return_value = True
    mock_page.locator.return_value = mock_btn

    with patch.object(joiner, "verify_campaign_membership", side_effect=[False, True]), \
         patch("whop.joiner.HumanActor") as MockActor:
        mock_actor_inst = MagicMock()
        MockActor.return_value = mock_actor_inst
        
        record = joiner.execute_join_mutation(
            page=mock_page,
            campaign=sample_candidate,
            account_identity="test_operator",
            dry_run_override=False,
        )
        assert record.join_state == "JOINED"
        assert record.membership_verified is True
        mock_actor_inst.human_click.assert_called_once()
