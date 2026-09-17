"""Regression tests for CampaignBrief attribute compatibility in ClipAssemblyEngine.

Specifically guards against:
- 'CampaignBrief' object has no attribute 'call_to_action'
- 'CampaignBrief' object has no attribute 'banned_keywords'
"""

from __future__ import annotations

import pytest

from autoclip.campaign import (
    CampaignBrief,
    CampaignSpecification,
    ClipAssemblyEngine,
    PreRenderQualityGate,
    RequirementItem,
    SmartBoundaryEngine,
)
from autoclip.db.models import ClipCandidateRecord, new_id, utcnow
from autoclip.pipeline.transcript import Transcript, Word


def _make_transcript() -> Transcript:
    sentences = [
        "Welcome to our automated production workflow for high velocity content creation.",
        "Here is the main concept of the video where we discuss automation in detail.",
        "Follow and subscribe for more daily AI updates and tips.",
    ]
    words: list[Word] = []
    t = 1.0
    for s in sentences:
        for w in s.split():
            words.append(Word(text=w, start=t, end=t + 0.3, speaker="SPEAKER_00"))
            t += 0.35
        t += 0.5
    return Transcript(words=words)


def _make_candidate(job_id: str = "test-job") -> ClipCandidateRecord:
    return ClipCandidateRecord(
        id=new_id(),
        job_id=job_id,
        rank=1,
        selected=True,
        status="selected",
        start_s=1.0,
        end_s=10.0,
        duration_s=9.0,
        start_word=0,
        end_word=20,
        title="Test Candidate",
        hook_text="Welcome to our automated production workflow",
        reason="Good opener",
        transcript_slice="Welcome to our automated production workflow...",
        score=85.0,
        score_breakdown={"explicit": 80.0},
        hook_signals={"type": "bold_claim", "score": 8.0, "start_s": 1.0, "end_s": 3.0},
        climax_signals={"start_s": 4.0, "end_s": 7.0, "score": 8.0},
        cta_signals={},
        requirement_matches=[],
        rejection_reasons=[],
        created_at=utcnow(),
        updated_at=utcnow(),
    )


def test_campaign_brief_no_attribute_call_to_action_regression():
    """Verify ClipAssemblyEngine.assemble handles standard CampaignBrief without crashing."""
    brief = CampaignBrief(
        name="Live Test Campaign",
        cta_required=False,
        banned_words=["forbidden"],
        banned_topics=["crypto"],
    )
    # Ensure CampaignBrief does NOT have call_to_action or banned_keywords
    assert not hasattr(brief, "call_to_action")
    assert not hasattr(brief, "banned_keywords")

    engine = ClipAssemblyEngine(campaign_brief=brief)
    transcript = _make_transcript()
    candidates = [_make_candidate()]

    # assemble() must not raise AttributeError: 'CampaignBrief' object has no attribute 'call_to_action'
    all_specs, approved_specs, telemetry = engine.assemble(
        candidates=candidates,
        transcript=transcript,
        job_id="test-job",
        source_id="test-source",
    )

    assert telemetry["candidates_received"] == 1
    assert isinstance(all_specs, list)
    assert isinstance(approved_specs, list)


def test_campaign_brief_cta_required_true():
    """Verify ClipAssemblyEngine respects cta_required=True on CampaignBrief."""
    brief = CampaignBrief(
        name="CTA Mandated Campaign",
        cta_required=True,
    )
    engine = ClipAssemblyEngine(campaign_brief=brief)
    transcript = _make_transcript()
    candidate = _make_candidate()

    all_specs, approved_specs, telemetry = engine.assemble(
        candidates=[candidate],
        transcript=transcript,
        job_id="test-job",
        source_id="test-source",
    )

    assert telemetry["candidates_received"] == 1
    # Candidate without CTA will be rejected when cta_required is True
    if all_specs:
        spec = all_specs[0]
        assert "campaign_mandated_cta_missing" in spec.rejection_reasons or spec.quality_gate_passed is False


def test_pre_render_quality_gate_banned_words_and_topics():
    """Verify PreRenderQualityGate evaluates banned_words and banned_topics on CampaignBrief."""
    brief = CampaignBrief(
        name="Filter Campaign",
        banned_words=["welcome"],
    )
    qg = PreRenderQualityGate(campaign_brief=brief)
    transcript = _make_transcript()
    candidate = _make_candidate()

    boundary_engine = SmartBoundaryEngine()
    opt = boundary_engine.optimize(candidate, transcript, duration_min_s=5.0, duration_max_s=30.0)

    # Transcript contains "Welcome", so quality gate should flag contains_banned_content(welcome)
    result = qg.evaluate(opt, transcript)
    assert result.status == "QUALITY_REJECT"
    assert any("contains_banned_content(welcome)" in r for r in result.rejection_reasons)


def test_backward_compatibility_ad_hoc_brief_objects():
    """Verify duck-typing handles older/mock objects that might have call_to_action or banned_keywords."""
    class LegacyBrief:
        call_to_action = True
        banned_keywords = ["legacy_banned"]

    legacy = LegacyBrief()
    engine = ClipAssemblyEngine(campaign_brief=legacy)
    transcript = _make_transcript()
    candidate = _make_candidate()

    all_specs, approved_specs, telemetry = engine.assemble(
        candidates=[candidate],
        transcript=transcript,
        job_id="test-job",
        source_id="test-source",
    )
    assert telemetry["candidates_received"] == 1
