"""Comprehensive automated tests for Step 8 Production Publishing & Distribution."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from autoclip.app import create_app
from autoclip.db import init as db_init, models, store
from autoclip.publishing.base import PublishingMetadata, PublishingResult
from autoclip.publishing.instagram import InstagramPublisher
from autoclip.publishing.service import PublishingService
from autoclip.publishing.telegram import TelegramPublisher
from autoclip.publishing.youtube import YouTubePublisher


@pytest.fixture(autouse=True)
def setup_test_db(initialised_db):
    """Ensure database is initialised for each test."""
    yield



@pytest.fixture
def sample_job_and_export(tmp_path):
    """Seed database with source, job, clip, and rendered export file."""
    # Source
    src = models.Source(
        id=models.new_id(),
        type="youtube",
        title="AL AMR Masterclass",
        path=str(tmp_path / "source.mp4"),
        url="https://youtube.com/watch?v=mock_video_123",
        duration_s=120.0,
    )
    store.create_source(src)


    # Job
    job = models.Job(
        id=models.new_id(),
        source_id=src.id,
        status="done",
        current_stage="completed",
        progress=1.0,
    )
    store.create_job(job)

    # Clip
    clip = models.Clip(
        id=models.new_id(),
        job_id=job.id,
        start_s=10.0,
        end_s=40.0,
        rank=1,
        title="The Winning Secret",
        hook="Nobody talks about this strategy",
        score=95,
        status="kept",
    )
    store.create_clip(clip)

    # Export file
    exp_file = tmp_path / f"clip_{clip.id}_9x16.mp4"
    exp_file.write_bytes(b"dummy mp4 video bytes")

    exp = models.Export(
        id=models.new_id(),
        clip_id=clip.id,
        path=str(exp_file),
        ratio="9:16",
        style="bold_pop",
        size_bytes=len(b"dummy mp4 video bytes"),
        drive_file_id="1MockDriveFileId_999",
        drive_web_view_link="https://drive.google.com/file/d/1MockDriveFileId_999/view",
        drive_storage_key="clips/mock/clip_9x16.mp4",
    )
    store.create_export(exp)

    return {"source": src, "job": job, "clip": clip, "export": exp, "file": exp_file}


# --------------------------------------------------------------------------
# 1. Database Schema v5 & Store Tests
# --------------------------------------------------------------------------

def test_schema_v5_and_publishing_store(sample_job_and_export):
    exp = sample_job_and_export["export"]
    job = sample_job_and_export["job"]

    rec = models.PublishingRecord(
        id=models.new_id(),
        export_id=exp.id,
        job_id=job.id,
        platform="telegram",
        status="pending",
        destination="@alamr_drops",
        metadata={"title": "Test Title"},
    )
    saved = store.create_or_update_publishing_record(rec)
    assert saved.id == rec.id
    assert saved.status == "pending"

    # Fetch by ID
    fetched = store.get_publishing_record(rec.id)
    assert fetched is not None
    assert fetched.platform == "telegram"
    assert fetched.destination == "@alamr_drops"

    # Fetch by Target
    by_target = store.get_publishing_record_by_target(exp.id, "telegram", "@alamr_drops")
    assert by_target is not None
    assert by_target.id == rec.id

    # Update record
    updated = store.update_publishing_record(
        rec.id,
        status="published",
        external_id="msg_98765",
        metadata={"url": "https://t.me/alamr_drops/98765"},
    )
    assert updated is not None
    assert updated.status == "published"
    assert updated.external_id == "msg_98765"
    assert updated.metadata.get("url") == "https://t.me/alamr_drops/98765"

    # List records
    all_recs = store.list_publishing_records(job_id=job.id)
    assert len(all_recs) == 1
    assert all_recs[0].id == rec.id


def test_publishing_idempotency_constraint(sample_job_and_export):
    exp = sample_job_and_export["export"]
    job = sample_job_and_export["job"]

    rec1 = models.PublishingRecord(
        id=models.new_id(),
        export_id=exp.id,
        job_id=job.id,
        platform="youtube",
        status="pending",
        destination="",
        metadata={"dry_run": True},
    )
    store.create_or_update_publishing_record(rec1)

    # Creating record with SAME (export_id, platform, destination) must NOT insert duplicate row
    rec2 = models.PublishingRecord(
        id=models.new_id(),
        export_id=exp.id,
        job_id=job.id,
        platform="youtube",
        status="published",
        external_id="yt_video_001",
        destination="",
        metadata={"url": "https://youtube.com/shorts/yt_video_001"},
    )
    store.create_or_update_publishing_record(rec2)

    rows = store.list_publishing_records(export_id=exp.id, platform="youtube")
    assert len(rows) == 1
    assert rows[0].status == "published"
    assert rows[0].external_id == "yt_video_001"


# --------------------------------------------------------------------------
# 2. Telegram Publisher Tests
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_telegram_publisher_success(sample_job_and_export, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100123456789")

    publisher = TelegramPublisher()
    assert publisher.is_configured() is True


    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "ok": True,
        "result": {
            "message_id": 4242,
            "chat": {"username": "alamr_channel"},
        },
    }

    mock_client = AsyncMock()
    mock_client.post.return_value = fake_response
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    with patch("httpx.AsyncClient", return_value=mock_client):
        meta = PublishingMetadata(
            title="Mindblowing Secret",
            description="Watch this clip to understand.",
            tags=["ALAMR", "Shorts"],
        )
        res = await publisher.publish(
            sample_job_and_export["file"],
            meta,
            drive_link=sample_job_and_export["export"].drive_web_view_link,
        )

        assert res.success is True
        assert res.status == "published"
        assert res.external_id == "4242"
        assert "t.me/alamr_channel/4242" in (res.url or "")


@pytest.mark.asyncio
async def test_telegram_publisher_missing_token(sample_job_and_export, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    publisher = TelegramPublisher()
    assert publisher.is_configured() is False

    meta = PublishingMetadata(title="Test")
    res = await publisher.publish(sample_job_and_export["file"], meta)
    assert res.success is False
    assert "TELEGRAM_BOT_TOKEN" in res.error


# --------------------------------------------------------------------------
# 3. YouTube Publisher Tests
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_youtube_publisher_dry_run(sample_job_and_export, monkeypatch):
    monkeypatch.setenv("YOUTUBE_CLIENT_ID", "mock_id.apps.googleusercontent.com")
    monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "mock_secret")
    monkeypatch.setenv("YOUTUBE_REFRESH_TOKEN", "1//mock_refresh_token")
    monkeypatch.setenv("YOUTUBE_PUBLISH_LIVE", "false")

    publisher = YouTubePublisher()
    assert publisher.is_configured() is True

    # In dry run mode, mock credentials validation
    with patch.object(publisher, "_get_credentials", return_value=MagicMock()):
        meta = PublishingMetadata(title="AL AMR Hook Strategy")
        res = await publisher.publish(sample_job_and_export["file"], meta, dry_run=True)

        assert res.success is True
        assert res.status == "ready_for_upload"
        assert res.details.get("mode") == "dry_run"




@pytest.mark.asyncio
async def test_youtube_publisher_missing_creds(sample_job_and_export, monkeypatch):
    monkeypatch.delenv("YOUTUBE_REFRESH_TOKEN", raising=False)
    publisher = YouTubePublisher()
    assert publisher.is_configured() is False

    meta = PublishingMetadata(title="Test")
    res = await publisher.publish(sample_job_and_export["file"], meta, dry_run=False)
    assert res.success is False
    assert "YOUTUBE_REFRESH_TOKEN" in res.error


# --------------------------------------------------------------------------
# 4. Instagram Publisher Tests
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_instagram_publisher_missing_creds(sample_job_and_export, monkeypatch):
    monkeypatch.delenv("INSTAGRAM_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("META_ACCESS_TOKEN", raising=False)

    publisher = InstagramPublisher()
    assert publisher.is_configured() is False

    meta = PublishingMetadata(title="Test")
    res = await publisher.publish(sample_job_and_export["file"], meta)
    assert res.success is False
    assert "META_ACCESS_TOKEN" in res.error




# --------------------------------------------------------------------------
# 5. Publishing Service & Orchestration Tests
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_publishing_service_orchestration(sample_job_and_export):
    exp = sample_job_and_export["export"]
    service = PublishingService()

    # Mock telegram adapter
    mock_adapter = AsyncMock()
    mock_adapter.publish.return_value = PublishingResult(
        success=True,
        platform="telegram",
        status="published",
        external_id="msg_777",
        url="https://t.me/alamr/777",
    )
    service.adapters["telegram"] = mock_adapter

    # 1. First publish
    rec = await service.publish_export(exp.id, "telegram")
    assert rec.status == "published"
    assert rec.external_id == "msg_777"
    assert mock_adapter.publish.call_count == 1

    # 2. Duplicate publish call -> Idempotency skips re-uploading
    rec_dup = await service.publish_export(exp.id, "telegram")
    assert rec_dup.id == rec.id
    assert mock_adapter.publish.call_count == 1  # Not called again


# --------------------------------------------------------------------------
# 6. REST API Endpoints Tests
# --------------------------------------------------------------------------

def test_publishing_api_endpoints(sample_job_and_export):
    app = create_app()
    client = TestClient(app)
    exp = sample_job_and_export["export"]
    job = sample_job_and_export["job"]

    # 1. GET /api/publishing/platforms
    resp = client.get("/api/publishing/platforms")
    assert resp.status_code == 200
    platforms = resp.json()
    assert len(platforms) == 3
    platform_names = [p["platform"] for p in platforms]
    assert "telegram" in platform_names
    assert "youtube" in platform_names
    assert "instagram" in platform_names

    # 2. Seed a publishing record
    rec = models.PublishingRecord(
        id=models.new_id(),
        export_id=exp.id,
        job_id=job.id,
        platform="telegram",
        status="published",
        destination="@alamr_test",
        external_id="msg_111",
    )
    store.create_or_update_publishing_record(rec)

    # 3. GET /api/publishing
    resp = client.get(f"/api/publishing?job_id={job.id}")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["id"] == rec.id

    # 4. GET /api/jobs/{job_id}/publishing
    resp = client.get(f"/api/jobs/{job.id}/publishing")
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    # 5. GET /api/publishing/{id}
    resp = client.get(f"/api/publishing/{rec.id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == rec.id

    # 6. POST /api/exports/{export_id}/publish with dry_run
    resp = client.post(
        f"/api/exports/{exp.id}/publish",
        json={
            "platforms": ["youtube"],
            "title": "API Test Clip",
            "dry_run": True,
        },
    )
    assert resp.status_code == 200
    pub_results = resp.json()
    assert len(pub_results) == 1
    assert pub_results[0]["platform"] == "youtube"


def test_worker_callback_ingests_publishing_records(sample_job_and_export):
    app = create_app()
    client = TestClient(app)
    exp = sample_job_and_export["export"]
    job = sample_job_and_export["job"]

    pub_payload = [
        {
            "id": models.new_id(),
            "export_id": exp.id,
            "platform": "telegram",
            "status": "published",
            "external_id": "tg_msg_999",
            "destination": "@alamr_drops",
            "metadata": {"url": "https://t.me/alamr_drops/999"},
        }
    ]

    resp = client.post(
        f"/api/jobs/{job.id}/worker-callback",
        json={
            "status": "done",
            "stage": "completed",
            "progress": 1.0,
            "publishing_records": pub_payload,
        },
    )
    assert resp.status_code == 200

    saved_recs = store.list_publishing_records(job_id=job.id)
    assert len(saved_recs) == 1
    assert saved_recs[0].platform == "telegram"
    assert saved_recs[0].status == "published"
    assert saved_recs[0].external_id == "tg_msg_999"
