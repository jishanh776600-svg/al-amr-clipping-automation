"""Unit tests for Director, Reframe, Clip, and Scoring contracts."""

import pytest
from pydantic import ValidationError
from clipping.contracts.director import (
    SpeakerLayout,
    ReframeCropKeyframe,
    ReframePlan,
)
from clipping.contracts.clip import (
    ClipCandidate,
    ClipScore,
    ComplianceResult,
)
from clipping.contracts.perception import WordTimestamp


def test_reframe_plan_validation():
    kf1 = ReframeCropKeyframe(timestamp=0.0, crop_x=420, crop_y=0, crop_w=1080, crop_h=1080, layout_mode=SpeakerLayout.SOLO)
    plan = ReframePlan(clip_id="CLIP_001", source_width=1920, source_height=1080, keyframes=[kf1])

    assert plan.target_width == 1080
    assert plan.target_height == 1920
    assert len(plan.keyframes) == 1

    # Empty keyframes validation
    with pytest.raises(ValidationError):
        ReframePlan(clip_id="CLIP_EMPTY", source_width=1920, source_height=1080, keyframes=[])


def test_clip_candidate_and_score():
    word = WordTimestamp(word="AI", start=0.0, end=0.5, probability=0.99)
    candidate = ClipCandidate(
        candidate_id="CAND_01",
        source_video_id="VID_01",
        campaign_id="CAMP_01",
        start_time=10.0,
        end_time=45.0,
        duration=35.0,
        transcript_text="AI is transforming media workflows forever.",
        words=[word],
        hook_sentence="AI is transforming media.",
    )
    assert candidate.duration == 35.0

    score = ClipScore(
        candidate_id="CAND_01",
        hook_strength=95.0,
        narrative_completeness=90.0,
        curiosity_factor=88.0,
        campaign_relevance=92.0,
        overall_virality_score=91.5,
        reasoning="Strong opening hook and clear resolution.",
    )
    assert score.overall_virality_score == 91.5

    # Out of bounds score validation
    with pytest.raises(ValidationError):
        ClipScore(
            candidate_id="CAND_01",
            hook_strength=105.0,
            narrative_completeness=90.0,
            curiosity_factor=88.0,
            campaign_relevance=92.0,
            overall_virality_score=91.5,
        )


def test_compliance_result():
    comp = ComplianceResult(
        candidate_id="CAND_01",
        is_compliant=True,
        violations=[],
        warnings=["Mentions legacy tool without link"],
        rule_references=["RULE_BRAND_01"],
        confidence=0.96,
    )
    assert comp.is_compliant is True
    assert comp.confidence == 0.96
