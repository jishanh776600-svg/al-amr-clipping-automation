"""Test HardScope Campaign extraction, platform-specific SEO, and Telegram review card formatting."""

import pytest
from autoclip.campaign.extractor import parse_guidelines_into_brief
from autoclip.seo.extractor import extract_campaign_seo_spec, extract_campaign_seo_requirements
from autoclip.seo.engine import SEOEngine
from autoclip.db.models import Clip


HARDSCOPE_BRIEF_TEXT = """
HardScope - Show Trailers
ClipFarm Campaign Brief · Clip + Theme Pages · Trailer Campaign

THE MISSION
EVERY CLIP TAGS @HARDSCOPE AND USES THE SHOW HASHTAG
No dedicated page needed. Post from your existing clip pages and theme pages.
Platforms: YouTube Shorts is the main stage. 70% of the budget sits on Shorts, with 10% each on TikTok, Instagram Reels, and X.

Tags: REQUIRED
YouTube: @HardScopeTV
TikTok: @hardscope
Instagram: @hardscope

Hashtag: REQUIRED
The show's hashtag in EVERY caption: #r3born, #loveandjustice, or #unpacked.

Captions
Short and conversational, like a fan posting. No long write-ups, no hard selling.

✕  Any mention of FaZe, anywhere in your content
✕  Posted before 12 PM ET today
✕  The same clip submitted to another agency's campaign
✕  Missing the @hardscope tag or the show hashtag

1 | The Trailers + Footage
Trailer | Who | Hashtag
R3born | Neon | #r3born
Love & Justice | Zach Justice | #loveandjustice
Unpacked | MatCrackz | #unpacked
📁 All 3 trailers: https://we.tl/t-kfBB3yKRyOqbHBee

2 | Tags, Captions + Platforms
🚫 NEVER MENTION FAZE
Not in captions, not in on-screen text, not in titles, not in hashtags, not in voiceover.
"""


def test_hardscope_campaign_extraction():
    brief = parse_guidelines_into_brief(HARDSCOPE_BRIEF_TEXT, filename="HardScope_Trailers.docx")

    # 1. Banned words check
    assert "faze" in brief.banned_words, "FaZe must be extracted as a banned word"
    assert "from both" not in brief.banned_words, "False positive 'from both' must not be in banned words"

    # 2. Platform mentions check
    assert "youtube" in brief.platform_mentions
    assert "@HardScopeTV" in brief.platform_mentions["youtube"]
    assert "instagram" in brief.platform_mentions
    assert "@hardscope" in brief.platform_mentions["instagram"]

    # 3. Show mappings check
    shows = {m["show"]: m["hashtag"] for m in brief.show_mappings}
    assert shows.get("R3born") == "#r3born"
    assert shows.get("Love & Justice") == "#loveandjustice"
    assert shows.get("Unpacked") == "#unpacked"


def test_hardscope_youtube_and_instagram_seo_generation():
    brief = parse_guidelines_into_brief(HARDSCOPE_BRIEF_TEXT, filename="HardScope_Trailers.docx")
    spec = extract_campaign_seo_spec(brief)
    reqs = extract_campaign_seo_requirements(brief)
    engine = SEOEngine(requirements=reqs, campaign_seo_spec=spec)

    # Clip 1: Neon in R3born
    clip_neon = Clip(id="clip_neon_1", job_id="j1", start_s=0.0, end_s=30.0, title="Neon in R3born Trailer Epic Moment", hook="Neon maxed out all gym machines")
    yt_neon = engine.generate_youtube_metadata(clip_neon, transcript_text="Neon returns in the R3born trailer pushing past limits.")
    ig_neon = engine.generate_instagram_metadata(clip_neon, transcript_text="Neon returns in the R3born trailer pushing past limits.")

    # YouTube assertions
    assert "@HardScopeTV" in yt_neon.mentions, "YouTube must target @HardScopeTV"
    assert "@HardScopeTV" in yt_neon.description, "YouTube description must tag @HardScopeTV"
    assert "#r3born" in yt_neon.hashtags, "R3born clip must include #r3born"
    assert "#loveandjustice" not in yt_neon.hashtags, "R3born clip must NOT include #loveandjustice"
    assert "#unpacked" not in yt_neon.hashtags, "R3born clip must NOT include #unpacked"
    assert yt_neon.compliance_score == 100.0, f"YouTube compliance score should be 100%, got {yt_neon.compliance_score}"

    # Instagram assertions
    assert "@hardscope" in ig_neon.mentions, "Instagram must target @hardscope"
    assert "@hardscope" in ig_neon.caption, "Instagram caption must tag @hardscope"
    assert "@HardScopeTV" not in ig_neon.caption, "Instagram caption must NOT tag YouTube handle @HardScopeTV"
    assert "#r3born" in ig_neon.hashtags, "Instagram must include #r3born"
    assert "#loveandjustice" not in ig_neon.hashtags, "Instagram must NOT include #loveandjustice"
    assert "#unpacked" not in ig_neon.hashtags, "Instagram must NOT include #unpacked"
    assert ig_neon.compliance_score == 100.0, f"Instagram compliance score should be 100%, got {ig_neon.compliance_score}"

    # Clip 2: Zach Justice in Love & Justice
    clip_zach = Clip(id="clip_zach_1", job_id="j1", start_s=0.0, end_s=30.0, title="Zach Justice Courtroom Drama", hook="Zach Justice delivers the verdict")
    yt_zach = engine.generate_youtube_metadata(clip_zach, transcript_text="Zach Justice stars in the Love & Justice trailer.")
    assert "#loveandjustice" in yt_zach.hashtags
    assert "#r3born" not in yt_zach.hashtags
    assert "#unpacked" not in yt_zach.hashtags

    # Negative constraint check: FaZe must be blocked
    clip_faze = Clip(id="clip_faze_1", job_id="j1", start_s=0.0, end_s=30.0, title="FaZe Clan Neon Special", hook="FaZe Clan highlights")
    yt_faze = engine.generate_youtube_metadata(clip_faze, transcript_text="FaZe Clan highlights in trailer.")
    assert "faze" not in yt_faze.title.lower()
    assert "faze" not in yt_faze.description.lower()
