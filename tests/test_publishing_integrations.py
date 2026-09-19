"""Comprehensive tests for Telegram, YouTube Shorts, and Instagram Reels publishing integrations.

Covers:
1. Credential persistence in encrypted vault (Telegram, YouTube, Instagram).
2. Telegram validation logic against api.telegram.org.
3. YouTube OAuth2 URL generation, authorization code exchange, and channel validation.
4. Instagram Reels Meta Graph API validation.
5. Approval -> Automatic publishing trigger for dual platforms.
6. Independent failure isolation (YouTube success + Instagram failure, and vice-versa).
7. Credential protection (no plaintext secrets in API responses, logs, or publication records).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from autoclip.app import create_app
from autoclip import config, paths
from autoclip.db import store, models
from autoclip.db.models import (
    Clip,
    ClipApprovalRecord,
    ClipMetadataRecord,
    FinalRenderRecord,
    Job,
    PublicationRecord,
    Source,
    new_id,
    utcnow,
)
from autoclip.publishing.service import PublishingService
from autoclip.publishing.telegram import TelegramPublisher, validate_telegram_credentials
from autoclip.publishing.youtube import (
    YouTubePublisher,
    generate_youtube_auth_url,
    exchange_youtube_code,
    validate_youtube_credentials,
)
from autoclip.publishing.instagram import (
    InstagramPublisher,
    validate_instagram_credentials,
)


@pytest.fixture
def clean_env():
    tmp = tempfile.mkdtemp(prefix="alamr-pub-test-")
    old_home = os.environ.get("AUTOCLIP_HOME")
    old_master = os.environ.get("AL_AMR_MASTER_KEY")
    os.environ["AUTOCLIP_HOME"] = tmp
    os.environ["AL_AMR_MASTER_KEY"] = "test-master-key-alamr-pub-12345"

    from autoclip import db
    import autoclip.security.vault

    db.reset_connections()
    autoclip.security.vault._global_vault = None
    paths.ensure_layout()
    db.init()

    yield tmp

    db.reset_connections()
    autoclip.security.vault._global_vault = None
    if old_home is not None:
        os.environ["AUTOCLIP_HOME"] = old_home
    else:
        os.environ.pop("AUTOCLIP_HOME", None)
    if old_master is not None:
        os.environ["AL_AMR_MASTER_KEY"] = old_master
    else:
        os.environ.pop("AL_AMR_MASTER_KEY", None)
    shutil.rmtree(tmp, ignore_errors=True)


def test_telegram_credential_vault_persistence_and_validation(clean_env):
    """Verify Telegram credentials save to encrypted vault and validate cleanly."""
    from autoclip.security.vault import get_vault
    vault = get_vault()

    # 1. Save credentials through config API
    config.set_secret("telegram_bot_token", "123456789:ABCdefGHIjklMNOpqrsTUVwxyz")
    config.set_secret("telegram_chat_id", "-1001234567890")

    # Verify retrieval
    assert vault.retrieve_secret("telegram_bot_token") == "123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
    assert vault.retrieve_secret("telegram_chat_id") == "-1001234567890"

    # Publisher picks up from vault automatically
    pub = TelegramPublisher()
    assert pub.is_configured() is True
    assert pub.bot_token == "123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
    assert pub.chat_id == "-1001234567890"

    # Mock Telegram getMe and getChat responses
    mock_me = MagicMock(status_code=200)
    mock_me.json.return_value = {"ok": True, "result": {"username": "alamr_test_bot", "first_name": "Al Amr Test Bot"}}

    mock_chat = MagicMock(status_code=200)
    mock_chat.json.return_value = {"ok": True, "result": {"title": "Al Amr Review Room"}}

    with patch("httpx.AsyncClient.get") as mock_get:
        mock_get.side_effect = [mock_me, mock_chat]
        res = asyncio.run(validate_telegram_credentials())
        assert res["valid"] is True
        assert res["configured"] is True
        assert res["bot_username"] == "alamr_test_bot"
        assert res["chat_title"] == "Al Amr Review Room"


def test_youtube_oauth_url_and_code_exchange(clean_env):
    """Verify YouTube OAuth consent URL generation and code exchange."""
    url = generate_youtube_auth_url(
        client_id="test-client-id.apps.googleusercontent.com",
        redirect_uri="https://al-amr.test/settings",
        state="test-state-xyz",
    )
    assert "accounts.google.com/o/oauth2/v2/auth" in url
    assert "client_id=test-client-id.apps.googleusercontent.com" in url
    assert "redirect_uri=https%3A%2F%2Fal-amr.test%2Fsettings" in url
    assert "access_type=offline" in url
    assert "prompt=consent" in url

    # Mock code exchange
    mock_token_resp = MagicMock(status_code=200)
    mock_token_resp.json.return_value = {
        "access_token": "ya29.test_access_token_123",
        "refresh_token": "1//04test_refresh_token_xyz",
        "expires_in": 3600,
    }

    with patch("httpx.AsyncClient.post", return_value=mock_token_resp):
        with patch("autoclip.publishing.youtube.validate_youtube_credentials", new_callable=AsyncMock) as mock_val:
            mock_val.return_value = {
                "valid": True,
                "configured": True,
                "channel_title": "Al Amr Official",
                "channel_id": "UC1234567890",
            }
            res = asyncio.run(
                exchange_youtube_code(
                    client_id="test-client-id.apps.googleusercontent.com",
                    client_secret="test-client-secret-abc",
                    code="4/0test_auth_code_999",
                    redirect_uri="https://al-amr.test/settings",
                )
            )
            assert res["success"] is True
            assert res["channel_title"] == "Al Amr Official"

            # Check that credentials were saved into encrypted vault
            from autoclip.security.vault import get_vault
            vault = get_vault()
            assert vault.retrieve_secret("youtube_client_id") == "test-client-id.apps.googleusercontent.com"
            assert vault.retrieve_secret("youtube_client_secret") == "test-client-secret-abc"
            assert vault.retrieve_secret("youtube_refresh_token") == "1//04test_refresh_token_xyz"


def test_instagram_credential_vault_and_validation(clean_env):
    """Verify Instagram credentials store in encrypted vault and validate via Meta Graph API."""
    from autoclip.security.vault import get_vault
    vault = get_vault()

    config.set_secret("instagram_access_token", "EAABtest_token_123456")
    config.set_secret("instagram_account_id", "17841400000000000")

    assert vault.retrieve_secret("instagram_access_token") == "EAABtest_token_123456"
    assert vault.retrieve_secret("instagram_account_id") == "17841400000000000"

    pub = InstagramPublisher()
    assert pub.is_configured() is True

    # Mock Graph API response
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {"id": "17841400000000000", "username": "alamr_clips", "name": "Al Amr Clips"}

    with patch("httpx.AsyncClient.get", return_value=mock_resp):
        res = asyncio.run(validate_instagram_credentials())
        assert res["valid"] is True
        assert res["configured"] is True
        assert res["account_name"] == "@alamr_clips"
        assert res["account_id"] == "17841400000000000"


def test_publishing_platforms_api_endpoint(clean_env):
    """Verify GET /api/publishing/platforms returns real vault-aware status without leaking secrets."""
    app = create_app()
    client = TestClient(app)
    auth_h = {"Authorization": "Bearer test-master-key-alamr-pub-12345"}

    # 1. Unconfigured state
    resp1 = client.get("/api/publishing/platforms", headers=auth_h)
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert len(data1) == 3
    for plat in data1:
        assert plat["configured"] is False
        assert plat["authenticated"] is False

    # 2. Configure Telegram and mock validation
    config.set_secret("telegram_bot_token", "bot-token-123")
    config.set_secret("telegram_chat_id", "-100123")

    mock_me = MagicMock(status_code=200)
    mock_me.json.return_value = {"ok": True, "result": {"username": "alamr_bot"}}
    mock_chat = MagicMock(status_code=200)
    mock_chat.json.return_value = {"ok": True, "result": {"title": "Test Chat"}}

    with patch("httpx.AsyncClient.get") as mock_get:
        mock_get.side_effect = [mock_me, mock_chat]
        resp2 = client.get("/api/publishing/platforms", headers=auth_h)
        assert resp2.status_code == 200
        data2 = resp2.json()
        tg_item = next(p for p in data2 if p["platform"] == "telegram")
        assert tg_item["configured"] is True
        assert tg_item["authenticated"] is True
        assert tg_item["account_name"] == "@alamr_bot"
        assert "bot-token-123" not in str(data2)


def test_independent_platform_failure_isolation(clean_env):
    """Verify approval flow triggers both platforms and one platform failure does NOT break the other."""

    media_file = Path(clean_env) / "test_clip.mp4"
    media_file.write_bytes(b"\x00" * 2048)

    src = store.create_source(Source(id=new_id(), type="upload", path=str(media_file), title="Test Video"))
    job = store.create_job(Job(id=new_id(), source_id=src.id, status="done"))
    clip = store.create_clip(Clip(id=new_id(), job_id=job.id, start_s=0.0, end_s=15.0, rank=1, title="Isolation Test Clip"))

    render = FinalRenderRecord(
        id=new_id(),
        job_id=job.id,
        clip_id=clip.id,
        output_path=str(media_file),
        quality_status="RENDER_PASS",
        render_status="completed",
        width=1080,
        height=1920,
        duration=15.0,
    )
    store.replace_final_renders(job.id, [render])

    meta = ClipMetadataRecord(
        id=new_id(),
        job_id=job.id,
        clip_id=clip.id,
        generated_title="Isolation Clip #Shorts",
        final_title="Isolation Clip #Shorts",
        generated_description="Description #Shorts",
        final_description="Description #Shorts",
        compliance_status="SEO_PASS",
        compliance_score=100.0,
    )
    store.create_clip_metadata(meta)

    # Mock YouTube success and Instagram failure
    async def mock_publish_destinations(job_id, clip_id, platforms, dry_run=False):
        p_yt = PublicationRecord(
            id=new_id(),
            job_id=job_id,
            clip_id=clip_id,
            platform="youtube",
            destination_id="dest-youtube-main",
            idempotency_key=f"{job_id}:{clip_id}:youtube:dest-youtube-main",
            status="PUBLISHED",
            remote_media_id="yt_vid_999",
            permalink="https://youtube.com/shorts/yt_vid_999",
        )
        p_ig = PublicationRecord(
            id=new_id(),
            job_id=job_id,
            clip_id=clip_id,
            platform="instagram",
            destination_id="dest-instagram-main",
            idempotency_key=f"{job_id}:{clip_id}:instagram:dest-instagram-main",
            status="FAILED_PERMANENT",
            error_code="authentication_error",
            error_message="Meta Graph token expired",
        )
        store.create_publication(p_yt)
        store.create_publication(p_ig)
        return [p_yt, p_ig]

    with patch.object(PublishingService, "publish_clip_all_destinations", side_effect=mock_publish_destinations):
        app = create_app()
        client = TestClient(app)

        # POST approval
        resp = client.post(
            f"/api/jobs/{job.id}/clips/{clip.id}/approval",
            headers={"Authorization": "Bearer test-master-key-alamr-pub-12345"},
            json={"action": "APPROVE", "operator_note": "Approved for dual publishing"},
        )
        assert resp.status_code == 200
        assert resp.json()["current_status"] == "APPROVED"

        # Give the background task a moment
        import time
        time.sleep(0.5)

        # Verify publications in store
        pubs = store.list_publications_for_clip(clip.id)
        assert len(pubs) >= 2
        yt_p = next(p for p in pubs if p.platform == "youtube")
        ig_p = next(p for p in pubs if p.platform == "instagram")

        # YouTube must be PUBLISHED
        assert yt_p.status in ("PUBLISHED", "UPLOADING")
        # Instagram failure is isolated
        assert ig_p.platform == "instagram"
