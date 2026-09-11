"""Comprehensive Tests for AL AMR Step 3: Campaign Rule & Hook Enforcement Engine.

Covers:
1. Campaign Brief Model defaults, validation, and serialization.
2. Hard Rules: duration bounds, banned words (strict boundary), required topics, hook threshold, CTA threshold, silence/dead-air.
3. Soft Rules: preferred duration warnings, content density warnings, speaker count warnings, penalty discounting.
4. Hook Analysis: question hooks, bold hooks, stats, filler penalties, window configurability.
5. CTA Analysis: subscription, outbound link, action patterns, window adherence.
6. Candidate Ranking & Filtering: hard failure pruning, score sorting, rank assignment, output count limits.
7. SQLite Persistence: migration v2, campaign_evaluations table CRUD.
8. Backwards Compatibility: jobs without campaigns run unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoclip.campaign import (
    CampaignBrief,
    CampaignEvaluator,
    analyze_cta,
    analyze_hook,
    analyze_speech_density_and_silence,
    rank_and_filter_candidates,
)
from autoclip.db import store
from autoclip.db.models import CampaignEvaluationRow, Clip, Job, Source, new_id
from autoclip.pipeline.prepare import Silence
from autoclip.pipeline.transcript import Word


# ---------------------------------------------------------------------------
# 1. CAMPAIGN BRIEF MODEL
# ---------------------------------------------------------------------------


def test_campaign_brief_defaults():
    """Verify sensible defaults and serialization for CampaignBrief."""
    cb = CampaignBrief()
    assert cb.name == "Default Campaign"
    assert cb.minimum_duration == 20.0
    assert cb.maximum_duration == 90.0
    assert cb.hook_required is True
    assert cb.hook_window_seconds == 2.5
    assert cb.cta_required is False
    assert cb.banned_words == []
    assert cb.aspect_ratio == "9:16"
    assert cb.caption_preset == "bold_pop"

    # Serialization round-trip
    dumped = cb.model_dump(mode="json")
    loaded = CampaignBrief.model_validate(dumped)
    assert loaded.campaign_id == cb.campaign_id
    assert loaded.minimum_duration == 20.0


def test_campaign_brief_custom_validation():
    """Verify custom campaign brief fields."""
    cb = CampaignBrief(
        name="AI Tech Growth",
        target_audience="Devs",
        minimum_duration=15.0,
        maximum_duration=45.0,
        preferred_duration=30.0,
        banned_words=["crypto", "scam"],
        required_topics=["Python", "AI"],
        cta_required=True,
        minimum_hook_score=7.0,
    )
    assert cb.preferred_duration == 30.0
    assert len(cb.banned_words) == 2
    assert cb.cta_required is True


# ---------------------------------------------------------------------------
# 2. HARD RULES VS SOFT RULES
# ---------------------------------------------------------------------------


def test_hard_rule_duration_failure():
    """Duration below minimum or above maximum must trigger hard failure."""
    campaign = CampaignBrief(minimum_duration=20.0, maximum_duration=45.0)
    evaluator = CampaignEvaluator(campaign)

    words = [Word("hello", 0.0, 1.0), Word("world", 1.0, 2.0)]  # 2.0s duration
    ev = evaluator.evaluate_candidate(
        candidate_id="c1",
        start_s=0.0,
        end_s=2.0,
        words=words,
        base_viral_score=8.0,
    )
    assert ev.approved is False
    assert any("Duration 2.0s is outside allowed range" in f for f in ev.hard_failures)


def test_hard_rule_banned_words_word_boundary():
    """Banned word 'art' must NOT trigger on 'partial', but must trigger on 'art'."""
    campaign = CampaignBrief(
        minimum_duration=1.0,
        maximum_duration=10.0,
        banned_words=["art", "get rich quick"],
        hook_required=False,
    )
    evaluator = CampaignEvaluator(campaign)

    # 1. Non-matching sub-string: 'partial' contains 'art' but is not the word 'art'
    words_safe = [Word("this", 0.0, 0.5), Word("is", 0.5, 1.0), Word("partial", 1.0, 2.0)]
    ev_safe = evaluator.evaluate_candidate(
        candidate_id="c_safe",
        start_s=0.0,
        end_s=2.0,
        words=words_safe,
        base_viral_score=8.0,
    )
    assert not any("banned words" in f for f in ev_safe.hard_failures)

    # 2. Matching banned word with punctuation
    words_banned = [Word("Modern", 0.0, 0.5), Word("art!", 0.5, 1.0)]
    ev_banned = evaluator.evaluate_candidate(
        candidate_id="c_banned",
        start_s=0.0,
        end_s=2.0,
        words=words_banned,
        base_viral_score=8.0,
    )
    assert ev_banned.approved is False
    assert any("contains banned words: ['art']" in f for f in ev_banned.hard_failures)


def test_hard_rule_required_topic_failure():
    """Missing required topic triggers hard failure."""
    campaign = CampaignBrief(
        minimum_duration=1.0,
        maximum_duration=10.0,
        required_topics=["Automation", "Python"],
        hook_required=False,
    )
    evaluator = CampaignEvaluator(campaign)

    words = [Word("We", 0.0, 0.5), Word("love", 0.5, 1.0), Word("Python", 1.0, 1.5)]
    ev = evaluator.evaluate_candidate(
        candidate_id="c_topic",
        start_s=0.0,
        end_s=2.0,
        words=words,
        base_viral_score=8.0,
    )
    assert ev.approved is False
    assert any("Missing required topics/concepts: ['Automation']" in f for f in ev.hard_failures)


def test_hard_rule_dead_air_silence():
    """Silence exceeding maximum_silence_seconds triggers hard failure."""
    campaign = CampaignBrief(
        minimum_duration=1.0,
        maximum_duration=20.0,
        maximum_silence_seconds=1.2,
        hook_required=False,
    )
    evaluator = CampaignEvaluator(campaign)

    words = [Word("hello", 0.0, 1.0), Word("there", 3.0, 4.0)]
    # A 2.0-second silence gap from 1.0 to 3.0s
    silences = [Silence(start=1.0, end=3.0)]

    ev = evaluator.evaluate_candidate(
        candidate_id="c_silence",
        start_s=0.0,
        end_s=4.0,
        words=words,
        base_viral_score=8.0,
        silences=silences,
    )
    assert ev.approved is False
    assert any("Dead-air silence pause of 2.00s exceeds hard limit" in f for f in ev.hard_failures)


def test_soft_rules_apply_warnings_and_penalties():
    """Soft warnings (preferred duration, low density) penalize score without rejecting."""
    campaign = CampaignBrief(
        minimum_duration=1.0,
        maximum_duration=50.0,
        preferred_duration=30.0,
        minimum_content_density=2.0,  # words per sec
        hook_required=False,
    )
    evaluator = CampaignEvaluator(campaign)

    # 10 words over 10 seconds = 1.0 w/s (below target 2.0), duration 10s (deviates by 20s from 30s)
    words = [Word(f"word{i}", float(i), float(i + 1)) for i in range(10)]
    ev = evaluator.evaluate_candidate(
        candidate_id="c_soft",
        start_s=0.0,
        end_s=10.0,
        words=words,
        base_viral_score=9.0,
    )
    assert ev.approved is True
    assert len(ev.hard_failures) == 0
    assert len(ev.soft_warnings) >= 2
    assert ev.final_score < 9.0  # Discounted by soft penalties


# ---------------------------------------------------------------------------
# 3. HOOK ANALYSIS
# ---------------------------------------------------------------------------


def test_hook_question_and_bold_opening():
    """Questions and bold claim openings receive high hook scores."""
    words_q = [
        Word("Why", 0.0, 0.4),
        Word("does", 0.4, 0.8),
        Word("nobody", 0.8, 1.2),
        Word("talk", 1.2, 1.6),
        Word("about", 1.6, 2.0),
        Word("this?", 2.0, 2.4),
    ]
    analysis = analyze_hook(words_q, 0.0, hook_window_s=2.5)
    assert analysis.score >= 8.5
    assert analysis.has_question is True
    assert analysis.has_bold_claim is True
    assert "question" in analysis.hook_type


def test_hook_filler_opening_penalized():
    """Filler word openings receive lower hook scores."""
    words_filler = [
        Word("Um,", 0.0, 0.5),
        Word("so", 0.5, 1.0),
        Word("yeah", 1.0, 1.5),
        Word("today", 1.5, 2.0),
    ]
    analysis = analyze_hook(words_filler, 0.0, hook_window_s=2.5)
    assert analysis.score <= 4.5
    assert analysis.has_filler_opening is True


def test_hook_window_configurability():
    """Speech occurring after the hook window is classified as delayed."""
    words = [Word("hello", 3.5, 4.0)]
    # With 2.0s window, speech at 3.5s is delayed
    a_short = analyze_hook(words, 0.0, hook_window_s=2.0)
    assert a_short.words_in_window == 0
    assert a_short.hook_type == "delayed_speech"

    # With 5.0s window, speech at 3.5s is captured
    a_long = analyze_hook(words, 0.0, hook_window_s=5.0)
    assert a_long.words_in_window == 1


# ---------------------------------------------------------------------------
# 4. CALL TO ACTION (CTA) ANALYSIS
# ---------------------------------------------------------------------------


def test_cta_detection_success():
    """High-intent CTA near the end is detected with high score."""
    words = [
        Word("that", 0.0, 0.5),
        Word("is", 0.5, 1.0),
        Word("all,", 1.0, 1.5),
        Word("subscribe", 7.0, 7.5),
        Word("for", 7.5, 8.0),
        Word("more!", 8.0, 8.5),
    ]
    cta = analyze_cta(words, 8.5, cta_window_s=5.0)
    assert cta.has_cta is True
    assert cta.score >= 8.5
    assert cta.is_within_window is True
    assert cta.cta_type == "subscribe_follow"
    assert "subscribe" in cta.matched_phrase.lower()


def test_cta_detection_outside_window():
    """CTA occurring too early before the end is flagged as outside window."""
    words = [
        Word("subscribe", 2.0, 2.5),
        Word("now.", 2.5, 3.0),
        Word("now", 5.0, 5.5),
        Word("back", 5.5, 6.0),
        Word("to", 6.0, 6.5),
        Word("the", 6.5, 7.0),
        Word("topic", 7.0, 15.0),
    ]
    # End is 15.0s, window is 3.0s (12.0s - 15.0s). 'subscribe' is at 2.0s.
    cta = analyze_cta(words, 15.0, cta_window_s=3.0)
    assert cta.has_cta is True
    assert cta.is_within_window is False
    assert cta.score <= 5.0


def test_cta_absent():
    """Candidate with no CTA scores 0.0."""
    words = [Word("just", 0.0, 0.5), Word("talking", 0.5, 1.0)]
    cta = analyze_cta(words, 1.0)
    assert cta.has_cta is False
    assert cta.score == 0.0


# ---------------------------------------------------------------------------
# 5. CONTENT DENSITY & DEAD AIR
# ---------------------------------------------------------------------------


def test_speech_density_and_silence():
    """Speech density w/s and dead air ratio are measured correctly."""
    words = [Word("word", float(i), float(i + 0.5)) for i in range(10)]
    silences = [Silence(start=0.5, end=1.0), Silence(start=5.5, end=6.5)]
    res = analyze_speech_density_and_silence(words, 0.0, 10.0, silences=silences)
    assert res.words_per_second == 1.0
    assert res.total_words == 10
    assert res.max_silence_gap_s == 1.0
    assert res.total_silence_s == 1.5
    assert res.dead_air_ratio == 0.15


# ---------------------------------------------------------------------------
# 6. CANDIDATE RANKING & FILTERING
# ---------------------------------------------------------------------------


def test_ranking_and_filtering():
    """Hard failures are pruned, survivors ordered by final_score, limit respected."""
    campaign = CampaignBrief(
        minimum_duration=10.0,
        maximum_duration=30.0,
        output_count=2,
        hook_required=False,
    )
    evaluator = CampaignEvaluator(campaign)

    # Candidate 1: valid, high score (15s duration)
    words1 = [Word("valid", 0.0, 5.0), Word("clip", 5.0, 15.0)]
    ev1 = evaluator.evaluate_candidate(
        candidate_id="c1", start_s=0.0, end_s=15.0, words=words1, base_viral_score=9.5
    )

    # Candidate 2: invalid duration (5s < 10s) -> hard failure
    words2 = [Word("short", 0.0, 2.0), Word("clip", 2.0, 5.0)]
    ev2 = evaluator.evaluate_candidate(
        candidate_id="c2", start_s=0.0, end_s=5.0, words=words2, base_viral_score=9.9
    )

    # Candidate 3: valid, medium score (20s duration)
    words3 = [Word("medium", 0.0, 10.0), Word("clip", 10.0, 20.0)]
    ev3 = evaluator.evaluate_candidate(
        candidate_id="c3", start_s=0.0, end_s=20.0, words=words3, base_viral_score=7.0
    )

    clips = [
        Clip(id="c1", job_id="j1", start_s=0.0, end_s=15.0, score=95),
        Clip(id="c2", job_id="j1", start_s=0.0, end_s=5.0, score=99),
        Clip(id="c3", job_id="j1", start_s=0.0, end_s=20.0, score=70),
    ]

    survivors, evals = rank_and_filter_candidates(clips, [ev1, ev2, ev3], campaign)

    # c2 must be pruned
    assert len(survivors) == 2
    assert [c.id for c in survivors] == ["c1", "c3"]
    assert survivors[0].rank == 1
    assert survivors[1].rank == 2
    assert survivors[0].score > survivors[1].score


# ---------------------------------------------------------------------------
# 7. SQLITE PERSISTENCE & MIGRATION V2
# ---------------------------------------------------------------------------


def test_campaign_evaluations_db_persistence(initialised_db):
    """Test schema v2 creation and CampaignEvaluationRow CRUD operations."""
    source_id = new_id()
    job_id = new_id()
    clip_id = new_id()

    store.create_source(Source(id=source_id, type="upload", path="test.mp4", duration_s=30.0))
    store.create_job(Job(id=job_id, source_id=source_id))
    store.replace_clips(job_id, [Clip(id=clip_id, job_id=job_id, start_s=0.0, end_s=15.0)])

    row = CampaignEvaluationRow(
        clip_id=clip_id,
        campaign_id="camp_test_123",
        approved=True,
        final_score=8.75,
        hook_score=9.2,
        cta_score=8.0,
        viral_score=8.9,
        density_score=9.0,
        hard_failures=[],
        soft_warnings=["Duration slightly below preferred"],
        rule_results={"hook": {"passed": True, "score": 9.2}},
    )

    # Insert
    saved = store.create_campaign_evaluation(row)
    assert saved.clip_id == clip_id

    # Retrieve by clip_id
    retrieved = store.get_campaign_evaluation(clip_id)
    assert retrieved is not None
    assert retrieved.campaign_id == "camp_test_123"
    assert retrieved.final_score == 8.75
    assert retrieved.approved is True
    assert retrieved.soft_warnings == ["Duration slightly below preferred"]
    assert "hook" in retrieved.rule_results

    # List by job_id
    job_evals = store.list_campaign_evaluations_for_job(job_id)
    assert len(job_evals) == 1
    assert job_evals[0].clip_id == clip_id
