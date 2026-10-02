"""Unit tests for Step 7.4 Video Source Capability, Probe, and No-Login Policy."""

from unittest.mock import MagicMock, patch
import pytest

from whop.models import (
    CampaignRule,
    CampaignState,
    RuleCategory,
    SourceCapability,
    SourceTier,
    WhopCampaignBrief,
)
from whop.config import AutoClipConfig, WhopConfig
from whop.catalog import evaluate_eligibility
from whop.autoclip_client import (
    AutoClipClient,
    AutoClipSourceMissingError,
    AutoClipSourceRestrictedError,
)
from whop.source_probe import (
    SourceProbe,
    classify_source_url,
    convert_dropbox_to_direct_url,
)


# ------------------------------------------------------------------------------
# 1. Deterministic URL Classification Tests
# ------------------------------------------------------------------------------

def test_classify_direct_media():
    cap, tier = classify_source_url("https://cdn.example.com/videos/episode1.mp4")
    assert cap == SourceCapability.DIRECT_MEDIA
    assert tier == SourceTier.TIER_0_DIRECT_CDN_S3

    cap_mov, tier_mov = classify_source_url("https://media.company.com/sample.mov")
    assert cap_mov == SourceCapability.DIRECT_MEDIA
    assert tier_mov == SourceTier.TIER_0_DIRECT_CDN_S3


def test_classify_public_s3():
    cap, tier = classify_source_url("https://mybucket.s3.us-east-1.amazonaws.com/assets/video.mp4")
    assert cap == SourceCapability.PUBLIC_S3
    assert tier == SourceTier.TIER_0_DIRECT_CDN_S3


def test_classify_public_cdn():
    cap, tier = classify_source_url("https://d123456.cloudfront.net/out/video.mp4")
    assert cap == SourceCapability.PUBLIC_CDN
    assert tier == SourceTier.TIER_0_DIRECT_CDN_S3


def test_classify_google_drive():
    cap, tier = classify_source_url("https://drive.google.com/drive/folders/1Qb7DigWjEt-eM5ujKL3VXL0h2knwDVZx")
    assert cap == SourceCapability.GOOGLE_DRIVE_INTEGRATED
    assert tier == SourceTier.TIER_1_GOOGLE_DRIVE


def test_classify_dropbox():
    cap, tier = classify_source_url("https://www.dropbox.com/scl/fi/xyz123/video.mp4?dl=0")
    assert cap == SourceCapability.DROPBOX_PUBLIC
    assert tier == SourceTier.TIER_3_DROPBOX_PUBLIC


def test_classify_youtube():
    cap, tier = classify_source_url("https://www.youtube.com/watch?v=DsC3qLVD8_Y")
    assert cap == SourceCapability.PUBLIC_YOUTUBE_RESTRICTED
    assert tier == SourceTier.TIER_4_YOUTUBE_RESTRICTED

    cap_short, tier_short = classify_source_url("https://youtu.be/DsC3qLVD8_Y")
    assert cap_short == SourceCapability.PUBLIC_YOUTUBE_RESTRICTED
    assert tier_short == SourceTier.TIER_4_YOUTUBE_RESTRICTED


def test_dropbox_url_conversion():
    url1 = "https://www.dropbox.com/scl/fi/abc/video.mp4?dl=0"
    assert convert_dropbox_to_direct_url(url1) == "https://www.dropbox.com/scl/fi/abc/video.mp4?dl=1"

    url2 = "https://www.dropbox.com/scl/fi/abc/video.mp4"
    assert convert_dropbox_to_direct_url(url2) == "https://www.dropbox.com/scl/fi/abc/video.mp4?dl=1"

    url3 = "https://www.dropbox.com/scl/fi/abc/video.mp4?dl=1"
    assert convert_dropbox_to_direct_url(url3) == "https://www.dropbox.com/scl/fi/abc/video.mp4?dl=1"


# ------------------------------------------------------------------------------
# 2. SourceProbe Bounded Inspection Tests
# ------------------------------------------------------------------------------

def test_source_probe_google_drive():
    probe = SourceProbe()
    res = probe.probe_url("https://drive.google.com/drive/folders/1Qb7DigWjEt-eM5ujKL3VXL0h2knwDVZx")
    assert res.capability == SourceCapability.GOOGLE_DRIVE_INTEGRATED
    assert res.tier == SourceTier.TIER_1_GOOGLE_DRIVE
    assert res.is_supported_no_login is True
    assert res.requires_integrated_auth is True
    assert res.rejection_reason is None


def test_source_probe_youtube_restricted():
    probe = SourceProbe()
    res = probe.probe_url("https://www.youtube.com/watch?v=DsC3qLVD8_Y")
    assert res.capability == SourceCapability.PUBLIC_YOUTUBE_RESTRICTED
    assert res.tier == SourceTier.TIER_4_YOUTUBE_RESTRICTED
    assert res.rejection_reason == "REJECTED_CLOUD_SOURCE_UNRELIABLE"
    assert res.details.get("production_reliable") is False


def test_source_probe_direct_mp4_mocked():
    probe = SourceProbe()
    mock_resp = MagicMock()
    mock_resp.url = "https://cdn.example.com/video.mp4"
    mock_resp.status_code = 206
    mock_resp.headers = {
        "content-type": "video/mp4",
        "content-range": "bytes 0-1023/5000000",
    }
    mock_resp.content = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 1000

    with patch("httpx.Client.get", return_value=mock_resp):
        res = probe.probe_url("https://cdn.example.com/video.mp4")
        assert res.capability == SourceCapability.DIRECT_MEDIA
        assert res.tier == SourceTier.TIER_0_DIRECT_CDN_S3
        assert res.is_supported_no_login is True
        assert res.is_valid_media is True
        assert res.media_format == "mp4"
        assert res.content_length == 5000000
        assert res.supports_range is True
        assert res.rejection_reason is None


def test_source_probe_auth_required_mocked():
    probe = SourceProbe()
    mock_resp = MagicMock()
    mock_resp.url = "https://private.example.com/video.mp4"
    mock_resp.status_code = 403
    mock_resp.headers = {"content-type": "application/json"}
    mock_resp.content = b'{"error": "Access Denied"}'

    with patch("httpx.Client.get", return_value=mock_resp):
        res = probe.probe_url("https://private.example.com/video.mp4")
        assert res.capability == SourceCapability.AUTH_REQUIRED
        assert res.tier == SourceTier.TIER_5_AUTH_BLOCKED
        assert res.requires_login is True
        assert res.is_supported_no_login is False
        assert "403" in (res.rejection_reason or "")


def test_source_probe_html_masquerading_mocked():
    probe = SourceProbe()
    mock_resp = MagicMock()
    mock_resp.url = "https://example.com/watch/video.mp4"
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "text/html; charset=utf-8"}
    mock_resp.content = b"<!DOCTYPE html><html><head><title>Sign in to view</title></head></html>"

    with patch("httpx.Client.get", return_value=mock_resp):
        res = probe.probe_url("https://example.com/watch/video.mp4")
        assert res.is_valid_media is False
        assert res.is_supported_no_login is False
        assert res.rejection_reason == "HTML_PAGE_NOT_DIRECT_MEDIA"


# ------------------------------------------------------------------------------
# 3. Source Selection & Policy Tests
# ------------------------------------------------------------------------------

def test_source_selection_drive_over_youtube():
    brief = WhopCampaignBrief(
        campaign_id="camp-multi-source-1",
        title="Multi Source Campaign",
        campaign_url="https://whop.com/discover/camp-1",
        allowed_sources=[
            "https://www.youtube.com/watch?v=DsC3qLVD8_Y",
            "https://drive.google.com/drive/folders/1Qb7DigWjEt-eM5ujKL3VXL0h2knwDVZx",
        ],
        supported_platforms=["youtube"],
    )
    client = AutoClipClient(config=AutoClipConfig(dry_run=True, allow_youtube_sources=False))
    norm = client.normalize_sources(brief)
    # Drive must be selected, YouTube filtered out
    assert norm == ["https://drive.google.com/drive/folders/1Qb7DigWjEt-eM5ujKL3VXL0h2knwDVZx"]


def test_source_selection_direct_over_youtube():
    brief = WhopCampaignBrief(
        campaign_id="camp-multi-source-2",
        title="Multi Source Direct",
        campaign_url="https://whop.com/discover/camp-2",
        allowed_sources=[
            "https://www.youtube.com/watch?v=DsC3qLVD8_Y",
            "https://s3.amazonaws.com/assets/stream.mp4",
        ],
        supported_platforms=["youtube"],
    )
    client = AutoClipClient(config=AutoClipConfig(dry_run=True, allow_youtube_sources=False))
    norm = client.normalize_sources(brief)
    assert norm == ["https://s3.amazonaws.com/assets/stream.mp4"]


def test_source_selection_youtube_only_restricted():
    brief = WhopCampaignBrief(
        campaign_id="camp-yt-only",
        title="YouTube Only Campaign",
        campaign_url="https://whop.com/discover/camp-3",
        allowed_sources=["https://www.youtube.com/watch?v=DsC3qLVD8_Y"],
        supported_platforms=["youtube"],
    )
    # When allow_youtube_sources is False (default)
    client = AutoClipClient(config=AutoClipConfig(dry_run=True, allow_youtube_sources=False))
    with pytest.raises(AutoClipSourceRestrictedError) as exc_info:
        client.normalize_sources(brief)
    assert "Direct YouTube cloud rendering is restricted" in str(exc_info.value)

    # When allow_youtube_sources is explicitly True
    client_allowed = AutoClipClient(config=AutoClipConfig(dry_run=True, allow_youtube_sources=True))
    norm = client_allowed.normalize_sources(brief)
    assert norm == ["https://www.youtube.com/watch?v=DsC3qLVD8_Y"]
