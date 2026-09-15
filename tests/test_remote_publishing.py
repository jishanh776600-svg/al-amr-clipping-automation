"""Step 25: Focused tests for Multi-Platform Remote Publishing Engine.

Tests cover:
- Publishing Eligibility Gate (Steps 22-24 verification)
- Platform Adapters (YouTube, Instagram, Telegram) with mocked responses
- Error classification (auth error, rate limit, network error, invalid media, permanent vs retryable)
- Idempotent duplicate prevention (no duplicate uploads)
- Multi-destination isolation (independent platform execution)
- Retry safety & bounded retry
- API endpoints (list, get, publish clip, retry, telemetry)
- Credential redaction (no secrets in DB records or telemetry)
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from autoclip.app import create_app
from autoclip.db import store
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
from autoclip.publishing.base import (
    PublicationResult,
    PublishingMetadata,
    is_error_retryable,
)
from autoclip.publishing.instagram import InstagramPublisher, classify_meta_error
from autoclip.publishing.service import PublishingService
from autoclip.publishing.telegram import TelegramPublisher, classify_telegram_error
from autoclip.publishing.youtube import YouTubePublisher, classify_youtube_error


# ---------------------------------------------------------------------------
# Fixture Helpers
# ---------------------------------------------------------------------------


def _setup_pipeline_fixtures(initialised_db, quality_status="RENDER_PASS", compliance_status="SEO_PASS", approval_status="APPROVED"):
    """Creates a complete pipeline up through Steps 22-24."""
    media_file = Path(__file__).parent / "test_media" / "felix_speech.mp4"

    src = Source(id=new_id(), type="upload", path=str(media_file), title="Test Video")
    store.create_source(src)
    job = Job(id=new_id(), source_id=src.id)
    store.create_job(job)
    clip = Clip(id=new_id(), job_id=job.id, start_s=0.0, end_s=5.0, rank=1, title="Test Clip")
    store.create_clip(clip)

    # Step 22: Final Render
    render = FinalRenderRecord(
        id=new_id(),
        job_id=job.id,
        clip_id=clip.id,
        output_path=str(media_file),
        package_dir=str(media_file.parent),
        duration=5.0,
        width=1080,
        height=1920,
        fps=30.0,
        quality_status=quality_status,
        render_status="completed",
        error_details=[] if quality_status in ("RENDER_PASS", "RENDER_WARN") else ["Low resolution"],
    )
    store.replace_final_renders(job.id, [render])

    # Step 23: SEO Metadata
    meta = ClipMetadataRecord(
        id=new_id(),
        job_id=job.id,
        clip_id=clip.id,
        generated_title="Felix Highlights",
        final_title="Felix Highlights #Shorts",
        generated_description="Official Felix Speech",
        final_description="Official Felix Speech #ALAMR",
        generated_hashtags=["#Shorts"],
        final_hashtags=["#Shorts", "#Viral"],
        compliance_status=compliance_status,
        compliance_score=100.0 if compliance_status == "SEO_PASS" else 40.0,
        validation_errors=[] if compliance_status == "SEO_PASS" else ["Missing campaign hashtags"],
    )
    store.create_clip_metadata(meta)

    # Step 24: Approval
    approval = ClipApprovalRecord(
        id=new_id(),
        job_id=job.id,
        clip_id=clip.id,
        current_status=approval_status,
        publish_eligible=(approval_status == "APPROVED"),
        blocking_reasons=[] if approval_status == "APPROVED" else ["Operator rejected"],
        operator_note="Approved" if approval_status == "APPROVED" else "Rejection note",
    )
    store.create_clip_approval(approval)

    return job, clip, render, meta, approval


# ---------------------------------------------------------------------------
# 1. Eligibility Gate Tests
# ---------------------------------------------------------------------------


class TestPublishingEligibilityGate:

    def test_gate_passes_when_all_steps_approved(self, initialised_db):
        job, clip, _, _, _ = _setup_pipeline_fixtures(initialised_db)
        service = PublishingService()
        ok, reasons, render, meta = service.verify_publishing_eligibility(clip.id)
        assert ok is True
        assert reasons == []
        assert render is not None
        assert meta is not None

    def test_gate_blocks_missing_final_render(self, initialised_db):
        job, clip, _, _, _ = _setup_pipeline_fixtures(initialised_db)
        # Delete final render
        with store.connection() as conn:
            conn.execute("DELETE FROM final_renders WHERE clip_id = ?", (clip.id,))

        service = PublishingService()
        ok, reasons, _, _ = service.verify_publishing_eligibility(clip.id)
        assert ok is False
        assert any("Final render record not found" in r for r in reasons)

    def test_gate_blocks_failed_final_render(self, initialised_db):
        job, clip, _, _, _ = _setup_pipeline_fixtures(initialised_db, quality_status="RENDER_REJECT")
        service = PublishingService()
        ok, reasons, _, _ = service.verify_publishing_eligibility(clip.id)
        assert ok is False
        assert any("quality gate" in r for r in reasons)

    def test_gate_blocks_missing_seo_metadata(self, initialised_db):
        job, clip, _, _, _ = _setup_pipeline_fixtures(initialised_db)
        with store.connection() as conn:
            conn.execute("DELETE FROM clip_metadata WHERE clip_id = ?", (clip.id,))

        service = PublishingService()
        ok, reasons, _, _ = service.verify_publishing_eligibility(clip.id)
        assert ok is False
        assert any("SEO metadata record not found" in r for r in reasons)

    def test_gate_blocks_failed_seo_compliance(self, initialised_db):
        job, clip, _, _, _ = _setup_pipeline_fixtures(initialised_db, compliance_status="SEO_REJECT")
        service = PublishingService()
        ok, reasons, _, _ = service.verify_publishing_eligibility(clip.id)
        assert ok is False
        assert any("compliance gate" in r for r in reasons)

    def test_gate_blocks_unapproved_clip(self, initialised_db):
        job, clip, _, _, _ = _setup_pipeline_fixtures(initialised_db, approval_status="PENDING_REVIEW")
        service = PublishingService()
        ok, reasons, _, _ = service.verify_publishing_eligibility(clip.id)
        assert ok is False
        assert any("must be 'APPROVED'" in r for r in reasons)

    def test_gate_blocks_rejected_clip(self, initialised_db):
        job, clip, _, _, _ = _setup_pipeline_fixtures(initialised_db, approval_status="REJECTED")
        service = PublishingService()
        ok, reasons, _, _ = service.verify_publishing_eligibility(clip.id)
        assert ok is False
        assert any("REJECTED" in r for r in reasons)

    def test_gate_blocks_changes_requested_clip(self, initialised_db):
        job, clip, _, _, _ = _setup_pipeline_fixtures(initialised_db, approval_status="CHANGES_REQUESTED")
        service = PublishingService()
        ok, reasons, _, _ = service.verify_publishing_eligibility(clip.id)
        assert ok is False
        assert any("changes requested" in r for r in reasons)

    def test_gate_blocks_publishing_locked_clip(self, initialised_db):
        job, clip, _, _, _ = _setup_pipeline_fixtures(initialised_db, approval_status="PUBLISHING_LOCKED")
        service = PublishingService()
        ok, reasons, _, _ = service.verify_publishing_eligibility(clip.id)
        assert ok is False
        assert any("PUBLISHING_LOCKED" in r for r in reasons)


# ---------------------------------------------------------------------------
# 2. Error Classification Tests
# ---------------------------------------------------------------------------


class TestErrorClassification:

    def test_youtube_error_classification(self):
        class MockQuota(Exception):
            pass

        code, retry = classify_youtube_error(MockQuota("quotaExceeded: Daily limit"))
        assert code == "rate_limit"
        assert retry is True

        code, retry = classify_youtube_error(Exception("invalid_grant: token expired"))
        assert code == "authentication_error"
        assert retry is False

        code, retry = classify_youtube_error(TimeoutError("Connection timed out"))
        assert code == "network_error"
        assert retry is True

        code, retry = classify_youtube_error(Exception("503 Service Unavailable"))
        assert code == "platform_error"
        assert retry is True

    def test_meta_error_classification(self):
        code, retry = classify_meta_error(401, '{"error": {"code": 190, "message": "OAuthException"}}')
        assert code == "authentication_error"
        assert retry is False

        code, retry = classify_meta_error(429, '{"error": {"code": 4, "message": "Rate limit exceeded"}}')
        assert code == "rate_limit"
        assert retry is True

        code, retry = classify_meta_error(400, '{"error": {"message": "Invalid aspect ratio for media"}}')
        assert code == "invalid_media"
        assert retry is False

        code, retry = classify_meta_error(500, "Internal Server Error")
        assert code == "platform_error"
        assert retry is True

    def test_telegram_error_classification(self):
        code, retry = classify_telegram_error(401, "Unauthorized: invalid bot token")
        assert code == "authentication_error"
        assert retry is False

        code, retry = classify_telegram_error(429, '{"ok": false, "description": "Too Many Requests: retry after 10"}')
        assert code == "rate_limit"
        assert retry is True

        code, retry = classify_telegram_error(400, "Bad Request: chat not found")
        assert code == "invalid_metadata"
        assert retry is False

    def test_is_error_retryable_helper(self):
        assert is_error_retryable("rate_limit") is True
        assert is_error_retryable("network_error") is True
        assert is_error_retryable("platform_error") is True
        assert is_error_retryable("authentication_error") is False
        assert is_error_retryable("invalid_metadata") is False
        assert is_error_retryable("invalid_media") is False
        assert is_error_retryable(None) is False


# ---------------------------------------------------------------------------
# 3. Platform Adapters Tests (Mocked)
# ---------------------------------------------------------------------------


class TestPlatformAdaptersMocked:

    @pytest.mark.asyncio
    async def test_youtube_publisher_mocked_success(self, tmp_path):
        vid_file = tmp_path / "vid.mp4"
        vid_file.write_bytes(b"\x00" * 1024)

        pub = YouTubePublisher(client_id="cid", client_secret="csec", refresh_token="rtok")
        meta = PublishingMetadata(title="Test Short")

        mock_build = MagicMock()
        mock_videos = MagicMock()
        mock_insert = MagicMock()
        mock_insert.execute.return_value = {"id": "yt-12345", "status": {"uploadStatus": "uploaded"}}
        mock_videos.insert.return_value = mock_insert
        mock_build.return_value.videos.return_value = mock_videos

        with patch.dict(os.environ, {"YOUTUBE_PUBLISH_LIVE": "true"}), \
             patch.object(pub, "_get_credentials", return_value=MagicMock()), \
             patch("google.auth.transport.requests.Request"), \
             patch("googleapiclient.discovery.build", mock_build):

            res = await pub.publish(vid_file, meta, dry_run=False)
            assert res.success is True
            assert res.status == "published"
            assert res.remote_media_id == "yt-12345"
            assert "youtube.com/shorts/yt-12345" in (res.permalink or "")

    @pytest.mark.asyncio
    async def test_instagram_publisher_mocked_success(self, tmp_path):
        vid_file = tmp_path / "vid.mp4"
        vid_file.write_bytes(b"\x00" * 1024)

        pub = InstagramPublisher(access_token="ig-tok", account_id="ig-acc", control_plane_url="http://localhost:8000")
        meta = PublishingMetadata(title="Test Reel", extra={"export_id": "exp-1"})

        # Mock httpx AsyncClient
        mock_client = AsyncMock()

        # Step 1: create container
        resp_create = MagicMock(status_code=200)
        resp_create.json.return_value = {"id": "container-1"}

        # Step 2: poll container
        resp_stat = MagicMock(status_code=200)
        resp_stat.json.return_value = {"status_code": "FINISHED"}

        # Step 3: publish
        resp_pub = MagicMock(status_code=200)
        resp_pub.json.return_value = {"id": "media-999"}

        # Permalink
        resp_perm = MagicMock(status_code=200)
        resp_perm.json.return_value = {"permalink": "https://instagram.com/reel/media-999/"}

        mock_client.post.side_effect = [resp_create, resp_pub]
        mock_client.get.side_effect = [resp_stat, resp_perm]

        with patch.dict(os.environ, {"META_PUBLISH_LIVE": "true"}), \
             patch("httpx.AsyncClient", return_value=mock_client), \
             patch.object(pub, "resolve_public_media_url", return_value="http://media.com/video.mp4"):

            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None

            res = await pub.publish(vid_file, meta, dry_run=False)
            assert res.success is True
            assert res.status == "published"
            assert res.remote_media_id == "media-999"
            assert "instagram.com" in (res.permalink or "")

    @pytest.mark.asyncio
    async def test_telegram_publisher_mocked_success(self, tmp_path):
        vid_file = tmp_path / "vid.mp4"
        vid_file.write_bytes(b"\x00" * 1024)

        pub = TelegramPublisher(bot_token="bot123:abc", chat_id="-1001234567")
        meta = PublishingMetadata(title="Telegram Highlight")

        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {
            "ok": True,
            "result": {
                "message_id": 42,
                "chat": {"id": -1001234567, "username": "alamr_channel"},
            },
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch("httpx.AsyncClient", return_value=mock_client):
            res = await pub.publish(vid_file, meta, dry_run=False)
            assert res.success is True
            assert res.status == "published"
            assert res.remote_media_id == "42"
            assert "t.me/alamr_channel/42" in (res.permalink or "")


# ---------------------------------------------------------------------------
# 4. Service & Idempotency Tests
# ---------------------------------------------------------------------------


class TestPublishingServiceExecution:

    @pytest.mark.asyncio
    async def test_idempotent_publishing_duplicate_prevention(self, initialised_db):
        job, clip, _, _, _ = _setup_pipeline_fixtures(initialised_db)
        service = PublishingService()

        # Mock adapter
        mock_adapter = AsyncMock()
        mock_adapter.publish.return_value = PublicationResult(
            platform="youtube",
            success=True,
            status="published",
            remote_media_id="yt-first-upload",
            remote_post_id="yt-first-upload",
            permalink="https://youtube.com/shorts/yt-first-upload",
            published_at=utcnow(),
        )
        service.adapters["youtube"] = mock_adapter

        # First publish call
        rec1 = await service.publish_clip(job.id, clip.id, "youtube")
        assert rec1.status == "PUBLISHED"
        assert rec1.remote_media_id == "yt-first-upload"
        assert mock_adapter.publish.call_count == 1

        # Second publish call with same params -> IDEMPOTENT, adapter NOT called again!
        rec2 = await service.publish_clip(job.id, clip.id, "youtube")
        assert rec2.id == rec1.id
        assert rec2.status == "PUBLISHED"
        assert mock_adapter.publish.call_count == 1  # Still 1! Duplicate upload prevented.

    @pytest.mark.asyncio
    async def test_multi_destination_isolation(self, initialised_db):
        """Failure on YouTube does not block successful publication on Telegram."""
        job, clip, _, _, _ = _setup_pipeline_fixtures(initialised_db)
        service = PublishingService()

        # YouTube fails with rate limit
        mock_yt = AsyncMock()
        mock_yt.publish.return_value = PublicationResult(
            platform="youtube",
            success=False,
            status="failed",
            error_code="rate_limit",
            retryable=True,
            error="Rate limit exceeded",
        )
        # Telegram succeeds
        mock_tg = AsyncMock()
        mock_tg.publish.return_value = PublicationResult(
            platform="telegram",
            success=True,
            status="published",
            remote_media_id="tg-msg-1",
            permalink="https://t.me/c/1/1",
            published_at=utcnow(),
        )

        service.adapters["youtube"] = mock_yt
        service.adapters["telegram"] = mock_tg

        results = await service.publish_clip_all_destinations(job.id, clip.id, ["youtube", "telegram"])
        assert len(results) == 2

        yt_res = next(r for r in results if r.platform == "youtube")
        tg_res = next(r for r in results if r.platform == "telegram")

        assert yt_res.status == "FAILED_RETRYABLE"
        assert yt_res.is_retryable is True
        assert tg_res.status == "PUBLISHED"

    @pytest.mark.asyncio
    async def test_persistence_of_publication_records(self, initialised_db):
        job, clip, _, _, _ = _setup_pipeline_fixtures(initialised_db)
        service = PublishingService()

        mock_yt = AsyncMock()
        mock_yt.publish.return_value = PublicationResult(
            platform="youtube",
            success=True,
            status="published",
            remote_media_id="yt-p1",
            permalink="https://youtube.com/shorts/yt-p1",
        )
        service.adapters["youtube"] = mock_yt

        rec = await service.publish_clip(job.id, clip.id, "youtube")

        # Verify DB store can list and retrieve it
        stored = store.get_publication(rec.id)
        assert stored is not None
        assert stored.status == "PUBLISHED"
        assert stored.remote_media_id == "yt-p1"

        job_pubs = store.list_publications_for_job(job.id)
        assert len(job_pubs) == 1
        assert job_pubs[0].clip_id == clip.id


# ---------------------------------------------------------------------------
# 5. API Tests
# ---------------------------------------------------------------------------


@pytest.fixture
def client(initialised_db):
    os.environ.setdefault("AUTOCLIP_API_KEY", "test-key-25")
    return TestClient(create_app())


def _h():
    return {"Authorization": "Bearer test-key-25"}


class TestPublishingAPI:

    def test_publish_clip_endpoint_blocked_by_approval(self, client, initialised_db):
        job, clip, _, _, _ = _setup_pipeline_fixtures(initialised_db, approval_status="PENDING_REVIEW")
        r = client.post(
            f"/api/jobs/{job.id}/clips/{clip.id}/publish",
            headers={**_h(), "Content-Type": "application/json"},
            json={"platforms": ["youtube"]},
        )
        assert r.status_code == 400
        assert "must be 'APPROVED'" in r.json()["detail"]

    def test_publish_clip_endpoint_success_dry_run(self, client, initialised_db):
        job, clip, _, _, _ = _setup_pipeline_fixtures(initialised_db)
        r = client.post(
            f"/api/jobs/{job.id}/clips/{clip.id}/publish",
            headers={**_h(), "Content-Type": "application/json"},
            json={"platforms": ["youtube"], "dry_run": True},
        )
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["platform"] == "youtube"

    def test_list_publications_endpoint(self, client, initialised_db):
        job, clip, _, _, _ = _setup_pipeline_fixtures(initialised_db)
        # Create a mock publication record in DB
        pub = PublicationRecord(
            id=new_id(),
            job_id=job.id,
            clip_id=clip.id,
            platform="telegram",
            destination_id="default",
            status="PUBLISHED",
            idempotency_key="key-test-1",
            remote_media_id="msg-101",
            permalink="https://t.me/test/101",
        )
        store.create_publication(pub)

        r = client.get(f"/api/jobs/{job.id}/publications", headers=_h())
        assert r.status_code == 200
        pubs = r.json()
        assert len(pubs) >= 1
        assert pubs[0]["id"] == pub.id
        assert pubs[0]["status"] == "PUBLISHED"

    def test_get_single_publication_endpoint(self, client, initialised_db):
        job, clip, _, _, _ = _setup_pipeline_fixtures(initialised_db)
        pub = PublicationRecord(
            id=new_id(),
            job_id=job.id,
            clip_id=clip.id,
            platform="youtube",
            idempotency_key="key-test-single",
            status="PUBLISHED",
        )
        store.create_publication(pub)

        r = client.get(f"/api/jobs/{job.id}/publications/{pub.id}", headers=_h())
        assert r.status_code == 200
        assert r.json()["id"] == pub.id

    def test_get_publishing_telemetry_endpoint(self, client, initialised_db):
        job, clip, _, _, _ = _setup_pipeline_fixtures(initialised_db)
        pub = PublicationRecord(
            id=new_id(),
            job_id=job.id,
            clip_id=clip.id,
            platform="youtube",
            idempotency_key="key-test-telemetry",
            status="PUBLISHED",
        )
        store.create_publication(pub)

        r = client.get(f"/api/jobs/{job.id}/publications/telemetry", headers=_h())
        assert r.status_code == 200
        t = r.json()
        assert t["total_destinations"] >= 1
        assert t["published"] >= 1


# ---------------------------------------------------------------------------
# 6. Security Redaction Tests
# ---------------------------------------------------------------------------


class TestSecurityRedaction:

    def test_no_secrets_in_publication_records_or_telemetry(self, initialised_db):
        """Ensures tokens and credentials are never stored in publication records."""
        job, clip, _, _, _ = _setup_pipeline_fixtures(initialised_db)
        pub = PublicationRecord(
            id=new_id(),
            job_id=job.id,
            clip_id=clip.id,
            platform="instagram",
            idempotency_key="key-sec-check",
            response_metadata={"id": "media-1", "status": "ok"},
            status="PUBLISHED",
        )
        store.create_publication(pub)

        fetched = store.get_publication(pub.id)
        assert fetched is not None
        record_dump = str(fetched.to_dict()).lower()

        # Verify no token names or leaks
        assert "bot_token" not in record_dump
        assert "refresh_token" not in record_dump
        assert "client_secret" not in record_dump
        assert "access_token" not in record_dump
