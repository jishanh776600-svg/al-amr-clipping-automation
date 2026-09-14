"""Tests for Step 15: Multi-Clip Autonomous Candidate Discovery & Contradiction-Aware Scoring."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

from autoclip.campaign import (
    CampaignBrief,
    CampaignConflict,
    CampaignSpecification,
    CandidateDiscoveryEngine,
    RequirementItem,
    candidates_to_clips,
)
from autoclip.db import store
from autoclip.db.models import ClipCandidateRecord, Job, Source, new_id, utcnow
from collections.abc import Iterator
from autoclip import app as app_module
from autoclip.app import create_app
from autoclip.pipeline.prepare import Silence
from autoclip.pipeline.transcript import Transcript, Word


@pytest.fixture
def client(autoclip_home, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv(app_module.ENV_NO_WORKER, "1")
    with TestClient(create_app()) as test_client:
        yield test_client


def _make_sample_transcript(sentence_count: int = 12) -> Transcript:
    """Creates a synthetic transcript with realistic words, punctuation, and timing."""
    sentences = [
        "The secret to viral automation is building autonomous systems that run continuously without failure.",
        "Most people don't know how powerful local AI inference has become in modern workflows.",
        "First, we acquire the video file and inspect all speech patterns carefully.",
        "Next, we extract the audio stream and transcribe every single word with microsecond precision.",
        "Why is this crucial for content creators and operators around the world?",
        "Because manual editing takes hours, whereas our engine analyzes narrative hooks in milliseconds!",
        "This completely transforms the entire clipping workflow and eliminates wasted editing budget.",
        "The climax of the system is the contradiction-aware candidate discovery engine that picks winning hooks.",
        "It balances explicit requirements against inferred signals with zero paid API overhead.",
        "If you want to master high-velocity content production, this is the ultimate blueprint.",
        "Click the link in bio to join our community and start automating your content pipeline today.",
        "Follow for more updates on next-generation artificial intelligence and automated publishing.",
    ]

    words: list[Word] = []
    current_time = 0.5

    for s_idx, sentence in enumerate(sentences[:sentence_count]):
        raw_words = sentence.split()
        for w_idx, w_text in enumerate(raw_words):
            duration = max(0.2, len(w_text) * 0.04)
            start_s = round(current_time, 2)
            end_s = round(current_time + duration, 2)
            words.append(
                Word(
                    text=w_text,
                    start=start_s,
                    end=end_s,
                    speaker="SPEAKER_00",
                )
            )
            current_time = end_s + (0.35 if w_idx == len(raw_words) - 1 else 0.08)

    return Transcript(
        words=words,
        language="en",
        model="whisper-base",
        source="whisper",
    )


# --------------------------------------------------------------------------
# 1. Window Discovery & Duration Bounds
# --------------------------------------------------------------------------


def test_window_discovery_duration_bounds():
    transcript = _make_sample_transcript(12)
    spec = CampaignSpecification(
        campaign_id="spec_1",
        title="Test Spec",
        duration_min_s=RequirementItem(value=15.0, confidence="explicit"),
        duration_max_s=RequirementItem(value=45.0, confidence="explicit"),
    )
    engine = CandidateDiscoveryEngine(campaign_spec=spec)
    windows = engine.discover_windows(transcript)

    assert len(windows) > 0
    for start_w, end_w, start_s, end_s in windows:
        dur = end_s - start_s
        assert dur >= 15.0 * 0.95
        assert dur <= 45.0 * 1.05
        assert start_w < end_w


def test_sentence_boundary_snapping():
    transcript = _make_sample_transcript(8)
    engine = CandidateDiscoveryEngine()
    windows = engine.discover_windows(transcript)

    for start_w, end_w, start_s, end_s in windows:
        # Check start word is not a dangling conjunction
        first_word = transcript.words[start_w].text.lower().strip()
        assert first_word not in {"and", "but", "so", "or", "because"}


# --------------------------------------------------------------------------
# 2. Milestone Detection (Hook, Setup, Climax, CTA)
# --------------------------------------------------------------------------


def test_milestone_detection():
    transcript = _make_sample_transcript(12)
    engine = CandidateDiscoveryEngine()
    words = transcript.words

    milestones = engine.detect_milestones(words, words[0].start, words[-1].end)
    assert milestones.hook_text != ""
    assert milestones.hook_score >= 5.0
    assert milestones.setup_start_s <= milestones.setup_end_s
    assert milestones.climax_start_s <= milestones.climax_end_s
    # In sentence 10 & 11 we have "link in bio" and "follow for more"
    assert milestones.cta_type in ("link", "follow", "community")
    assert milestones.cta_score > 0.0

    m_dict = milestones.to_dict()
    assert "hook" in m_dict
    assert "climax" in m_dict
    assert "cta" in m_dict


# --------------------------------------------------------------------------
# 3. Scoring: Explicit (2.0x) vs Inferred (1.0x) Weights
# --------------------------------------------------------------------------


def test_scoring_explicit_vs_inferred_weights():
    transcript = _make_sample_transcript(12)
    spec_explicit = CampaignSpecification(
        campaign_id="spec_exp",
        title="Explicit Spec",
        desired_topics=[
            RequirementItem(value="viral automation", confidence="explicit", weight=2.0),
        ],
    )
    spec_inferred = CampaignSpecification(
        campaign_id="spec_inf",
        title="Inferred Spec",
        desired_topics=[
            RequirementItem(value="viral automation", confidence="inferred", weight=1.0),
        ],
    )

    engine_exp = CandidateDiscoveryEngine(campaign_spec=spec_explicit)
    engine_inf = CandidateDiscoveryEngine(campaign_spec=spec_inferred)

    # Slice covering first sentence ("The secret to viral automation...")
    words = transcript.words[:14]
    start_s = words[0].start
    end_s = words[-1].end

    m_exp = engine_exp.detect_milestones(words, start_s, end_s)
    score_exp = engine_exp.score_candidate(words, start_s, end_s, m_exp)

    m_inf = engine_inf.detect_milestones(words, start_s, end_s)
    score_inf = engine_inf.score_candidate(words, start_s, end_s, m_inf)

    # Explicit requirement match receives higher explicit score
    assert score_exp.explicit_match_score >= score_inf.inferred_match_score


# --------------------------------------------------------------------------
# 4. Banned Words / Topics Rejection
# --------------------------------------------------------------------------


def test_banned_words_rejection():
    transcript = _make_sample_transcript(8)
    spec = CampaignSpecification(
        campaign_id="spec_banned",
        title="Banned Word Spec",
        banned_words=[RequirementItem(value="microsecond", confidence="explicit")],
    )
    engine = CandidateDiscoveryEngine(campaign_spec=spec)

    # Words containing "microsecond" (Sentence 4)
    target_words = [w for w in transcript.words if "microsecond" in w.text.lower()]
    assert len(target_words) > 0

    mid_idx = transcript.words.index(target_words[0])
    test_slice = transcript.words[max(0, mid_idx - 10) : min(len(transcript.words), mid_idx + 10)]

    milestones = engine.detect_milestones(test_slice, test_slice[0].start, test_slice[-1].end)
    score_res = engine.score_candidate(test_slice, test_slice[0].start, test_slice[-1].end, milestones)

    assert not score_res.approved
    assert score_res.total_score == 0.0
    assert any("banned word: 'microsecond'" in r for r in score_res.rejection_reasons)


# --------------------------------------------------------------------------
# 5. Campaign Conflicts Penalization
# --------------------------------------------------------------------------


def test_conflict_penalties():
    transcript = _make_sample_transcript(8)
    spec_with_conflicts = CampaignSpecification(
        campaign_id="spec_conf",
        title="Conflict Spec",
        conflicts=[
            CampaignConflict(
                id="conf_1",
                rule_category="duration",
                severity="warning",
                document_a="DocA",
                document_b="DocB",
                description="Duration bounds conflict",
                resolution_status="unresolved",
            )
        ],
    )
    engine = CandidateDiscoveryEngine(campaign_spec=spec_with_conflicts)
    words = transcript.words[:20]
    milestones = engine.detect_milestones(words, words[0].start, words[-1].end)
    score_res = engine.score_candidate(words, words[0].start, words[-1].end, milestones)

    assert score_res.penalties > 0.0
    assert "penalties" in score_res.breakdown


# --------------------------------------------------------------------------
# 6. Top-N Selection & Deduplication
# --------------------------------------------------------------------------


def test_top_n_selection_and_deduplication():
    transcript = _make_sample_transcript(12)
    spec = CampaignSpecification(
        campaign_id="spec_top_n",
        title="Top-N Spec",
        output_count=RequirementItem(value=2, confidence="explicit"),
    )
    engine = CandidateDiscoveryEngine(campaign_spec=spec)
    selected, all_cands, telemetry = engine.run(transcript, job_id="job_top_n")

    assert len(selected) <= 2
    assert len(all_cands) >= len(selected)
    assert telemetry["selected_count"] == len(selected)

    # Verify no selected candidates overlap heavily (IoU <= 0.35)
    if len(selected) == 2:
        from autoclip.campaign.candidate_discovery import compute_iou
        iou = compute_iou(selected[0].start_s, selected[0].end_s, selected[1].start_s, selected[1].end_s)
        assert iou <= 0.35

    # Check that clips bridge successfully
    clips = candidates_to_clips(selected, job_id="job_top_n")
    assert len(clips) == len(selected)
    for c in clips:
        assert c.job_id == "job_top_n"
        assert c.score > 0


# --------------------------------------------------------------------------
# 7. Empty / Poor Transcript Safety
# --------------------------------------------------------------------------


def test_empty_transcript_safety():
    empty_transcript = Transcript(words=[], language="en", model="whisper-tiny")
    engine = CandidateDiscoveryEngine()
    selected, all_cands, telemetry = engine.run(empty_transcript, job_id="job_empty")

    assert selected == []
    assert all_cands == []
    assert telemetry["discovered_count"] == 0
    assert telemetry["selected_count"] == 0


# --------------------------------------------------------------------------
# 8. Database Persistence & Migration _V10
# --------------------------------------------------------------------------


def test_db_persistence_and_retrieval(initialised_db):
    job_id = new_id()
    src = Source(id=new_id(), type="upload", path="test.mp4", duration_s=60.0)
    store.create_source(src)
    job = Job(id=job_id, source_id=src.id)
    store.create_job(job)

    cand1 = ClipCandidateRecord(
        id=new_id(),
        job_id=job_id,
        rank=1,
        selected=True,
        status="selected",
        start_s=10.0,
        end_s=35.0,
        duration_s=25.0,
        start_word=5,
        end_word=40,
        title="Candidate 1",
        hook_text="Hook 1",
        reason="Reason 1",
        score=88.5,
        score_breakdown={"explicit_match": 90.0},
        hook_signals={"type": "question", "score": 8.5},
        rejection_reasons=[],
    )

    cand2 = ClipCandidateRecord(
        id=new_id(),
        job_id=job_id,
        rank=0,
        selected=False,
        status="rejected",
        start_s=15.0,
        end_s=40.0,
        duration_s=25.0,
        start_word=10,
        end_word=45,
        title="Candidate 2",
        score=0.0,
        rejection_reasons=["Contains banned word"],
    )

    store.replace_clip_candidates(job_id, [cand1, cand2])

    all_cands = store.list_clip_candidates(job_id)
    assert len(all_cands) == 2

    selected_cands = store.list_clip_candidates(job_id, selected_only=True)
    assert len(selected_cands) == 1
    assert selected_cands[0].id == cand1.id
    assert selected_cands[0].selected is True
    assert selected_cands[0].score_breakdown["explicit_match"] == 90.0

    retrieved = store.get_clip_candidate(cand1.id)
    assert retrieved is not None
    assert retrieved.title == "Candidate 1"


# --------------------------------------------------------------------------
# 9. API Endpoint GET /api/jobs/{job_id}/candidates
# --------------------------------------------------------------------------


def test_api_job_candidates_endpoint(client):
    job_id = new_id()
    src = Source(id=new_id(), type="upload", path="test.mp4", duration_s=50.0)
    store.create_source(src)
    job = Job(id=job_id, source_id=src.id)
    store.create_job(job)

    cand = ClipCandidateRecord(
        id=new_id(),
        job_id=job_id,
        rank=1,
        selected=True,
        status="selected",
        start_s=5.0,
        end_s=25.0,
        duration_s=20.0,
        title="API Candidate",
        hook_text="API Hook",
        score=92.0,
        score_breakdown={"hook": 90.0},
    )
    store.replace_clip_candidates(job_id, [cand])

    res = client.get(f"/api/jobs/{job_id}/candidates")
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 1
    assert data[0]["title"] == "API Candidate"
    assert data[0]["selected"] is True
    assert data[0]["score"] == 92.0

    # 404 for nonexistent job
    res_404 = client.get("/api/jobs/nonexistent_job_123/candidates")
    assert res_404.status_code == 404


# --------------------------------------------------------------------------
# 10. Pipeline Runner Integration Simulation
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_runner_highlights_integration(initialised_db):
    from autoclip.pipeline.runner import PipelineRunner

    job_id = new_id()
    src = Source(id=new_id(), type="upload", path="test.mp4", duration_s=120.0)
    store.create_source(src)
    job = Job(id=job_id, source_id=src.id)
    store.create_job(job)

    transcript = _make_sample_transcript(12)
    silences = [Silence(start=2.0, end=2.4)]

    runner = PipelineRunner(job, src)
    clips = await runner._stage_highlights(transcript, silences)

    assert len(clips) > 0
    stored_clips = store.list_clips(job_id)
    assert len(stored_clips) == len(clips)

    # Verify candidates were persisted to DB
    cands = store.list_clip_candidates(job_id)
    assert len(cands) > 0

    # Verify telemetry was stored in job settings
    updated_job = store.get_job(job_id)
    assert "candidate_telemetry" in updated_job.settings
    assert updated_job.settings["candidate_telemetry"]["selected_count"] == len(clips)
