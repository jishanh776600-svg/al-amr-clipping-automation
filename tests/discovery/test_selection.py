"""Unit tests for Clip Selection, 5-10 Policy & Yield Governance."""

import pytest
from clipping.contracts.clip import ClipCandidate, ClipScore
from clipping.discovery.selection import ClipSelector
from clipping.discovery.config import ClipDiscoveryConfig


def make_candidate_pair(cand_id: str, start: float, score_val: float):
    cand = ClipCandidate(
        candidate_id=cand_id,
        source_video_id="VID_TEST",
        start_time=start,
        end_time=start + 30.0,
        duration=30.0,
        transcript_text=f"Transcript content for {cand_id}",
        hook_sentence=f"Hook for {cand_id}",
        words=[],
    )
    score = ClipScore(
        candidate_id=cand_id,
        hook_strength=score_val,
        narrative_completeness=score_val,
        curiosity_factor=score_val,
        overall_virality_score=score_val,
    )
    return (cand, score)


def test_selection_yield_policy_enough_candidates():
    # 12 qualified candidates (score >= 60.0), spaced by 40s
    pairs = [make_candidate_pair(f"cand_{i}", float(i * 40), 65.0 + i) for i in range(12)]

    config = ClipDiscoveryConfig(min_clips_target=5, max_clips_target=10, quality_threshold=55.0)
    selector = ClipSelector(config=config)
    result = selector.select_clips("VID_TEST", pairs)

    assert len(result.selected_clips) == 10  # Capped at max 10
    assert result.insufficient_candidate_warning is False
    assert result.selected_clips[0].rank == 1
    assert result.selected_clips[0].score.overall_virality_score > result.selected_clips[-1].score.overall_virality_score


def test_strict_quality_floor_no_padding():
    # Only 3 candidates pass quality threshold (>= 55.0), remaining 4 are weak (score 30.0-40.0)
    qualified = [make_candidate_pair(f"good_{i}", float(i * 50), 75.0) for i in range(3)]
    weak = [make_candidate_pair(f"weak_{i}", float(200 + i * 50), 35.0) for i in range(4)]
    all_pairs = qualified + weak

    config = ClipDiscoveryConfig(min_clips_target=5, max_clips_target=10, quality_threshold=55.0)
    selector = ClipSelector(config=config)
    result = selector.select_clips("VID_TEST", all_pairs)

    # STRICT RULE: Must select exactly 3, NEVER pad weak clips to reach 5
    assert len(result.selected_clips) == 3
    assert result.insufficient_candidate_warning is True
    assert len(result.rejected_clips) == 4
    for sel in result.selected_clips:
        assert sel.score.overall_virality_score >= 55.0
