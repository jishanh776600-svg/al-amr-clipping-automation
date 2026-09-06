"""Unit tests for Deterministic Clip Scoring."""

import pytest
from clipping.contracts.clip import ClipCandidate
from clipping.contracts.perception import WordTimestamp
from clipping.discovery.scoring import DeterministicClipScorer
from clipping.discovery.config import ClipDiscoveryConfig


def make_candidate(text: str, hook: str, duration: float = 30.0) -> ClipCandidate:
    words = []
    tokens = text.split()
    t = 0.0
    step = duration / max(1, len(tokens))
    for tok in tokens:
        words.append(WordTimestamp(word=tok, start=round(t, 2), end=round(t + step, 2), probability=0.98))
        t += step

    return ClipCandidate(
        candidate_id="cand_test",
        source_video_id="VID_TEST",
        start_time=0.0,
        end_time=duration,
        duration=duration,
        transcript_text=text,
        hook_sentence=hook,
        words=words,
    )


def test_high_quality_hook_scoring():
    text = "The truth is most people build products nobody wants. I realized this after losing $50K on my first startup. Here is how you fix it."
    hook = "The truth is most people build products nobody wants."
    cand = make_candidate(text, hook)

    scorer = DeterministicClipScorer()
    score = scorer.score_candidate(cand)

    assert score.hook_strength >= 70.0
    assert score.breakdown.specificity_score >= 40.0
    assert score.overall_virality_score >= 60.0


def test_filler_penalty():
    text_clean = "We tested fifty different models and found that faster whisper runs ten times faster on CPU."
    text_filler = "Um so like we basically um tested like you know sort of fifty models and like found that it runs ten times faster."

    cand_clean = make_candidate(text_clean, text_clean)
    cand_filler = make_candidate(text_filler, text_filler)

    scorer = DeterministicClipScorer()
    score_clean = scorer.score_candidate(cand_clean)
    score_filler = scorer.score_candidate(cand_filler)

    assert score_filler.breakdown.filler_penalty > score_clean.breakdown.filler_penalty
    assert score_clean.overall_virality_score > score_filler.overall_virality_score


def test_deterministic_reproducibility():
    text = "Here is why autonomous video clipping transforms content production. You save 90% of your editing time."
    cand = make_candidate(text, text)

    scorer = DeterministicClipScorer()
    score1 = scorer.score_candidate(cand)
    score2 = scorer.score_candidate(cand)

    assert score1.overall_virality_score == score2.overall_virality_score
    assert score1.breakdown.hook_score == score2.breakdown.hook_score


def test_opinion_bomb_detection():
    """Verifies that contrarian statements are detected and scored appropriately."""
    text = "Unpopular opinion: everyone is wrong about startup funding. Stop doing what gurus preach."
    hook = "Unpopular opinion: everyone is wrong about startup funding."
    cand = make_candidate(text, hook)

    scorer = DeterministicClipScorer()
    score = scorer.score_candidate(cand)

    assert score.breakdown.opinion_bomb_score >= 50.0
    assert score.breakdown.virality_rationale is not None
    assert "contrarian opinion" in score.breakdown.virality_rationale.lower()


def test_revelation_contrarian_paradox_detection():
    """Verifies that counterintuitive revelations are detected and scored appropriately."""
    text = "The paradox is it actually does the opposite when you scale up. We realized this after testing fifty variations."
    hook = "The paradox is it actually does the opposite when you scale up."
    cand = make_candidate(text, hook)

    scorer = DeterministicClipScorer()
    score = scorer.score_candidate(cand)

    assert score.breakdown.revelation_score >= 50.0
    assert score.breakdown.virality_rationale is not None
    assert "counterintuitive revelation" in score.breakdown.virality_rationale.lower()


def test_virality_rationale_generation():
    """Verifies that virality_rationale is deterministic and reflects detected triggers."""
    # Case A: Combined hook + opinion bomb + revelation
    text_rich = "Unpopular opinion: everyone gets this wrong. The counterintuitive truth is it actually does the opposite."
    hook_rich = "Unpopular opinion: everyone gets this wrong."
    cand_rich = make_candidate(text_rich, hook_rich)

    scorer = DeterministicClipScorer()
    score_rich = scorer.score_candidate(cand_rich)
    breakdown_rich = score_rich.breakdown

    assert breakdown_rich.opinion_bomb_score > 0.0
    assert breakdown_rich.revelation_score > 0.0
    assert "contrarian opinion" in breakdown_rich.virality_rationale.lower()
    assert "counterintuitive revelation" in breakdown_rich.virality_rationale.lower()

    # Case B: Neutral text without strong virality markers
    text_plain = "The system updates every morning at six. Database backups run every hour on the local disk."
    hook_plain = "The system updates every morning at six."
    cand_plain = make_candidate(text_plain, hook_plain)

    score_plain = scorer.score_candidate(cand_plain)
    assert score_plain.breakdown.opinion_bomb_score == 0.0
    assert score_plain.breakdown.revelation_score == 0.0
    assert "Standard narrative segment with neutral engagement signals" in score_plain.breakdown.virality_rationale


def test_existing_scoring_behavior_preserved():
    """Ensures existing scoring factors and penalty calculations remain consistent."""
    text = "The truth is most people build products nobody wants. I realized this after losing $50K on my first startup."
    hook = "The truth is most people build products nobody wants."
    cand = make_candidate(text, hook)

    scorer = DeterministicClipScorer()
    score = scorer.score_candidate(cand)

    assert score.breakdown.opinion_bomb_score == 0.0
    assert score.breakdown.revelation_score == 0.0
    assert score.hook_strength >= 70.0
    assert score.breakdown.specificity_score >= 40.0
    assert score.breakdown.emotion_score >= 50.0
    assert score.breakdown.virality_rationale is not None

