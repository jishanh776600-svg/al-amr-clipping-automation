"""Tests for TikTok publishing adapter and Instagram account management."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autoclip.publishing.base import PublishingMetadata, PublishingResult
from autoclip.publishing.instagram import InstagramPublisher, discover_instagram_accounts
from autoclip.publishing.orchestrator import PublishingOrchestrator
from autoclip.publishing.service import PublishingService
from autoclip.publishing.tiktok import (
    TikTokPublisher,
    classify_tiktok_error,
    validate_tiktok_credentials,
)


# ===========================================================================
# 1. TikTok Publisher Tests
# ===========================================================================

def test_tiktok_publisher_configuration_detection(monkeypatch):
    """Verify TikTokPublisher correctly detects configured state."""
    monkeypatch.delenv("TIKTOK_ACCESS_TOKEN", raising=False)
    pub = TikTokPublisher(access_token="")
    assert not pub.is_configured()

    pub_configured = TikTokPublisher(access_token="act_test_tiktok_123")
    assert pub_configured.is_configured()


def test_tiktok_error_classification():
    """Verify classification of various TikTok API responses."""
    code, retryable = classify_tiktok_error(429, "rate_limit_exceeded", "Too many requests")
    assert code == "rate_limit"
    assert retryable is True

    code, retryable = classify_tiktok_error(401, "access_token_invalid", "Invalid access token")
    assert code == "authentication_error"
    assert retryable is False

    code, retryable = classify_tiktok_error(403, "scope_not_authorized", "Scope video.publish missing")
    assert code == "permission_error"
    assert retryable is False

    code, retryable = classify_tiktok_error(400, "video_format_invalid", "Invalid video container")
    assert code == "invalid_media"
    assert retryable is False

    code, retryable = classify_tiktok_error(500, "server_error", "Internal server error")
    assert code == "platform_error"
    assert retryable is True


def test_tiktok_caption_formatting():
    """Verify TikTok caption formatting and 2200 character ceiling."""
    pub = TikTokPublisher(access_token="test_tok")
    meta = PublishingMetadata(
        title="Check out this viral moment!",
        description="Full breakdown of the high-speed strategy.",
        tags=["shorts", "gaming", "viral"],
    )
    caption = pub.format_caption(meta)
    assert "Check out this viral moment!" in caption
    assert "Full breakdown of the high-speed strategy." in caption
    assert "#shorts #gaming #viral" in caption
    assert len(caption) <= 2200

    # Long text truncation
    long_meta = PublishingMetadata(
        title="A" * 3000,
        tags=["tiktok"],
    )
    long_caption = pub.format_caption(long_meta)
    assert len(long_caption) <= 2200
    assert long_caption.endswith("...")


@pytest.mark.asyncio
async def test_tiktok_rejects_invalid_media(tmp_path):
    """TikTokPublisher rejects non-existent or corrupted files with invalid_media."""
    fake_file = tmp_path / "corrupt.mp4"
    fake_file.write_text("<!DOCTYPE html><html><body>Error</body></html>")

    pub = TikTokPublisher(access_token="test_tok")
    meta = PublishingMetadata(title="Test title")

    result = await pub.publish(media_path=fake_file, metadata=meta)
    assert not result.success
    assert result.error_code == "invalid_media"
    assert "not a valid MP4" in result.error


@pytest.mark.asyncio
async def test_tiktok_dry_run_mode(tmp_path, monkeypatch):
    """Verify dry-run mode returns ready_for_upload without network requests."""
    # Create valid mock mp4 bytes (ftyp + moov)
    valid_mp4 = tmp_path / "test_valid.mp4"
    valid_mp4.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00" + b"\x00" * 60000 + b"moov" + b"\x00" * 100)

    monkeypatch.setenv("TIKTOK_DRY_RUN", "true")
    pub = TikTokPublisher(access_token="test_tok")
    meta = PublishingMetadata(title="Dry Run Title")

    result = await pub.publish(media_path=valid_mp4, metadata=meta, dry_run=True)
    assert result.success
    assert result.status == "ready_for_upload"
    assert result.platform == "tiktok"
    assert "dry_run" in result.details


# ===========================================================================
# 2. Instagram Discovery and Dynamic Resolution Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_discover_instagram_accounts_empty_token():
    """Verify discover_instagram_accounts returns [] if token is missing."""
    accounts = await discover_instagram_accounts(access_token="")
    assert accounts == []


@pytest.mark.asyncio
async def test_discover_instagram_accounts_parsing():
    """Verify parsing of Meta Graph API /me/accounts response."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "data": [
            {
                "id": "page_111",
                "name": "My Business Page",
                "instagram_business_account": {
                    "id": "17841499999999999",
                    "username": "my_new_instagram",
                    "name": "New Brand Account",
                    "profile_picture_url": "https://example.com/pic.jpg",
                },
            },
            {
                "id": "page_222",
                "name": "Page Without Instagram",
            },
        ]
    }

    with patch("httpx.AsyncClient.get", return_value=mock_response):
        accounts = await discover_instagram_accounts(access_token="EAABtesttoken")
        assert len(accounts) == 1
        assert accounts[0]["account_id"] == "17841499999999999"
        assert accounts[0]["username"] == "my_new_instagram"
        assert accounts[0]["page_name"] == "My Business Page"


def test_instagram_publisher_prioritizes_configured_account():
    """Verify InstagramPublisher uses explicitly provided account_id over fallbacks."""
    pub = InstagramPublisher(
        access_token="test_tok",
        account_id="99887766554433221",
    )
    assert pub.account_id == "99887766554433221"


# ===========================================================================
# 3. Publishing Service & Orchestrator Integration Tests
# ===========================================================================

def test_publishing_service_includes_tiktok():
    """Verify PublishingService has TikTok adapter registered."""
    service = PublishingService()
    assert "tiktok" in service.adapters
    assert isinstance(service.get_adapter("tiktok"), TikTokPublisher)


def test_orchestrator_creates_tiktok_destination():
    """Verify PublishingOrchestrator registers default destination for TikTok."""
    orchestrator = PublishingOrchestrator()
    destinations = orchestrator.ensure_default_destinations()
    platforms = [d.platform for d in destinations]
    assert "tiktok" in platforms
    assert "youtube" in platforms
    assert "instagram" in platforms
    assert "telegram" in platforms
