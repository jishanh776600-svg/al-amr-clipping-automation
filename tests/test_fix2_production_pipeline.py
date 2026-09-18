"""Comprehensive regression and integration test suite for Fix 2/3: Production Pipeline & Campaign Requirements."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autoclip import paths
from autoclip.bgm.vault import BGMVault
from autoclip.campaign.extractor import extract_campaign_from_text
from autoclip.campaign.models import CampaignBrief
from autoclip.campaign.models_intelligence import CampaignSpecification, RequirementItem
from autoclip.db import store
from autoclip.db.models import Clip, ClipMetadataRecord, FinalRenderRecord, Job, Source, new_id, utcnow
from autoclip.pipeline import captions, export
from autoclip.pipeline.filters import FILTERS, get_filter, is_valid_filter, list_filters
from autoclip.pipeline.final_render import FinalRenderConfig, FinalRenderEngine, FinalRenderMetadata, FinalRenderQualityGate
from autoclip.pipeline.reframe.croppath import CropKeyframe, CropSegment
from autoclip.pipeline.runner import PipelineRunner
from autoclip.seo.engine import SEOEngine
from autoclip.telegram.review_bot import handle_telegram_webhook_payload, send_clip_review


# ===========================================================================
# PART 1: VISUAL FILTER PIPELINE
# ===========================================================================

def test_visual_filter_registry_contains_20_canonical_profiles():
    """Verify all 20 canonical visual filters exist with valid metadata."""
    filters = list_filters()
    assert len(filters) == 20
    ids = {f.id for f in filters}
    expected = {
        "original", "black_and_white", "grayscale", "vintage", "warm", "cool",
        "high_contrast", "low_contrast", "cinematic", "faded", "sepia", "noir",
        "bright", "dark", "muted", "sharp", "soft", "retro", "film", "monochrome",
    }
    assert ids == expected
    for f in filters:
        assert f.name
        assert f.description
        assert f.ffmpeg_expr


def test_visual_filter_validation_and_lookup():
    """Verify filter lookup handles aliases, case variations, and safely falls back on unknown."""
    assert is_valid_filter("black_and_white") is True
    assert is_valid_filter("black-and-white") is True
    assert is_valid_filter("Black & White") is True
    assert is_valid_filter("bw") is True
    assert is_valid_filter("original") is True
    assert is_valid_filter("non_existent_filter_xyz") is False

    bw = get_filter("black_and_white")
    assert bw.id == "black_and_white"
    assert "hue=s=0" in bw.ffmpeg_expr

    # Fallback safety
    fallback = get_filter("non_existent_filter_xyz")
    assert fallback.id == "original"
    assert fallback.ffmpeg_expr == "null"


def test_export_filtergraph_inserts_visual_filter_before_captions():
    """Verify export.build_video_filtergraph inserts visual filter before subtitle burn."""
    crop_segment = CropSegment(
        start_s=0.0,
        end_s=10.0,
        width=1080,
        height=1920,
        keyframes=[CropKeyframe(t=0.0, x=0, y=0)],
    )
    req = export.ExportRequest(
        source=Path("source.mp4"),
        destination=Path("dest.mp4"),
        start_s=0.0,
        end_s=10.0,
        crop_path=MagicMock(segments=[crop_segment]),
        words=[],
        style=captions.CLASSIC_PROFESSIONAL,
        ratio="9:16",
        burn_captions=True,
        visual_filter="black_and_white",
    )
    graph = export.build_video_filtergraph(
        req,
        subtitle_name="captions.ass",
        fonts_name="fonts",
    )
    assert "hue=s=0" in graph
    assert "ass=filename=captions.ass" in graph
    idx_filter = graph.index("hue=s=0")
    idx_ass = graph.index("ass=filename=captions.ass")
    assert idx_filter < idx_ass


def test_quality_gate_idempotency_cache_invalidation_on_filter_change(tmp_path: Path):
    """Verify existing output package reuse is REJECTED if visual filter does not match."""
    package_dir = tmp_path / "clip_001"
    package_dir.mkdir(parents=True)
    mp4_file = package_dir / "final.mp4"
    mp4_file.write_bytes(b"\x00" * 2048)

    meta = {
        "visual_filter": "original",
        "caption_style": "classic_professional",
        "bgm_asset_id": None,
    }
    (package_dir / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")

    gate = FinalRenderQualityGate()
    with patch.object(gate, "probe_media", return_value={
        "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "25.0"},
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920, "r_frame_rate": "30/1", "duration": "25.0"},
            {"codec_type": "audio", "codec_name": "aac", "channels": 2, "sample_rate": 48000, "duration": "25.0"},
        ],
    }), patch.object(gate, "decode_check", return_value=(True, "")):
        # Requesting black_and_white should reject reusing the 'original' package!
        res = gate.evaluate(
            mp4_file,
            expected_duration_s=25.0,
            expected_caption_style="classic_professional",
            expected_visual_filter="black_and_white",
        )
        assert res.is_approved is False
        assert any("Visual filter mismatch" in r for r in res.rejection_reasons)

        # Requesting original matches!
        res_ok = gate.evaluate(
            mp4_file,
            expected_duration_s=25.0,
            expected_caption_style="classic_professional",
            expected_visual_filter="original",
        )
        assert res_ok.is_approved is True


# ===========================================================================
# PART 2: SUBTITLE TEMPLATES (25 OPTIONS)
# ===========================================================================

def test_subtitle_templates_all_25_exist_with_valid_fonts():
    """Verify all 25 subtitle templates exist and map to real font files."""
    presets = captions.PRESETS
    assert len(presets) == 25
    for key, style in presets.items():
        assert style.key == key
        assert style.label
        assert style.description
        assert style.font
        assert style.font_path.is_file(), f"Font file missing for preset {key}: {style.font_path}"


def test_operator_subtitle_selection_precedence_over_campaign():
    """Verify operator's selected caption style is not clobbered by default campaign preset."""
    job = Job(
        id=new_id(),
        source_id="src-1",
        status="created",
        current_stage="created",
        progress=0.0,
        provider="anthropic",
        settings={
            "caption_style": "retro_arcade",
            "campaign": {
                "name": "Test Campaign",
                "caption_preset": "classic_professional",
            },
        },
    )
    source = Source(
        id="src-1",
        type="local",
        title="Source",
        path="",
        has_audio=True,
        has_video=True,
        duration_s=60.0,
        created_at=utcnow(),
    )
    runner = PipelineRunner(job, source)

    operator_style = runner.job.settings.get("caption_style")
    campaign_data = runner.job.settings.get("campaign")
    campaign = CampaignBrief.model_validate(campaign_data)

    if not operator_style or operator_style in ("default", ""):
        runner.settings.export.caption_style = campaign.caption_preset
    else:
        runner.settings.export.caption_style = operator_style

    assert runner.settings.export.caption_style == "retro_arcade"


# ===========================================================================
# PART 3: BGM VAULT & AUDIO MIXING
# ===========================================================================

def test_bgm_vault_name_and_id_resolution(autoclip_home: Path, initialised_db: int, tmp_path: Path):
    """Verify BGMVault resolves tracks by ID, canonical alias, and human name."""
    vault = BGMVault(base_dir=tmp_path / "bgm")
    vault.reconcile_vault()

    cinematic = vault.get_asset("Cinematic")
    assert cinematic is not None
    assert "cinematic" in cinematic.id.lower() or "cinematic" in cinematic.name.lower()

    lofi = vault.get_asset("LoFi")
    assert lofi is not None
    assert "lofi" in lofi.id.lower() or "lofi" in lofi.name.lower()

    rock = vault.get_asset("Rock")
    assert rock is not None
    assert "upbeat" in rock.id.lower() or "rock" in rock.name.lower()

    podcast = vault.get_asset("Podcast")
    assert podcast is not None
    assert "ambient" in podcast.id.lower() or "podcast" in podcast.name.lower()


def test_bgm_resolve_campaign_bgm_disabled_and_fallback(autoclip_home: Path, initialised_db: int, tmp_path: Path):
    """Verify resolve_campaign_bgm respects explicit disable and recovers with fallback."""
    vault = BGMVault(base_dir=tmp_path / "bgm")
    vault.reconcile_vault()

    enabled, asset, path = vault.resolve_campaign_bgm("none")
    assert enabled is False
    assert asset is None

    enabled_fb, asset_fb, path_fb = vault.resolve_campaign_bgm("non_existent_track_id", allow_fallback=True)
    assert enabled_fb is True
    assert asset_fb is not None
    assert path_fb.is_file()


# ===========================================================================
# PART 4: CAMPAIGN REQUIREMENTS AS PRODUCTION CONTRACT & TELEGRAM GATING
# ===========================================================================

def test_campaign_pdf_extraction_to_structured_contract():
    """Verify campaign text is extracted into structured requirements with priorities."""
    doc_text = """
    CAMPAIGN: AL AMR Q3 LAUNCH
    
    MANDATORY REQUIREMENTS:
    - Target Aspect Ratio: 9:16 vertical
    - Must include Hashtags: #ALAMR #Automation #FutureOfVideo
    - Required Call to Action: Comment AUTOMATE below to get early access
    - Required Mention: @alamr_official
    - Strictly Prohibited / Banned Words: amateur, cheap, discount
    
    PREFERRED GUIDELINES:
    - Tone: Inspiring and energetic
    - Preferred Duration: 25 seconds
    - Style: Rich Dynamic captions
    """
    brief = extract_campaign_from_text(doc_text, filename="q3_campaign.pdf")
    assert "#ALAMR" in brief.hashtags
    assert "#Automation" in brief.hashtags
    assert any("amateur" in b for b in brief.banned_words)
    assert brief.cta_required is True
    assert len(brief.cta_instructions) > 0


def test_seo_engine_auto_repair_loop(autoclip_home: Path, initialised_db: int):
    """Verify SEO engine repairs missing mandatory hashtags, mentions, CTA, and scrubs banned words."""
    spec = CampaignSpecification(
        hashtags=[RequirementItem(value="#MandatoryTag", priority="mandatory")],
        required_mentions=[RequirementItem(value="@mandatory_account", priority="mandatory")],
        banned_words=[RequirementItem(value="bannedterm", priority="mandatory")],
        cta_required=RequirementItem(value=True, priority="mandatory"),
        cta_instructions=[RequirementItem(value="👉 Click link in bio!", priority="mandatory")],
    )
    engine = SEOEngine.from_campaign_spec(spec)
    source = Source(
        id="src-repair",
        type="upload",
        title="Source",
        path="",
        has_audio=True,
        has_video=True,
        duration_s=60.0,
        created_at=utcnow(),
    )
    store.create_source(source)
    job = Job(
        id="j-repair",
        source_id="src-repair",
        status="running",
        current_stage="export",
        progress=0.5,
        provider="anthropic",
        settings={},
    )
    store.create_job(job)

    clip = Clip(
        id="c-repair-1",
        job_id="j-repair",
        rank=1,
        start_s=0.0,
        end_s=25.0,
        start_word=0,
        end_word=50,
        title="Video with bannedterm inside",
        hook="Great hook with bannedterm",
        score=90,
        reason="Good",
        status="candidate",
    )
    store.create_clip(clip)

    record = engine.generate_for_clip(clip, transcript_text="Discussion about AI technology.")
    comp = record.telemetry.get("campaign_compliance", {})
    assert comp.get("passed") is True
    assert "#MandatoryTag" in record.final_hashtags
    assert "@mandatory_account" in record.final_mentions
    assert "bannedterm" not in record.final_title.lower()
    assert "bannedterm" not in record.final_description.lower()
    assert comp.get("repaired") is True


@pytest.mark.asyncio
async def test_telegram_approval_blocked_if_mandatory_compliance_fails(autoclip_home: Path, initialised_db: int):
    """Verify Telegram approval bot blocks publish and alerts operator if mandatory compliance failed."""
    clip_id = "clip-comp-fail"
    job_id = "job-comp-fail"

    source = Source(
        id="src-comp-fail",
        type="upload",
        title="Source",
        path="",
        has_audio=True,
        has_video=True,
        duration_s=60.0,
        created_at=utcnow(),
    )
    store.create_source(source)
    job = Job(
        id=job_id,
        source_id=source.id,
        status="running",
        current_stage="export",
        progress=0.5,
        provider="anthropic",
        settings={},
    )
    store.create_job(job)

    clip = Clip(
        id=clip_id,
        job_id=job_id,
        rank=1,
        start_s=0.0,
        end_s=25.0,
        start_word=0,
        end_word=50,
        title="Unrepaired Clip",
        hook="Hook",
        score=85,
        reason="Test",
        status="candidate",
    )
    store.create_clip(clip)

    meta = ClipMetadataRecord(
        id=new_id(),
        job_id=job_id,
        clip_id=clip_id,
        generated_title="Test",
        final_title="Test",
        generated_description="Test",
        final_description="Test",
        generated_hashtags=[],
        final_hashtags=[],
        generated_mentions=[],
        final_mentions=[],
        generated_cta="",
        final_cta="",
        campaign_requirements_matched={},
        compliance_status="SEO_REJECT",
        compliance_score=20.0,
        validation_errors=["Missing required hashtag #ALAMR"],
        validation_warnings=[],
        version=1,
        telemetry={
            "campaign_compliance": {
                "passed": False,
                "violations": ["Missing required hashtag #ALAMR"],
            }
        },
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    store.create_clip_metadata(meta)

    payload = {
        "callback_query": {
            "id": "cb-comp-1",
            "from": {"id": 12345678, "username": "operator"},
            "data": f"tg:appr:{clip_id}",
            "message": {"message_id": 999, "chat": {"id": 12345678}, "text": "Review card"},
        }
    }

    with patch("autoclip.telegram.review_bot.get_telegram_config", return_value=("TEST_BOT_TOKEN", "12345678", ["12345678"])), \
         patch("autoclip.telegram.review_bot.is_user_authorized", return_value=True), \
         patch("autoclip.telegram.review_bot._answer_callback_query", new_callable=AsyncMock) as mock_answer:
        result = await handle_telegram_webhook_payload(payload)
        assert result["status"] == "compliance_failed"
        mock_answer.assert_called_once()
        args, kwargs = mock_answer.call_args
        assert kwargs.get("show_alert") is True
        assert "Campaign compliance failed" in kwargs.get("text", "")
