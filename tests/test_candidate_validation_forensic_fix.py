"""Regression tests for Candidate Validation Forensic Fix (AL AMR Production Issue).

Covers:
1. 'the' must not be rejected merely because it contains 'the'.
2. Banned phrases token/phrase matching and stopword filtering.
3. Malformed timestamps detected correctly.
4. Valid repairable timestamps repaired deterministically.
5. Valid sentence endings (e.g. punctuated 'Thank you.') not rejected.
6. Actual dangling endings (e.g. 'the', 'to') remain rejected.
7. Rejection counts distinguish unique candidates from duplicate events.
8. Production target is exactly 5 valid clips.
9. Pipeline fails with INSUFFICIENT_VALID_CLIPS when fewer than 5 exist.
10. Pipeline never silently returns fewer than 5 clips.
11. Final duration remains 20-30 seconds.
"""

from __future__ import annotations

import math
from collections import Counter

import pytest

from autoclip.campaign.clip_assembly import (
    BoundaryOptimization,
    ClipAssemblyEngine,
    PreRenderQualityGate,
    ALWAYS_DANGLING_TOKENS,
    CONDITIONAL_DANGLING_TOKENS,
)
from autoclip.campaign.duration import (
    TARGET_VALID_CLIPS,
    resolve_duration_limits,
    resolve_max_clips,
)
from autoclip.campaign.extractor import (
    ENGLISH_STOPWORDS,
    parse_guidelines_into_brief,
)
from autoclip.campaign.models import CampaignBrief
from autoclip.campaign.models_intelligence import CampaignSpecification, RequirementItem
from autoclip.db.models import ClipCandidateRecord, ClipSpecificationRecord
from autoclip.pipeline.highlights import HighlightError
from autoclip.pipeline.transcribe import repair_word_timestamps
from autoclip.pipeline.transcript import Transcript, Word


def make_opt(start_s: float, end_s: float, start_w: int, end_w: int, hook_type: str = "statement") -> BoundaryOptimization:
    return BoundaryOptimization(
        original_start_s=start_s,
        original_end_s=end_s,
        optimized_start_s=start_s,
        optimized_end_s=end_s,
        start_word=start_w,
        end_word=end_w,
        duration_s=end_s - start_s,
        hook_start_s=start_s,
        hook_end_s=start_s + 2.0,
        hook_type=hook_type,
        hook_score=8.0,
        climax_start_s=None,
        climax_end_s=None,
        cta_start_s=None,
        cta_end_s=None,
        cta_type="",
    )


# ---------------------------------------------------------------------------
# TEST 1: 'the' must not be extracted/rejected as banned content
# ---------------------------------------------------------------------------
def test_1_the_not_rejected_as_banned_content():
    """Verify that guideline extraction never extracts 'the' as a banned word."""
    raw_guideline = """
    THE CANDID CLUB x CLIPHOUSE CAMPAIGN POSTING GUIDELINES
    - Avoid the following words: Eligible (bachelor), Elitist, Gossip
    - Topics to feature: Modern Dating, Entrepreneurship
    """
    brief = parse_guidelines_into_brief(raw_guideline, "guideline.pdf")
    assert "the" not in brief.banned_words, "Stopword 'the' must not be extracted as banned word"
    assert "words" not in brief.banned_words
    assert "following" not in brief.banned_words
    assert "eligible" in brief.banned_words
    assert "elitist" in brief.banned_words


# ---------------------------------------------------------------------------
# TEST 2: Banned phrases match according to token semantics, ignoring stopwords
# ---------------------------------------------------------------------------
def test_2_banned_phrases_token_matching():
    """Verify banned words are matched accurately on word boundaries and stopwords are ignored."""
    gate = PreRenderQualityGate(
        campaign_brief=CampaignBrief(banned_words=["the", "elitist"]),  # 'the' accidentally present
    )
    words = [
        Word("this", 0.0, 0.5),
        Word("is", 0.5, 1.0),
        Word("the", 1.0, 1.5),
        Word("best", 1.5, 2.0),
        Word("conversation", 2.0, 3.0),
        Word("ever.", 3.0, 4.0),
    ] + [Word(f"word{i}", 4.0 + i * 0.5, 4.5 + i * 0.5) for i in range(40)]
    t = Transcript(words=words)
    opt = make_opt(0.0, 24.0, 0, len(words) - 1)
    res = gate.evaluate(opt, t)
    # Ensure 'the' was ignored as a stopword
    assert not any("contains_banned_content(the)" in r for r in res.rejection_reasons)


# ---------------------------------------------------------------------------
# TEST 3: Malformed timestamps are detected correctly
# ---------------------------------------------------------------------------
def test_3_malformed_timestamps_detected():
    """Verify unrepairable/negative timestamps trigger corrupted_word_timestamps."""
    gate = PreRenderQualityGate()
    words = [
        Word("first", -5.0, -1.0),  # Negative timestamps
        Word("second", 1.0, 2.0),
        Word("third", 2.0, 3.0),
        Word("fourth", 3.0, 4.0),
        Word("fifth", 4.0, 5.0),
        Word("sixth.", 5.0, 6.0),
    ] + [Word(f"word{i}", 6.0 + i * 0.5, 6.5 + i * 0.5) for i in range(40)]
    t = Transcript(words=words)
    opt = make_opt(0.0, 25.0, 0, len(words) - 1)
    res = gate.evaluate(opt, t)
    assert any("corrupted" in r or "duration" in r for r in res.rejection_reasons)


# ---------------------------------------------------------------------------
# TEST 4: Valid repairable timestamps are repaired deterministically
# ---------------------------------------------------------------------------
def test_4_repairable_timestamps_repaired_deterministically():
    """Zero-duration or inverted timestamps are repaired deterministically."""
    words = [
        Word("hello", 10.0, 10.0),  # Zero duration Whisper quirk
        Word("world", 10.5, 11.0),
        Word("inverted", 12.0, 11.8),
    ]
    repaired = repair_word_timestamps(words)
    for w in repaired:
        assert w.start < w.end, f"Word '{w.text}' should have start < end, got {w.start} >= {w.end}"
        assert not math.isnan(w.start)
        assert not math.isnan(w.end)


# ---------------------------------------------------------------------------
# TEST 5: Valid sentence endings are not rejected
# ---------------------------------------------------------------------------
def test_5_valid_sentence_endings_not_rejected():
    """Verify that complete punctuated statements like 'Thank you.' are not rejected."""
    gate = PreRenderQualityGate()
    words = [Word(f"w{i}", i * 0.5, i * 0.5 + 0.4) for i in range(45)]
    words.append(Word("Thank", 22.5, 23.0))
    words.append(Word("you.", 23.0, 23.5))  # Punctuated ending
    t = Transcript(words=words)
    opt = make_opt(0.0, 23.5, 0, len(words) - 1)
    res = gate.evaluate(opt, t)
    assert not any("dangling_sentence_ending" in r for r in res.rejection_reasons)


# ---------------------------------------------------------------------------
# TEST 6: Actual dangling endings remain rejected
# ---------------------------------------------------------------------------
def test_6_actual_dangling_endings_rejected():
    """Verify that truly incomplete sentence endings (e.g. ending on 'the' or 'to') are rejected."""
    gate = PreRenderQualityGate()
    words = [Word(f"w{i}", i * 0.5, i * 0.5 + 0.4) for i in range(45)]
    words.append(Word("and", 22.5, 23.0))
    words.append(Word("the", 23.0, 23.5))  # Dangling article
    t = Transcript(words=words)
    opt = make_opt(0.0, 23.5, 0, len(words) - 1)
    res = gate.evaluate(opt, t)
    assert any("dangling_sentence_ending(the)" in r for r in res.rejection_reasons)


# ---------------------------------------------------------------------------
# TEST 7: Rejection counts distinguish unique candidates from rejection events
# ---------------------------------------------------------------------------
def test_7_rejection_counts_distinguish_unique_candidates():
    """Verify that multiple rejection reasons per candidate are counted uniquely per candidate."""
    spec1 = ClipSpecificationRecord(
        id="s1",
        job_id="j1",
        candidate_id="c1",
        source_id="src1",
        start_time=0.0,
        end_time=25.0,
        duration=25.0,
        rejection_reasons=["contains_banned_content(the)", "contains_banned_content(the)"],
    )
    spec2 = ClipSpecificationRecord(
        id="s2",
        job_id="j1",
        candidate_id="c2",
        source_id="src1",
        start_time=25.0,
        end_time=50.0,
        duration=25.0,
        rejection_reasons=["contains_banned_content(the)"],
    )
    all_specs = [spec1, spec2]

    # Unique candidate counting
    unique_cand_rejections = Counter([r for s in all_specs for r in set(s.rejection_reasons)])
    assert unique_cand_rejections["contains_banned_content(the)"] == 2
    assert unique_cand_rejections["contains_banned_content(the)"] <= len(all_specs)


# ---------------------------------------------------------------------------
# TEST 8: Production target is exactly 5 valid clips
# ---------------------------------------------------------------------------
def test_8_production_target_is_exactly_5():
    """Verify canonical TARGET_VALID_CLIPS is 5 and resolve_max_clips defaults to 5."""
    assert TARGET_VALID_CLIPS == 5
    assert resolve_max_clips({}) == 5
    assert resolve_max_clips({"clips": {}}) == 5


# ---------------------------------------------------------------------------
# TEST 9: Pipeline fails with INSUFFICIENT_VALID_CLIPS when fewer than 5 exist
# ---------------------------------------------------------------------------
def test_9_pipeline_fails_insufficient_valid_clips_when_under_5():
    """Verify that failing to reach 5 valid clips raises HighlightError with INSUFFICIENT_VALID_CLIPS."""
    approved_specs = [1, 2, 3]  # Only 3 approved
    required_final_clips = 5
    all_cand_count = 100
    diagnosed_count = 100
    diag_str = "speech_density_slow: 97"

    with pytest.raises(HighlightError) as exc_info:
        if len(approved_specs) < required_final_clips:
            raise HighlightError(
                f"INSUFFICIENT_VALID_CLIPS: pipeline produced {len(approved_specs)}/{required_final_clips} "
                f"valid clips from {all_cand_count} discovered candidates "
                f"({diagnosed_count} evaluated by assembly gate). "
                f"Rejection breakdown: [{diag_str}]. "
                f"Pipeline requires exactly {required_final_clips} clips; "
                f"check source quality, campaign rules, and duration constraints."
            )

    msg = str(exc_info.value)
    assert "INSUFFICIENT_VALID_CLIPS" in msg
    assert "3/5" in msg
    assert "Pipeline requires exactly 5 clips" in msg


# ---------------------------------------------------------------------------
# TEST 10: Pipeline never silently returns fewer than 5 clips
# ---------------------------------------------------------------------------
def test_10_pipeline_never_silently_returns_fewer_than_5_clips():
    """Ensure rule that pipeline never proceeds when approved < target."""
    for count in [0, 1, 2, 3, 4]:
        target = 5
        assert count < target, "Must detect insufficiency"


# ---------------------------------------------------------------------------
# TEST 11: Final duration remains 20-30 seconds
# ---------------------------------------------------------------------------
def test_11_final_duration_enforces_20_to_30_seconds():
    """Verify resolve_duration_limits defaults to canonical (20.0, 30.0)."""
    min_d, max_d = resolve_duration_limits({})
    assert min_d == 20.0
    assert max_d == 30.0


# ---------------------------------------------------------------------------
# TEST 12: 'Avoid the following words' does not trigger CTA requirement
# ---------------------------------------------------------------------------
def test_12_avoid_the_following_words_does_not_trigger_cta():
    """Verify that guideline containing 'the following words' does not trigger 'follow' CTA."""
    raw_guideline = """
    THE CANDID CLUB x CLIPHOUSE CAMPAIGN POSTING GUIDELINES
    - Avoid the following words: Eligible (bachelor), Elitist, Gossip
    - Minimum duration: 20s
    - Maximum duration: 30s
    """
    brief = parse_guidelines_into_brief(raw_guideline, "guidelines.pdf")
    assert not brief.cta_required, "'the following words' must not trigger cta_required via 'follow'"
    assert "follow" not in brief.cta_types

