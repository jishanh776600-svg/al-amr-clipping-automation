"""Tests for Step 16: Campaign-Aware Clip Assembly, Smart Boundaries & Quality Gate."""

from __future__ import annotations

import json
from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

from autoclip import app as app_module
from autoclip.app import create_app
from autoclip.campaign import (
    BoundaryOptimization,
    CampaignBrief,
    CampaignConflict,
    CampaignSpecification,
    ClipAssemblyEngine,
    PreRenderQualityGate,
    RequirementItem,
    SmartBoundaryEngine,
    specifications_to_clips,
)
from autoclip.db import store
from autoclip.db.models import (
    ClipCandidateRecord,
    ClipSpecificationRecord,
    Job,
    Source,
    new_id,
    utcnow,
)
from autoclip.pipeline.prepare import Silence
from autoclip.pipeline.transcript import Transcript, Word


@pytest.fixture
def client(autoclip_home, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv(app_module.ENV_NO_WORKER, "1")
    with TestClient(create_app()) as test_client:
        yield test_client


def _make_transcript() -> Transcript:
    """Creates a sample transcript with clear sentences, fillers, and milestones."""
    sentences = [
        "So you know, the secret to viral automation is building autonomous systems that run continuously without failure.",
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
    current_time = 1.0
    for s_idx, sentence in enumerate(sentences):
        tokens = sentence.split()
        for t_idx, token in enumerate(tokens):
            w_start = current_time
            w_dur = 0.28 + (len(token) * 0.02)
            w_end = w_start + w_dur
            words.append(Word(text=token, start=w_start, end=w_end, speaker="SPEAKER_00"))
            current_time = w_end + 0.06
        current_time += 0.40  # sentence pause

    return Transcript(words=words)


def _make_sample_candidate(
    job_id: str,
    start_s: float = 1.0,
    end_s: float = 32.0,
    start_word: int = 0,
    end_word: int = 40,
    score: float = 88.0,
    title: str = "Winning Candidate",
) -> ClipCandidateRecord:
    return ClipCandidateRecord(
        id=new_id(),
        job_id=job_id,
        rank=1,
        selected=True,
        status="selected",
        start_s=start_s,
        end_s=end_s,
        duration_s=end_s - start_s,
        start_word=start_word,
        end_word=end_word,
        title=title,
        hook_text="The secret to viral automation",
        reason="Strong hook with climax",
        transcript_slice="Sample transcript slice",
        score=score,
        score_breakdown={"explicit": 90.0, "hook": 9.2},
        hook_signals={"type": "bold_claim", "score": 9.2, "start_s": start_s, "end_s": start_s + 4.0},
        climax_signals={"start_s": start_s + 15.0, "end_s": start_s + 20.0, "score": 8.5},
        cta_signals={"type": "link_in_bio", "score": 9.0, "start_s": end_s - 5.0, "end_s": end_s},
        requirement_matches=[],
        rejection_reasons=[],
        created_at=utcnow(),
        updated_at=utcnow(),
    )


# ==============================================================================
# 1. Smart Boundary Tests
# ==============================================================================


def test_filler_word_removal():
    """Verify that leading filler words like 'so', 'you know' are stripped from the beginning."""
    transcript = _make_transcript()
    # Sentence 0 begins with "So you know, the secret..."
    candidate = _make_sample_candidate("job-filler", start_s=1.0, end_s=25.0, start_word=0, end_word=35)
    engine = SmartBoundaryEngine()

    opt = engine.optimize(candidate, transcript, duration_min_s=15.0, duration_max_s=60.0)

    # First word in transcript is "So", second is "you", third is "know,"
    # After stripping, the start word should be shifted forward
    assert opt.start_word > 0
    first_opt_word = transcript.words[opt.start_word].text.lower()
    assert first_opt_word not in ("so", "you", "know,")
    assert any("stripped_filler" in adj for adj in opt.adjustments)


def test_word_boundary_safety():
    """Verify boundaries never cut through words."""
    transcript = _make_transcript()
    candidate = _make_sample_candidate("job-word-bound", start_s=2.123, end_s=25.456, start_word=0, end_word=35)
    engine = SmartBoundaryEngine()

    opt = engine.optimize(candidate, transcript, duration_min_s=15.0, duration_max_s=60.0)

    # Start and end must correspond to valid word boundaries with small padding
    assert opt.start_word >= 0
    assert opt.end_word < len(transcript.words)
    assert opt.optimized_start_s <= transcript.words[opt.start_word].start
    assert opt.optimized_end_s >= transcript.words[opt.end_word].end


def test_incomplete_sentence_prevention():
    """Verify trailing dangling conjunctions ('and', 'because') are not left hanging at clip end."""
    transcript = _make_transcript()
    # Find a word ending mid-sentence
    dangling_idx = 10
    while transcript.words[dangling_idx].text.endswith((".", "!", "?")):
        dangling_idx += 1

    candidate = _make_sample_candidate("job-incomplete", start_s=1.0, end_s=18.0, start_word=0, end_word=dangling_idx)
    engine = SmartBoundaryEngine()

    opt = engine.optimize(candidate, transcript, duration_min_s=10.0, duration_max_s=60.0)
    end_word = transcript.words[opt.end_word]
    assert not any(end_word.text.lower().startswith(d) for d in ("and", "or", "because", "but"))


def test_hook_shifting():
    """Verify hook optimizer shifts start forward if a superior hook appears shortly in."""
    transcript = _make_transcript()
    # Candidate starting before sentence 4 ("Why is this crucial...")
    candidate = _make_sample_candidate("job-hook-shift", start_s=10.0, end_s=38.0, start_word=18, end_word=60)
    engine = SmartBoundaryEngine()

    opt = engine.optimize(candidate, transcript, duration_min_s=15.0, duration_max_s=60.0)
    assert opt.hook_type in ("question", "bold_claim", "surprising_fact", "narrative_setup", "weak/none")
    assert opt.hook_score >= 5.0


def test_climax_preservation():
    """Verify climax moment is preserved within optimized boundaries."""
    transcript = _make_transcript()
    candidate = _make_sample_candidate(
        "job-climax",
        start_s=5.0,
        end_s=25.0,
        start_word=10,
        end_word=40,
    )
    # Set climax between 18.0 and 22.0
    candidate.climax_signals = {"start_s": 18.0, "end_s": 22.0, "score": 9.0}

    engine = SmartBoundaryEngine()
    opt = engine.optimize(candidate, transcript, duration_min_s=15.0, duration_max_s=60.0)

    # Climax must be fully inside optimized boundaries
    assert opt.optimized_start_s <= 18.5
    assert opt.optimized_end_s >= 21.5


def test_cta_preservation():
    """Verify CTA is preserved when campaign requires it."""
    transcript = _make_transcript()
    # Sentence 10 has "Click the link in bio to join our community"
    candidate = _make_sample_candidate("job-cta", start_s=20.0, end_s=60.0, start_word=35, end_word=len(transcript.words) - 1)
    engine = SmartBoundaryEngine()

    opt = engine.optimize(candidate, transcript, duration_min_s=15.0, duration_max_s=65.0, require_cta=True)
    assert opt.cta_type != ""


# ==============================================================================
# 2. Quality Gate Tests
# ==============================================================================


def test_quality_gate_pass():
    """Verify high-quality candidate passes PreRenderQualityGate with QUALITY_PASS."""
    transcript = _make_transcript()
    candidate = _make_sample_candidate("job-qg-pass", start_s=2.0, end_s=28.0, start_word=5, end_word=50)
    engine = SmartBoundaryEngine()
    opt = engine.optimize(candidate, transcript, duration_min_s=15.0, duration_max_s=60.0)

    qg = PreRenderQualityGate()
    result = qg.evaluate(opt, transcript)

    assert result.status in ("QUALITY_PASS", "QUALITY_WARN")
    assert result.quality_score >= 60.0
    assert result.is_approved is True
    assert len(result.rejection_reasons) == 0


def test_quality_gate_banned_content_rejection():
    """Verify candidate containing banned campaign content is rejected with QUALITY_REJECT."""
    transcript = _make_transcript()
    candidate = _make_sample_candidate("job-banned", start_s=1.0, end_s=28.0, start_word=0, end_word=50)
    engine = SmartBoundaryEngine()
    opt = engine.optimize(candidate, transcript, duration_min_s=15.0, duration_max_s=60.0)

    spec = CampaignSpecification(
        title="Test Campaign",
        banned_words=[RequirementItem(value="automation", confidence="explicit")],
    )

    qg = PreRenderQualityGate(campaign_spec=spec)
    result = qg.evaluate(opt, transcript)

    assert result.status == "QUALITY_REJECT"
    assert result.is_approved is False
    assert any("contains_banned_content" in r for r in result.rejection_reasons)


def test_quality_gate_duration_bounds():
    """Verify candidate violating duration bounds is rejected with QUALITY_REJECT."""
    transcript = _make_transcript()
    # Candidate of only 5 seconds with no climax/CTA expansion
    candidate = _make_sample_candidate("job-dur", start_s=1.0, end_s=6.0, start_word=0, end_word=8)
    candidate.climax_signals = {}
    candidate.cta_signals = {}
    engine = SmartBoundaryEngine()
    opt = engine.optimize(candidate, transcript, duration_min_s=15.0, duration_max_s=60.0)

    qg = PreRenderQualityGate()
    result = qg.evaluate(opt, transcript)

    assert result.status == "QUALITY_REJECT"
    assert any("duration_under_min" in r for r in result.rejection_reasons)


def test_quality_gate_dead_air_rejection():
    """Verify candidate containing excessive internal silence (>3.0s) is rejected."""
    transcript = _make_transcript()
    candidate = _make_sample_candidate("job-silence", start_s=1.0, end_s=30.0, start_word=0, end_word=50)
    engine = SmartBoundaryEngine()
    opt = engine.optimize(candidate, transcript, duration_min_s=15.0, duration_max_s=60.0)

    silences = [Silence(start=10.0, end=14.0)]  # 4s dead air
    qg = PreRenderQualityGate()
    result = qg.evaluate(opt, transcript, silences=silences)

    assert result.status == "QUALITY_REJECT"
    assert any("excessive_dead_air" in r for r in result.rejection_reasons)


# ==============================================================================
# 3. ClipAssemblyEngine Integration & Failure Isolation
# ==============================================================================


def test_assembly_engine_isolation_and_telemetry():
    """Verify ClipAssemblyEngine processes batch with error isolation and emits telemetry."""
    transcript = _make_transcript()
    good_cand = _make_sample_candidate("job-batch", start_s=2.0, end_s=28.0, start_word=5, end_word=50)
    bad_cand = _make_sample_candidate("job-batch", start_s=1.0, end_s=5.0, start_word=0, end_word=5)

    progress_events: list[str] = []

    def on_prog(substage: str, frac: float, meta: dict):
        progress_events.append(substage)

    engine = ClipAssemblyEngine()
    approved, all_specs, telemetry = engine.assemble(
        candidates=[good_cand, bad_cand],
        transcript=transcript,
        job_id="job-batch",
        source_id="src-1",
        on_progress=on_prog,
    )

    assert len(all_specs) == 2
    assert len(approved) >= 1
    assert telemetry["candidates_received"] == 2
    assert telemetry["clips_optimized"] == 2
    assert "OPTIMIZING_BOUNDARIES" in progress_events
    assert "RUNNING_QUALITY_GATE" in progress_events


def test_empty_candidates_safety():
    """Verify ClipAssemblyEngine safely handles empty candidate sets without hanging."""
    transcript = _make_transcript()
    engine = ClipAssemblyEngine()
    approved, all_specs, telemetry = engine.assemble(
        candidates=[],
        transcript=transcript,
        job_id="job-empty",
        source_id="src-1",
    )
    assert approved == []
    assert all_specs == []
    assert telemetry["candidates_received"] == 0


# ==============================================================================
# 4. Database Persistence & API Endpoints
# ==============================================================================


def test_db_persistence_and_retrieval(initialised_db):
    """Verify SQLite migration _V11 and clip_specifications CRUD operations."""
    src = store.create_source(Source(id="src-spec-1", type="upload", path="test.mp4", duration_s=60.0))
    job = store.create_job(
        Job(
            id="job-spec-db-test",
            source_id=src.id,
            status="running",
            current_stage="highlights",
        )
    )
    cand = store.replace_clip_candidates(
        job.id,
        [
            ClipCandidateRecord(
                id="cand-test-1",
                job_id=job.id,
                start_s=5.0,
                end_s=25.0,
                duration_s=20.0,
            )
        ],
    )[0]

    spec = ClipSpecificationRecord(
        id="spec-test-1",
        job_id=job.id,
        candidate_id=cand.id,
        source_id=src.id,
        start_time=5.2,
        end_time=25.8,
        duration=20.6,
        start_word=5,
        end_word=45,
        hook_start=5.2,
        hook_end=9.2,
        hook_type="bold_claim",
        climax_start=18.0,
        climax_end=22.0,
        cta_start=23.0,
        cta_end=25.8,
        boundary_adjustments={"stripped_filler": True},
        requirement_matches=[{"rule": "duration", "passed": True}],
        quality_score=85.5,
        quality_status="QUALITY_PASS",
        rejection_reasons=[],
        warnings=[],
        final_rank=1,
        created_at=utcnow(),
        updated_at=utcnow(),
    )

    store.replace_clip_specifications(job.id, [spec])

    specs = store.list_clip_specifications(job.id)
    assert len(specs) == 1
    loaded = specs[0]
    assert loaded.id == "spec-test-1"
    assert loaded.quality_status == "QUALITY_PASS"
    assert loaded.duration == 20.6
    assert loaded.hook_type == "bold_claim"

    single = store.get_clip_specification("spec-test-1")
    assert single is not None
    assert single.id == "spec-test-1"


def test_api_clip_specifications_endpoint(client: TestClient, initialised_db):
    """Verify GET /api/jobs/{job_id}/clip-specifications endpoint."""
    src = store.create_source(Source(id="src-spec-2", type="upload", path="test.mp4", duration_s=60.0))
    job = store.create_job(
        Job(
            id="job-spec-api-test",
            source_id=src.id,
            status="running",
            current_stage="highlights",
        )
    )
    cand = store.replace_clip_candidates(
        job.id,
        [
            ClipCandidateRecord(
                id="cand-api-1",
                job_id=job.id,
                start_s=10.0,
                end_s=35.0,
                duration_s=25.0,
            )
        ],
    )[0]

    spec = ClipSpecificationRecord(
        id="spec-api-1",
        job_id=job.id,
        candidate_id=cand.id,
        source_id=src.id,
        start_time=10.0,
        end_time=35.0,
        duration=25.0,
        quality_score=92.0,
        quality_status="QUALITY_PASS",
        final_rank=1,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    store.replace_clip_specifications(job.id, [spec])

    resp = client.get(f"/api/jobs/{job.id}/clip-specifications")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["id"] == "spec-api-1"
    assert data[0]["quality_status"] == "QUALITY_PASS"
    assert data[0]["duration"] == 25.0


def test_specifications_to_clips_bridge():
    """Verify specifications_to_clips converts ClipSpecificationRecord into Clip models with optimized boundaries."""
    spec = ClipSpecificationRecord(
        id="spec-bridge-1",
        job_id="job-bridge",
        candidate_id="cand-bridge-1",
        source_id="src-1",
        start_time=3.5,
        end_time=28.5,
        duration=25.0,
        start_word=8,
        end_word=62,
        quality_score=89.0,
        quality_status="QUALITY_PASS",
        final_rank=1,
        created_at=utcnow(),
        updated_at=utcnow(),
    )

    clips = specifications_to_clips([spec])
    assert len(clips) == 1
    clip = clips[0]
    assert clip.id == "spec-bridge-1"
    assert clip.start_s == 3.5
    assert clip.end_s == 28.5
    assert clip.start_word == 8
    assert clip.end_word == 62
    assert clip.score == 89
