"""Tests for the Semantic Visual Matching & Contextual Evidence Engine."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from autoclip.pipeline.broll import (
    ContextualSemanticParser,
    EvidenceCardGenerator,
    MultiFactorRelevanceScorer,
    PresentationMode,
    RelevanceScore,
    SemanticBrollEngine,
    SemanticVisualCue,
    VisualAsset,
    VisualAssetVault,
    VisualType,
)
from autoclip.pipeline.captions import (
    KYLE_KIRSHNER_CORE,
    build_ass,
    create_hook_headline_event,
    get_semantic_word_color,
    get_style,
    resolve_style,
)
from autoclip.pipeline.export import ExportRequest, build_video_filtergraph
from autoclip.pipeline.reframe.croppath import CropKeyframe, CropPath, CropSegment, Strategy
from autoclip.pipeline.retention.visual_enhancer import VisualRetentionEnhancer
from autoclip.pipeline.transcript import Word


# --------------------------------------------------------------------------
# 1. Semantic Parser & Literal vs Figurative Guard Tests
# --------------------------------------------------------------------------

def test_semantic_parser_literal_vs_figurative():
    parser = ContextualSemanticParser()

    # Literal grass mention -> Valid cue
    literal_words = [
        Word(text="we", start=1.0, end=1.2),
        Word(text="literally", start=1.2, end=1.6),
        Word(text="touched", start=1.6, end=2.0),
        Word(text="grass", start=2.0, end=2.5),
        Word(text="outside", start=2.5, end=3.0),
    ]
    cues = parser.parse_transcript(literal_words)
    assert len(cues) == 1
    assert cues[0].concept == "nature_grass"
    assert cues[0].preferred_mode == PresentationMode.FULL_SCREEN
    assert cues[0].start_s >= 1.0
    assert cues[0].duration_s >= 1.5

    # Figurative/forbidden grass idiom -> Rejected, no cue emitted
    figurative_words = [
        Word(text="our", start=1.0, end=1.2),
        Word(text="brand", start=1.2, end=1.5),
        Word(text="logo", start=1.5, end=1.8),
        Word(text="has", start=1.8, end=2.0),
        Word(text="a", start=2.0, end=2.1),
        Word(text="grass-colored", start=2.1, end=2.7),
        Word(text="border", start=2.7, end=3.1),
    ]
    fig_cues = parser.parse_transcript(figurative_words)
    assert len(fig_cues) == 0

    # Figurative fire ("under fire") -> Rejected
    under_fire_words = [
        Word(text="we", start=4.0, end=4.2),
        Word(text="were", start=4.2, end=4.4),
        Word(text="under", start=4.4, end=4.7),
        Word(text="fire", start=4.7, end=5.1),
        Word(text="from", start=5.1, end=5.3),
        Word(text="the", start=5.3, end=5.5),
        Word(text="press", start=5.5, end=6.0),
    ]
    assert len(parser.parse_transcript(under_fire_words)) == 0

    # Literal fire ("caught on fire") -> Valid cue
    literal_fire_words = [
        Word(text="the", start=4.0, end=4.2),
        Word(text="building", start=4.2, end=4.6),
        Word(text="caught", start=4.6, end=4.9),
        Word(text="on", start=4.9, end=5.1),
        Word(text="fire", start=5.1, end=5.5),
    ]
    fire_cues = parser.parse_transcript(literal_fire_words)
    assert len(fire_cues) == 1
    assert fire_cues[0].concept == "fire_danger"


def test_semantic_parser_evidence_cues():
    parser = ContextualSemanticParser()

    # Revenue metrics -> PARTIAL_OVERLAY preferred
    revenue_words = [
        Word(text="our", start=2.0, end=2.2),
        Word(text="revenue", start=2.2, end=2.6),
        Word(text="hit", start=2.6, end=2.9),
        Word(text="$1,200,000", start=2.9, end=3.5),
        Word(text="this", start=3.5, end=3.7),
        Word(text="month", start=3.7, end=4.1),
    ]
    cues = parser.parse_transcript(revenue_words)
    assert len(cues) == 1
    assert cues[0].concept == "financial_revenue"
    assert cues[0].preferred_mode == PresentationMode.PARTIAL_OVERLAY
    assert "$1,200,000" in cues[0].metadata.get("value", "") or "$1,000,000+" in cues[0].metadata.get("value", "")

    # Shutdown cue
    shutdown_words = [
        Word(text="the", start=5.0, end=5.2),
        Word(text="company", start=5.2, end=5.6),
        Word(text="was", start=5.6, end=5.8),
        Word(text="completely", start=5.8, end=6.2),
        Word(text="shut", start=6.2, end=6.5),
        Word(text="down", start=6.5, end=6.9),
    ]
    sd_cues = parser.parse_transcript(shutdown_words)
    assert len(sd_cues) == 1
    assert sd_cues[0].concept == "business_shutdown"


# --------------------------------------------------------------------------
# 2. Relevance Scoring & Confidence Gate Tests
# --------------------------------------------------------------------------

def test_relevance_scorer_and_confidence_gate(tmp_path):
    scorer = MultiFactorRelevanceScorer(confidence_threshold=0.75)

    cue = SemanticVisualCue(
        cue_id="cue_1",
        concept="nature_grass",
        trigger_phrase="touched grass outside",
        trigger_word="grass",
        start_s=2.0,
        end_s=4.0,
        preferred_mode=PresentationMode.FULL_SCREEN,
        visual_type=VisualType.STOCK_VIDEO,
    )

    test_file = tmp_path / "grass_clip.mp4"
    test_file.write_bytes(b"dummy video")

    # High match asset
    high_match_asset = VisualAsset(
        asset_id="asset_grass_1",
        file_path=test_file,
        visual_type=VisualType.STOCK_VIDEO,
        concept="nature_grass",
        presentation_mode=PresentationMode.FULL_SCREEN,
        width=1080,
        height=1920,
        duration_s=3.0,
        tags=["nature_grass", "grass", "outdoor"],
        source_provider="local_vault",
        is_video=True,
    )

    score = scorer.score_asset(high_match_asset, cue)
    assert score.is_approved is True
    assert score.overall_score >= 0.75

    # Low match / mismatched concept asset
    mismatch_asset = VisualAsset(
        asset_id="asset_office_1",
        file_path=test_file,
        visual_type=VisualType.STOCK_PHOTO,
        concept="team_office",
        presentation_mode=PresentationMode.FULL_SCREEN,
        width=1080,
        height=1920,
        duration_s=3.0,
        tags=["team", "desk"],
        source_provider="local_vault",
    )

    low_score = scorer.score_asset(mismatch_asset, cue)
    assert low_score.is_approved is False
    assert low_score.overall_score < 0.75

    # Repetition penalty
    score_first = scorer.score_asset(high_match_asset, cue, recent_usages=[])
    score_repeated = scorer.score_asset(high_match_asset, cue, recent_usages=[("asset_grass_1", 2.2)])
    assert score_repeated.overall_score < score_first.overall_score
    assert score_repeated.repetition_penalty > 0.0


# --------------------------------------------------------------------------
# 3. Dynamic UI Evidence Card Generator Tests
# --------------------------------------------------------------------------

def test_evidence_card_generator(tmp_path):
    generator = EvidenceCardGenerator(output_dir=tmp_path)

    card_path = generator.generate_card(
        concept="financial_revenue",
        metadata={"value": "$1,480,000", "title": "TOTAL SALES (AMAZON)"},
    )

    assert card_path.is_file()
    assert card_path.suffix.lower() == ".png"

    with Image.open(card_path) as img:
        assert img.size == (1080, 1920)
        assert img.mode == "RGBA"

    # Shutdown card
    sd_path = generator.generate_card(concept="business_shutdown", metadata={})
    assert sd_path.is_file()
    with Image.open(sd_path) as img:
        assert img.size == (1080, 1920)


# --------------------------------------------------------------------------
# 4. Semantic B-Roll Engine & EDL Generation Tests
# --------------------------------------------------------------------------

def test_semantic_broll_engine_edl(tmp_path):
    vault = VisualAssetVault(vault_dir=tmp_path / "vault")
    engine = SemanticBrollEngine(vault=vault)

    words = [
        Word(text="in", start=0.0, end=0.3),
        Word(text="our", start=0.3, end=0.6),
        Word(text="first", start=0.6, end=1.0),
        Word(text="year", start=1.0, end=1.4),
        Word(text="our", start=1.4, end=1.6),
        Word(text="revenue", start=1.6, end=2.0),
        Word(text="hit", start=2.0, end=2.3),
        Word(text="$1,000,000", start=2.3, end=3.0),
        Word(text="then", start=3.0, end=3.3),
        Word(text="we", start=3.3, end=3.5),
        Word(text="shipped", start=3.5, end=3.8),
        Word(text="everything", start=3.8, end=4.2),
        Word(text="from", start=4.2, end=4.4),
        Word(text="our", start=4.4, end=4.6),
        Word(text="warehouse", start=4.6, end=5.1),
        Word(text="and", start=5.1, end=5.4),
        Word(text="touched", start=5.4, end=5.8),
        Word(text="grass", start=5.8, end=6.3),
    ]

    edl = engine.generate_edl(clip_id="test_clip", words=words, clip_start_s=0.0, clip_end_s=7.0)
    assert len(edl.entries) >= 1
    # Check that entries have valid paths and times
    for entry in edl.entries:
        assert Path(entry.asset_path).is_file()
        assert entry.duration_s >= 1.2
        assert entry.end_s > entry.start_s

    # Serialization test
    edl_dict = edl.to_dict()
    assert "entries" in edl_dict
    assert "fallbacks" in edl_dict


def test_engine_fallback_when_confidence_low(tmp_path):
    # Mock vault returning low match candidates
    mock_vault = MagicMock()
    mock_vault.discover_candidates.return_value = [
        VisualAsset(
            asset_id="bad_asset",
            file_path=tmp_path / "bad.jpg",
            visual_type=VisualType.STOCK_PHOTO,
            concept="mismatch",
            presentation_mode=PresentationMode.FULL_SCREEN,
            width=200,
            height=200,
            duration_s=1.0,
            tags=["unrelated"],
        )
    ]
    engine = SemanticBrollEngine(vault=mock_vault, confidence_threshold=0.85)

    words = [
        Word(text="we", start=1.0, end=1.2),
        Word(text="literally", start=1.2, end=1.5),
        Word(text="touched", start=1.5, end=1.8),
        Word(text="grass", start=1.8, end=2.2),
    ]

    edl = engine.generate_edl(clip_id="test_clip", words=words, clip_start_s=0.0, clip_end_s=5.0)
    # Gating rejected bad visual; clean fallback to A-roll recorded!
    assert len(edl.entries) == 0
    assert len(edl.fallbacks) == 1
    assert edl.fallbacks[0]["fallback_action"] == "a_roll_punch_in"


# --------------------------------------------------------------------------
# 5. Kyle Kirshner Style Preset & Captions Tests
# --------------------------------------------------------------------------

def test_kyle_kirshner_style_preset():
    style = get_style("kyle_kirshner_core")
    assert style.font == "Anton"
    assert style.all_caps is True
    assert style.max_words == 3
    assert style.animation == "phrase_pop"

    # Aliases
    assert get_style("kyle").key == "kyle_kirshner_core"
    assert get_style("kyle_kirshner").key == "kyle_kirshner_core"
    assert get_style("reference").key == "kyle_kirshner_core"


def test_semantic_word_color_mapping():
    # Money / Growth -> Neon Green
    assert get_semantic_word_color("$1,000,000") == "#00FF00"
    assert get_semantic_word_color("revenue") == "#00FF00"
    assert get_semantic_word_color("profit") == "#00FF00"
    assert get_semantic_word_color("$500k") == "#00FF00"

    # Danger / Loss / Shutdown -> Bright Red
    assert get_semantic_word_color("shutdown") == "#FF2B2B"
    assert get_semantic_word_color("fire") == "#FF2B2B"
    assert get_semantic_word_color("closed") == "#FF2B2B"
    assert get_semantic_word_color("killed") == "#FF2B2B"

    # Hook / Subject -> Vibrant Yellow
    assert get_semantic_word_color("grass") == "#FFE500"
    assert get_semantic_word_color("amazon") == "#FFE500"
    assert get_semantic_word_color("secret") == "#FFE500"

    # Neutral word -> None
    assert get_semantic_word_color("the") is None
    assert get_semantic_word_color("went") is None


def test_phrase_pop_ass_generation():
    style = get_style("kyle_kirshner_core")
    words = [
        Word(text="our", start=0.0, end=0.3),
        Word(text="revenue", start=0.3, end=0.7),
        Word(text="exploded", start=0.7, end=1.2),
    ]
    subs = build_ass(
        words,
        style,
        width=1080,
        height=1920,
        hook_headline="HOW WE SCALED\nTO $1,000,000",
    )

    # Contains subtitle events
    assert len(subs.events) > 0

    # Hook headline event present with \an8 and stacked lines
    headline_events = [e for e in subs.events if r"\an8" in e.text]
    assert len(headline_events) == 1
    assert r"\N" in headline_events[0].text
    # Check green semantic color in headline for $1,000,000 (&H00FF00&)
    assert "00FF00" in headline_events[0].text

    # Check phrase pop event contains active word highlight and all caps
    pop_events = [e for e in subs.events if r"\an8" not in e.text]
    assert len(pop_events) == 3
    assert "REVENUE" in pop_events[1].text


# --------------------------------------------------------------------------
# 6. Export Filtergraph & Visual Retention Enhancer Tests
# --------------------------------------------------------------------------

def test_visual_retention_enhancer_default_zoom():
    enhancer = VisualRetentionEnhancer()
    assert enhancer.punch_in_zoom == 1.22

    crop_path = CropPath(
        source_width=1920,
        source_height=1080,
        segments=[
            CropSegment(
                start_s=0.0,
                end_s=10.0,
                width=607,
                height=1080,
                keyframes=[CropKeyframe(t=1.0, x=500.0, y=0.0)],
                strategy=Strategy.TRACK,
            )
        ],
    )
    enhanced, moments = enhancer.enhance_composition(
        crop_path,
        hook_start_s=0.5,
        hook_end_s=3.5,
        duration_s=10.0,
    )
    assert len(moments) >= 1
    assert moments[0]["zoom_factor"] == 1.22


def test_build_video_filtergraph_with_edl(tmp_path):
    from autoclip.pipeline.broll.models import EDLEntry, EditDecisionList

    dummy_source = tmp_path / "source.mp4"
    dummy_source.write_bytes(b"dummy")
    dummy_asset = tmp_path / "card.png"
    Image.new("RGBA", (1080, 1920), (0, 0, 0, 0)).save(dummy_asset)

    crop_path = CropPath(
        source_width=1920,
        source_height=1080,
        segments=[
            CropSegment(
                start_s=0.0,
                end_s=10.0,
                width=607,
                height=1080,
                keyframes=[CropKeyframe(t=0.0, x=500.0, y=0.0)],
                strategy=Strategy.TRACK,
            )
        ],
    )

    cue = SemanticVisualCue(
        cue_id="c1",
        concept="financial_revenue",
        trigger_word="revenue",
        trigger_phrase="revenue hit $1M",
        start_s=2.0,
        end_s=4.0,
        preferred_mode=PresentationMode.PARTIAL_OVERLAY,
        visual_type=VisualType.DASHBOARD,
    )

    score = RelevanceScore(
        semantic_score=1.0,
        literal_score=1.0,
        quality_score=1.0,
        aspect_score=1.0,
        repetition_penalty=0.0,
        total_score=0.9,
        is_approved=True,
    )
    edl = EditDecisionList(
        clip_id="test_clip",
        clip_start_s=0.0,
        clip_end_s=10.0,
        entries=[
            EDLEntry(
                entry_id="e1",
                start_s=2.0,
                end_s=4.0,
                presentation_mode=PresentationMode.PARTIAL_OVERLAY,
                visual_type=VisualType.DASHBOARD,
                asset_path=dummy_asset,
                trigger_phrase="revenue hit $1M",
                concept="financial_revenue",
                relevance_score=score,
            )
        ],
    )

    style = get_style("kyle_kirshner_core")
    req = ExportRequest(
        source=dummy_source,
        destination=tmp_path / "out.mp4",
        start_s=0.0,
        end_s=10.0,
        crop_path=crop_path,
        words=[],
        style=style,
        edl=edl,
        burn_captions=False,
    )

    filtergraph = build_video_filtergraph(req, subtitle_name=None)
    assert "broll_prep_0" in filtergraph
    assert "overlay=x=0:y=0:enable='between(t,2.0000,4.0000)'" in filtergraph


def test_real_export_clip_with_semantic_broll_render(tmp_path):
    """Real production export test verifying FFmpeg render with B-roll cut and card overlay."""
    import subprocess
    from autoclip.pipeline import export, ffmpeg
    from autoclip.pipeline.broll.models import EDLEntry, EditDecisionList

    # 1. Synthesize a 5s 1920x1080 test video with audio
    source_mp4 = tmp_path / "source_head.mp4"
    res = subprocess.run(
        [
            ffmpeg.ffmpeg_path(),
            "-y",
            "-f", "lavfi", "-i", "testsrc=duration=5:size=1920x1080:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=5:sample_rate=48000",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            str(source_mp4),
        ],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"FFmpeg test source creation failed: {res.stderr}"

    # 2. Create sample evidence assets
    overlay_card = tmp_path / "evidence_card.png"
    generator = EvidenceCardGenerator(output_dir=tmp_path)
    generator.generate_card(
        concept="financial_revenue",
        metadata={"value": "$1,000,000", "title": "TOTAL REVENUE"},
        out_path=overlay_card,
    )

    fullscreen_img = tmp_path / "grass_evidence.jpg"
    img = Image.new("RGB", (1080, 1920), (34, 139, 34))
    img.save(fullscreen_img)

    # 3. Build CropPath
    crop_path = CropPath(
        source_width=1920,
        source_height=1080,
        segments=[
            CropSegment(
                start_s=0.0,
                end_s=5.0,
                width=607,
                height=1080,
                keyframes=[CropKeyframe(t=0.0, x=656.0, y=0.0)],
                strategy=Strategy.TRACK,
            )
        ],
    )

    # 4. Build EDL: Full-screen cut at [1.0s, 2.5s], Card overlay at [3.0s, 4.5s]
    score = RelevanceScore(
        semantic_score=1.0,
        literal_score=1.0,
        quality_score=1.0,
        aspect_score=1.0,
        repetition_penalty=0.0,
        total_score=0.92,
        is_approved=True,
    )

    edl = EditDecisionList(
        clip_id="prod_clip",
        clip_start_s=0.0,
        clip_end_s=5.0,
        entries=[
            EDLEntry(
                entry_id="e1",
                start_s=1.0,
                end_s=2.5,
                presentation_mode=PresentationMode.FULL_SCREEN,
                visual_type=VisualType.STOCK_PHOTO,
                asset_path=fullscreen_img,
                trigger_phrase="touched grass outside",
                concept="nature_grass",
                relevance_score=score,
            ),
            EDLEntry(
                entry_id="e2",
                start_s=3.0,
                end_s=4.5,
                presentation_mode=PresentationMode.PARTIAL_OVERLAY,
                visual_type=VisualType.DASHBOARD,
                asset_path=overlay_card,
                trigger_phrase="revenue hit $1M",
                concept="financial_revenue",
                relevance_score=score,
            ),
        ],
    )

    # 5. Words with Kyle Kirshner core captions
    words = [
        Word(text="we", start=0.2, end=0.6),
        Word(text="touched", start=0.6, end=1.0),
        Word(text="grass", start=1.0, end=1.8),
        Word(text="and", start=1.8, end=2.4),
        Word(text="revenue", start=2.4, end=3.2),
        Word(text="exploded", start=3.2, end=4.2),
        Word(text="$1,000,000", start=4.2, end=4.9),
    ]

    style = get_style("kyle_kirshner_core")
    dest_mp4 = tmp_path / "final_prod_render.mp4"

    req = ExportRequest(
        source=source_mp4,
        destination=dest_mp4,
        start_s=0.0,
        end_s=5.0,
        crop_path=crop_path,
        words=words,
        style=style,
        edl=edl,
        burn_captions=True,
    )

    # 6. Render clip
    out_path = export.export_clip(
        req,
        work_dir=tmp_path / "render_work",
    )

    # 7. Validate output file
    assert out_path.is_file()
    assert out_path.stat().st_size > 10000

    info = ffmpeg.probe(out_path)
    assert info.has_video is True
    assert abs(info.duration_s - 5.0) < 0.3


def test_final_render_engine_with_semantic_broll_edl_packaging(tmp_path):
    """Integration test: FinalRenderEngine auto-generates EDL, packages edl.json, and renders approved final.mp4."""
    import subprocess
    from autoclip.db.models import Clip, new_id, utcnow
    from autoclip.pipeline import export, ffmpeg
    from autoclip.pipeline.final_render import FinalRenderConfig, FinalRenderEngine

    # 1. Synthesize 25s test video with audio (within the 20-30s duration limits)
    source_mp4 = tmp_path / "talking_head_25s.mp4"
    res = subprocess.run(
        [
            ffmpeg.ffmpeg_path(),
            "-y",
            "-f", "lavfi", "-i", "testsrc=duration=25:size=1920x1080:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=25:sample_rate=48000",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            str(source_mp4),
        ],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"FFmpeg failed: {res.stderr}"

    # 2. Words across the 25s clip
    words = [
        Word(text="in", start=0.5, end=0.8),
        Word(text="the", start=0.8, end=1.0),
        Word(text="beginning", start=1.0, end=1.5),
        Word(text="we", start=1.5, end=1.8),
        Word(text="literally", start=1.8, end=2.2),
        Word(text="touched", start=2.2, end=2.6),
        Word(text="grass", start=2.6, end=3.2),
        Word(text="outside", start=3.2, end=3.8),
        Word(text="then", start=6.0, end=6.5),
        Word(text="our", start=6.5, end=7.0),
        Word(text="revenue", start=7.0, end=7.8),
        Word(text="hit", start=7.8, end=8.2),
        Word(text="$1,000,000", start=8.2, end=9.5),
        Word(text="this", start=9.5, end=9.8),
        Word(text="year", start=9.8, end=10.4),
        Word(text="and", start=14.0, end=14.5),
        Word(text="the", start=14.5, end=14.8),
        Word(text="company", start=14.8, end=15.3),
        Word(text="was", start=15.3, end=15.6),
        Word(text="shut", start=15.6, end=16.0),
        Word(text="down", start=16.0, end=16.8),
        Word(text="for", start=16.8, end=17.2),
        Word(text="a", start=17.2, end=17.5),
        Word(text="week", start=17.5, end=18.0),
        Word(text="and", start=22.0, end=22.5),
        Word(text="recovered", start=22.5, end=23.5),
    ]

    clip = Clip(
        id=new_id(),
        job_id="job_semantic_test",
        title="Scaling to $1M and touching grass",
        start_s=0.0,
        end_s=25.0,
        start_word=0,
        end_word=len(words),
        rank=1,
        score=95,
        status="candidate",
        created_at=utcnow(),
    )

    crop_path = CropPath(
        source_width=1920,
        source_height=1080,
        segments=[
            CropSegment(
                start_s=0.0,
                end_s=25.0,
                width=607,
                height=1080,
                keyframes=[CropKeyframe(t=0.0, x=656.0, y=0.0)],
                strategy=Strategy.TRACK,
            )
        ],
    )

    engine = FinalRenderEngine(config=FinalRenderConfig(ratio="9:16"))
    exports_dir = tmp_path / "exports"
    work_dir = tmp_path / "work"

    final_path, record = engine.render_and_package(
        clip=clip,
        source_media_path=source_mp4,
        crop_path=crop_path,
        caption_style_key="kyle_kirshner_core",
        ass_path=None,
        audio_path=None,
        bgm_asset_id=None,
        bgm_asset_name="",
        exports_base_dir=exports_dir,
        render_work_dir=work_dir,
        words=words,
        min_duration_s=20.0,
        max_duration_s=30.0,
    )

    assert final_path.is_file()
    assert record.is_approved is True
    assert record.quality_status in ("RENDER_PASS", "RENDER_WARN")

    # Check packaged edl.json
    package_dir = Path(record.package_dir)
    edl_file = package_dir / "edl.json"
    assert edl_file.is_file()

    edl_data = json.loads(edl_file.read_text(encoding="utf-8"))
    assert "entries" in edl_data
    # Should have entries for grass and revenue and/or shutdown
    assert len(edl_data["entries"]) >= 2

    # Check presentation modes used
    modes = {entry["presentation_mode"] for entry in edl_data["entries"]}
    # Grass is FULL_SCREEN, Revenue is PARTIAL_OVERLAY
    assert "FULL_SCREEN" in modes
    assert "PARTIAL_OVERLAY" in modes
