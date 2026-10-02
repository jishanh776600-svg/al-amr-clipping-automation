"""Unit tests for Whop Campaign Catalog, Parsing, Normalization, Eligibility, and Routing (Step 2)."""

import json
from pathlib import Path
import pytest

from whop.catalog import (
    ACCOUNT_1_FINANCE_BUSINESS,
    ACCOUNT_2_ENTERTAINMENT_PODCASTS,
    ACCOUNT_3_ALL_IN_ONE_VIRAL,
    DiscoveredCampaign,
    build_discovered_campaign,
    evaluate_eligibility,
    normalize_platforms,
    parse_cpm,
    route_niche,
)
from whop.scraper import DiscoveryRunReport, generate_markdown_summary, save_discovery_artifacts


def test_parse_cpm_standard_patterns():
    # 1. Standard "$1.00 CPM" -> 1.0
    assert parse_cpm("$1.00 CPM") == 1.00
    # 2. "$2.50 / 1k" -> 2.5
    assert parse_cpm("$2.50 / 1k") == 2.50
    # 3. "$3 / 1,000 views" -> 3.0
    assert parse_cpm("$3 / 1,000 views") == 3.00
    # 4. "CPM: $1.75" -> 1.75
    assert parse_cpm("CPM: $1.75") == 1.75
    # 5. "$0.50 CPM" -> 0.50
    assert parse_cpm("$0.50 CPM") == 0.50
    # 6. "€2.50 / 1k views" -> 2.5
    assert parse_cpm("€2.50 / 1k views") == 2.50


def test_parse_cpm_ambiguous_returns_none():
    # Ambiguous flat rates, percentages, or non-CPM strings must be None (never guess)
    assert parse_cpm("Flat $500 payout") is None
    assert parse_cpm("50% commission") is None
    assert parse_cpm("Earn cash rewards") is None
    assert parse_cpm("") is None
    assert parse_cpm(None) is None


def test_normalize_platforms_single_and_multiple():
    # Single platform
    assert normalize_platforms("YouTube Shorts") == ["youtube"]
    assert normalize_platforms("Instagram") == ["instagram"]
    assert normalize_platforms("TikTok") == ["tiktok"]

    # Multiple platforms with various separators
    raw = "YouTube, Instagram Reels, TikTok"
    assert normalize_platforms(raw) == ["instagram", "tiktok", "youtube"]

    # List inputs
    assert normalize_platforms(["yt", "reels"]) == ["instagram", "youtube"]


def test_normalize_platforms_unsupported_or_empty():
    assert normalize_platforms("") == []
    assert normalize_platforms(None) == []
    assert normalize_platforms("Twitter, LinkedIn, Facebook") == []


def test_evaluate_eligibility_eligible():
    is_eligible, reasons = evaluate_eligibility(
        cpm=2.50,
        platforms=["youtube", "instagram"],
        source_urls=["https://drive.google.com/drive/folders/123"],
        title="High Yield Crypto Trading Podcast",
        raw_text="Clip the best moments",
    )
    assert is_eligible is True
    assert reasons == ["ELIGIBLE"]


def test_evaluate_eligibility_cpm_below_threshold():
    is_eligible, reasons = evaluate_eligibility(
        cpm=0.75,
        platforms=["tiktok"],
        source_urls=["https://youtube.com/watch?v=abc"],
        title="Gaming funny clips",
        raw_text="Earn $0.75 CPM",
    )
    assert is_eligible is False
    assert "REJECTED_PAYOUT" in reasons


def test_evaluate_eligibility_unknown_payout():
    is_eligible, reasons = evaluate_eligibility(
        cpm=None,
        platforms=["tiktok"],
        source_urls=["https://youtube.com/watch?v=abc"],
        title="Gaming funny clips",
        raw_text="Earn revenue share",
    )
    assert is_eligible is False
    assert "UNKNOWN_PAYOUT" in reasons


def test_evaluate_eligibility_unsupported_platform():
    is_eligible, reasons = evaluate_eligibility(
        cpm=2.00,
        platforms=["twitter"],
        source_urls=["https://drive.google.com/xyz"],
        title="Twitter text campaign",
        raw_text="Post threads on Twitter",
    )
    assert is_eligible is False
    assert "REJECTED_PLATFORM" in reasons


def test_evaluate_eligibility_missing_source():
    is_eligible, reasons = evaluate_eligibility(
        cpm=3.00,
        platforms=["youtube", "tiktok"],
        source_urls=[],
        title="Valid Campaign With No Media",
        raw_text="Bring your own content",
    )
    assert is_eligible is False
    assert "REJECTED_SOURCE" in reasons


def test_evaluate_eligibility_require_drive_or_direct():
    # YouTube-only source rejected when require_drive_or_direct=True
    is_eligible, reasons = evaluate_eligibility(
        cpm=2.00,
        platforms=["youtube"],
        source_urls=["https://www.youtube.com/watch?v=DsC3qLVD8_Y"],
        title="Podcast with YouTube Link",
        raw_text="Clip our podcast",
        require_drive_or_direct=True,
    )
    assert is_eligible is False
    assert "REJECTED_NON_DRIVE_SOURCE" in reasons

    # Google Drive source accepted when require_drive_or_direct=True
    is_eligible_drive, reasons_drive = evaluate_eligibility(
        cpm=2.00,
        platforms=["youtube"],
        source_urls=["https://drive.google.com/drive/folders/1Qb7DigWjEt-eM5ujKL3VXL0h2knwDVZx"],
        title="Drive Media Campaign",
        raw_text="Clip our drive folder",
        require_drive_or_direct=True,
    )
    assert is_eligible_drive is True
    assert reasons_drive == ["ELIGIBLE"]

    # Direct S3 / MP4 accepted when require_drive_or_direct=True
    is_eligible_s3, reasons_s3 = evaluate_eligibility(
        cpm=1.50,
        platforms=["instagram"],
        source_urls=["https://s3.amazonaws.com/my-bucket/video.mp4"],
        title="S3 Media Campaign",
        raw_text="Clip our direct video",
        require_drive_or_direct=True,
    )
    assert is_eligible_s3 is True
    assert reasons_s3 == ["ELIGIBLE"]


def test_route_niche_finance_business():
    candidates, rec_acc, reason = route_niche(
        title="Apex Forex Trading Academy",
        raw_text="Clip our weekly trading webinars and crypto wealth strategies.",
        hashtags=["#forex", "#crypto"],
    )
    assert ACCOUNT_1_FINANCE_BUSINESS in candidates
    assert rec_acc == ACCOUNT_1_FINANCE_BUSINESS


def test_route_niche_entertainment_podcasts():
    candidates, rec_acc, reason = route_niche(
        title="The Raw Comedy & Lifestyle Podcast",
        raw_text="Clip funny moments, hilarious storytime interviews, and talk show clips.",
        hashtags=["#comedy", "#podcast"],
    )
    assert ACCOUNT_2_ENTERTAINMENT_PODCASTS in candidates
    assert rec_acc == ACCOUNT_2_ENTERTAINMENT_PODCASTS


def test_route_niche_all_in_one_viral():
    candidates, rec_acc, reason = route_niche(
        title="Viral Street Challenges & Shocking Facts",
        raw_text="Create viral clips with crazy experiments and compilation highlights.",
        hashtags=["#viral", "#challenge"],
    )
    assert ACCOUNT_3_ALL_IN_ONE_VIRAL in candidates
    assert rec_acc == ACCOUNT_3_ALL_IN_ONE_VIRAL


def test_route_niche_ambiguous_or_no_match():
    # No keywords match
    candidates, rec_acc, reason = route_niche(
        title="Random Blue Widgets",
        raw_text="A completely neutral description with zero matched niche words.",
    )
    assert candidates == []
    assert rec_acc is None
    assert reason == "NO_CONFIDENT_NICHE_MATCH"


def test_build_discovered_campaign_never_fabricates():
    camp = build_discovered_campaign(
        campaign_id="camp_123",
        title="Sample Whop Campaign",
        campaign_url="https://whop.com/discover/camp_123",
        payout_raw="",
        platforms_raw="",
        source_urls=None,
        guideline_urls=None,
    )
    assert camp.campaign_id == "camp_123"
    assert camp.cpm is None
    assert camp.platforms == []
    assert camp.source_urls == []
    assert camp.guideline_urls == []
    assert camp.eligible is False
    assert "UNKNOWN_PAYOUT" in camp.eligibility_reasons
    assert "UNKNOWN_PLATFORM" in camp.eligibility_reasons
    assert "REJECTED_SOURCE" in camp.eligibility_reasons


def test_discovery_artifacts_generation_and_secret_redaction(tmp_path: Path):
    secret_val = "SECRET_WHOP_TOKEN_XYZ123"
    report = DiscoveryRunReport(
        run_id="run_test_01",
        timestamp="2026-10-02T12:00:00Z",
        authenticated=True,
        dry_run=True,
        final_url=f"https://whop.com/discover?token={secret_val}",
        page_title="Discover Campaigns",
        campaign_count=1,
        eligible_count=1,
        rejected_count=0,
        campaigns=[
            DiscoveredCampaign(
                campaign_id="c_1",
                title="SaaS Founder Podcast",
                campaign_url=f"https://whop.com/discover/c_1?auth={secret_val}",
                payout_raw="$2.00 CPM",
                cpm=2.00,
                platforms=["youtube", "tiktok"],
                source_urls=["https://drive.google.com/1"],
                eligible=True,
                eligibility_reasons=["ELIGIBLE"],
                niche_candidates=[ACCOUNT_1_FINANCE_BUSINESS],
                recommended_account=ACCOUNT_1_FINANCE_BUSINESS,
            ).to_dict()
        ],
    )

    json_path, md_path = save_discovery_artifacts(report, tmp_path)
    assert json_path.exists()
    assert md_path.exists()

    md_text = md_path.read_text(encoding="utf-8")
    assert "Whop Campaign Discovery Summary" in md_text
    assert "SaaS Founder Podcast" in md_text
    assert "$2.00 CPM" in md_text