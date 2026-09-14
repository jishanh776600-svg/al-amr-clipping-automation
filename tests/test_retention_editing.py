"""Tests for Step 18: AI-Assisted Retention Editing & Final Clip Quality Optimization.

Covers:
- DynamicPacingEngine (silence detection, boundary tightening, dramatic pause preservation, word safety)
- RetentionAnalyzer (speech density wps, time-to-hook, narrative structure, deterministic 0-100 score)
- VisualRetentionEnhancer (punch-in zoom 1.10x, dwell-time rate limiting, source bounds clamping, even dimensions)
- FinalPreRenderQualityGate (media integrity, A/V sync, word safety, silence ratio, campaign compliance, status assignment)
- RetentionEditingEngine (end-to-end orchestration, composite ranking, top-N selection, telemetry)
- SQLite DB store CRUD and schema migration V13
- API endpoint GET /api/jobs/{job_id}/retention-optimizations
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from autoclip.app import create_app
from autoclip.db import store
from autoclip.db.models import (
    Clip,
    Job,
    RetentionOptimizationRecord,
    Source,
    new_id,
    utcnow,
)
from autoclip.campaign.models_intelligence import CampaignSpecification, RequirementItem
from autoclip.pipeline.prepare import Silence
from autoclip.pipeline.reframe.croppath import CropKeyframe, CropPath, CropSegment, Strategy
from autoclip.pipeline.retention.analyzer import RetentionAnalyzer
from autoclip.pipeline.retention.engine import RetentionEditingEngine
from autoclip.pipeline.retention.pacing import DynamicPacingEngine
from autoclip.pipeline.retention.quality_gate import FinalPreRenderQualityGate
from autoclip.pipeline.retention.visual_enhancer import VisualRetentionEnhancer
from autoclip.pipeline.transcribe import Transcript, Word


@pytest.fixture
def temp_store(initialised_db):
    """Setup a clean test database."""
    return store


@pytest.fixture
def sample_words() -> list[Word]:
    """Words covering 1.0s to 12.0s with varied speech rate and intentional pauses."""
    words = [
        Word(text="Why", start=1.0, end=1.3),
        Word(text="do", start=1.35, end=1.5),
        Word(text="most", start=1.55, end=1.8),
        Word(text="creators", start=1.85, end=2.3),
        Word(text="fail?", start=2.35, end=2.8),
        # Dramatic pause: 2.8 to 3.8 (1.0s pause after question)
        Word(text="Here", start=3.8, end=4.1),
        Word(text="is", start=4.15, end=4.3),
        Word(text="the", start=4.35, end=4.5),
        Word(text="secret", start=4.55, end=5.0),
        Word(text="nobody", start=5.05, end=5.4),
        Word(text="talks", start=5.45, end=5.8),
        Word(text="about.", start=5.85, end=6.3),
        # Normal pause: 6.3 to 7.4 (1.1s dead air - should be tightened)
        Word(text="They", start=7.4, end=7.7),
        Word(text="focus", start=7.75, end=8.1),
        Word(text="on", start=8.15, end=8.3),
        Word(text="gear", start=8.35, end=8.7),
        Word(text="instead", start=8.75, end=9.2),
        Word(text="of", start=9.25, end=9.4),
        Word(text="story.", start=9.45, end=9.9),
        Word(text="Follow", start=10.5, end=10.9),
        Word(text="for", start=10.95, end=11.1),
        Word(text="more", start=11.15, end=11.4),
        Word(text="tips.", start=11.45, end=11.8),
    ]
    return words


@pytest.fixture
def sample_crop_path() -> CropPath:
    return CropPath(
        source_width=1920,
        source_height=1080,
        segments=[
            CropSegment(
                start_s=0.0,
                end_s=15.0,
                width=608,
                height=1080,
                keyframes=[
                    CropKeyframe(t=0.0, x=656, y=0),
                    CropKeyframe(t=15.0, x=656, y=0),
                ],
                strategy=Strategy.TRACK,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Dynamic Pacing Engine Tests
# ---------------------------------------------------------------------------
class TestDynamicPacingEngine:
    def test_dead_air_and_boundary_tightening(self, sample_words) -> None:
        engine = DynamicPacingEngine(pause_threshold_s=0.75, word_padding_s=0.1)
        # Clip defined from 0.0 to 14.0 (starts 1.0s before first word, ends 2.2s after last word)
        res = engine.analyze_and_tighten(
            words=sample_words,
            clip_start_s=0.0,
            clip_end_s=14.0,
        )

        # Start boundary should be tightened to first word start (1.0 - 0.1 = 0.9)
        assert res.tightened_start_s >= 0.8
        assert res.tightened_start_s <= sample_words[0].start - 0.08
        # End boundary should be tightened to last word end (11.8 + 0.1 = 11.9)
        assert res.tightened_end_s <= 12.1
        assert res.tightened_end_s >= sample_words[-1].end + 0.08
        assert res.dead_air_s > 0

    def test_dramatic_pause_preservation(self, sample_words) -> None:
        engine = DynamicPacingEngine(pause_threshold_s=0.75)
        res = engine.analyze_and_tighten(
            words=sample_words,
            clip_start_s=0.5,
            clip_end_s=12.0,
        )

        # The pause after "fail?" at 2.8-3.8s is dramatic (follows question mark)
        dramatic_pauses = [p for p in res.pause_regions if p.is_intentional]
        assert len(dramatic_pauses) >= 1
        assert any("question" in p.reason or "punchline" in p.reason for p in dramatic_pauses)

    def test_word_boundary_safety_no_clipping(self, sample_words) -> None:
        engine = DynamicPacingEngine(pause_threshold_s=0.5, word_padding_s=0.08)
        res = engine.analyze_and_tighten(
            words=sample_words,
            clip_start_s=0.95,
            clip_end_s=11.85,
        )
        # Verify speech is strictly preserved without clipping
        assert res.tightened_start_s <= sample_words[0].start
        assert res.tightened_end_s >= sample_words[-1].end


# ---------------------------------------------------------------------------
# Retention Analyzer Tests
# ---------------------------------------------------------------------------
class TestRetentionAnalyzer:
    def test_speech_density_and_hook_analysis(self, sample_words) -> None:
        analyzer = RetentionAnalyzer()
        res = analyzer.analyze(
            words=sample_words,
            duration_s=11.2,
            pacing_score=90.0,
            dead_air_percentage=4.0,
            hook_text="Why do most creators fail?",
        )

        # 23 words in ~11.2 seconds => ~2.0 - 2.1 wps
        assert 1.5 <= res.speech_density_wps <= 3.5
        # Hook starts early (< 1.5s) and contains question
        assert res.time_to_hook_s <= 2.0
        assert res.hook_strength >= 70.0
        # Deterministic retention score should be in 0-100 range
        assert 0 <= res.retention_score <= 100
        assert res.metrics.get("has_cta") is True

    def test_low_density_or_slow_hook_penalty(self) -> None:
        analyzer = RetentionAnalyzer()
        # Very few words spread over 20s
        sparse_words = [
            Word(text="Um", start=5.0, end=5.5),
            Word(text="yeah.", start=15.0, end=15.5),
        ]
        res = analyzer.analyze(
            words=sparse_words,
            duration_s=20.0,
            pacing_score=40.0,
            dead_air_percentage=45.0,
            hook_text="Um",
        )
        # Low density should penalize retention score
        assert res.speech_density_wps < 1.0
        assert res.retention_score < 65.0


# ---------------------------------------------------------------------------
# Visual Retention Enhancer Tests
# ---------------------------------------------------------------------------
class TestVisualRetentionEnhancer:
    def test_punch_in_application_and_bounds(self, sample_crop_path) -> None:
        enhancer = VisualRetentionEnhancer(punch_in_zoom=1.10, min_dwell_s=3.0)
        enhanced, moments = enhancer.enhance_composition(
            crop_path=sample_crop_path,
            hook_start_s=1.0,
            hook_end_s=3.0,
            climax_start_s=8.0,
            climax_end_s=10.0,
            duration_s=15.0,
        )

        assert len(moments) >= 1
        assert len(enhanced.segments) == len(sample_crop_path.segments)
        seg = enhanced.segments[0]
        # Dimensions must remain even
        assert seg.width % 2 == 0
        assert seg.height % 2 == 0
        # Bounded within source
        assert seg.width <= sample_crop_path.source_width
        assert seg.height <= sample_crop_path.source_height
        for kf in seg.keyframes:
            assert kf.x >= 0
            assert kf.x + seg.width <= sample_crop_path.source_width
            assert kf.y >= 0
            assert kf.y + seg.height <= sample_crop_path.source_height

    def test_dwell_time_rate_limiting(self, sample_crop_path) -> None:
        enhancer = VisualRetentionEnhancer(min_dwell_s=3.0)
        # Hook and climax very close together (spacing < 4s)
        _, moments = enhancer.enhance_composition(
            crop_path=sample_crop_path,
            hook_start_s=1.0,
            hook_end_s=2.5,
            climax_start_s=2.8,
            climax_end_s=4.0,
            duration_s=15.0,
        )
        # Second punch-in must be rate limited
        assert len(moments) == 1


# ---------------------------------------------------------------------------
# Final Pre-Render Quality Gate Tests
# ---------------------------------------------------------------------------
class TestFinalPreRenderQualityGate:
    @patch("autoclip.pipeline.retention.quality_gate.ffmpeg.probe")
    @patch("pathlib.Path.exists", return_value=True)
    @patch("pathlib.Path.stat")
    def test_clean_clip_passes_quality_gate(self, mock_stat, mock_exists, mock_probe, sample_words, sample_crop_path) -> None:
        mock_stat.return_value = MagicMock(st_size=1024 * 1024)
        probe_info = MagicMock(has_video=True, has_audio=True)
        mock_probe.return_value = probe_info

        gate = FinalPreRenderQualityGate()
        res = gate.evaluate(
            video_path=Path("dummy.mp4"),
            clip_start_s=0.8,
            clip_end_s=12.0,
            words=sample_words,
            crop_path=sample_crop_path,
            retention_score=85.0,
            pacing_dead_air_pct=5.0,
        )
        assert res.status == "FINAL_PASS"
        assert len(res.rejection_reasons) == 0

    @patch("autoclip.pipeline.retention.quality_gate.ffmpeg.probe")
    @patch("pathlib.Path.exists", return_value=True)
    @patch("pathlib.Path.stat")
    def test_campaign_banned_word_rejects_clip(self, mock_stat, mock_exists, mock_probe, sample_words, sample_crop_path) -> None:
        mock_stat.return_value = MagicMock(st_size=1024 * 1024)
        probe_info = MagicMock(has_video=True, has_audio=True)
        mock_probe.return_value = probe_info

        spec = CampaignSpecification(
            campaign_id="camp_test",
            title="Test Campaign",
            banned_words=[RequirementItem(value="secret", confidence="explicit")],
        )
        gate = FinalPreRenderQualityGate()
        res = gate.evaluate(
            video_path=Path("dummy.mp4"),
            clip_start_s=0.8,
            clip_end_s=12.0,
            words=sample_words,  # Contains "secret"
            crop_path=sample_crop_path,
            campaign_spec=spec,
            retention_score=85.0,
            pacing_dead_air_pct=5.0,
        )
        assert res.status == "FINAL_REJECT"
        assert any("banned" in r.lower() for r in res.rejection_reasons)

    @patch("autoclip.pipeline.retention.quality_gate.ffmpeg.probe")
    @patch("pathlib.Path.exists", return_value=True)
    @patch("pathlib.Path.stat")
    def test_excessive_silence_warns_or_rejects(self, mock_stat, mock_exists, mock_probe, sample_words, sample_crop_path) -> None:
        mock_stat.return_value = MagicMock(st_size=1024 * 1024)
        probe_info = MagicMock(has_video=True, has_audio=True)
        mock_probe.return_value = probe_info

        gate = FinalPreRenderQualityGate()
        res = gate.evaluate(
            video_path=Path("dummy.mp4"),
            clip_start_s=0.8,
            clip_end_s=12.0,
            words=sample_words,
            crop_path=sample_crop_path,
            retention_score=40.0,
            pacing_dead_air_pct=45.0,  # > 35%
        )
        assert res.status in ("FINAL_WARN", "FINAL_REJECT")
        assert any("dead_air" in r.lower() or "silence" in r.lower() for r in res.rejection_reasons)


# ---------------------------------------------------------------------------
# Retention Editing Engine Orchestration & Composite Ranking Tests
# ---------------------------------------------------------------------------
class TestRetentionEditingEngine:
    @patch("autoclip.pipeline.retention.quality_gate.ffmpeg.probe")
    @patch("pathlib.Path.exists", return_value=True)
    @patch("pathlib.Path.stat")
    def test_composite_ranking_and_top_n_selection(self, mock_stat, mock_exists, mock_probe, sample_words, sample_crop_path) -> None:
        mock_stat.return_value = MagicMock(st_size=1024 * 1024)
        probe_info = MagicMock(has_video=True, has_audio=True)
        mock_probe.return_value = probe_info

        engine = RetentionEditingEngine()
        clip1 = Clip(
            id="c1",
            job_id="job_1",
            rank=1,
            start_s=0.5,
            end_s=12.5,
            start_word=0,
            end_word=len(sample_words),
            title="Clip 1",
            hook="Why do most creators fail?",
            score=90.0,  # S15
            reason="Strong hook",
            status="candidate",
        )
        clip2 = Clip(
            id="c2",
            job_id="job_1",
            rank=2,
            start_s=0.5,
            end_s=12.5,
            start_word=0,
            end_word=len(sample_words),
            title="Clip 2",
            hook="They focus on gear.",
            score=70.0,  # S15
            reason="Average hook",
            status="candidate",
        )
        transcript = Transcript(words=sample_words)
        crop_paths = {"c1": sample_crop_path, "c2": sample_crop_path}

        final_clips, final_crops, records, telemetry = engine.optimize_and_rank(
            clips=[clip1, clip2],
            transcript=transcript,
            crop_paths=crop_paths,
            source_path=Path("dummy.mp4"),
            job_id="job_1",
            target_output_count=1,
        )

        assert len(final_clips) == 1
        assert final_clips[0].id == "c1"
        assert len(records) == 2
        assert telemetry["clips_analyzed"] == 2
        assert telemetry["final_selected"] == 1
        assert "optimizations" in telemetry


# ---------------------------------------------------------------------------
# Database Persistence Tests
# ---------------------------------------------------------------------------
class TestRetentionStoreCRUD:
    def test_retention_optimizations_store_crud(self, temp_store) -> None:
        source_id = new_id()
        temp_store.create_source(Source(id=source_id, type="upload", url="https://example.com/v.mp4", path="v.mp4"))
        job_id = new_id()
        temp_store.create_job(Job(id=job_id, source_id=source_id, status="running", provider="gemini"))
        clip_id_1 = new_id()
        clip_id_2 = new_id()
        temp_store.replace_clips(
            job_id,
            [
                Clip(id=clip_id_1, job_id=job_id, rank=1, start_s=0, end_s=10, start_word=0, end_word=5, title="C1", hook="H1", score=80, reason="R1", status="candidate"),
                Clip(id=clip_id_2, job_id=job_id, rank=2, start_s=10, end_s=20, start_word=5, end_word=10, title="C2", hook="H2", score=75, reason="R2", status="candidate"),
            ],
        )

        rec1 = RetentionOptimizationRecord(
            id=new_id(),
            clip_id=clip_id_1,
            job_id=job_id,
            retention_score=88.0,
            final_score=89.5,
            quality_status="FINAL_PASS",
            hook_strength=85.0,
            speech_density_wps=2.6,
            dead_air_percentage=4.2,
            pacing_score=92.0,
            narrative_score=86.0,
            editing_decisions={"trimmed_start_s": 0.2},
            visual_emphasis=[{"time_s": 1.5, "type": "punch_in"}],
            scoring_breakdown={"s15": 80, "s18": 88},
            rejection_reasons=[],
            warnings=[],
            processing_time_s=0.15,
            version=1,
            telemetry={},
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        rec2 = RetentionOptimizationRecord(
            id=new_id(),
            clip_id=clip_id_2,
            job_id=job_id,
            retention_score=45.0,
            final_score=52.0,
            quality_status="FINAL_REJECT",
            hook_strength=40.0,
            speech_density_wps=1.2,
            dead_air_percentage=42.0,
            pacing_score=50.0,
            narrative_score=40.0,
            editing_decisions={},
            visual_emphasis=[],
            scoring_breakdown={},
            rejection_reasons=["excessive_dead_air"],
            warnings=[],
            processing_time_s=0.12,
            version=1,
            telemetry={},
            created_at=utcnow(),
            updated_at=utcnow(),
        )

        temp_store.replace_retention_optimizations(job_id, [rec1, rec2])

        all_records = temp_store.list_retention_optimizations(job_id)
        assert len(all_records) == 2

        passed = temp_store.list_retention_optimizations(job_id, status="FINAL_PASS")
        assert len(passed) == 1
        assert passed[0].clip_id == clip_id_1

        approved = temp_store.list_retention_optimizations(job_id, approved_only=True)
        assert len(approved) == 1

        single = temp_store.get_retention_optimization(clip_id_1)
        assert single is not None
        assert single.retention_score == 88.0
        assert single.editing_decisions.get("trimmed_start_s") == 0.2


# ---------------------------------------------------------------------------
# API Endpoint Tests
# ---------------------------------------------------------------------------
class TestRetentionAPI:
    def test_get_retention_optimizations_endpoint(self, temp_store) -> None:
        source_id = new_id()
        temp_store.create_source(Source(id=source_id, type="upload", url="https://example.com/v.mp4", path="v.mp4"))
        job_id = new_id()
        temp_store.create_job(Job(id=job_id, source_id=source_id, status="running", provider="gemini"))
        clip_id = new_id()
        temp_store.replace_clips(
            job_id,
            [Clip(id=clip_id, job_id=job_id, rank=1, start_s=0, end_s=10, start_word=0, end_word=5, title="C1", hook="H1", score=85, reason="R1", status="candidate")],
        )
        rec = RetentionOptimizationRecord(
            id=new_id(),
            clip_id=clip_id,
            job_id=job_id,
            retention_score=91.0,
            final_score=92.5,
            quality_status="FINAL_PASS",
            hook_strength=90.0,
            speech_density_wps=2.8,
            dead_air_percentage=3.5,
            pacing_score=94.0,
            narrative_score=90.0,
            editing_decisions={"trimmed_start_s": 0.1},
            visual_emphasis=[],
            scoring_breakdown={},
            rejection_reasons=[],
            warnings=[],
            processing_time_s=0.1,
            version=1,
            telemetry={},
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        temp_store.replace_retention_optimizations(job_id, [rec])

        client = TestClient(create_app())
        resp = client.get(f"/api/jobs/{job_id}/retention-optimizations")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["retention_score"] == 91.0
        assert data[0]["quality_status"] == "FINAL_PASS"
