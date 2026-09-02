"""Unit tests for Campaign contracts."""

import pytest
from pydantic import ValidationError
from clipping.contracts.campaign import (
    BoundingBox,
    CampaignRuleCategory,
    CampaignRuleSeverity,
    CampaignRule,
    CampaignSpec,
)


def test_valid_campaign_spec():
    bbox = BoundingBox(page_no=1, left=0.1, top=0.2, right=0.9, bottom=0.8)
    rule = CampaignRule(
        rule_id="RULE_01",
        category=CampaignRuleCategory.PROHIBITED_WORD,
        severity=CampaignRuleSeverity.CRITICAL,
        description="Do not mention competitor brand X",
        exact_match_patterns=["CompetitorX", "BrandY"],
        provenance=bbox,
    )

    spec = CampaignSpec(
        campaign_id="CAMP_2026_AI",
        campaign_name="AI Tools Showcase",
        target_audience="Developers",
        min_duration_seconds=30.0,
        max_duration_seconds=60.0,
        rules=[rule],
        required_cta_text="Subscribe for more AI workflows!",
    )

    assert spec.campaign_id == "CAMP_2026_AI"
    assert len(spec.rules) == 1
    assert spec.rules[0].provenance.page_no == 1
    assert spec.created_at.tzinfo is not None

    # Serialization / Deserialization
    json_str = spec.model_dump_json()
    reconstructed = CampaignSpec.model_validate_json(json_str)
    assert reconstructed == spec


def test_invalid_bounding_box():
    with pytest.raises(ValidationError):
        BoundingBox(page_no=0, left=-1.0, top=0.0, right=1.0, bottom=1.0)


def test_invalid_duration_validation():
    spec = CampaignSpec(
        campaign_id="CAMP_01",
        campaign_name="Test",
        min_duration_seconds=75.0,
        max_duration_seconds=45.0,
    )
    with pytest.raises(ValueError, match="cannot exceed"):
        spec.validate_durations()
