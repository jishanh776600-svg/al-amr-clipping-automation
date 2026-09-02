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
