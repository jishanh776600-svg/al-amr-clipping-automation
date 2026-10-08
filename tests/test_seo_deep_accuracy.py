"""Deep unit tests for 100% accurate, high-CTR, zero-slop SEO Engine."""

import pytest
from autoclip.campaign.models_intelligence import CampaignSpecification, RequirementItem
from autoclip.db.models import Clip, Job, Source
from autoclip.db import store
from autoclip.seo.engine import SEOEngine, to_title_case, strip_conversational_filler
from autoclip.seo.extractor import is_clean_public_hashtag, is_clean_public_mention, extract_campaign_seo_requirements


def test_title_casing_and_contractions():
    """Contractions like We'll, Don't, and Nobody's do not get capitalized after apostrophe."""
    raw = "we'll see if we don't make it, honestly"
    cleaned = strip_conversational_filler(raw)
    assert cleaned == "we'll see if we don't make it"
    title = to_title_case(cleaned)
    assert "We'll" in title
    assert "Don't" in title
    assert "We'Ll" not in title
    assert "Don'T" not in title


def test_filler_stripping():
    """Conversational filler at beginning and end of transcript hooks is cleanly removed."""
    examples = [
        ("yeah so basically starting a company is hard, I think", "starting a company is hard"),
        ("you know, building tiny homes will change the world, so yeah", "building tiny homes will change the world"),
        ("honestly we'll see if we get there, I guess", "we'll see if we get there"),
        ("listen, never compromise on engineering talent, you know", "never compromise on engineering talent"),
    ]
    for inp, expected in examples:
        assert strip_conversational_filler(inp).lower() == expected.lower()


def test_public_hashtag_and_mention_hygiene():
    """SOP, payment, and slop tokens are 100% rejected, while clean brand tags pass."""
    slop_tags = [
        "#WONTGETPAIDREJECTEDLoweffortslop",
        "#baitBUDGET85",
        "#000CPM0501KVIEWSMAXPERCLIP500READTHISFIRST",
        "#tier-1",
        "#payout",
        "#rules",
        "#guidelines",
        "#drive",
    ]
    for tag in slop_tags:
        assert not is_clean_public_hashtag(tag), f"Expected slop tag {tag} to be rejected"

    clean_tags = ["#Boxabl", "#FutureFounders", "#Shorts", "#Reels", "#Innovation", "#Tech"]
    for tag in clean_tags:
        assert is_clean_public_hashtag(tag), f"Expected clean tag {tag} to pass"


def test_seo_engine_high_ctr_synthesis_and_brand_injection(initialised_db):
    """SEO Engine synthesizes a high-CTR, Title-Cased title integrating the brand name."""
    store.create_source(Source(id="src-box-1", type="upload", path="box.mp4"))
    store.create_job(Job(id="job-box-1", source_id="src-box-1", status="running"))

    clip = Clip(
        id="clip-box-1",
        job_id="job-box-1",
        rank=1,
        start_s=0.0,
        end_s=25.0,
        title="Tiny Home Revolution",
        hook="very, very tiny goal. We'll see if we get there, I think",
        score=95,
    )
    store.create_clip(clip)

    spec = CampaignSpecification(
        campaign_id="camp-boxabl",
        title="Boxabl Innovation",
        brand_name="Boxabl",
        required_mentions=[RequirementItem(value="@Boxabl"), RequirementItem(value="@GalianoTiramani")],
        hashtags=[RequirementItem(value="#Boxabl"), RequirementItem(value="#TinyHome")],
        campaign_url="https://boxabl.com/invest",
    )

    engine = SEOEngine.from_campaign_spec(spec)
    transcript = (
        "We have set a very, very tiny goal. We'll see if we get there, I think. "
        "Our factory produces a new home every ninety minutes. "
        "Traditional construction cannot compete with this speed and efficiency."
    )

    yt_meta = engine.generate_youtube_metadata(clip, transcript_text=transcript)
    
    # 1. Title verification
    assert "#Shorts" in yt_meta.title
    assert "I think" not in yt_meta.title
    assert "Boxabl" in yt_meta.title
    assert len(yt_meta.title) <= 95
    assert "We'Ll" not in yt_meta.title

    # 2. Description verification
    assert "📌 Key Highlights:" in yt_meta.description
    assert "•" in yt_meta.description
    assert "🔗 Link: https://boxabl.com/invest" in yt_meta.description
    assert "@Boxabl" in yt_meta.description
    assert "#Boxabl" in yt_meta.hashtags
    assert "#TinyHome" in yt_meta.hashtags

    # 3. Instagram Reels verification
    ig_meta = engine.generate_instagram_metadata(clip, transcript_text=transcript)
    assert len(ig_meta.first_line_hook) <= 125
    assert "I think" not in ig_meta.first_line_hook
    assert "@Boxabl" in ig_meta.mentions
    assert any(h in ig_meta.hashtags for h in ["#reels", "#Boxabl"])
