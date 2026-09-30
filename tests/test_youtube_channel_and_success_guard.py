"""Comprehensive test suite for AL AMR YouTube Publication Success Definition,
Channel Identity Guard, Incomplete Upload Protection, Visibility Enforcement,
and Telegram Status Mapping.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autoclip import config, paths
from autoclip.db import models, store
from autoclip.publishing.base import PublishingMetadata, PublishingResult
from autoclip.publishing.service import PublishingService
from autoclip.publishing.youtube import (
    YouTubePublisher,
    classify_youtube_error,
    resolve_expected_youtube_channel_id,
    validate_youtube_credentials,
)
from autoclip.telegram.review_bot import _execute_auto_publish


@pytest.fixture
def clean_env():
    tmp = tempfile.mkdtemp(prefix="alamr-yt-guard-test-")
    old_home = os.environ.get("AUTOCLIP_HOME")
    old_master = os.environ.get("AL_AMR_MASTER_KEY")
    old_ch = os.environ.get("AL_AMR_YOUTUBE_CHANNEL_ID")
    os.environ["AUTOCLIP_HOME"] = tmp
    os.environ["AL_AMR_MASTER_KEY"] = "test-master-key-alamr-pub-12345"
    if "AL_AMR_YOUTUBE_CHANNEL_ID" in os.environ:
        del os.environ["AL_AMR_YOUTUBE_CHANNEL_ID"]

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
    if old_ch is not None:
        os.environ["AL_AMR_YOUTUBE_CHANNEL_ID"] = old_ch
    else:
        os.environ.pop("AL_AMR_YOUTUBE_CHANNEL_ID", None)
    shutil.rmtree(tmp, ignore_errors=True)


class TestYouTubeChannelIdentityGuard:

    def test_resolve_expected_channel_id(self, clean_env):
        # 1. Not explicitly configured → falls back to the authoritative Future Founders channel ID
        resolved = resolve_expected_youtube_channel_id()
        assert resolved == "UCtaOzeFW2kEOexMoSNA75tA"  # Authoritative AL AMR hardcoded fallback

        # 2. Vault configuration overrides hardcoded fallback
        config.set_secret("al_amr_youtube_channel_id", "UC_VAULT_CHANNEL")
        assert resolve_expected_youtube_channel_id() == "UC_VAULT_CHANNEL"

        # 3. Env variable is used when no vault secret is set (vault takes precedence over env)
        config.delete_secret("al_amr_youtube_channel_id")
        with patch.dict(os.environ, {"AL_AMR_YOUTUBE_CHANNEL_ID": "UC_ENV_CHANNEL"}):
            assert resolve_expected_youtube_channel_id() == "UC_ENV_CHANNEL"

        # 4. Explicit override always wins
        assert resolve_expected_youtube_channel_id(explicit="UC_EXPLICIT") == "UC_EXPLICIT"

    @pytest.mark.asyncio
    async def test_channel_identity_guard_blocks_mismatched_channel(self, clean_env, tmp_path):
        """Authenticated channel 'Forgotten Files' (UCeH6er) != Expected 'AL AMR' (UC_AL_AMR) -> HARD STOP."""
        vid_file = tmp_path / "clip.mp4"
        vid_file.write_bytes(b"\x00" * 1024)

        pub = YouTubePublisher(client_id="cid", client_secret="sec", refresh_token="tok")
        meta = PublishingMetadata(title="Test Highlight")

        mock_build = MagicMock()
        mock_channels = MagicMock()
        mock_ch_list = MagicMock()
        mock_ch_list.execute.return_value = {
            "items": [{"id": "UCeH6er-cVAIwZd_9EvehmPw", "snippet": {"title": "Forgotten Files"}}]
        }
        mock_channels.list.return_value = mock_ch_list
        mock_build.return_value.channels.return_value = mock_channels

        mock_videos = MagicMock()
        mock_build.return_value.videos.return_value = mock_videos

        with patch.dict(os.environ, {"YOUTUBE_PUBLISH_LIVE": "true", "AL_AMR_YOUTUBE_CHANNEL_ID": "UC_AL_AMR_OFFICIAL"}), \
             patch.object(pub, "_get_credentials", return_value=MagicMock()), \
             patch("google.auth.transport.requests.Request"), \
             patch("googleapiclient.discovery.build", mock_build):

            result = await pub.publish(vid_file, meta, dry_run=False)

            # Verification: HARD STOP
            assert result.success is False
            assert result.status == "failed"
            assert result.error_code == "channel_mismatch"
            assert "channel mismatch" in result.error.lower()
            assert "Forgotten Files" in result.error
            assert "UC_AL_AMR_OFFICIAL" in result.error
            # Videos insert was NOT called
            mock_videos.insert.assert_not_called()

    @pytest.mark.asyncio
    async def test_channel_identity_guard_blocks_when_unconfigured(self, clean_env, tmp_path):
        """If expected channel is not explicitly configured, the system uses the Future Founders fallback.
        Any mismatch against the fallback is still a hard stop channel_mismatch error."""
        vid_file = tmp_path / "clip.mp4"
        vid_file.write_bytes(b"\x00" * 1024)

        pub = YouTubePublisher(client_id="cid", client_secret="sec", refresh_token="tok")
        meta = PublishingMetadata(title="Test Highlight")

        mock_build = MagicMock()
        mock_channels = MagicMock()
        mock_ch_list = MagicMock()
        mock_ch_list.execute.return_value = {
            "items": [{"id": "UCeH6er-cVAIwZd_9EvehmPw", "snippet": {"title": "Forgotten Files"}}]
        }
        mock_channels.list.return_value = mock_ch_list
        mock_build.return_value.channels.return_value = mock_channels

        mock_videos = MagicMock()
        mock_build.return_value.videos.return_value = mock_videos

        with patch.dict(os.environ, {"YOUTUBE_PUBLISH_LIVE": "true"}, clear=False), \
             patch.object(pub, "_get_credentials", return_value=MagicMock()), \
             patch("google.auth.transport.requests.Request"), \
             patch("googleapiclient.discovery.build", mock_build):

            result = await pub.publish(vid_file, meta, dry_run=False)

            assert result.success is False
            assert result.status == "failed"
            assert result.error_code == "channel_mismatch"
            # System uses authoritative fallback (Future Founders), mismatch blocked
            assert "channel mismatch" in result.error.lower()
            assert "UCeH6er-cVAIwZd_9EvehmPw".lower() in result.error.lower() or "Forgotten Files" in result.error
            mock_videos.insert.assert_not_called()


class TestYouTubePostUploadVerification:

    @pytest.mark.asyncio
    async def test_post_upload_verification_rejects_unlisted(self, clean_env, tmp_path):
        """If uploaded video ends up unlisted and cannot be corrected, do not mark PUBLISHED."""
        vid_file = tmp_path / "clip.mp4"
        vid_file.write_bytes(b"\x00" * 1024)

        pub = YouTubePublisher(client_id="cid", client_secret="sec", refresh_token="tok")
        meta = PublishingMetadata(title="Test Highlight")

        mock_build = MagicMock()
        mock_channels = MagicMock()
        mock_ch_list = MagicMock()
        mock_ch_list.execute.return_value = {
            "items": [{"id": "UC_AL_AMR_CORRECT", "snippet": {"title": "AL AMR Official"}}]
        }
        mock_channels.list.return_value = mock_ch_list
        mock_build.return_value.channels.return_value = mock_channels

        mock_videos = MagicMock()
        mock_insert = MagicMock()
        mock_insert.execute.return_value = {"id": "yt_unlisted_vid", "status": {"uploadStatus": "uploaded"}}
        mock_videos.insert.return_value = mock_insert

        # Post-upload verification returns unlisted
        mock_vid_list = MagicMock()
        mock_vid_list.execute.return_value = {
            "items": [
                {
                    "id": "yt_unlisted_vid",
                    "snippet": {"channelId": "UC_AL_AMR_CORRECT", "channelTitle": "AL AMR Official", "title": "Test Highlight #Shorts"},
                    "status": {"uploadStatus": "processed", "privacyStatus": "unlisted"},
                    "contentDetails": {"duration": "PT25S"},
                }
            ]
        }
        mock_videos.list.return_value = mock_vid_list
        mock_videos.update.side_effect = Exception("Status update forbidden")
        mock_build.return_value.videos.return_value = mock_videos

        with patch.dict(os.environ, {"YOUTUBE_PUBLISH_LIVE": "true", "AL_AMR_YOUTUBE_CHANNEL_ID": "UC_AL_AMR_CORRECT"}), \
             patch.object(pub, "_get_credentials", return_value=MagicMock()), \
             patch("google.auth.transport.requests.Request"), \
             patch("googleapiclient.discovery.build", mock_build):

            result = await pub.publish(vid_file, meta, dry_run=False)

            assert result.success is False
            assert result.status == "failed"
            assert result.error_code == "visibility_incorrect"
            assert "unlisted" in result.error.lower()

    @pytest.mark.asyncio
    async def test_incomplete_upload_protection(self, clean_env, tmp_path):
        """If YouTube is still processing after timeout, report incomplete rather than Published."""
        vid_file = tmp_path / "clip.mp4"
        vid_file.write_bytes(b"\x00" * 1024)

        pub = YouTubePublisher(client_id="cid", client_secret="sec", refresh_token="tok")
        meta = PublishingMetadata(title="Test Highlight")

        mock_build = MagicMock()
        mock_channels = MagicMock()
        mock_ch_list = MagicMock()
        mock_ch_list.execute.return_value = {
            "items": [{"id": "UC_AL_AMR_CORRECT", "snippet": {"title": "AL AMR Official"}}]
        }
        mock_channels.list.return_value = mock_ch_list
        mock_build.return_value.channels.return_value = mock_channels

        mock_videos = MagicMock()
        mock_insert = MagicMock()
        mock_insert.execute.return_value = {"id": "yt_proc_vid", "status": {"uploadStatus": "uploaded"}}
        mock_videos.insert.return_value = mock_insert

        # Videos list returns 'processing'
        mock_vid_list = MagicMock()
        mock_vid_list.execute.return_value = {
            "items": [
                {
                    "id": "yt_proc_vid",
                    "snippet": {"channelId": "UC_AL_AMR_CORRECT", "channelTitle": "AL AMR Official"},
                    "status": {"uploadStatus": "processing", "privacyStatus": "public"},
                    "contentDetails": {"duration": "PT25S"},
                }
            ]
        }
        mock_videos.list.return_value = mock_vid_list
        mock_build.return_value.videos.return_value = mock_videos

        # Fast timeout to simulate wait limit reached
        with patch.dict(os.environ, {"YOUTUBE_PUBLISH_LIVE": "true", "AL_AMR_YOUTUBE_CHANNEL_ID": "UC_AL_AMR_CORRECT"}), \
             patch.object(pub, "_get_credentials", return_value=MagicMock()), \
             patch("google.auth.transport.requests.Request"), \
             patch("googleapiclient.discovery.build", mock_build), \
             patch.object(pub, "verify_video_publication", new_callable=AsyncMock) as mock_ver:

            mock_ver.return_value = (False, "processing_incomplete", {"uploadStatus": "processing", "channelId": "UC_AL_AMR_CORRECT"})

            result = await pub.publish(vid_file, meta, dry_run=False)

            assert result.success is False
            assert result.status == "ready_for_upload"
            assert result.error_code == "processing_incomplete"
            assert result.retryable is True
            assert "processing incomplete" in result.error.lower()


class TestTelegramStatusAndPartialPublishing:

    @pytest.mark.asyncio
    async def test_telegram_channel_mismatch_status(self, clean_env):
        """Telegram card must report '❌ Wrong channel — publication blocked' on channel mismatch."""
        yt_rec = models.PublicationRecord(
            id=models.new_id(),
            job_id="j1",
            clip_id="c1",
            platform="youtube",
            destination_id="dest-youtube-main",
            idempotency_key="j1:c1:youtube:dest-youtube-main",
            status="FAILED_PERMANENT",
            error_code="channel_mismatch",
            error_message="YouTube channel mismatch: expected UC_AL_AMR",
        )
        ig_rec = models.PublicationRecord(
            id=models.new_id(),
            job_id="j1",
            clip_id="c1",
            platform="instagram",
            destination_id="dest-instagram-main",
            idempotency_key="j1:c1:instagram:dest-instagram-main",
            status="FAILED_PERMANENT",
            error_code="authentication_error",
            error_message="Token expired",
        )

        with patch.object(PublishingService, "publish_clip", side_effect=[yt_rec, ig_rec]), \
             patch("autoclip.telegram.review_bot._safe_edit_telegram_message", new_callable=AsyncMock) as mock_edit, \
             patch("autoclip.telegram.review_bot._safe_send_telegram_message", new_callable=AsyncMock) as mock_send:

            res = await _execute_auto_publish(
                job_id="j1",
                clip_id="c1",
                bot_token="tok",
                chat_id="123",
                message_id=456,
            )

            # Overall status must be publish_failed, NOT partially_published
            assert res["status"] == "publish_failed"
            # Edited message must contain the exact mismatch message
            last_edit_call = mock_edit.call_args_list[-1]
            card_text = last_edit_call.kwargs.get("text", "")
            assert "Wrong channel — publication blocked" in card_text
            assert "❌ *PUBLISHING FAILED*" in card_text

    @pytest.mark.asyncio
    async def test_partial_publishing_rule_unlisted_not_partial(self, clean_env):
        """If YouTube is unlisted/failed and Instagram failed, result MUST NOT be PARTIALLY_PUBLISHED."""
        yt_rec = models.PublicationRecord(
            id=models.new_id(),
            job_id="j1",
            clip_id="c1",
            platform="youtube",
            destination_id="dest-youtube-main",
            idempotency_key="j1:c1:youtube:dest-youtube-main",
            status="FAILED_PERMANENT",
            error_code="visibility_incorrect",
            error_message="YouTube video visibility is 'unlisted' rather than 'public'",
        )
        ig_rec = models.PublicationRecord(
            id=models.new_id(),
            job_id="j1",
            clip_id="c1",
            platform="instagram",
            destination_id="dest-instagram-main",
            idempotency_key="j1:c1:instagram:dest-instagram-main",
            status="FAILED_PERMANENT",
            error_code="authentication_error",
            error_message="Token expired",
        )

        with patch.object(PublishingService, "publish_clip", side_effect=[yt_rec, ig_rec]), \
             patch("autoclip.telegram.review_bot._safe_edit_telegram_message", new_callable=AsyncMock) as mock_edit, \
             patch("autoclip.telegram.review_bot._safe_send_telegram_message", new_callable=AsyncMock) as mock_send:

            res = await _execute_auto_publish(
                job_id="j1",
                clip_id="c1",
                bot_token="tok",
                chat_id="123",
                message_id=456,
            )

            assert res["status"] == "publish_failed"
            last_edit_call = mock_edit.call_args_list[-1]
            card_text = last_edit_call.kwargs.get("text", "")
            assert "Visibility is unlisted" in card_text
            assert "❌ *PUBLISHING FAILED*" in card_text

    @pytest.mark.asyncio
    async def test_partial_publishing_rule_true_partial(self, clean_env):
        """If YouTube is PUBLISHED (verified public on expected channel) and Instagram failed -> PARTIALLY_PUBLISHED."""
        yt_rec = models.PublicationRecord(
            id=models.new_id(),
            job_id="j1",
            clip_id="c1",
            platform="youtube",
            destination_id="dest-youtube-main",
            idempotency_key="j1:c1:youtube:dest-youtube-main",
            status="PUBLISHED",
            permalink="https://youtube.com/shorts/yt_ok_123",
            response_metadata={"channel_title": "AL AMR Official", "verification_status": "VERIFIED_PUBLIC"},
        )
        ig_rec = models.PublicationRecord(
            id=models.new_id(),
            job_id="j1",
            clip_id="c1",
            platform="instagram",
            destination_id="dest-instagram-main",
            idempotency_key="j1:c1:instagram:dest-instagram-main",
            status="FAILED_PERMANENT",
            error_code="authentication_error",
            error_message="Token expired",
        )

        with patch.object(PublishingService, "publish_clip", side_effect=[yt_rec, ig_rec]), \
             patch("autoclip.telegram.review_bot._safe_edit_telegram_message", new_callable=AsyncMock) as mock_edit, \
             patch("autoclip.telegram.review_bot._safe_send_telegram_message", new_callable=AsyncMock) as mock_send:

            res = await _execute_auto_publish(
                job_id="j1",
                clip_id="c1",
                bot_token="tok",
                chat_id="123",
                message_id=456,
            )

            assert res["status"] == "partially_published"
            last_edit_call = mock_edit.call_args_list[-1]
            card_text = last_edit_call.kwargs.get("text", "")
            assert "Published (AL AMR Official)" in card_text
            assert "⚠️ *PARTIALLY PUBLISHED*" in card_text
