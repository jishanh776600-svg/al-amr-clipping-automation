"""Tests for Step 17: Production-Grade 9:16 Smart Reframing & Visual Composition.

Covers:
- VisualCompositionEngine framing and native portrait passthrough
- VisualQualityGate evaluation (PASS, WARN, REJECT)
- Bounds checking, even dimensions, and camera panning speed detection
- DB persistence (CRUD on visual_compositions table)
- API endpoint GET /api/jobs/{job_id}/visual-compositions
- Pipeline runner integration and quality gating
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from autoclip.app import create_app
from autoclip.db import store
from autoclip.db.models import Clip, Job, Source, VisualCompositionRecord, new_id, utcnow
from autoclip.pipeline.reframe.croppath import CropKeyframe, CropPath, CropSegment, Strategy
from autoclip.pipeline.reframe.faces import FaceObservation
from autoclip.pipeline.reframe.visual_composition import (
    VisualCompositionEngine,
    VisualQualityGate,
)


@pytest.fixture
def temp_store(initialised_db):
    """Setup a clean test database."""
    return store


@pytest.fixture
def sample_clip() -> Clip:
    return Clip(
        id=new_id(),
        job_id=new_id(),
        rank=1,
        start_s=5.0,
        end_s=25.0,
        start_word=10,
        end_word=50,
        title="Key Insights",
        hook="Listen closely to this.",
        score=88.5,
        reason="Compelling insight",
        status="candidate",
    )


class TestVisualQualityGate:
    def test_dimensions_check_fails_on_odd_dimensions(self) -> None:
        gate = VisualQualityGate()
        crop_path = CropPath(
            source_width=1920,
            source_height=1080,
            segments=[
                CropSegment(
                    start_s=0.0,
                    end_s=10.0,
                    width=607,
                    height=1080,
                    keyframes=[CropKeyframe(t=0.0, x=656, y=0)],
                    strategy=Strategy.GENERAL,
                )
            ],
        )
        res = gate.evaluate(
            crop_path=crop_path,
            source_w=1920,
            source_h=1080,
            output_w=607,
            output_h=1080,
        )
        assert res.status == "VISUAL_REJECT"
        assert any("non_even" in r for r in res.rejection_reasons)

    def test_out_of_bounds_crop_rejected(self) -> None:
        gate = VisualQualityGate()
        crop_path = CropPath(
            source_width=1920,
            source_height=1080,
            segments=[
                CropSegment(
                    start_s=0.0,
                    end_s=10.0,
                    width=608,
                    height=1080,
                    keyframes=[
                        CropKeyframe(t=0.0, x=-50, y=0),
                    ],
                    strategy=Strategy.GENERAL,
                )
            ],
        )
        res = gate.evaluate(
            crop_path=crop_path,
            source_w=1920,
            source_h=1080,
            output_w=608,
            output_h=1080,
        )
        assert res.status == "VISUAL_REJECT"
        assert any("out_of_bounds" in r or "black_border" in r for r in res.rejection_reasons)

    def test_rapid_camera_pan_triggers_warning(self) -> None:
        gate = VisualQualityGate()
        crop_path = CropPath(
            source_width=1920,
            source_height=1080,
            segments=[
                CropSegment(
                    start_s=0.0,
                    end_s=1.0,
                    width=608,
                    height=1080,
                    keyframes=[
                        CropKeyframe(t=0.0, x=100, y=0),
                        CropKeyframe(t=1.0, x=1900, y=0),
                    ],
                    strategy=Strategy.TRACK,
                )
            ],
        )
        res = gate.evaluate(
            crop_path=crop_path,
            source_w=1920,
            source_h=1080,
            output_w=608,
            output_h=1080,
        )
        assert any("rapid_camera_pan_detected" in w for w in res.warnings)

    def test_normal_track_passes_gate(self) -> None:
        gate = VisualQualityGate()
        crop_path = CropPath(
            source_width=1920,
            source_height=1080,
            segments=[
                CropSegment(
                    start_s=0.0,
                    end_s=10.0,
                    width=608,
                    height=1080,
                    keyframes=[
                        CropKeyframe(t=0.0, x=656, y=0),
                        CropKeyframe(t=5.0, x=670, y=0),
                        CropKeyframe(t=10.0, x=660, y=0),
                    ],
                    strategy=Strategy.TRACK,
                )
            ],
        )
        obs = [
            FaceObservation(t=float(i), cx=960, cy=410, width=200, height=250, eye_y=380, mar=0.1)
            for i in range(11)
        ]
        res = gate.evaluate(
            crop_path=crop_path,
            source_w=1920,
            source_h=1080,
            output_w=608,
            output_h=1080,
            observations=obs,
        )
        assert res.status == "VISUAL_PASS"
        assert res.quality_score >= 68.0
        assert not res.rejection_reasons


class TestVisualCompositionEngine:
    def test_native_portrait_passthrough(self, sample_clip: Clip) -> None:
        engine = VisualCompositionEngine()
        fake_video = Path("fake_portrait.mp4")

        fake_probe = MagicMock()
        fake_probe.has_video = True
        fake_probe.width = 1080
        fake_probe.height = 1920

        with patch("autoclip.pipeline.reframe.visual_composition.ffmpeg.probe", return_value=fake_probe):
            crop_path, record = engine.compose(fake_video, sample_clip)

        assert record.crop_strategy == "passthrough"
        assert record.tracking_strategy == "native_portrait"
        assert record.quality_status == "VISUAL_PASS"
        assert record.source_width == 1080
        assert record.source_height == 1920
        assert record.output_width == 1080
        assert record.output_height == 1920
        assert not record.fallback_used

    def test_landscape_fallback_when_no_faces(self, sample_clip: Clip) -> None:
        engine = VisualCompositionEngine()
        fake_video = Path("fake_landscape.mp4")

        fake_probe = MagicMock()
        fake_probe.has_video = True
        fake_probe.width = 1920
        fake_probe.height = 1080

        with (
            patch("autoclip.pipeline.reframe.visual_composition.ffmpeg.probe", return_value=fake_probe),
            patch("autoclip.pipeline.reframe.visual_composition.sample_faces", return_value=[]),
        ):
            crop_path, record = engine.compose(fake_video, sample_clip)

        assert record.fallback_used is True
        assert "no_faces_detected" in record.fallback_reason
        assert record.output_width % 2 == 0
        assert record.output_height % 2 == 0
        assert record.quality_status in ("VISUAL_PASS", "VISUAL_WARN")

    def test_landscape_face_tracking(self, sample_clip: Clip) -> None:
        engine = VisualCompositionEngine(sample_fps=5.0)
        fake_video = Path("fake_landscape.mp4")

        fake_probe = MagicMock()
        fake_probe.has_video = True
        fake_probe.width = 1920
        fake_probe.height = 1080

        obs = [
            FaceObservation(t=sample_clip.start_s + i * 0.2, cx=900, cy=400, width=200, height=250, eye_y=380, mar=0.1)
            for i in range(25)
        ]

        progress_calls = []
        def progress_cb(substage: str, frac: float, meta: dict) -> None:
            progress_calls.append((substage, frac))

        with (
            patch("autoclip.pipeline.reframe.visual_composition.ffmpeg.probe", return_value=fake_probe),
            patch("autoclip.pipeline.reframe.visual_composition.sample_faces", return_value=obs),
        ):
            crop_path, record = engine.compose(fake_video, sample_clip, on_progress=progress_cb)

        assert len(progress_calls) > 0
        assert record.output_width == 608
        assert record.output_height == 1080
        assert record.tracking_strategy == "mediapipe"
        assert record.quality_status in ("VISUAL_PASS", "VISUAL_WARN")
        assert not record.fallback_used


class TestVisualCompositionPersistence:
    def test_crud_visual_compositions(self, temp_store) -> None:
        source_id = new_id()
        temp_store.create_source(Source(id=source_id, type="upload", url="https://example.com/video.mp4", path="video.mp4"))

        job_id = new_id()
        job = Job(id=job_id, source_id=source_id, status="running", current_stage="reframe", progress=0.7, provider="gemini")
        temp_store.create_job(job)

        clip_id_1 = new_id()
        clip_id_2 = new_id()
        clip1 = Clip(id=clip_id_1, job_id=job_id, rank=1, start_s=0.0, end_s=10.0, start_word=0, end_word=10, title="C1", hook="H1", score=90.0, reason="R1", status="candidate")
        clip2 = Clip(id=clip_id_2, job_id=job_id, rank=2, start_s=10.0, end_s=20.0, start_word=11, end_word=20, title="C2", hook="H2", score=85.0, reason="R2", status="candidate")
        temp_store.replace_clips(job_id, [clip1, clip2])

        rec1 = VisualCompositionRecord(
            id=new_id(),
            clip_id=clip_id_1,
            job_id=job_id,
            source_width=1920,
            source_height=1080,
            output_width=608,
            output_height=1080,
            crop_strategy="track",
            tracking_strategy="mediapipe",
            tracking_confidence=0.95,
            camera_movement_score=12.5,
            smoothing_parameters={"min_cutoff": 0.6, "beta": 0.02},
            fallback_used=False,
            fallback_reason="",
            quality_score=92.0,
            quality_status="VISUAL_PASS",
            warnings=[],
            rejection_reasons=[],
            version=1,
            telemetry={"framing": "center_weight"},
            created_at=utcnow(),
            updated_at=utcnow(),
        )

        rec2 = VisualCompositionRecord(
            id=new_id(),
            clip_id=clip_id_2,
            job_id=job_id,
            source_width=1920,
            source_height=1080,
            output_width=608,
            output_height=1080,
            crop_strategy="centre",
            tracking_strategy="centre_fallback",
            tracking_confidence=0.0,
            camera_movement_score=0.0,
            smoothing_parameters={},
            fallback_used=True,
            fallback_reason="no_faces",
            quality_score=55.0,
            quality_status="VISUAL_WARN",
            warnings=["no_faces_detected_fallback_used"],
            rejection_reasons=[],
            version=1,
            telemetry={},
            created_at=utcnow(),
            updated_at=utcnow(),
        )

        temp_store.replace_visual_compositions(job_id, [rec1, rec2])

        all_records = temp_store.list_visual_compositions(job_id)
        assert len(all_records) == 2

        passed_only = temp_store.list_visual_compositions(job_id, status="VISUAL_PASS")
        assert len(passed_only) == 1
        assert passed_only[0].clip_id == clip_id_1

        single = temp_store.get_visual_composition(clip_id_1)
        assert single is not None
        assert single.quality_score == 92.0
        assert single.smoothing_parameters.get("min_cutoff") == 0.6


class TestVisualCompositionAPI:
    def test_get_visual_compositions_endpoint(self, temp_store) -> None:
        source_id = new_id()
        temp_store.create_source(Source(id=source_id, type="upload", url="https://example.com/video.mp4", path="video.mp4"))

        job_id = new_id()
        job = Job(
            id=job_id,
            source_id=source_id,
            status="running",
            current_stage="reframe",
            progress=0.7,
            provider="gemini",
        )
        temp_store.create_job(job)

        clip_id = new_id()
        clip = Clip(id=clip_id, job_id=job_id, rank=1, start_s=0.0, end_s=10.0, start_word=0, end_word=10, title="C1", hook="H1", score=90.0, reason="R1", status="candidate")
        temp_store.replace_clips(job_id, [clip])

        rec = VisualCompositionRecord(
            id=new_id(),
            clip_id=clip_id,
            job_id=job_id,
            source_width=1920,
            source_height=1080,
            output_width=608,
            output_height=1080,
            crop_strategy="track",
            tracking_strategy="mediapipe",
            tracking_confidence=0.88,
            camera_movement_score=8.5,
            smoothing_parameters={"min_cutoff": 0.6},
            fallback_used=False,
            fallback_reason="",
            quality_score=89.5,
            quality_status="VISUAL_PASS",
            warnings=[],
            rejection_reasons=[],
            version=1,
            telemetry={},
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        temp_store.replace_visual_compositions(job_id, [rec])

        client = TestClient(create_app())
        resp = client.get(f"/api/jobs/{job_id}/visual-compositions")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["quality_status"] == "VISUAL_PASS"
        assert data[0]["output_width"] == 608
        assert data[0]["output_height"] == 1080

    def test_get_visual_compositions_not_found(self, temp_store) -> None:
        client = TestClient(create_app())
        resp = client.get(f"/api/jobs/{new_id()}/visual-compositions")
        assert resp.status_code == 404
