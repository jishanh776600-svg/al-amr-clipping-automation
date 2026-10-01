"""Comprehensive test suite for Media Guard, MP4 Binary Validation,
Corrupted Upload Prevention, and Honest YouTube/Instagram Publishing Status.
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

from autoclip import paths
from autoclip.app import create_app
from autoclip.db import models, store
from autoclip.media_guard import is_valid_mp4, materialize_valid_clip_media
from autoclip.publishing.base import PublishingMetadata
from autoclip.publishing.instagram import InstagramPublisher
from autoclip.publishing.service import PublishingService
from autoclip.publishing.youtube import YouTubePublisher


@pytest.fixture
def clean_test_env():
    tmp = tempfile.mkdtemp(prefix="alamr-media-guard-test-")
    old_home = os.environ.get("AUTOCLIP_HOME")
    os.environ["AUTOCLIP_HOME"] = tmp

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
    shutil.rmtree(tmp, ignore_errors=True)


class TestMediaBinaryGuard:

    def test_is_valid_mp4_rejects_non_media(self, clean_test_env, tmp_path):
        # 1. Rejects None and empty
        assert not is_valid_mp4(None)
        assert not is_valid_mp4(b"")

        # 2. Rejects HTML error pages (e.g. Google Drive 404 / access denied)
        drive_html = b'<html lang="en"><head><title>404 Not Found</title></head><body>File not found</body></html>' * 20
        assert not is_valid_mp4(drive_html)

        html_file = tmp_path / "fake.mp4"
        html_file.write_bytes(drive_html)
        assert not is_valid_mp4(html_file)

        # 3. Rejects JSON error responses
        json_err = b'{"error": {"code": 404, "message": "File not found"}}' * 50
        assert not is_valid_mp4(json_err)

        # 4. Rejects truncated / zero byte stubs
        small_file = tmp_path / "small.mp4"
        small_file.write_bytes(b"\x00\x00\x00\x1cftypisom" + b"\x00" * 100)
        assert not is_valid_mp4(small_file)

    def test_is_valid_mp4_accepts_valid_mp4(self, clean_test_env, tmp_path):
        # Valid MP4 header (ftyp atom in first 64 bytes and size >= 50KB)
        valid_bytes = b"\x00\x00\x00\x1cftypisom\x00\x00\x02\x00" + b"\x00" * 60_000
        assert is_valid_mp4(valid_bytes)

        valid_file = tmp_path / "valid.mp4"
        valid_file.write_bytes(valid_bytes)
        assert is_valid_mp4(valid_file)


class TestUploadCorruptionPrevention:

    @pytest.mark.asyncio
    async def test_youtube_publisher_blocks_html_file_upload(self, clean_test_env, tmp_path):
        """YouTube publisher must never send an HTML file to the videos().insert API."""
        corrupt_file = tmp_path / "clip_corrupt.mp4"
        corrupt_file.write_bytes(b'<!DOCTYPE html><html><head><title>Google Drive Error</title></head><body>404</body></html>' * 30)

        pub = YouTubePublisher(client_id="cid", client_secret="sec", refresh_token="tok")
        meta = PublishingMetadata(title="Test Short")

        mock_build = MagicMock()
        mock_channels = MagicMock()
        mock_ch_list = MagicMock()
        mock_ch_list.execute.return_value = {
            "items": [{"id": "UC_AL_AMR_CORRECT", "snippet": {"title": "AL AMR Official"}}]
        }
        mock_channels.list.return_value = mock_ch_list
        mock_build.return_value.channels.return_value = mock_channels

        mock_videos = MagicMock()
        mock_build.return_value.videos.return_value = mock_videos

        with patch.dict(os.environ, {"YOUTUBE_PUBLISH_LIVE": "true", "AL_AMR_YOUTUBE_CHANNEL_ID": "UC_AL_AMR_CORRECT"}), \
             patch.object(pub, "_get_credentials", return_value=MagicMock()), \
             patch("google.auth.transport.requests.Request"), \
             patch("googleapiclient.discovery.build", mock_build):

            result = await pub.publish(corrupt_file, meta, dry_run=False)

            # Blocked before upload!
            assert result.success is False
            assert result.status == "failed"
            assert result.error_code == "invalid_media"
            assert "invalid or corrupted" in result.error
            mock_videos.insert.assert_not_called()

    @pytest.mark.asyncio
    async def test_instagram_publisher_blocks_html_file_upload(self, clean_test_env, tmp_path):
        """Instagram publisher must never attempt container creation for corrupt media."""
        corrupt_file = tmp_path / "clip_corrupt.mp4"
        corrupt_file.write_bytes(b'<!DOCTYPE html><html><head><title>Error</title></head></html>' * 40)

        pub = InstagramPublisher(account_id="ig_123", access_token="tok_123")
        meta = PublishingMetadata(title="Test Reel")

        with patch.dict(os.environ, {"META_PUBLISH_LIVE": "true", "CONTROL_PLANE_URL": "https://alamr.test"}):
            result = await pub.publish(corrupt_file, meta, dry_run=False)

            assert result.success is False
            assert result.status == "failed"
            assert result.error_code == "invalid_media"
            assert "invalid or corrupted" in result.error

    @pytest.mark.asyncio
    async def test_youtube_verification_detects_processing_failure_and_cleans_up(self, clean_test_env):
        """If YouTube backend processing fails (e.g. cannotProcessFile), report processing_failed and delete ghost video."""
        pub = YouTubePublisher(client_id="cid", client_secret="sec", refresh_token="tok")

        mock_service = MagicMock()
        mock_videos = MagicMock()
        mock_service.videos.return_value = mock_videos

        # YouTube API reports 'failed' with failureReason 'cannotProcessFile'
        mock_list = MagicMock()
        mock_list.execute.return_value = {
            "items": [
                {
                    "id": "yt_ghost_vid",
                    "snippet": {"channelId": "UC_CORRECT", "title": "Ghost Video"},
                    "status": {
                        "uploadStatus": "failed",
                        "failureReason": "cannotProcessFile",
                        "privacyStatus": "public",
                    },
                    "processingDetails": {"processingStatus": "failed"},
                }
            ]
        }
        mock_videos.list.return_value = mock_list

        mock_del = MagicMock()
        mock_videos.delete.return_value = mock_del

        verified, reason, info = await pub.verify_video_publication(
            service=mock_service,
            video_id="yt_ghost_vid",
            expected_channel_id="UC_CORRECT",
            wait_for_processing=True,
            max_wait_seconds=5.0,
        )

        assert verified is False
        assert reason == "processing_failed"
        assert info.get("failureReason") == "cannotProcessFile"
        # Successfully cleaned up failed video from channel
        mock_videos.delete.assert_called_once_with(id="yt_ghost_vid")
        mock_del.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_publishing_service_rejects_corrupted_cached_download(self, clean_test_env):
        """PublishingService must purge corrupt HTML in media_cache and fail safely with invalid_media."""
        clip_id = "test_clip_purge_1"
        job_id = "job_test_purge"

        cache_dir = paths.root() / "media_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cached_fake = cache_dir / f"clip_{clip_id}.mp4"
        cached_fake.write_bytes(b'<html>404 Not Found from Google Drive</html>' * 30)

        # Create minimal DB source, job and clip
        source = models.Source(id="src", type="upload", path="p", title="T", created_at=models.utcnow())
        store.create_source(source)
        job = models.Job(id=job_id, source_id="src", status="done", current_stage="export", progress=1.0, created_at=models.utcnow(), updated_at=models.utcnow())
        store.create_job(job)
        clip = models.Clip(id=clip_id, job_id=job_id, start_s=0, end_s=30, rank=1, start_word=0, end_word=0, title="T", hook="H", score=90, status="exported", created_at=models.utcnow())
        store.create_clip(clip)
        fr = models.FinalRenderRecord(id="fr1", job_id=job_id, clip_id=clip_id, output_path="", quality_status="RENDER_PASS", quality_score=95, duration=25, width=1080, height=1920, fps=30, video_codec="h264", audio_codec="aac", created_at=models.utcnow(), updated_at=models.utcnow())
        store.create_final_render(fr)
        meta = models.ClipMetadataRecord(id="m1", job_id=job_id, clip_id=clip_id, generated_title="T", final_title="T", generated_description="D", final_description="D", final_hashtags=["#Shorts"], compliance_status="SEO_PASS", compliance_score=100, created_at=models.utcnow(), updated_at=models.utcnow())
        store.create_clip_metadata(meta)
        app = models.ClipApprovalRecord(id="a1", job_id=job_id, clip_id=clip_id, current_status="APPROVED", publish_eligible=True, version=1, created_at=models.utcnow(), updated_at=models.utcnow())
        store.create_clip_approval(app)

        service = PublishingService()
        pub_rec = await service.publish_clip(job_id=job_id, clip_id=clip_id, platform="youtube", destination="dest-youtube-main", dry_run=False)

        assert pub_rec.status == "FAILED_PERMANENT"
        assert pub_rec.error_code == "invalid_media"
        # Corrupt file must have been purged from cache
        assert not cached_fake.exists()


class TestPublicMediaEndpointProtection:

    def test_public_media_endpoint_never_serves_html_error(self, clean_test_env):
        """The /api/media/{clip_id} endpoint must never return HTML text as video/mp4."""
        clip_id = "test_clip_endpoint_safe"

        cache_dir = paths.root() / "media_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cached_fake = cache_dir / f"clip_{clip_id}.mp4"
        cached_fake.write_bytes(b'<!DOCTYPE html><html><body>Error</body></html>' * 20)

        app = create_app()
        client = TestClient(app)

        resp = client.get(f"/api/media/{clip_id}")
        # Must return 404 rather than serving HTML as video/mp4 (which broke Instagram crawler with 2207082)
        assert resp.status_code == 404
        assert not cached_fake.exists()
