"""Tests for clip assembly replenishment, natural pause snapping, and partial quota acceptance."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from autoclip.campaign.clip_assembly import (
    BoundaryOptimization,
    ClipAssemblyEngine,
    PreRenderQualityGate,
    SmartBoundaryEngine,
)
from autoclip.db.models import ClipCandidateRecord, ClipSpecificationRecord, new_id, utcnow
from autoclip.pipeline.highlights import HighlightError
from autoclip.pipeline.transcript import Transcript, Word


def _make_transcript_with_words(word_tuples: list[tuple[str, float, float]]) -> Transcript:
    return Transcript(words=[Word(text=w, start=s, end=e, speaker="SPEAKER_00") for w, s, e in word_tuples])


def test_conditional_token_with_natural_pause_not_rejected():
    """Verify that a sentence ending with 'you' followed by a natural pause is not rejected."""
    words = [Word(f"word{i}", float(i * 0.5), float(i * 0.5 + 0.4)) for i in range(40)]
    # Add ending words: 'for' at 20.0-20.4, 'you' at 20.5-20.9
    words.append(Word("for", 20.0, 20.4))
    words.append(Word("you", 20.5, 20.9))  # No punctuation
    # Next word starts after a 0.45s pause
    words.append(Word("Now", 21.35, 21.8))
    words.extend([Word(f"tail{i}", 22.0 + i * 0.5, 22.4 + i * 0.5) for i in range(10)])

    t = Transcript(words=words)
    end_w = 41  # Index of 'you'
    opt = BoundaryOptimization(
        original_start_s=0.0,
        original_end_s=20.9,
        optimized_start_s=0.0,
        optimized_end_s=20.98,
        start_word=0,
        end_word=end_w,
        duration_s=20.98,
        hook_start_s=0.0,
        hook_end_s=3.0,
        hook_type="bold_claim",
        hook_score=8.5,
        climax_start_s=None,
        climax_end_s=None,
        cta_start_s=None,
        cta_end_s=None,
        cta_type="",
    )

    gate = PreRenderQualityGate()
    res = gate.evaluate(opt, t)
    assert not any("dangling_sentence_ending" in r for r in res.rejection_reasons)
    assert res.is_approved


def test_smart_boundary_engine_snaps_forward_to_terminal():
    """Verify that boundary engine snaps forward to sentence terminal if end word is dangling."""
    words = [Word(f"w{i}", float(i * 0.5), float(i * 0.5 + 0.4)) for i in range(42)]
    # At word 42 (21.0s), 'you' without punctuation
    words.append(Word("you", 21.0, 21.4))
    # At word 43 (21.5s), 'know.' with terminal punctuation
    words.append(Word("know.", 21.5, 21.9))
    words.extend([Word(f"after{i}", 22.5 + i * 0.5, 22.9 + i * 0.5) for i in range(10)])

    t = Transcript(words=words)
    candidate = ClipCandidateRecord(
        id=new_id(),
        job_id="job-snap",
        rank=1,
        selected=True,
        status="selected",
        start_s=0.0,
        end_s=21.4,
        duration_s=21.4,
        start_word=0,
        end_word=42,  # Ends on 'you'
        title="Snap test",
        hook_text="Hook",
        reason="Reason",
        transcript_slice="Slice",
        score=85.0,
        created_at=utcnow(),
        updated_at=utcnow(),
    )

    engine = SmartBoundaryEngine()
    opt = engine.optimize(candidate, t, duration_min_s=20.0, duration_max_s=30.0)

    # Must snap forward to 'know.'
    last_word = t.words[opt.end_word].text
    assert last_word == "know."
    assert any("snapped_forward" in adj for adj in opt.adjustments)


def test_clip_replenishment_pass_fills_quota():
    """Verify that ClipAssemblyEngine replenishes quota when primary pass has overlapping candidates."""
    # Build a transcript of 160 seconds
    words = []
    curr = 0.0
    for i in range(250):
        punct = "." if i % 10 == 9 else ""
        words.append(Word(text=f"word{i}{punct}", start=curr, end=curr + 0.4, speaker="SPEAKER_00"))
        curr += 0.6
    t = Transcript(words=words)

    # Create 6 candidates: 4 non-overlapping, 2 overlapping with candidate 1 but with high score
    candidates = []
    # Candidate 1: [0.0s, 24.0s]
    candidates.append(
        ClipCandidateRecord(
            id="c1", job_id="job-rep", rank=1, selected=True, status="scored",
            start_s=0.0, end_s=24.0, duration_s=24.0, start_word=0, end_word=39,
            title="Clip 1", hook_text="Hook 1", reason="R1", transcript_slice="Slice 1", score=90.0,
            hook_signals={"type": "bold_claim", "score": 9.0, "start_s": 0.0, "end_s": 3.0},
            created_at=utcnow(), updated_at=utcnow(),
        )
    )
    # Candidate 2: [30.0s, 54.0s]
    candidates.append(
        ClipCandidateRecord(
            id="c2", job_id="job-rep", rank=2, selected=True, status="scored",
            start_s=30.0, end_s=54.0, duration_s=24.0, start_word=50, end_word=89,
            title="Clip 2", hook_text="Hook 2", reason="R2", transcript_slice="Slice 2", score=88.0,
            hook_signals={"type": "bold_claim", "score": 9.0, "start_s": 30.0, "end_s": 33.0},
            created_at=utcnow(), updated_at=utcnow(),
        )
    )
    # Candidate 3: [60.0s, 84.0s]
    candidates.append(
        ClipCandidateRecord(
            id="c3", job_id="job-rep", rank=3, selected=True, status="scored",
            start_s=60.0, end_s=84.0, duration_s=24.0, start_word=100, end_word=139,
            title="Clip 3", hook_text="Hook 3", reason="R3", transcript_slice="Slice 3", score=86.0,
            hook_signals={"type": "bold_claim", "score": 9.0, "start_s": 60.0, "end_s": 63.0},
            created_at=utcnow(), updated_at=utcnow(),
        )
    )
    # Candidate 4: [90.0s, 114.0s]
    candidates.append(
        ClipCandidateRecord(
            id="c4", job_id="job-rep", rank=4, selected=True, status="scored",
            start_s=90.0, end_s=114.0, duration_s=24.0, start_word=150, end_word=189,
            title="Clip 4", hook_text="Hook 4", reason="R4", transcript_slice="Slice 4", score=84.0,
            hook_signals={"type": "bold_claim", "score": 9.0, "start_s": 90.0, "end_s": 93.0},
            created_at=utcnow(), updated_at=utcnow(),
        )
    )
    # Candidate 5 (overlaps with c4 slightly, IoU ~0.40): [105.0s, 130.0s]
    candidates.append(
        ClipCandidateRecord(
            id="c5", job_id="job-rep", rank=5, selected=True, status="scored",
            start_s=105.0, end_s=130.0, duration_s=25.0, start_word=175, end_word=216,
            title="Clip 5", hook_text="Hook 5", reason="R5", transcript_slice="Slice 5", score=82.0,
            hook_signals={"type": "bold_claim", "score": 9.0, "start_s": 105.0, "end_s": 108.0},
            created_at=utcnow(), updated_at=utcnow(),
        )
    )

    engine = ClipAssemblyEngine()
    approved, all_specs, telemetry = engine.assemble(
        candidates=candidates,
        transcript=t,
        job_id="job-rep",
        source_id="src-rep",
        target_count=5,
    )

    # Candidate 5 should be replenished to meet target count of 5
    assert len(approved) == 5, f"Expected 5 approved clips, got {len(approved)}"
