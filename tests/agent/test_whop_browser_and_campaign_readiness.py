"""Targeted verification of Whop Campaign Source and Browser Discovery Readiness.

Verifies that:
1. Missing WHOP_API_KEY does not by itself block campaign-source readiness when browser discovery is available.
2. An active, legitimate cached/registered campaign satisfies campaign-source readiness.
3. An explicit legitimate target campaign satisfies campaign-source readiness.
4. Mock or synthetic campaign data is rejected (anti-mock enforcement).
5. Absence of any legitimate campaign source fails closed.
6. Fail-closed live operation gates remain strictly intact.
"""

import os
from unittest.mock import patch, MagicMock
import pytest

from clipping.preflight.validator import SystemPreflightValidator, PreflightStatus
from clipping.agent.campaign.models import (
    CampaignRecord,
    CampaignStatus,
    CampaignLifecycleState,
    PayoutTerms,
    PayoutModel,
    SourceMaterial,
    PostingRequirements,
)
from clipping.agent.campaign.repository import CampaignRepository
from clipping.storage.local import LocalStorageDriver


@pytest.fixture
def local_storage(tmp_path):
    return LocalStorageDriver(root_dir=str(tmp_path / "vault_test"))


@pytest.mark.anyio
async def test_01_missing_whop_api_key_does_not_block_readiness_when_browser_available(local_storage):
    """Verifies missing WHOP_API_KEY does NOT block campaign source readiness when Playwright is available."""
    with patch.dict(os.environ, {"WHOP_API_KEY": "", "WHOP_API_TOKEN": ""}, clear=True):
        validator = SystemPreflightValidator(storage_driver=local_storage)
        report = await validator.validate()

        whop_check = next(c for c in report.checks if c.name == "whop_campaign_discovery")
        assert whop_check.status == PreflightStatus.PASS
        assert whop_check.details.get("mechanism") == "browser_discovery"
        assert report.activation_matrix.campaign_source_ready is True
        for rec in report.actionable_recommendations:
            assert "WHOP_API_KEY" not in rec


@pytest.mark.anyio
async def test_02_active_cached_campaign_satisfies_readiness_without_browser(local_storage):
    """Verifies an active, legitimate cached campaign satisfies campaign source readiness even without browser."""
    camp_repo = CampaignRepository(storage_driver=local_storage)
    legit_campaign = CampaignRecord(
        campaign_id="whop_camp_legit_883210",
        name="Real AI Productivity Clipping Campaign",
        creator_community="AI Tools Hub",
        source="https://whop.com/creator-rewards",
        source_url="https://whop.com/creator-rewards",
        status=CampaignStatus.ACTIVE,
        lifecycle_state=CampaignLifecycleState.DISCOVERED,
        payout_terms=PayoutTerms(model=PayoutModel.CPM, cpm_rate=2.50, min_payout=5.0),
        source_material=SourceMaterial(video_urls=["https://www.youtube.com/watch?v=realVideo123"]),
        posting_requirements=PostingRequirements(required_hashtags=["#aitools", "#productivity"]),
    )
    await camp_repo.save_campaign(legit_campaign)

    with patch.dict(os.environ, {"DISABLE_BROWSER_DISCOVERY": "1", "WHOP_API_KEY": ""}, clear=True):
        validator = SystemPreflightValidator(storage_driver=local_storage)
        report = await validator.validate()

        whop_check = next(c for c in report.checks if c.name == "whop_campaign_discovery")
        assert whop_check.status == PreflightStatus.PASS
        assert whop_check.details.get("mechanism") == "repository_cache"
        assert report.activation_matrix.campaign_source_ready is True


@pytest.mark.anyio
async def test_03_explicit_target_campaign_satisfies_readiness(local_storage):
    """Verifies an explicit valid target campaign satisfies readiness."""
    camp_repo = CampaignRepository(storage_driver=local_storage)
    legit_campaign = CampaignRecord(
        campaign_id="whop_camp_target_999",
        name="Explicit Target Video Challenge",
        source="https://whop.com/creator-rewards",
        creator_community="Content Creators",
        source_url="https://whop.com/creator-rewards/target-challenge",
        status=CampaignStatus.ACTIVE,
        lifecycle_state=CampaignLifecycleState.DISCOVERED,
        payout_terms=PayoutTerms(model=PayoutModel.CPM, cpm_rate=3.00),
        source_material=SourceMaterial(video_urls=["https://www.youtube.com/watch?v=targetVid789"]),
    )
    await camp_repo.save_campaign(legit_campaign)

    with patch.dict(os.environ, {"DISABLE_BROWSER_DISCOVERY": "1", "WHOP_API_KEY": ""}, clear=True):
        validator = SystemPreflightValidator(
            storage_driver=local_storage,
            target_campaign_id="whop_camp_target_999",
        )
        report = await validator.validate()

        whop_check = next(c for c in report.checks if c.name == "whop_campaign_discovery")
        assert whop_check.status == PreflightStatus.PASS
        assert whop_check.details.get("mechanism") == "explicit_target"
        assert report.activation_matrix.campaign_source_ready is True


@pytest.mark.anyio
async def test_04_mock_or_synthetic_campaign_rejected(local_storage):
    """Verifies that mock, fake, or synthetic campaign IDs are strictly rejected."""
    camp_repo = CampaignRepository(storage_driver=local_storage)
    fake_campaign = CampaignRecord(
        campaign_id="mock_campaign_fake_123",
        name="Synthetic Fake Clipping Campaign",
        source="https://whop.com/fake",
        creator_community="Mock Hub",
        source_url="https://whop.com/fake",
        status=CampaignStatus.ACTIVE,
        payout_terms=PayoutTerms(model=PayoutModel.CPM, cpm_rate=2.00),
        source_material=SourceMaterial(video_urls=["https://mock.url/fake.mp4"]),
    )
    await camp_repo.save_campaign(fake_campaign)

    with patch.dict(os.environ, {"DISABLE_BROWSER_DISCOVERY": "1"}):
        validator = SystemPreflightValidator(
            storage_driver=local_storage,
            target_campaign_id="mock_campaign_fake_123",
        )
        check = await validator.check_campaign_source()
        assert check.status == PreflightStatus.FAIL
        assert check.blocks_live_publishing is True
        assert "synthetic or mock" in check.message.lower()


@pytest.mark.anyio
async def test_05_no_campaign_source_available_fails_closed(local_storage):
    """Verifies that when no legitimate source is available, system fails closed."""
    with patch.dict(os.environ, {"DISABLE_BROWSER_DISCOVERY": "1", "WHOP_API_KEY": ""}, clear=True):
        validator = SystemPreflightValidator(storage_driver=local_storage)
        report = await validator.validate()

        whop_check = next(c for c in report.checks if c.name == "whop_campaign_discovery")
        assert whop_check.status == PreflightStatus.WARN
        assert whop_check.blocks_live_publishing is True
        assert report.activation_matrix.campaign_source_ready is False
        assert report.activation_matrix.live_operation_allowed is False
        assert report.activation_matrix.can_run_single_live is False
        assert any("browser discovery" in r.lower() for r in report.actionable_recommendations)


@pytest.mark.anyio
async def test_06_invalid_whop_api_token_fails_closed(local_storage):
    """Verifies that explicitly configuring an invalid/unauthorized WHOP_API_KEY results in FAIL."""
    mock_verifier = MagicMock()
    mock_res = MagicMock()
    mock_res.configured = True
    mock_res.verified = False
    mock_res.status_code = 401
    mock_res.message = "Whop API token authentication failed (401)"
    mock_res.why_required = "Live campaign discovery"
    mock_res.configuration_requirement = "Valid token"
    mock_res.blocks_dry_run = False
    mock_res.blocks_live_operation = True
    mock_res.details = {}

    import asyncio
    fut = asyncio.Future()
    fut.set_result(mock_res)
    mock_verifier.verify_whop.return_value = fut

    validator = SystemPreflightValidator(storage_driver=local_storage)
    check = await validator.check_campaign_source(verifier=mock_verifier)

    assert check.status == PreflightStatus.FAIL
    assert check.blocks_live_publishing is True
    assert "authentication failed" in check.message.lower()
