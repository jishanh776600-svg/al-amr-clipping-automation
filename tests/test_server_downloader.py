"""Comprehensive unit and integration tests for ServerDownloader subsystem and internal API."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from autoclip.app import create_app
from autoclip.pipeline.ffmpeg import MediaInfo
from autoclip.pipeline.source_acquisition.base import (
    AcquisitionResult,
    JobContext,
    SourceAcquisitionError,
    SourceErrorCode,
)
from autoclip.pipeline.source_acquisition.registry import SourceAcquisitionRegistry
from autoclip.pipeline.source_acquisition.server_downloader.engine import (
    AcquisitionTelemetry,
    ServerDownloaderEngine,
)
from autoclip.pipeline.source_acquisition.server_downloader.provider import (
    ServerDownloaderProvider,
)


SAMPLE_MP4 = Path(__file__).parent / "test_media" / "felix_speech.mp4"


@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp(prefix="alamr_test_server_dl_")
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def test_client():
    os.environ["AUTOCLIP_API_KEY"] = "test-secret-key-123"
    os.environ["AUTOCLIP_NO_WORKER"] = "1"
    app = create_app()
    client = TestClient(app)
    yield client
    os.environ.pop("AUTOCLIP_API_KEY", None)
    os.environ.pop("AUTOCLIP_NO_WORKER", None)


class TestServerDownloaderProviderContract:
    def test_provider_name_and_configured(self):
        provider = ServerDownloaderProvider()
        assert provider.provider_name == "server-downloader"
        assert provider.is_configured() is True

    def test_ssrf_rejection_on_acquire(self, tmp_dir):
        provider = ServerDownloaderProvider()
        blocked_urls = [
            "http://127.0.0.1:8000/video.mp4",
            "http://localhost:5000/video.mp4",
            "http://169.254.169.254/latest/meta-data",
            "http://10.0.0.5/stream",
            "file:///etc/passwd",
        ]
        for url in blocked_urls:
            with pytest.raises(SourceAcquisitionError) as exc_info:
                provider.acquire(url, tmp_dir)
            assert exc_info.value.code in (
                SourceErrorCode.SOURCE_INVALID_URL,
                SourceErrorCode.SOURCE_ACCESS_BLOCKED,
            )


class TestServerDownloaderEngine:
    def test_timeout_handling(self, tmp_dir):
        engine = ServerDownloaderEngine(timeout_s=0.01)

        def _slow_extract(*args, **kwargs):
            import time
            time.sleep(0.5)
            return {}

        with patch("yt_dlp.YoutubeDL") as mock_ydl:
            mock_ydl.return_value.__enter__.return_value.extract_info.side_effect = _slow_extract
            with pytest.raises(SourceAcquisitionError) as exc_info:
                engine.download("https://www.youtube.com/watch?v=dQw4w9WgXcQ", tmp_dir)
            assert exc_info.value.code == SourceErrorCode.SOURCE_PROVIDER_TIMEOUT

    def test_successful_mock_download_and_validation(self, tmp_dir):
        engine = ServerDownloaderEngine()

        def _mock_extract(url, download=True):
            out_file = tmp_dir / "acquired_source.mp4"
            if SAMPLE_MP4.exists():
                shutil.copy2(SAMPLE_MP4, out_file)
            else:
                out_file.write_bytes(b"dummy" * 500)
            return {"title": "Test Video", "id": "test1234"}

        with patch("yt_dlp.YoutubeDL") as mock_ydl:
            mock_ydl.return_value.__enter__.return_value.extract_info.side_effect = _mock_extract
            result, telemetry = engine.download(
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                tmp_dir,
                job_context=JobContext(job_id="test-job-1"),
            )

        assert result.success is True
        assert result.provider_name == "server-downloader"
        assert result.local_media_path.name == "source.mp4"
        assert result.file_size > 0
        assert len(result.sha256) == 64
        assert telemetry.final_status == "SUCCESS"
        assert telemetry.job_id == "test-job-1"

    def test_bot_detection_classification(self, tmp_dir):
        engine = ServerDownloaderEngine()

        with patch("yt_dlp.YoutubeDL") as mock_ydl:
            mock_ydl.return_value.__enter__.return_value.extract_info.side_effect = Exception(
                "ERROR: Sign in to confirm you're not a bot. Use --cookies"
            )
            with pytest.raises(SourceAcquisitionError) as exc_info:
                engine.download("https://www.youtube.com/watch?v=jNQXAC9IVRw", tmp_dir)
            assert exc_info.value.code == SourceErrorCode.SOURCE_ACCESS_BLOCKED
            assert "direct" in exc_info.value.hint.lower()


class TestInternalAcquireEndpoint:
    def test_unauthenticated_request_rejected(self, test_client):
        resp = test_client.post("/internal/acquire", json={"url": "https://www.youtube.com/watch?v=123"})
        assert resp.status_code == 401

    def test_authenticated_ssrf_rejection(self, test_client):
        headers = {"X-API-Key": "test-secret-key-123"}
        resp = test_client.post(
            "/internal/acquire",
            json={"url": "http://127.0.0.1:8000/internal", "job_id": "job-ssrf"},
            headers=headers,
        )
        assert resp.status_code == 400

    def test_authenticated_successful_acquire_and_stream(self, test_client):
        headers = {"X-API-Key": "test-secret-key-123"}

        mock_media_info = MediaInfo(
            path=SAMPLE_MP4,
            duration_s=15.0,
            has_video=True,
            has_audio=True,
            width=1920,
            height=1080,
            fps=24.0,
            video_codec="h264",
            audio_codec="aac",
        )

        mock_result = AcquisitionResult(
            success=True,
            local_media_path=SAMPLE_MP4,
            provider_name="server-downloader",
            source_url="https://www.youtube.com/watch?v=valid",
            duration=15.0,
            file_size=1998712,
            sha256="abcdef1234567890" * 4,
            media_info=mock_media_info,
        )
        mock_telemetry = AcquisitionTelemetry(final_status="SUCCESS")

        with patch.object(ServerDownloaderEngine, "download", return_value=(mock_result, mock_telemetry)):
            resp = test_client.post(
                "/internal/acquire",
                json={"url": "https://www.youtube.com/watch?v=valid", "job_id": "job-test-internal"},
                headers=headers,
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "success"
            assert data["provider"] == "server-downloader"
            assert "stream_url" in data


class TestRegistryWithServerDownloader:
    def test_deterministic_priority_order(self):
        reg = SourceAcquisitionRegistry()
        names = [p.provider_name for p in reg.providers]
        assert names == ["server-downloader", "yt-dlp", "http-api"]

    def test_fallback_when_server_downloader_fails(self, tmp_dir):
        provider1 = MagicMock(spec=ServerDownloaderProvider)
        provider1.provider_name = "server-downloader"
        provider1.is_configured.return_value = True
        provider1.acquire.side_effect = SourceAcquisitionError(
            "Server download failed",
            code=SourceErrorCode.SOURCE_ACCESS_BLOCKED,
            provider_name="server-downloader",
        )

        dest_file = tmp_dir / "source.mp4"
        dest_file.write_bytes(b"media" * 200)

        mock_media_info = MediaInfo(
            path=dest_file,
            duration_s=10.0,
            has_video=True,
            has_audio=True,
            width=1280,
            height=720,
            fps=30.0,
            video_codec="h264",
            audio_codec="aac",
        )


        provider2 = MagicMock()
        provider2.provider_name = "yt-dlp"
        provider2.is_configured.return_value = True
        provider2.acquire.return_value = AcquisitionResult(
            success=True,
            local_media_path=dest_file,
            provider_name="yt-dlp",
            source_url="https://www.youtube.com/watch?v=test",
            duration=10.0,
            file_size=dest_file.stat().st_size,
            sha256="abc123hash",
            media_info=mock_media_info,
            provider_metadata={},
        )

        reg = SourceAcquisitionRegistry(providers=[provider1, provider2])
        result = reg.acquire("https://www.youtube.com/watch?v=test", tmp_dir)

        assert result.success is True
        assert result.provider_name == "yt-dlp"
        assert result.acquisition_attempts == 2
        # Check telemetry provenance
        prov = result.provider_metadata["provenance"]
        assert prov["provider"] == "yt-dlp"
        assert prov["attempts"] == 2
        assert len(prov["fallback_history"]) == 1
        assert prov["fallback_history"][0]["provider"] == "server-downloader"
