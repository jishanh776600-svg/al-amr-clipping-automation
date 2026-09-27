"""Comprehensive tests for AL AMR V3 Content Intelligence & Platform-Specific SEO Upgrade."""

from __future__ import annotations

import pytest
from autoclip.campaign.content_intelligence import (
    ClipQualityModel,
    DiversityOptimizer,
    HookAnalysis,
    QualityBreakdown,
    build_v2_engine_telemetry,
)
from autoclip.campaign.models_intelligence import CampaignSpecification
from autoclip.db.models import Clip, ClipCandidateRecord, ClipMetadataRecord
from autoclip.pipeline.transcript import Word
from autoclip.publishing.base import PublishingMetadata
from autoclip.publishing.service import PublishingService
from autoclip.seo.extractor import extract_campaign_seo_spec
from autoclip.seo.models import CampaignSEOSpec, InstagramMetadata, YouTubeMetadata
from autoclip.seo.quality_gate import validate_instagram_metadata, validate_youtube_metadata
from autoclip.seo.engine import SEOEngine


def test_platform_specific_seo_extraction():
    """Verify that CampaignSEOSpec extracts independent rules for YouTube and Instagram."""
    spec_data = {
        "campaign_id": "test_camp_v3",
        "brand_name": "Future Founders",
        "guidelines": {
            "global": {
                "prohibited_terms": ["crypto", "get rich quick"],
                "required_terms": ["entrepreneurship"],
            },
            "youtube": {
                "title_rules": ["Must include brand name"],
                "hashtag_rules": ["#Shorts", "#Founders"],
                "cta_rules": ["Subscribe to Future Founders"],
            },
            "instagram": {
                "caption_rules": ["Hook in first sentence"],
                "mention_rules": ["@black_boxvault"],
                "hashtag_rules": ["#reels", "#startup"],
                "cta_rules": ["Follow @black_boxvault and check link in bio"],
            },
        },
    }
    seo_spec = extract_campaign_seo_spec(spec_data)

    assert isinstance(seo_spec, CampaignSEOSpec)
    assert seo_spec.brand_name == "Future Founders"
    assert "crypto" in seo_spec.global_rules.prohibited_terms
    assert "entrepreneurship" in seo_spec.global_rules.required_terms

    # Check YouTube specific rules
    assert "#Shorts" in seo_spec.youtube_rules.hashtag_rules
    assert any("Subscribe" in cta for cta in seo_spec.youtube_rules.cta_rules)

    # Check Instagram specific rules
    assert "@black_boxvault" in seo_spec.instagram_rules.mention_rules
    assert "#reels" in seo_spec.instagram_rules.hashtag_rules
    assert any("bio" in cta for cta in seo_spec.instagram_rules.cta_rules)


def test_youtube_metadata_validation():
    """Verify deterministic YouTube quality gate checks title length, #Shorts, prohibited words."""
    spec = CampaignSEOSpec(
        brand_name="Future Founders",
    )
    spec.global_rules.prohibited_terms = ["banned_word"]
    spec.youtube_rules.required_phrases = ["Scale Fast"]

    # Failing metadata: Title too long (>100 chars), contains prohibited word, missing required phrase
    bad_yt = YouTubeMetadata(
        title="A" * 105 + " banned_word",
        description="Just a normal description without the required phrase.",
        hashtags=["#random"],
        tags=["random"],
    )
    validated = validate_youtube_metadata(bad_yt, spec=spec)
    assert not validated.is_compliant
    assert validated.compliance_status == "FAIL"
    assert any("exceeds maximum 100" in e for e in validated.errors)
    assert any("Prohibited term" in e for e in validated.errors)
    assert any("Scale Fast" in e for e in validated.errors)

    # Passing metadata: Compliant title, required phrase, #Shorts, clear CTA
    good_yt = YouTubeMetadata(
        title="How We Scale Fast | Future Founders",
        description="An essential breakdown of scaling your company. Key Focus: Scale Fast\n\n👉 Subscribe to Future Founders\n\n#Shorts #Founders",
        hashtags=["#Shorts", "#Founders"],
        tags=["Shorts", "Founders"],
        cta="Subscribe to Future Founders",
    )
    validated_good = validate_youtube_metadata(good_yt, spec=spec)
    assert validated_good.is_compliant
    assert validated_good.compliance_status == "PASS"
    assert validated_good.compliance_score == 100.0
    assert validated_good.optimization_score >= 80.0


def test_instagram_metadata_validation():
    """Verify deterministic Instagram quality gate checks first-line hook, @black_boxvault mention, line breaks."""
    spec = CampaignSEOSpec(
        brand_name="BlackBox",
    )
    spec.global_rules.prohibited_terms = ["guaranteed profit"]
    spec.instagram_rules.mention_rules = ["@black_boxvault"]

    # Failing metadata: Prohibited word, missing mention
    bad_ig = InstagramMetadata(
        caption="Get guaranteed profit right now without doing any work.",
        first_line_hook="Get guaranteed profit right now.",
        hashtags=["#money"],
        mentions=[],
    )
    validated_bad = validate_instagram_metadata(bad_ig, spec=spec)
    assert not validated_bad.is_compliant
    assert validated_bad.compliance_status == "FAIL"
    assert any("Prohibited term" in e for e in validated_bad.errors)
    assert any("@black_boxvault" in e for e in validated_bad.errors)

    # Passing metadata: First line hook, clean line breaks, @black_boxvault mention, link in bio
    good_caption = (
        "The #1 mistake founders make when raising capital...\n\n"
        "Most entrepreneurs focus on valuation instead of term sheets.\n\n"
        "👉 Follow @black_boxvault for daily founder breakdowns.\n"
        "🔗 Full resource guide via link in bio.\n\n"
        "#reels #startup #founders #business"
    )
    good_ig = InstagramMetadata(
        caption=good_caption,
        first_line_hook="The #1 mistake founders make when raising capital...",
        hashtags=["#reels", "#startup", "#founders", "#business"],
        mentions=["@black_boxvault"],
        cta="Follow @black_boxvault and check link in bio",
    )
    validated_good = validate_instagram_metadata(good_ig, spec=spec)
    assert validated_good.is_compliant
    assert validated_good.compliance_status == "PASS"
    assert validated_good.compliance_score == 100.0
    assert validated_good.optimization_score >= 80.0


def test_dual_platform_metadata_generation(initialised_db):
    """Verify SEOEngine generates independent YouTube and Instagram metadata."""

    spec_data = {
        "campaign_id": "c_v3",
        "brand_name": "Future Founders",
        "guidelines": {
            "youtube": {
                "hashtag_rules": ["#Shorts", "#FutureFounders"],
                "cta_rules": ["Subscribe to Future Founders"],
            },
            "instagram": {
                "mention_rules": ["@black_boxvault"],
                "hashtag_rules": ["#reels", "#founders"],
                "cta_rules": ["Follow @black_boxvault for daily lessons"],
            },
        },
    }
    camp_spec = CampaignSpecification.from_dict(spec_data)
    engine = SEOEngine.from_campaign_spec(camp_spec)

    from autoclip.db import store
    from autoclip.db.models import Job, Source

    store.create_source(Source(id="src_v3", type="upload", path="test.mp4"))
    store.create_job(Job(id="job_v3", source_id="src_v3", status="running"))
    clip = store.create_clip(
        Clip(
            id="clip_test_123",
            job_id="job_v3",
            start_s=10.0,
            end_s=35.0,
            rank=1,
            title="Zero to One",
            hook="Why 99% of startups fail before day 100",
            status="candidate",
        )
    )
    slice_text = "Why 99% of startups fail before day 100. It is because they build products nobody wants."

    rec = engine.generate_for_clip(clip, transcript_text=slice_text)

    # Must have both platforms in telemetry
    assert "youtube" in rec.telemetry
    assert "instagram" in rec.telemetry
    yt_data = rec.telemetry["youtube"]
    ig_data = rec.telemetry["instagram"]

    # YouTube and Instagram metadata MUST NOT be identical
    assert yt_data["title"] != ig_data["caption"]
    assert "#Shorts" in " ".join(yt_data["hashtags"])
    assert "@black_boxvault" in ig_data["mentions"] or "@black_boxvault" in ig_data["caption"]
    assert rec.compliance_status in ("SEO_PASS", "SEO_WARN")


def test_publishing_service_platform_resolution():
    """Verify PublishingService feeds YouTube metadata to YouTube and Instagram metadata to Instagram."""
    svc = PublishingService()
    # Mock clip metadata with dual platform packages
    clip_meta = ClipMetadataRecord(
        id="meta_dual_1",
        job_id="job_dual",
        clip_id="clip_dual_1",
        generated_title="Fallback Title",
        final_title="Fallback Title",
        generated_description="Fallback Desc",
        final_description="Fallback Desc",
        generated_hashtags=["#fallback"],
        final_hashtags=["#fallback"],
        generated_mentions=[],
        final_mentions=[],
        generated_cta="",
        final_cta="",
        compliance_status="SEO_PASS",
        compliance_score=100.0,
        telemetry={
            "youtube": {
                "title": "YouTube Specific Title | Future Founders",
                "description": "YouTube Specific Description #Shorts",
                "hashtags": ["#Shorts", "#FutureFounders"],
            },
            "instagram": {
                "first_line_hook": "Instagram Hook",
                "caption": "Instagram Caption with @black_boxvault and link in bio",
                "hashtags": ["#reels", "#blackboxvault"],
            },
        },
    )

    # YouTube resolution
    yt_title = clip_meta.final_title
    yt_desc = clip_meta.final_description
    yt_tags = clip_meta.final_hashtags
    if clip_meta.telemetry and "youtube" in clip_meta.telemetry:
        yt_data = clip_meta.telemetry["youtube"]
        yt_title = yt_data.get("title", yt_title)
        yt_desc = yt_data.get("description", yt_desc)
        yt_tags = yt_data.get("hashtags", yt_tags)

    assert yt_title == "YouTube Specific Title | Future Founders"
    assert yt_desc == "YouTube Specific Description #Shorts"
    assert "#Shorts" in yt_tags

    # Instagram resolution
    ig_title = clip_meta.final_title
    ig_caption = clip_meta.final_description
    ig_tags = clip_meta.final_hashtags
    if clip_meta.telemetry and "instagram" in clip_meta.telemetry:
        ig_data = clip_meta.telemetry["instagram"]
        ig_caption = ig_data.get("caption", ig_caption)
        ig_tags = ig_data.get("hashtags", ig_tags)
        ig_title = ig_data.get("first_line_hook", ig_title)

    assert ig_title == "Instagram Hook"
    assert "@black_boxvault" in ig_caption
    assert "#reels" in ig_tags


def test_clip_quality_model_and_penalties():
    """Verify 9-dimensional quality scoring and strict penalty enforcement."""
    model = ClipQualityModel()

    # Good clip: Strong question hook, numbers, takeaways, solid ending
    good_words = [
        Word(text="Why", start=0.0, end=0.4),
        Word(text="did", start=0.4, end=0.7),
        Word(text="we", start=0.7, end=0.9),
        Word(text="generate", start=0.9, end=1.5),
        Word(text="$1.2M", start=1.5, end=2.2),
        Word(text="in", start=2.2, end=2.4),
        Word(text="profit", start=2.4, end=2.9),
        Word(text="in", start=2.9, end=3.1),
        Word(text="30", start=3.1, end=3.5),
        Word(text="days?", start=3.5, end=4.1),
        Word(text="Because", start=4.2, end=4.8),
        Word(text="the", start=4.8, end=5.0),
        Word(text="strategy", start=5.0, end=5.6),
        Word(text="focused", start=5.6, end=6.1),
        Word(text="on", start=6.1, end=6.3),
        Word(text="customer", start=6.3, end=6.8),
        Word(text="retention.", start=6.8, end=7.5),
    ]
    q_good, hook_good = model.evaluate_clip(good_words, duration_s=7.5)
    assert q_good.composite_score >= 80.0
    assert q_good.hook_strength >= 80.0
    assert hook_good.hook_type == "curiosity_question"
    assert len(q_good.penalty_details) == 0

    # Bad clip: Conversational rambling intro ("so um"), dangling pronoun ("he"), dangling terminal ("and")
    bad_words = [
        Word(text="so", start=0.0, end=0.3),
        Word(text="um", start=0.3, end=0.6),
        Word(text="he", start=0.6, end=0.9),
        Word(text="was", start=0.9, end=1.2),
        Word(text="talking", start=1.2, end=1.8),
        Word(text="about", start=1.8, end=2.2),
        Word(text="stuff", start=2.2, end=2.6),
        Word(text="and", start=2.6, end=3.0),
    ]
    q_bad, hook_bad = model.evaluate_clip(bad_words, duration_s=3.0)
    assert q_bad.composite_score < 60.0
    assert any("weak_opening_filler" in p for p in q_bad.penalty_details)
    assert any("context_dependency" in p for p in q_bad.penalty_details)
    assert any("dangling_terminal" in p for p in q_bad.penalty_details)


def test_diversity_optimizer_mmr():
    """Verify DiversityOptimizer eliminates repetitive clips and maximizes topic diversity."""
    optimizer = DiversityOptimizer(lambda_balance=0.7)

    items = [
        {
            "id": "c1",
            "text": "How we scaled to one million dollars with direct sales strategies.",
            "start_s": 10.0,
            "end_s": 35.0,
            "quality": type("Q", (), {"composite_score": 95.0})(),
        },
        {
            "id": "c2_dup",
            # Nearly identical text and overlapping time window with c1
            "text": "How we scaled to one million dollars with direct sales techniques.",
            "start_s": 12.0,
            "end_s": 37.0,
            "quality": type("Q", (), {"composite_score": 93.0})(),
        },
        {
            "id": "c3_diverse",
            # Completely different topic and non-overlapping time window
            "text": "The hardest part of firing your first executive when the company breaks.",
            "start_s": 120.0,
            "end_s": 145.0,
            "quality": type("Q", (), {"composite_score": 88.0})(),
        },
    ]

    selected = optimizer.select_diverse_set(items, target_count=2)
    selected_ids = [s["id"] for s in selected]

    # c1 is picked first (highest score)
    assert selected_ids[0] == "c1"
    # c3_diverse should be preferred over c2_dup due to diversity penalty on c2_dup!
    assert selected_ids[1] == "c3_diverse"


def test_v2_engine_telemetry_schema():
    """Verify Remotion + FFmpeg V2 telemetry structure."""
    hook = HookAnalysis(
        native_hook="Why nobody talks about revenue churn",
        editorial_hook="The Revenue Churn Trap",
        hook_type="counter_intuitive",
        hook_duration_s=3.8,
        hook_strength_score=92.0,
    )
    quality = QualityBreakdown(
        hook_strength=92.0,
        content_value=85.0,
        visual_potential=75.0,
        emotional_impact=80.0,
        information_density=90.0,
        composite_score=88.5,
    )
    v2_data = build_v2_engine_telemetry(
        clip_id="clip_v2_001",
        start_s=50.0,
        end_s=75.0,
        hook=hook,
        quality=quality,
    )

    assert v2_data["engine_version"] == "v2_ready"
    assert v2_data["timeline"]["duration_s"] == 25.0
    assert v2_data["hook_overlay"]["editorial_headline"] == "The Revenue Churn Trap"
    assert v2_data["visual_cues"]["b_roll_recommended"] is True
    assert v2_data["visual_cues"]["energy_level"] == "high"
