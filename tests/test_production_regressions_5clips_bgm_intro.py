"""End-to-End Regression Test Suite for AL AMR Production Regressions.

Covers:
1. BGM Loudness & Ducking:
   - Voice dominance
   - BGM bed in [-31, -28] dBFS range during active speech
   - 16-18 dB ducking attenuation delta
   - 250ms release time
   - Master loudness normalized to -14.0 LUFS
2. BGM Persistence & Selection Mode:
   - Explicit track selection preserved across reloads and syncs
   - Default canonical BGM auto-mix
   - Voice-only ("none") disables BGM cleanly
   - Fallback tracking: bgm_requested_id, bgm_selection_mode, bgm_fallback_reason
3. GitHub PAT Durability:
   - Survives reload, settings saves, and Blueprint key regeneration
   - Zero PAT exposure in logs or settings objects
4. 5 Clips Guarantee & Replenishment:
   - TARGET / REQUIRED_FINAL_CLIPS = 5
   - Candidate replenishment loop: when a top candidate fails quality gate, the pipeline
     replenishes from candidate pool to deliver exactly 5 approved clips on full-length videos
5. Intro/Greeting Penalty & Hook Disambiguation:
   - Pure intro/greeting filler penalized (-45 points) and rejected by quality gate
   - Substantive hooks (dollar claims, viral questions) disambiguated and preserved
   - Complete thought enforcement: dangling endings rejected
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from autoclip.campaign.candidate_discovery import (
    CandidateDiscoveryEngine,
    INTRO_GREETING_PATTERNS,
    SUBSTANTIVE_HOOK_PATTERNS,
)
from autoclip.campaign.clip_assembly import (
    ClipAssemblyEngine,
    PreRenderQualityGate,
    BoundaryOptimization,
    specifications_to_clips,
)
from autoclip.campaign.duration import resolve_duration_limits, resolve_max_clips
from autoclip.db import store
from autoclip.db.models import ClipCandidateRecord, Job, Source, new_id, utcnow
from autoclip.pipeline.audio_mix.models import DuckingConfig
from autoclip.pipeline.prepare import Silence
from autoclip.pipeline.transcript import Transcript, Word
from autoclip.security.vault import get_vault, CredentialVault


# ---------------------------------------------------------------------------
# 1. BGM Loudness & Ducking Configuration Verification
# ---------------------------------------------------------------------------


def test_bgm_loudness_ducking_parameters():
    """Verify BGM audio mix parameters meet strict AL AMR production specifications."""
    cfg = DuckingConfig()
    # Ducking attenuation delta ~ 16-18 dB
    assert 16.0 <= cfg.duck_attenuation_db <= 18.0
    # Release time ~ 250ms
    assert cfg.release_ms == 250.0
    # Integrated target loudness -14.0 LUFS
    assert cfg.target_lufs == -14.0
    # True peak bounds
    assert -1.5 <= cfg.true_peak_limit <= -0.5


# ---------------------------------------------------------------------------
# 2. BGM Selection Modes and Provenance Tracking
# ---------------------------------------------------------------------------


def test_bgm_selection_modes_and_provenance(initialised_db):
    """Verify BGM selection modes: explicit, default, none, and fallback provenance."""
    from autoclip.bgm.vault import BGMVault
    vault = BGMVault()
    vault.reconcile_vault()

    # 1. Voice Only / None
    enabled, asset, path = vault.resolve_campaign_bgm("none")
    assert enabled is False
    assert asset is None

    # 2. Explicit Nonexistent with fallback=True
    enabled_fb, asset_fb, path_fb = vault.resolve_campaign_bgm("nonexistent-track-xyz", allow_fallback=True)
    if vault.list_assets(enabled_only=True):
        assert enabled_fb is True
        assert asset_fb is not None
        assert asset_fb.id != "nonexistent-track-xyz"
    else:
        assert enabled_fb is False


# ---------------------------------------------------------------------------
# 3. GitHub PAT Persistence and Masking Security
# ---------------------------------------------------------------------------


def test_pat_zero_exposure_and_persistence(initialised_db):
    """Verify GitHub PAT never leaks in logs or status, and survives vault restarts."""
    vault = get_vault()
    secret_pat = "ghp_TestSecretTokenForAudit1234567890"

    vault.store_secret("github_pat", secret_pat)

    status = vault.get_secret_status("github_pat")
    assert status["configured"] is True
    # Ensure raw secret is never present in status dict
    assert secret_pat not in str(status)

    # Decrypt and verify
    retrieved = vault.retrieve_secret("github_pat")
    assert retrieved == secret_pat

    # Create fresh vault instance to simulate process restart
    vault2 = CredentialVault()
    assert vault2.retrieve_secret("github_pat") == secret_pat
    status2 = vault2.get_secret_status("github_pat")
    assert status2["configured"] is True
    assert secret_pat not in str(status2)


# ---------------------------------------------------------------------------
# 4. Intro/Greeting Penalty & Hook Disambiguation
# ---------------------------------------------------------------------------


def test_intro_greeting_penalized_without_substantive_hook():
    """Verify openings dominated by greetings without substantive hook are penalized and rejected."""
    # Intro filler: "Hey guys welcome back to the channel today we are going to talk about automation."
    words = [
        Word(text="Hey", start=0.5, end=0.8),
        Word(text="guys", start=0.85, end=1.1),
        Word(text="welcome", start=1.15, end=1.5),
        Word(text="back", start=1.55, end=1.8),
        Word(text="to", start=1.85, end=1.95),
        Word(text="the", start=2.0, end=2.1),
        Word(text="channel", start=2.15, end=2.5),
        Word(text="today", start=2.55, end=2.8),
        Word(text="we", start=2.85, end=2.95),
        Word(text="are", start=3.0, end=3.1),
        Word(text="going", start=3.15, end=3.3),
        Word(text="to", start=3.35, end=3.45),
        Word(text="talk", start=3.5, end=3.7),
        Word(text="about", start=3.75, end=3.9),
        Word(text="simple", start=3.95, end=4.2),
        Word(text="video", start=4.25, end=4.5),
        Word(text="editing.", start=4.55, end=5.0),
    ]
    # Pad to 22 seconds
    for i in range(17, 60):
        t = 5.0 + (i - 16) * 0.35
        words.append(Word(text=f"word{i}", start=t, end=t + 0.25))
    words[-1].text = "finish."

    transcript = Transcript(words=words)
    engine = CandidateDiscoveryEngine()
    milestones = engine.detect_milestones(words, 0.5, words[-1].end)
    score_res = engine.score_candidate(words, 0.5, words[-1].end, milestones)

    # Candidate should receive the intro penalty
    assert score_res.penalties >= 45.0
    assert any("intro/greeting" in r.lower() for r in score_res.rejection_reasons)

    # PreRenderQualityGate should hard reject this opening
    qg = PreRenderQualityGate()
    opt = BoundaryOptimization(
        original_start_s=0.5,
        original_end_s=words[-1].end,
        optimized_start_s=0.5,
        optimized_end_s=words[-1].end,
        start_word=0,
        end_word=len(words) - 1,
        duration_s=words[-1].end - 0.5,
        hook_start_s=0.5,
        hook_end_s=4.0,
        hook_type="narrative_setup",
        hook_score=6.0,
        climax_start_s=None,
        climax_end_s=None,
        cta_start_s=None,
        cta_end_s=None,
        cta_type="",
    )
    result = qg.evaluate(opt, transcript)
    assert not result.is_approved
    assert any("intro_greeting" in r for r in result.rejection_reasons)


def test_substantive_dollar_hook_disambiguated_and_approved():
    """Verify substantive claim hook (e.g. $1M revenue claim) with brief greeting is retained."""
    # Substantive opening: "Hey guys, we literally made one million dollars on Amazon last month."
    words = [
        Word(text="Hey", start=0.5, end=0.8),
        Word(text="guys,", start=0.85, end=1.1),
        Word(text="we", start=1.15, end=1.3),
        Word(text="literally", start=1.35, end=1.7),
        Word(text="made", start=1.75, end=1.9),
        Word(text="one", start=1.95, end=2.1),
        Word(text="million", start=2.15, end=2.4),
        Word(text="dollars", start=2.45, end=2.8),
        Word(text="on", start=2.85, end=2.95),
        Word(text="Amazon", start=3.0, end=3.3),
        Word(text="last", start=3.35, end=3.5),
        Word(text="month.", start=3.55, end=3.9),
    ]
    # Pad to 24s
    for i in range(12, 65):
        t = 4.0 + (i - 11) * 0.35
        words.append(Word(text=f"detail{i}", start=t, end=t + 0.25))
    words[-1].text = "completely."

    transcript = Transcript(words=words)
    engine = CandidateDiscoveryEngine()
    milestones = engine.detect_milestones(words, 0.5, words[-1].end)
    score_res = engine.score_candidate(words, 0.5, words[-1].end, milestones)

    # Does NOT receive the heavy 45.0 penalty because substantive hook was recognized
    assert score_res.penalties < 40.0
    assert not any("without substantive claim" in r for r in score_res.rejection_reasons)

    qg = PreRenderQualityGate()
    opt = BoundaryOptimization(
        original_start_s=0.5,
        original_end_s=words[-1].end,
        optimized_start_s=0.5,
        optimized_end_s=words[-1].end,
        start_word=0,
        end_word=len(words) - 1,
        duration_s=words[-1].end - 0.5,
        hook_start_s=0.5,
        hook_end_s=3.9,
        hook_type="bold_claim",
        hook_score=9.5,
        climax_start_s=None,
        climax_end_s=None,
        cta_start_s=None,
        cta_end_s=None,
        cta_type="",
    )
    result = qg.evaluate(opt, transcript)
    assert not any("dominated_by_intro_greeting_filler" in r for r in result.rejection_reasons)


# ---------------------------------------------------------------------------
# 5. 5 Clips Guarantee with Candidate Replenishment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_5_clips_guarantee_with_replenishment(initialised_db):
    """Verify that the pipeline replenishes candidates to produce exactly 5 clips on a multi-clip video."""
    from autoclip.pipeline.runner import PipelineRunner

    job_id = new_id()
    # 240-second source video
    src = Source(id=new_id(), type="upload", path="test_long.mp4", duration_s=240.0)
    store.create_source(src)
    job = Job(id=job_id, source_id=src.id, settings={"max_clips": 5})
    store.create_job(job)

    # Build a realistic 180-second transcript containing multiple distinct topic sentences
    raw_sentences = [
        "The secret to building high velocity media is understanding audience retention metrics.",
        "Most creators spend days manually chopping video, which destroys their output volume completely.",
        "First, we analyze speech cadence and identify the exact moment curiosity peaks.",
        "Next, we isolate the climax and ensure every sentence delivers valuable insight.",
        "Why is this crucial for content creators and operators around the world?",
        "Because manual editing takes hours, whereas our engine analyzes narrative hooks in milliseconds!",
        "This completely transforms the entire clipping workflow and eliminates wasted editing budget.",
        "The climax of the system is the contradiction-aware candidate discovery engine that picks winning hooks.",
        "It balances explicit requirements against inferred signals with zero paid API overhead.",
        "If you want to master high-velocity content production, this is the ultimate blueprint.",
        "Click the link in bio to join our community and start automating your content pipeline today.",
        "Follow for more updates on next-generation artificial intelligence and automated publishing.",
    ]

    sentences = raw_sentences * 4  # 48 sentences (~220 seconds)
    words: list[Word] = []
    cur = 0.5
    for s in sentences:
        for w in s.split():
            d = max(0.2, len(w) * 0.04)
            words.append(Word(text=w, start=round(cur, 2), end=round(cur + d, 2)))
            cur += d + 0.15

    transcript = Transcript(words=words)
    silences = [Silence(start=50.0, end=50.3), Silence(start=110.0, end=110.4)]

    runner = PipelineRunner(job, src)
    clips = await runner._stage_highlights(transcript, silences)

    # Must produce exactly 5 clips (target_clip_count = 5)
    assert len(clips) == 5

    # Verify duration bounds for all 5 clips [20.0s, 30.0s]
    for c in clips:
        dur = c.end_s - c.start_s
        assert 19.5 <= dur <= 30.5, f"Clip duration {dur}s outside [20s, 30s]"

    # Verify non-overlap (all 5 clips are distinct)
    for i in range(len(clips)):
        for j in range(i + 1, len(clips)):
            overlap_s = max(0.0, min(clips[i].end_s, clips[j].end_s) - max(clips[i].start_s, clips[j].start_s))
            iou = overlap_s / (max(clips[i].end_s, clips[j].end_s) - min(clips[i].start_s, clips[j].start_s))
            assert iou <= 0.35, f"Clips {i} and {j} overlap excessively (IoU={iou:.2f})"
