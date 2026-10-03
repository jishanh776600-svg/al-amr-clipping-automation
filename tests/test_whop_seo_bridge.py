"""Unit tests for Whop Multi-Platform Strict SEO Bridge and Quality Gate."""

import pytest
from whop.models import WhopCampaignBrief, ParsingStatus
from whop.seo_bridge import WhopSEOBridge, brief_to_seo_requirements


@pytest.fixture
def sample_brief() -> WhopCampaignBrief:
    return WhopCampaignBrief(
        campaign_id="camp_candid_1",
        title="The Candid Club x ClipHouse",
        campaign_url="https://whop.com/candid",
        guideline_hash="5eef9115f5c40461",
        parsing_status=ParsingStatus.PARSED,
        duration_min_s=20.0,
        duration_max_s=30.0,
        hashtags=["#CandidClub", "#ClipHouse"],
        required_mentions=["@thecandidclub", "@whop"],
        banned_words=["vulgarity", "piracy", "competitor"],
        banned_topics=["hate speech"],
        link_in_bio="https://whop.com/candid",
        cta_wording="Join Candid Club now!",
    )


def test_brief_to_seo_requirements(sample_brief: WhopCampaignBrief):
    reqs = brief_to_seo_requirements(sample_brief)
    assert "#CandidClub" in reqs.required_hashtags
    assert "#ClipHouse" in reqs.required_hashtags
    assert "@thecandidclub" in reqs.required_mentions
    assert "@whop" in reqs.required_mentions
    assert "vulgarity" in reqs.prohibited_terms
    assert "hate speech" in reqs.prohibited_terms
    assert reqs.cta_required is True


def test_generate_clip_seo_compliance(sample_brief: WhopCampaignBrief):
    bridge = WhopSEOBridge.from_brief(sample_brief)
    clip_meta = bridge.generate_clip_seo(
        brief=sample_brief,
        clip_id="clip_001",
        drive_file_id="drive_file_12345",
        hook_or_title="Amazing creator secrets revealed",
        transcript_snippet="Here is how top creators build audiences without spending a dime.",
        clip_index=1,
    )

    # 1. Platform fields present
    assert clip_meta.clip_id == "clip_001"
    assert clip_meta.drive_file_id == "drive_file_12345"
    assert len(clip_meta.youtube_title) <= 70
    assert "Amazing creator secrets revealed" in clip_meta.youtube_title

    # 2. YouTube Shorts requirements
    assert "#Shorts" in clip_meta.youtube_description
    assert "#CandidClub" in clip_meta.youtube_description
    assert "#ClipHouse" in clip_meta.youtube_description
    assert "@thecandidclub" in clip_meta.youtube_description
    assert "@whop" in clip_meta.youtube_description
    assert "Check the link in bio" in clip_meta.youtube_description

    # 3. Instagram Reels requirements
    assert "#CandidClub" in clip_meta.instagram_caption
    assert "#ClipHouse" in clip_meta.instagram_caption
    assert "@thecandidclub" in clip_meta.instagram_caption

    # 4. TikTok requirements
    assert "#CandidClub" in clip_meta.tiktok_caption
    assert "#ClipHouse" in clip_meta.tiktok_caption
    assert "@thecandidclub" in clip_meta.tiktok_caption

    # 5. Quality Gate 100% compliance
    assert clip_meta.is_compliant is True
    assert clip_meta.compliance_score == 100.0
    assert len(clip_meta.violations) == 0


def test_seo_self_repair_and_sanitization(sample_brief: WhopCampaignBrief):
    bridge = WhopSEOBridge.from_brief(sample_brief)

    # Inject prohibited words and pipeline tokens into the hook
    dirty_hook = "Vulgarity in autoclip c10875452a89 job ef232f703efe4778"
    clip_meta = bridge.generate_clip_seo(
        brief=sample_brief,
        clip_id="clip_002",
        drive_file_id="drive_file_67890",
        hook_or_title=dirty_hook,
        transcript_snippet="Normal transcript content",
        clip_index=2,
    )

    # Verify prohibited word "vulgarity" was stripped
    assert "vulgarity" not in clip_meta.youtube_title.lower()
    assert "vulgarity" not in clip_meta.youtube_description.lower()

    # Verify internal pipeline tokens were purged
    assert "c10875452a89" not in clip_meta.youtube_title
    assert "ef232f703efe4778" not in clip_meta.youtube_title

    # Must still achieve 100% compliance via repair
    assert clip_meta.is_compliant is True
    assert clip_meta.compliance_score == 100.0


def test_generate_campaign_seo_package_5_clips(sample_brief: WhopCampaignBrief):
    bridge = WhopSEOBridge.from_brief(sample_brief)
    mock_clips = [
        {"clip_id": f"clip_00{i}", "drive_file_id": f"drive_id_{i}", "hook_or_title": f"Creator Tip #{i}"}
        for i in range(1, 6)
    ]

    package = bridge.generate_campaign_seo_package(sample_brief, mock_clips)
    assert package.total_clips == 5
    assert package.all_compliant is True
    assert len(package.clips_metadata) == 5
    for c in package.clips_metadata:
        assert c.is_compliant is True
        assert c.compliance_score == 100.0
