"""Tests for Step 19: Operator-Selectable Dynamic Caption Styles."""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import MagicMock

from autoclip.campaign import CampaignSpecification, RequirementItem
from autoclip.db import store
from autoclip.db.models import (
    CaptionOptimizationRecord,
    Clip,
    Job,
    Source,
    VisualCompositionRecord,
    new_id,
    utcnow,
)
from autoclip.pipeline import captions
from autoclip.pipeline.captions import (
    CLASSIC_PROFESSIONAL,
    RICH_DYNAMIC,
    STYLE_NAME,
    CaptionEngine,
    CaptionQualityGate,
    CaptionGateResult,
    CaptionStyle,
    build_ass,
    calculate_caption_safe_margin,
    get_style,
    resolve_style,
    write_ass,
    write_srt,
)
from autoclip.pipeline.reframe.croppath import CropPath, CropSegment, CropKeyframe
from autoclip.pipeline.transcript import Word


def _make_words(n: int = 15, base_time: float = 0.0) -> list[Word]:
    text_tokens = [
        "start", "your", "day", "with", "deep", "passion", "and", "energy",
        "because", "success", "demands", "focused", "action", "subscribe", "now"
    ]
    words = []
    t = base_time
    for i in range(min(n, len(text_tokens))):
        start = round(t, 2)
        end = round(t + 0.35, 2)
        words.append(Word(text=text_tokens[i], start=start, end=end))
        t += 0.40
    return words


def _make_clip(clip_id: str = "c-1", job_id: str = "j-1", start_s: float = 0.0, end_s: float = 6.0) -> Clip:
    return Clip(
        id=clip_id,
        job_id=job_id,
        start_word=0,
        end_word=14,
        start_s=start_s,
        end_s=end_s,
        rank=1,
        title="Success Demands Action",
        hook="Start your day with passion",
        reason="Motivational message on focus.",
        score=9.2,
    )


class TestOperatorStyleResolution:
    def test_default_style_is_classic_professional(self) -> None:
        assert captions.DEFAULT_STYLE == "classic_professional"
        style = resolve_style(None)
        assert style.key == "classic_professional"
        assert style.font == "Inter"
        assert style.label == "Classic Professional"

    def test_resolve_explicit_styles(self) -> None:
        pro = resolve_style("classic_professional")
        assert pro.key == "classic_professional"
        assert pro.font_file == "Inter-Variable.ttf"
        assert pro.animation == "subtle"

        rich = resolve_style("rich_dynamic")
        assert rich.key == "rich_dynamic"
        assert rich.font_file == "Anton-Regular.ttf"
        assert rich.animation == "scale"
        assert rich.scale_percent == 116

    def test_backward_compatibility_presets(self) -> None:
        clean = resolve_style("clean_lower")
        assert clean.key == "clean_lower"
        assert clean.animation == "none"

        bold = resolve_style("bold_pop")
        assert bold.key == "bold_pop"
        assert bold.animation == "scale"

        karaoke = resolve_style("karaoke_fill")
        assert karaoke.key == "karaoke_fill"

    def test_fallback_on_unknown_style(self) -> None:
        fallback = resolve_style("nonexistent_style_xyz")
        assert fallback.key == "classic_professional"

    def test_get_style_raises_on_unknown(self) -> None:
        with pytest.raises(ValueError, match="Unknown caption style"):
            get_style("nonexistent_style_xyz")


class TestStyleRenderingAndAss:
    def test_classic_professional_ass_generation(self) -> None:
        words = _make_words(10)
        subs = build_ass(words, CLASSIC_PROFESSIONAL, width=1080, height=1920)
        assert subs.info["PlayResX"] == "1080"
        assert subs.info["PlayResY"] == "1920"
        style_entry = subs.styles[STYLE_NAME]
        assert style_entry.fontname == "Inter"
        assert len(subs.events) > 0

    def test_rich_dynamic_ass_generation_with_treatments(self) -> None:
        words = _make_words(15)
        subs = build_ass(
            words,
            RICH_DYNAMIC,
            width=1080,
            height=1920,
            hook_window=(0.0, 3.5),
            climax_window=(3.5, 5.0),
            cta_window=(5.0, 6.0),
        )
        assert subs.styles[STYLE_NAME].fontname == "Anton"
        assert len(subs.events) == 15

        event_texts = [ev.text for ev in subs.events]
        assert any(r"\c&H" in t for t in event_texts)
        assert any(r"\fscx" in t for t in event_texts)

    def test_write_ass_and_srt_files(self, tmp_path: Path) -> None:
        words = _make_words(8)
        ass_path = tmp_path / "test_captions.ass"
        srt_path = tmp_path / "test_captions.srt"

        out_ass = write_ass(ass_path, words, CLASSIC_PROFESSIONAL, width=1080, height=1920)
        assert out_ass.is_file()
        assert out_ass.stat().st_size > 0

        out_srt = write_srt(srt_path, words, time_offset_s=0.0)
        assert out_srt.is_file()
        content = out_srt.read_text(encoding="utf-8")
        assert "start your day" in content


class TestVisualSafetyAndFaceAvoidance:
    def test_default_safe_margin(self) -> None:
        margin = calculate_caption_safe_margin(
            crop_path=None,
            height=1920,
            style=CLASSIC_PROFESSIONAL,
            composition_record=None,
        )
        assert margin == round(1920 * CLASSIC_PROFESSIONAL.margin_v_ratio)

    def test_face_avoidance_shifts_margin_upward(self) -> None:
        seg = CropSegment(
            start_s=0.0,
            end_s=5.0,
            width=1080,
            height=1920,
            keyframes=[CropKeyframe(t=0.0, x=0, y=900)],
        )
        cp = CropPath(segments=[seg], source_width=1080, source_height=1920)

        margin = calculate_caption_safe_margin(
            crop_path=cp,
            height=1920,
            style=RICH_DYNAMIC,
        )
        base_margin = round(1920 * RICH_DYNAMIC.margin_v_ratio)
        assert margin > base_margin
        assert margin <= round(1920 * RICH_DYNAMIC.max_margin_v_ratio)


class TestCaptionQualityGate:
    def test_valid_captions_pass(self) -> None:
        words = _make_words(10)
        subs = build_ass(words, CLASSIC_PROFESSIONAL, width=1080, height=1920)
        gate = CaptionQualityGate()
        eval_result = gate.evaluate(
            subs=subs,
            words=words,
            clip_duration_s=4.0,
            style=CLASSIC_PROFESSIONAL,
        )
        assert eval_result.status in ("CAPTION_PASS", "CAPTION_WARN")
        assert eval_result.is_approved
        assert len(eval_result.rejection_reasons) == 0

    def test_banned_word_triggers_rejection(self) -> None:
        spec = CampaignSpecification(
            campaign_id="camp-1",
            title="Clean Content",
            banned_words=[RequirementItem(value="bannedword", confidence="explicit")],
            duration_min_s=RequirementItem(value=3.0),
            duration_max_s=RequirementItem(value=60.0),
            duration_preferred_s=RequirementItem(value=30.0),
            output_count=RequirementItem(value=5),
            aspect_ratio=RequirementItem(value="9:16"),
            hook_required=RequirementItem(value=True),
            hook_window_s=RequirementItem(value=3.5),
            hook_min_score=RequirementItem(value=7.0),
        )
        gate = CaptionQualityGate(campaign_spec=spec)
        words = [
            Word(text="this", start=0.0, end=0.4),
            Word(text="is", start=0.5, end=0.8),
            Word(text="bannedword", start=0.9, end=1.3),
        ]
        subs = build_ass(words, CLASSIC_PROFESSIONAL, width=1080, height=1920)
        eval_result = gate.evaluate(
            subs=subs,
            words=words,
            clip_duration_s=1.5,
            style=CLASSIC_PROFESSIONAL,
        )
        assert eval_result.status == "CAPTION_REJECT"
        assert not eval_result.is_approved
        assert any("banned" in f.lower() for f in eval_result.rejection_reasons)

    def test_invalid_negative_timestamps_rejected(self) -> None:
        words = [
            Word(text="invalid", start=-1.5, end=-0.5),
            Word(text="time", start=-0.4, end=0.2),
        ]
        gate = CaptionQualityGate()
        subs = MagicMock(events=[MagicMock(start=-1500, end=-500, text="invalid")])
        eval_result = gate.evaluate(
            subs=subs,
            words=words,
            clip_duration_s=1.0,
            style=CLASSIC_PROFESSIONAL,
        )
        assert eval_result.status == "CAPTION_REJECT"
        assert any("negative" in f.lower() for f in eval_result.rejection_reasons)


class TestCaptionEngine:
    def test_engine_generates_record_and_ass(self) -> None:
        engine = CaptionEngine()
        clip = _make_clip()
        words = _make_words(12)

        ssa_file, record = engine.generate_captions(
            clip=clip,
            words=words,
            style_key="rich_dynamic",
            width=1080,
            height=1920,
        )
        assert isinstance(record, CaptionOptimizationRecord)
        assert record.style_key == "rich_dynamic"
        assert record.quality_status in ("CAPTION_PASS", "CAPTION_WARN")
        assert len(record.caption_segments) > 0
        assert ssa_file is not None
        assert len(ssa_file.events) > 0

    def test_engine_fallback_on_catastrophic_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        engine = CaptionEngine()
        clip = _make_clip()
        words = _make_words(5)

        monkeypatch.setattr(captions, "build_ass", MagicMock(side_effect=RuntimeError("libass rendering failed")))

        ssa_file, record = engine.generate_captions(
            clip=clip,
            words=words,
            style_key="rich_dynamic",
            width=1080,
            height=1920,
        )
        assert record.fallback_used is True
        assert "libass rendering failed" in record.fallback_reason
        assert ssa_file is not None


class TestDatabaseAndStore:
    def test_store_caption_optimization_crud(self, initialised_db: int) -> None:
        job_id = new_id()
        source = Source(id=new_id(), type="upload", path="dummy.mp4", title="Test Video")
        store.create_source(source)
        job = Job(id=job_id, source_id=source.id, provider="auto")
        store.create_job(job)
        clip_id = new_id()
        clip = _make_clip(clip_id=clip_id, job_id=job_id)
        store.create_clip(clip)
        record = CaptionOptimizationRecord(
            id=new_id(),
            clip_id=clip_id,
            job_id=job_id,
            style_key="rich_dynamic",
            style_label="Rich Dynamic",
            caption_segments=[{"start_s": 0.0, "end_s": 2.0, "text": "Test line"}],
            emphasis_metadata={"animation": "scale"},
            hook_treatment={"enabled": True},
            climax_treatment={"enabled": True},
            cta_treatment={"enabled": True},
            quality_score=94.5,
            quality_status="CAPTION_PASS",
            rejection_reasons=[],
            warnings=[],
            fallback_used=False,
            fallback_reason="",
            render_time_s=0.015,
            version=1,
            telemetry={"font_scale": 1.16},
            created_at=utcnow(),
            updated_at=utcnow(),
        )

        store.replace_caption_optimizations(job_id, [record])

        records = store.list_caption_optimizations(job_id)
        assert len(records) == 1
        assert records[0].id == record.id
        assert records[0].style_key == "rich_dynamic"
        assert records[0].is_approved

        single = store.get_caption_optimization(clip_id)
        assert single is not None
        assert single.clip_id == clip_id
        assert single.style_label == "Rich Dynamic"

    def test_api_get_job_captions(self, initialised_db: int, autoclip_home, monkeypatch: pytest.MonkeyPatch) -> None:
        from starlette.testclient import TestClient
        from autoclip import app as app_module
        from autoclip.app import create_app

        monkeypatch.setenv(app_module.ENV_NO_WORKER, "1")
        with TestClient(create_app()) as client:
            job_id = "job-caption-api-test"
            src = store.create_source(Source(id="src-caption-test", type="upload", path="test.mp4", duration_s=60.0))
            job = store.create_job(Job(id=job_id, source_id=src.id, status="running", current_stage="captions"))
            clip = store.create_clip(_make_clip(clip_id="clip-caption-api-1", job_id=job_id))

            rec = CaptionOptimizationRecord(
                id=new_id(),
                clip_id=clip.id,
                job_id=job.id,
                style_key="rich_dynamic",
                style_label="Rich Dynamic",
                caption_segments=[{"start_s": 0.0, "end_s": 2.0, "text": "Start your day"}],
                emphasis_metadata={"animation": "scale"},
                hook_treatment={"enabled": True},
                climax_treatment={"enabled": False},
                cta_treatment={"enabled": False},
                quality_score=95.0,
                quality_status="CAPTION_PASS",
                rejection_reasons=[],
                warnings=[],
                fallback_used=False,
                fallback_reason="",
                render_time_s=0.012,
                version=1,
                telemetry={},
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            store.replace_caption_optimizations(job.id, [rec])

            response = client.get(f"/api/jobs/{job.id}/captions")
            assert response.status_code == 200
            data = response.json()
            assert len(data) == 1
            assert data[0]["clip_id"] == clip.id
            assert data[0]["style_key"] == "rich_dynamic"
            assert data[0]["quality_status"] == "CAPTION_PASS"
            assert data[0]["style_label"] == "Rich Dynamic"
