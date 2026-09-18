"""Automated test suite for BGM, Visual Filters, Subtitle Templates, and Campaign Compliance."""

from __future__ import annotations

import io
import wave
from pathlib import Path
from unittest.mock import AsyncMock, patch
import pytest
from fastapi.testclient import TestClient

from autoclip.app import create_app
from autoclip.bgm.vault import BGMVault
from autoclip.pipeline.filters import (
    FILTERS,
    get_filter,
    list_filters,
)
from autoclip.pipeline.captions import PRESETS, get_style, build_ass
from autoclip.pipeline.transcript import Word
from autoclip.pipeline.export import ExportRequest, build_video_filtergraph
from autoclip.pipeline.reframe.croppath import CropPath, CropSegment, CropKeyframe, Strategy
from autoclip.campaign.models import CampaignBrief
from autoclip.campaign.models_intelligence import CampaignSpecification
from autoclip.campaign.extractor import parse_guidelines_into_brief
from autoclip.seo.engine import SEOEngine
from autoclip.telegram.review_bot import send_clip_review
from autoclip.db import models


# ---------------------------------------------------------------------------
# 1. BGM System Tests
# ---------------------------------------------------------------------------

def test_bgm_vault_auto_seeds_canonical_assets(tmp_path: Path, initialised_db):
    vault = BGMVault(base_dir=tmp_path / "bgm_vault")
    tracks = vault.list_assets()
    assert len(tracks) >= 2
    names = [t.name for t in tracks]
    assert any("Ambient" in n for n in names)
    assert any("Upbeat" in n for n in names)
    
    for t in tracks:
        p = Path(t.file_path)
        assert p.exists()
        assert p.stat().st_size > 0


def test_bgm_resolution_default_vs_disabled(tmp_path: Path, initialised_db):
    vault = BGMVault(base_dir=tmp_path / "bgm_vault")
    tracks = vault.list_assets()
    assert len(tracks) > 0

    # 1. Default (None or empty string) -> returns (True, canonical_asset, path)
    enabled, asset, path = vault.resolve_campaign_bgm(None)
    assert enabled is True
    assert asset is not None
    assert path is not None
    assert path.exists()

    enabled, asset, path = vault.resolve_campaign_bgm("")
    assert enabled is True
    assert asset is not None
    assert path is not None
    assert path.exists()

    # 2. Explicitly disabled ("none" or "disabled") -> returns (False, None, None)
    enabled, asset, path = vault.resolve_campaign_bgm("none")
    assert enabled is False
    assert asset is None
    assert path is None

    enabled, asset, path = vault.resolve_campaign_bgm("disabled")
    assert enabled is False
    assert asset is None
    assert path is None

    enabled, asset, path = vault.resolve_campaign_bgm("NONE")
    assert enabled is False
    assert asset is None
    assert path is None

    # 3. Specific asset ID -> returns exact asset
    chosen = tracks[0]
    enabled, asset, path = vault.resolve_campaign_bgm(chosen.id)
    assert enabled is True
    assert asset is not None
    assert asset.id == chosen.id
    assert path == Path(chosen.file_path)

    # 4. Unknown asset ID with fallback=True -> falls back to default canonical track
    enabled, asset, path = vault.resolve_campaign_bgm("non-existent-id", allow_fallback=True)
    assert enabled is True
    assert asset is not None
    assert path is not None
    assert path.exists()


# ---------------------------------------------------------------------------
# 2. Visual Filters System Tests
# ---------------------------------------------------------------------------

def test_visual_filter_registry_and_definitions():
    filters = list_filters()
    assert len(filters) >= 20
    
    canonical_ids = {f.id for f in filters}
    expected_subset = {
        "original", "black_and_white", "grayscale", "vintage", "warm", "cool",
        "high_contrast", "low_contrast", "cinematic", "faded", "sepia", "noir",
        "bright", "dark", "muted", "sharp", "soft", "retro", "film", "monochrome"
    }
    assert expected_subset.issubset(canonical_ids)

    # Check properties
    for f in filters:
        assert f.id
        assert f.name
        assert f.description
        assert f.ffmpeg_expr

    # get_filter resolution
    assert get_filter(None).id == "original"
    assert get_filter("original").id == "original"
    assert get_filter("cinematic").id == "cinematic"
    assert get_filter("unknown_filter").id == "original"


def test_visual_filter_ffmpeg_filtergraph_construction(tmp_path: Path):
    source = tmp_path / "in.mp4"
    dest = tmp_path / "out.mp4"
    crop_path = CropPath(
        source_width=1920,
        source_height=1080,
        segments=[
            CropSegment(
                start_s=0.0,
                end_s=5.0,
                width=1080,
                height=1920,
                keyframes=[CropKeyframe(t=0.0, x=0.0, y=0.0)],
                strategy=Strategy.GENERAL,
                zoom=0.0,
            )
        ],
    )

    # 1. Without visual filter (or original)
    req_orig = ExportRequest(
        source=source,
        destination=dest,
        start_s=0.0,
        end_s=5.0,
        crop_path=crop_path,
        words=[],
        style=get_style("classic_professional"),
        ratio="9:16",
        burn_captions=True,
        visual_filter="original",
    )
    graph_orig = build_video_filtergraph(
        req_orig,
        subtitle_name="caps.ass",
        fonts_name="fonts",
    )
    assert "vfiltered" not in graph_orig
    assert "[v0]ass=filename=caps.ass" in graph_orig

    # 2. With cinematic visual filter
    req_cinematic = ExportRequest(
        source=source,
        destination=dest,
        start_s=0.0,
        end_s=5.0,
        crop_path=crop_path,
        words=[],
        style=get_style("classic_professional"),
        ratio="9:16",
        burn_captions=True,
        visual_filter="cinematic",
    )
    graph_cinematic = build_video_filtergraph(
        req_cinematic,
        subtitle_name="caps.ass",
        fonts_name="fonts",
    )
    assert "[v0]" in graph_cinematic
    assert "[vfiltered]" in graph_cinematic
    assert "curves=" in graph_cinematic or "eq=" in graph_cinematic
    assert "[vfiltered]ass=filename=caps.ass" in graph_cinematic


def test_api_filters_endpoint(autoclip_home, monkeypatch: pytest.MonkeyPatch):
    with TestClient(create_app()) as client:
        resp = client.get("/api/filters")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 20
        first = data[0]
        assert "id" in first
        assert "name" in first
        assert "description" in first


# ---------------------------------------------------------------------------
# 3. Subtitle Templates System Tests
# ---------------------------------------------------------------------------

def test_subtitle_presets_registry():
    assert len(PRESETS) >= 25
    assert "classic_professional" in PRESETS
    
    # Check that each preset has required layout & styling fields and valid font
    for name, style in PRESETS.items():
        assert style.font
        assert style.primary
        assert style.margin_v_ratio >= 0.1
        assert style.font_path.exists(), f"Preset {name} references non-existent font {style.font_path}"


def test_subtitle_generation_across_all_presets():
    words = [
        Word(text="Build", start=0.0, end=0.4),
        Word(text="an", start=0.4, end=0.6),
        Word(text="Amazon", start=0.6, end=1.0),
        Word(text="empire", start=1.0, end=1.5),
        Word(text="today!", start=1.5, end=2.0),
    ]

    for preset_name in PRESETS:
        style = get_style(preset_name)
        subs = build_ass(words, style, width=1080, height=1920)
        assert len(subs.events) > 0
        assert len(subs.styles) > 0


# ---------------------------------------------------------------------------
# 4. Campaign PDF Compliance & Intelligence Tests
# ---------------------------------------------------------------------------

SAMPLE_PDF_TEXT = """
Kyle Kirshner Amazon FBA Brand Guidelines
Campaign Brief:
Objective: Drive qualified leads to Amazon FBA coaching program.
Target Audience: Aspiring eCommerce entrepreneurs.
Hashtags: #AmazonFBA #KyleKirshner #FBASecrets #EcommerceSuccess
Title Format: How Kyle Kirshner Built an 8-Figure Amazon Business
Description: Follow the step-by-step framework Kyle Kirshner uses to scale Amazon stores.
Call to Action: Visit kylekirshner.com/apply to book your strategy call today!
Required Mentions: @KyleKirshner
Prohibited Words: get rich quick, easy money, overnight success
Tone: Professional, direct, authoritative.
"""

def test_campaign_guideline_extraction():
    brief = parse_guidelines_into_brief(SAMPLE_PDF_TEXT)
    assert isinstance(brief, CampaignBrief)
    
    # Verify SEO & metadata extracted
    assert any("AmazonFBA" in h for h in brief.hashtags)
    assert any("KyleKirshner" in h for h in brief.hashtags)
    assert len(brief.title_patterns) > 0
    assert "Kyle Kirshner" in brief.title_patterns[0]
    assert len(brief.description_guidelines) > 0
    assert "@KyleKirshner" in brief.required_mentions
    assert "kylekirshner.com/apply" in brief.cta_text
    assert any("get rich quick" in w for w in brief.banned_words) or any("get rich quick" in t for t in brief.banned_topics)


def test_campaign_specification_bidirectional_conversion():
    brief = parse_guidelines_into_brief(SAMPLE_PDF_TEXT)
    spec = CampaignSpecification.from_campaign_brief(brief)
    
    assert spec.campaign_id == brief.campaign_id
    assert spec.title == brief.name
    assert [item.value for item in spec.hashtags] == brief.hashtags
    assert [item.value for item in spec.required_mentions] == brief.required_mentions

    # Back to brief
    converted_brief = spec.to_campaign_brief()
    assert converted_brief.campaign_id == spec.campaign_id
    assert converted_brief.hashtags == [item.value for item in spec.hashtags]
    assert converted_brief.required_mentions == [item.value for item in spec.required_mentions]


def test_deterministic_seo_repair_loop_and_compliance(initialised_db):
    from autoclip.db import store
    brief = parse_guidelines_into_brief(SAMPLE_PDF_TEXT)
    spec = CampaignSpecification.from_campaign_brief(brief)
    engine = SEOEngine.from_campaign_spec(spec)

    source_id = models.new_id()
    store.create_source(models.Source(id=source_id, type="upload", path="/tmp/test.mp4"))
    job_id = models.new_id()
    store.create_job(models.Job(id=job_id, source_id=source_id, status="running"))

    clip = store.create_clip(
        models.Clip(
            id=models.new_id(),
            job_id=job_id,
            start_s=0.0,
            end_s=25.0,
            rank=1,
            title="easy money Amazon business with Kyle Kirshner",
            hook="This is an easy money video about Kyle Kirshner",
        )
    )

    # Initial SEO metadata missing required hashtags, with prohibited words and missing CTA
    seo_record = engine.generate_for_clip(
        clip=clip,
        transcript_text="This is an easy money video about how Kyle Kirshner built an 8-figure Amazon business.",
    )

    # 1. Prohibited words stripped
    assert "easy money" not in seo_record.final_description.lower()
    assert "easy money" not in seo_record.final_title.lower()

    # 2. Mandatory hashtags injected
    for ht in brief.hashtags:
        clean_tag = ht.lstrip("#").lower()
        assert any(clean_tag in h.lower() for h in seo_record.final_hashtags)

    # 3. Mention and CTA text present
    assert any("kylekirshner" in m.lower() for m in seo_record.final_mentions) or "kylekirshner" in seo_record.final_description.lower()
    assert "kylekirshner.com/apply" in seo_record.final_cta or "kylekirshner.com/apply" in seo_record.final_description

    # 4. Compliance telemetry passed
    comp_telemetry = seo_record.telemetry.get("campaign_compliance", {})
    assert comp_telemetry.get("passed") is True
    assert comp_telemetry.get("repaired") is True


@pytest.mark.asyncio
async def test_telegram_card_campaign_compliance_gating(tmp_path: Path):
    dummy_video = tmp_path / "clip.mp4"
    dummy_video.write_bytes(b"dummy")

    job = models.Job(
        id="job-campaign-test",
        source_id="src-1",
        settings={"campaign": {"name": "Test Campaign"}},
    )
    clip = models.Clip(
        id="clip-campaign-test",
        job_id="job-campaign-test",
        start_s=0.0,
        end_s=25.0,
        rank=1,
    )
    final_render = models.FinalRenderRecord(
        id="fr-test-1",
        job_id="job-campaign-test",
        clip_id="clip-campaign-test",
        output_path=str(dummy_video),
        quality_score=9.5,
        quality_status="APPROVED",
    )

    # Scenario A: Non-compliant clip -> send_clip_review blocks and returns None
    failing_meta = models.ClipMetadataRecord(
        id="meta-failing",
        job_id="job-campaign-test",
        clip_id="clip-campaign-test",
        generated_title="Non Compliant Title",
        final_title="Non Compliant Title",
        generated_description="Non compliant description",
        final_description="Non compliant description",
        compliance_status="NON_COMPLIANT",
        telemetry={
            "campaign_compliance": {
                "passed": False,
                "violations": ["Missing required hashtag #AmazonFBA"],
            }
        },
    )

    with patch("autoclip.db.store.get_job", return_value=job), \
         patch("autoclip.db.store.get_clip", return_value=clip), \
         patch("autoclip.db.store.get_clip_approval", return_value=None), \
         patch("autoclip.db.store.get_final_render", return_value=final_render), \
         patch("autoclip.db.store.get_clip_metadata", return_value=failing_meta), \
         patch("autoclip.db.store.list_exports", return_value=[]), \
         patch("autoclip.telegram.review_bot._safe_send_telegram_message", new_callable=AsyncMock) as mock_send:
        
        res_blocked = await send_clip_review(
            job_id="job-campaign-test",
            clip_id="clip-campaign-test",
            job_settings={"telegram": {"bot_token": "fake_tok", "chat_id": "12345"}}
        )
        assert res_blocked is None
        mock_send.assert_not_called()

    # Scenario B: Compliant clip -> send_clip_review passes gating and sends review card with badge & hashtags
    passing_meta = models.ClipMetadataRecord(
        id="meta-passing",
        job_id="job-campaign-test",
        clip_id="clip-campaign-test",
        generated_title="Kyle Kirshner Amazon FBA Success",
        final_title="Kyle Kirshner Amazon FBA Success",
        generated_description="Learn with @KyleKirshner. Visit kylekirshner.com/apply",
        final_description="Learn with @KyleKirshner. Visit kylekirshner.com/apply",
        final_hashtags=["#AmazonFBA", "#KyleKirshner"],
        final_cta="Visit kylekirshner.com/apply",
        compliance_status="COMPLIANT",
        telemetry={
            "campaign_compliance": {
                "passed": True,
                "violations": [],
                "repaired": ["Inserted #AmazonFBA"],
            }
        },
    )

    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.json = lambda: {"result": {"message_id": 999}}

    with patch("autoclip.db.store.get_job", return_value=job), \
         patch("autoclip.db.store.get_clip", return_value=clip), \
         patch("autoclip.db.store.get_clip_approval", return_value=None), \
         patch("autoclip.db.store.get_final_render", return_value=final_render), \
         patch("autoclip.db.store.get_clip_metadata", return_value=passing_meta), \
         patch("autoclip.db.store.list_exports", return_value=[]), \
         patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_resp)) as mock_post, \
         patch("autoclip.telegram.review_bot._record_review_sent"):
        res_sent = await send_clip_review(
            job_id="job-campaign-test",
            clip_id="clip-campaign-test",
            job_settings={"telegram": {"bot_token": "fake_tok", "chat_id": "12345"}}
        )
        assert res_sent is not None
        assert mock_post.called
        call_kwargs = mock_post.call_args.kwargs
        caption = call_kwargs.get("data", {}).get("caption", "")
        assert "Campaign Compliance:" in caption
        assert "PASSED" in caption
        assert "#AmazonFBA" in caption
        assert "kylekirshner.com/apply" in caption
