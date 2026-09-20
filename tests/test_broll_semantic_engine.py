"""Tests for Step 22 AL AMR Semantic B-Roll & Pexels Engine enhancements."""

from __future__ import annotations

from pathlib import Path
import pytest
from autoclip.pipeline.broll.models import (
    EDL,
    EditDecisionList,
    PresentationMode,
    RelevanceScore,
    SemanticVisualCue,
    VisualAsset,
    VisualType,
)
from autoclip.pipeline.broll.semantic_parser import ContextualSemanticParser
from autoclip.pipeline.broll.pexels_client import PexelsVideoClient
from autoclip.pipeline.broll.scorer import VisualRelevanceScorer
from autoclip.pipeline.final_render.quality_gate import FinalRenderQualityGate
from autoclip.pipeline.transcript import Word


def make_word(word: str, start: float, end: float) -> Word:
    return Word(text=word, start=start, end=end)


def test_figurative_language_translation():
    """Verify idioms are translated to underlying concrete visual concepts."""
    parser = ContextualSemanticParser()

    # "crushed the competition" -> competition_market
    sentence = "we totally crushed the competition in the third quarter"
    words = [make_word(w, i * 0.5, (i + 1) * 0.5) for i, w in enumerate(sentence.split())]

    cues = parser.parse_transcript_segment(words, clip_start_s=0.0, clip_end_s=10.0)
    concept_names = [c.concept for c in cues]
    assert "competition_market" in concept_names, f"Expected competition_market in {concept_names}"

    # "money on the table" -> money_cash
    sentence2 = "you are leaving money on the table if you ignore this"
    words2 = [make_word(w, i * 0.5, (i + 1) * 0.5) for i, w in enumerate(sentence2.split())]

    cues2 = parser.parse_transcript_segment(words2, clip_start_s=0.0, clip_end_s=10.0)
    concept_names2 = [c.concept for c in cues2]
    assert "money_cash" in concept_names2, f"Expected money_cash in {concept_names2}"


def test_contextual_query_synthesis():
    """Verify synthesized queries combine concept search term with salient sentence words."""
    parser = ContextualSemanticParser()

    sentence = "we analyzed the amazon seller analytics dashboard yesterday"
    words = [make_word(w, i * 0.5, (i + 1) * 0.5) for i, w in enumerate(sentence.split())]

    cues = parser.parse_transcript_segment(words, clip_start_s=0.0, clip_end_s=10.0)
    assert len(cues) >= 1
    # Check that context_query is populated and contains salient context
    has_contextual = any(len(c.context_query.split()) >= 2 for c in cues)
    assert has_contextual, f"Expected multi-word contextual query in {[c.context_query for c in cues]}"


def test_dwell_times_enforced():
    """Verify visual cues have meaningful dwell times (>= 1.8s, default 2.4-3.2s)."""
    parser = ContextualSemanticParser()

    sentence = "this ecommerce brand scaled rapidly to seven figures"
    words = [make_word(w, i * 0.3, (i + 1) * 0.3) for i, w in enumerate(sentence.split())]

    cues = parser.parse_transcript_segment(words, clip_start_s=0.0, clip_end_s=15.0)
    assert len(cues) >= 1
    for cue in cues:
        duration = cue.end_s - cue.start_s
        assert duration >= 1.8, f"Cue duration {duration}s was below 1.8s minimum"
        assert duration <= 4.0, f"Cue duration {duration}s exceeded max reasonable limit"


def test_long_gap_hunter():
    """Verify long uninterrupted A-roll gaps (>4.5s) trigger proactive cue insertion."""
    parser = ContextualSemanticParser()

    # 15 seconds of generic talk with a cue at 1.0s and then 12 seconds of generic speech
    words = [
        make_word("hello", 0.0, 0.5),
        make_word("amazon", 1.0, 1.5),
        make_word("and", 2.0, 2.5),
        make_word("then", 5.0, 5.5),
        make_word("talking", 8.0, 8.5),
        make_word("about", 11.0, 11.5),
        make_word("operations", 14.0, 14.5),
    ]

    cues = parser.parse_transcript_segment(
        words=words,
        clip_start_s=0.0,
        clip_end_s=15.0,
    )
    # The gap from ~3.5s to 15s (11.5s gap) must be broken up by gap hunter
    assert len(cues) >= 2, f"Gap hunter should have added at least one cue, got {len(cues)}"
    # Check max gap between cues
    times = sorted([c.start_s for c in cues])
    max_gap = max(times[i] - times[i-1] for i in range(1, len(times)))
    assert max_gap <= 7.0, f"Expected gap hunter to split long gap, but max gap between cue starts was {max_gap}s"


def test_pexels_relevance_and_orientation_scoring():
    """Verify landscape footage is rated decently (0.75) and relevance is prioritized."""
    from autoclip.pipeline.broll.pexels_client import _relevance_score

    # High-relevance landscape video (1920x1080)
    score_landscape = _relevance_score(
        {"width": 1920, "height": 1080, "duration": 5, "tags": [{"name": "shipping"}]},
        query="shipping",
        concept="warehouse_shipping",
    )

    # High-relevance portrait video (1080x1920)
    score_portrait = _relevance_score(
        {"width": 1080, "height": 1920, "duration": 5, "tags": [{"name": "shipping"}]},
        query="shipping",
        concept="warehouse_shipping",
    )

    # Both must comfortably pass the acceptance threshold
    assert score_landscape >= 0.70, f"Landscape score {score_landscape} should be >= 0.70"
    assert score_portrait >= score_landscape
    assert score_portrait <= 1.0


def test_repetition_penalties():
    """Verify heavy penalty applied when repeating identical asset or same concept."""
    scorer = VisualRelevanceScorer()

    asset1 = VisualAsset(
        asset_id="warehouse_asset_1",
        file_path=Path("warehouse1.mp4"),
        visual_type=VisualType.STOCK_VIDEO,
        concept="warehouse_logistics",
        presentation_mode=PresentationMode.FULL_SCREEN,
        duration_s=4.0,
    )
    cue = SemanticVisualCue(
        cue_id="cue_1",
        concept="warehouse_logistics",
        trigger_phrase="warehouse",
        trigger_word="warehouse",
        start_s=2.0,
        end_s=5.0,
        preferred_mode=PresentationMode.FULL_SCREEN,
        visual_type=VisualType.STOCK_VIDEO,
    )

    # First use: high score
    score1 = scorer.score_candidate(cue=cue, asset=asset1, recent_asset_ids=[])

    # Second use in same clip: heavy repetition penalty
    score2 = scorer.score_candidate(cue=cue, asset=asset1, recent_asset_ids=[asset1.asset_id])

    assert score1.total_score > 0.75, f"Initial score {score1.total_score} should be high"
    assert score2.total_score < (score1.total_score - 0.35), f"Repeated asset score {score2.total_score} should be noticeably lower than {score1.total_score}"


def test_final_render_visual_qa_gate():
    """Verify Quality Gate calculates visual metrics and assigns correct status."""
    gate = FinalRenderQualityGate()

    edl = {
        "clip_id": "test_clip_1",
        "entries": [
            {
                "start_s": 2.0,
                "end_s": 5.0,
                "duration_s": 3.0,
            },
            {
                "start_s": 10.0,
                "end_s": 13.0,
                "duration_s": 3.0,
            },
        ],
    }

    # 20s clip with two 3.0s overlays = 6.0s total broll = 30% coverage
    # Gaps: [0, 2] = 2s, [5, 10] = 5s, [13, 20] = 7s -> longest gap 7s
    metrics = gate.compute_visual_metrics(edl, video_duration=20.0, clip_start_s=0.0)

    assert metrics["broll_event_count"] == 2
    assert metrics["total_broll_duration_s"] == 6.0
    assert metrics["broll_coverage_pct"] == 30.0
    assert metrics["longest_a_roll_gap_s"] == 7.0
    assert metrics["visual_qa_status"] == "VISUAL_PASS"
