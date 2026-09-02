"""Unit tests for Candidate Deduplication."""

import pytest
from clipping.contracts.clip import ClipCandidate, ClipScore
from clipping.discovery.dedup import (
    CandidateDeduplicator,
    calculate_temporal_iou,
    calculate_text_jaccard,
)


def make_pair(cand_id: str, start: float, end: float, text: str, score_val: float):
    cand = ClipCandidate(
        candidate_id=cand_id,
        source_video_id="VID_TEST",
        start_time=start,
        end_time=end,
        duration=end - start,
        transcript_text=text,
        hook_sentence=text[:30] if len(text) >= 30 else text,
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


def test_temporal_iou_math():
    pair1 = make_pair("c1", 10.0, 40.0, "Valid transcript text for candidate one.", 70.0)[0]
    pair2 = make_pair("c2", 15.0, 45.0, "Valid transcript text for candidate two.", 80.0)[0]

    iou = calculate_temporal_iou(pair1, pair2)
    # Intersection = 40 - 15 = 25s
    # Union = 45 - 10 = 35s
    # IoU = 25 / 35 = ~0.714
    assert iou == pytest.approx(25.0 / 35.0, rel=1e-2)


def test_nms_deduplication():
    # 2 heavily overlapping candidates (10-40s and 12-42s), candidate 2 has higher score
    p1 = make_pair("c1", 10.0, 40.0, "The biggest mistake in AI startups is ignoring distribution.", 65.0)
    p2 = make_pair("c2", 12.0, 42.0, "The biggest mistake in AI startups is ignoring distribution and focus.", 85.0)
    # 1 separate candidate at 120-150s
    p3 = make_pair("c3", 120.0, 150.0, "Second unique insight about scaling cloud compute.", 75.0)

    deduplicator = CandidateDeduplicator()
    results = deduplicator.deduplicate([p1, p2, p3])

    assert len(results) == 2
    # p2 should be kept over p1 because p2 has score 85.0
    cand_ids = [c[0].candidate_id for c in results]
    assert "c2" in cand_ids
    assert "c1" not in cand_ids
    assert "c3" in cand_ids
