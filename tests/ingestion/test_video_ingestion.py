"""Unit tests for Remote Video Ingestion Subsystem."""

import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock
from clipping.ingestion.source import SourceReference, SourceType
from clipping.ingestion.remote import RemoteVideoIngestor
from clipping.ingestion.exceptions import (
    InvalidSourceError,
    IngestionNetworkError,
    UnsupportedMediaError,
)
from clipping.storage.local import LocalStorageDriver


def test_source_reference_auto_detection():
    yt_ref = SourceReference.from_uri("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert yt_ref.source_type == SourceType.YOUTUBE

    yt_short_ref = SourceReference.from_uri("https://youtu.be/dQw4w9WgXcQ")
    assert yt_short_ref.source_type == SourceType.YOUTUBE

    gdrive_ref = SourceReference.from_uri("gdrive://vault_media/episode_42.mp4")
    assert gdrive_ref.source_type == SourceType.GDRIVE

    direct_ref = SourceReference.from_uri("https://example.com/videos/master.mp4")
    assert direct_ref.source_type == SourceType.DIRECT_URL


def test_invalid_source_uri():
    with pytest.raises(InvalidSourceError):
        SourceReference.from_uri("   ")


@pytest.mark.asyncio
async def test_remote_ingestion_with_mock_ydl(temp_vault_dir):
    storage = LocalStorageDriver(root_dir=temp_vault_dir)

    # Create mock yt-dlp client
    mock_ydl = MagicMock()

    def fake_extract_info(url, download=True):
        if download:
            # Simulate writing a video file into the temporary directory
            # yt-dlp outtmpl pattern was formatted
            # Find the tempdir created by RemoteVideoIngestor
            # We can write a dummy file in the temp directory
            # For testing, we mock extract_info to write a file
            pass
        return {
            "id": "dQw4w9WgXcQ",
            "title": "Autonomous Video Pipeline Podcast",
            "duration": 1800.0,
            "width": 1920,
            "height": 1080,
            "fps": 30.0,
            "ext": "mp4",
        }

    # Instead of complex temp dir inspection, we create an ingestor with custom _get_info_dict
    ingestor = RemoteVideoIngestor()

    def mock_get_info(uri, download=False, outtmpl=None):
        if download and outtmpl:
            # write a dummy mock video file at outtmpl formatted with mp4
            file_path = outtmpl.replace("%(ext)s", "mp4")
            with open(file_path, "wb") as f:
                f.write(b"MOCK_MP4_VIDEO_STREAM_BINARY_DATA_CHUNK")
        return {
            "id": "dQw4w9WgXcQ",
            "title": "Autonomous Video Pipeline Podcast",
            "duration": 1800.0,
            "width": 1920,
            "height": 1080,
            "fps": 30.0,
        }

    ingestor._get_info_dict = mock_get_info  # type: ignore

    source_ref = SourceReference.from_uri("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    metadata = await ingestor.ingest(
        source_ref=source_ref,
        storage_driver=storage,
        source_video_id="VID_AUTO_001",
    )

    # 1. Verify returned metadata
    assert metadata.video_id == "VID_AUTO_001"
    assert metadata.title == "Autonomous Video Pipeline Podcast"
    assert metadata.duration_seconds == 1800.0
    assert metadata.master_video_storage_key == "sources/VID_AUTO_001/master.mp4"

    # 2. Verify files in storage vault
    assert await storage.exists("sources/VID_AUTO_001/master.mp4") is True
    assert await storage.exists("sources/VID_AUTO_001/metadata.json") is True

    # 3. Idempotency Check: Re-run ingestion without force_reingest
    # Should return cached metadata without calling _get_info_dict
    ingestor._get_info_dict = MagicMock(side_effect=RuntimeError("Should not be called!"))  # type: ignore

    cached_meta = await ingestor.ingest(
        source_ref=source_ref,
        storage_driver=storage,
        source_video_id="VID_AUTO_001",
        force_reingest=False,
    )
    assert cached_meta.video_id == "VID_AUTO_001"
    assert cached_meta.duration_seconds == 1800.0


@pytest.mark.asyncio
async def test_gdrive_source_ingestion(temp_vault_dir):
    storage = LocalStorageDriver(root_dir=temp_vault_dir)

    # Seed a file in the vault representing a GDrive upload
    raw_key = "raw_uploads/interview.mp4"
    await storage.upload_bytes(b"INTERVIEW_VIDEO_BYTES", raw_key, content_type="video/mp4")

    ingestor = RemoteVideoIngestor()
    source_ref = SourceReference(
        source_type=SourceType.GDRIVE,
        uri=f"gdrive://{raw_key}",
        title_hint="Client Master Interview",
        extra_params={"duration": 900.0, "width": 1920, "height": 1080, "fps": 60.0},
    )

    metadata = await ingestor.ingest(
        source_ref=source_ref,
        storage_driver=storage,
        source_video_id="VID_GDRIVE_001",
    )

    assert metadata.video_id == "VID_GDRIVE_001"
    assert metadata.title == "Client Master Interview"
    assert metadata.duration_seconds == 900.0
    assert await storage.exists("sources/VID_GDRIVE_001/master.mp4") is True


@pytest.mark.asyncio
async def test_ingestion_network_failure(temp_vault_dir):
    storage = LocalStorageDriver(root_dir=temp_vault_dir)
    ingestor = RemoteVideoIngestor()

    def failing_get_info(uri, download=False, outtmpl=None):
        raise IngestionNetworkError("HTTP 404 Video Unavailable")

    ingestor._get_info_dict = failing_get_info  # type: ignore

    source_ref = SourceReference.from_uri("https://www.youtube.com/watch?v=invalid_id")
    with pytest.raises(IngestionNetworkError, match="404"):
        await ingestor.ingest(
            source_ref=source_ref,
            storage_driver=storage,
            source_video_id="VID_FAIL_001",
        )
