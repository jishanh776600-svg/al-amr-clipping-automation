"""Tests for Step 13 Source Acquisition Subsystem.

Covers:
1. Provider interface contract and normalized AcquisitionResult.
2. Security perimeter and SSRF validation (blocking loopback, RFC 1918, cloud metadata 169.254.169.254, file://, path traversal).
3. Unified media validation gate (0-byte, HTML masquerade, FFprobe check, SHA-256 computation).
4. yt-dlp provider wrapping and error mapping.
5. HTTP API secondary provider configuration, streaming, and error mapping.
6. Registry deterministic fallback chain (Primary yt-dlp -> Secondary HTTP API -> clean operator error).
7. Provenance tracking in Source and Job Manifest.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autoclip.config import IngestSettings
from autoclip.pipeline.ffmpeg import FFmpegError, MediaInfo
from autoclip.pipeline import ingest
from autoclip.pipeline.source_acquisition import (
    AcquisitionResult,
    HttpApiAcquisitionProvider,
    JobContext,
    SourceAcquisitionError,
    SourceAcquisitionProvider,
    SourceAcquisitionRegistry,
    SourceErrorCode,
    YtDlpAcquisitionProvider,
    safe_target_path,
    validate_media_gate,
    validate_remote_url,
)
from autoclip.pipeline.youtube_acquirer import (
    YouTubeErrorCode,
    YouTubeIngestError,
)


# ---------------------------------------------------------------------------
# 1. Security Perimeter & SSRF Validation Tests
# ---------------------------------------------------------------------------

def test_validate_remote_url_accepts_valid_public_urls():
    assert validate_remote_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert validate_remote_url("https://youtu.be/dQw4w9WgXcQ") == "https://youtu.be/dQw4w9WgXcQ"
    assert validate_remote_url("http://example.com/video.mp4") == "http://example.com/video.mp4"


@pytest.mark.parametrize(
    "bad_url",
    [
        "file:///etc/passwd",
        "file:///C:/Windows/System32/cmd.exe",
        "ftp://example.com/video.mp4",
        "gopher://example.com",
        "javascript:alert(1)",
        "data:text/html,test",
        "",
        "   ",
        "not_a_url",
    ],
)
def test_validate_remote_url_rejects_invalid_schemes(bad_url):
    with pytest.raises(SourceAcquisitionError) as exc_info:
        validate_remote_url(bad_url)
    assert exc_info.value.code == SourceErrorCode.SOURCE_INVALID_URL


@pytest.mark.parametrize(
    "ssrf_target",
    [
        "http://127.0.0.1/video.mp4",
        "http://localhost:8000/video.mp4",
        "http://127.0.0.1:4416/test",
        "http://10.0.0.1/video.mp4",
        "http://172.16.0.5/video.mp4",
        "http://192.168.1.100/video.mp4",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/video.mp4",
    ],
)
def test_validate_remote_url_rejects_private_and_cloud_metadata(ssrf_target):
    with pytest.raises(SourceAcquisitionError) as exc_info:
        validate_remote_url(ssrf_target)
    assert exc_info.value.code in (SourceErrorCode.SOURCE_INVALID_URL, SourceErrorCode.SOURCE_ACCESS_BLOCKED)


def test_safe_target_path_prevents_directory_traversal(tmp_path):
    root_dir = tmp_path / "workspace"
    root_dir.mkdir()

    # Valid filename inside root
    safe = safe_target_path(root_dir, "source.mp4")
    assert str(safe).startswith(str(root_dir))

    # Path traversal attempt
    with pytest.raises(SourceAcquisitionError) as exc_info:
        safe_target_path(root_dir, "../../../secret.txt")
    assert exc_info.value.code == SourceErrorCode.SOURCE_MEDIA_INVALID


# ---------------------------------------------------------------------------
# 2. Unified Media Validation Gate Tests
# ---------------------------------------------------------------------------

def test_validate_media_gate_rejects_non_existent_file(tmp_path):
    missing = tmp_path / "missing.mp4"
    with pytest.raises(SourceAcquisitionError) as exc_info:
        validate_media_gate(missing)
    assert exc_info.value.code == SourceErrorCode.SOURCE_MEDIA_INVALID


def test_validate_media_gate_rejects_zero_byte_file(tmp_path):
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    with pytest.raises(SourceAcquisitionError) as exc_info:
        validate_media_gate(empty)
    assert exc_info.value.code == SourceErrorCode.SOURCE_MEDIA_INVALID
    assert "0 bytes" in str(exc_info.value)


def test_validate_media_gate_rejects_html_masquerade(tmp_path):
    html_file = tmp_path / "blocked.mp4"
    html_file.write_bytes(b"<!DOCTYPE html><html><head><title>Bot Check</title></head><body>Sign in</body></html>")
    with pytest.raises(SourceAcquisitionError) as exc_info:
        validate_media_gate(html_file)
    assert exc_info.value.code == SourceErrorCode.SOURCE_MEDIA_INVALID
    assert "HTML document" in str(exc_info.value)


def test_validate_media_gate_rejects_missing_video_or_audio(tmp_path):
    fake_audio_only = tmp_path / "audio.mp4"
    fake_audio_only.write_bytes(b"dummy audio container bytes")

    mock_info = MediaInfo(path=fake_audio_only, duration_s=10.0, has_video=False, has_audio=True)
    with patch("autoclip.pipeline.source_acquisition.validation.ffmpeg.probe", return_value=mock_info):
        with pytest.raises(SourceAcquisitionError) as exc_info:
            validate_media_gate(fake_audio_only)
        assert exc_info.value.code == SourceErrorCode.SOURCE_MEDIA_INVALID
        assert "no valid video stream" in str(exc_info.value)


def test_validate_media_gate_computes_sha256_on_valid_media(tmp_path):
    valid_file = tmp_path / "valid.mp4"
    payload = b"\x00\x00\x00\x20ftypisom" + b"\x11" * 1024
    valid_file.write_bytes(payload)

    expected_sha256 = hashlib.sha256(payload).hexdigest()
    mock_info = MediaInfo(path=valid_file, duration_s=15.5, has_video=True, has_audio=True, width=1920, height=1080, fps=30.0)

    with patch("autoclip.pipeline.source_acquisition.validation.ffmpeg.probe", return_value=mock_info):
        info, sha = validate_media_gate(valid_file)
        assert info.duration_s == 15.5
        assert sha == expected_sha256


# ---------------------------------------------------------------------------
# 3. YtDlpAcquisitionProvider Tests
# ---------------------------------------------------------------------------

def test_ytdlp_provider_success(tmp_path):
    provider = YtDlpAcquisitionProvider()
    assert provider.provider_name == "yt-dlp"
    assert provider.is_configured() is True

    fake_media = tmp_path / "source.mp4"
    fake_media.write_bytes(b"valid video payload")

    mock_media_info = MediaInfo(path=fake_media, duration_s=20.0, has_video=True, has_audio=True, width=1280, height=720)
    mock_yt_res = MagicMock(
        media_path=fake_media,
        media_info=mock_media_info,
        metadata={"title": "Test Video", "uploader": "AL AMR Channel"},
        method="cloud_resilient_innertube",
    )

    with patch("autoclip.pipeline.source_acquisition.providers.ytdlp_provider.YouTubeSourceAcquirer") as mock_acq_cls:
        mock_acq_inst = MagicMock()
        mock_acq_inst.acquire.return_value = mock_yt_res
        mock_acq_cls.return_value = mock_acq_inst

        result = provider.acquire("https://www.youtube.com/watch?v=12345", tmp_path)

        assert result.success is True
        assert result.provider_name == "yt-dlp"
        assert result.duration == 20.0
        assert result.provider_metadata["strategy"] == "cloud_resilient_innertube"
        assert result.provider_metadata["title"] == "Test Video"
        assert result.sha256 == hashlib.sha256(b"valid video payload").hexdigest()


def test_ytdlp_provider_maps_bot_block_to_source_access_blocked(tmp_path):
    provider = YtDlpAcquisitionProvider()

    with patch("autoclip.pipeline.source_acquisition.providers.ytdlp_provider.YouTubeSourceAcquirer") as mock_acq_cls:
        mock_acq_inst = MagicMock()
        mock_acq_inst.acquire.side_effect = YouTubeIngestError(
            "YouTube refused automated retrieval from cloud environment.",
            code=YouTubeErrorCode.EXTRACTION_BLOCKED,
        )
        mock_acq_cls.return_value = mock_acq_inst

        with pytest.raises(SourceAcquisitionError) as exc_info:
            provider.acquire("https://www.youtube.com/watch?v=blocked", tmp_path)

        assert exc_info.value.code == SourceErrorCode.SOURCE_ACCESS_BLOCKED
        assert exc_info.value.provider_name == "yt-dlp"


# ---------------------------------------------------------------------------
# 4. HttpApiAcquisitionProvider Tests
# ---------------------------------------------------------------------------

def test_http_api_provider_not_configured_by_default(monkeypatch):
    monkeypatch.delenv("AUTOCLIP_ACQUISITION_ENDPOINT", raising=False)
    provider = HttpApiAcquisitionProvider()
    assert provider.is_configured() is False

    with pytest.raises(SourceAcquisitionError) as exc_info:
        provider.acquire("https://example.com/video.mp4", Path("/tmp"))
    assert exc_info.value.code == SourceErrorCode.SOURCE_PROVIDER_UNAVAILABLE


def test_http_api_provider_configured_and_successful(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOCLIP_ACQUISITION_ENDPOINT", "https://ingest-proxy.example.com/acquire")
    monkeypatch.setenv("AUTOCLIP_ACQUISITION_KEY", "secret_key_123")

    provider = HttpApiAcquisitionProvider()
    assert provider.is_configured() is True
    assert provider.provider_name == "http-api"

    target_dir = tmp_path / "http_out"
    target_dir.mkdir()

    mock_media_info = MediaInfo(path=target_dir / "source.mp4", duration_s=30.0, has_video=True, has_audio=True)

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_resp = MagicMock(status_code=200, headers={"content-length": "12"})
        mock_resp.iter_bytes.return_value = [b"mock ", b"video ", b"data"]
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None

        mock_client.stream.return_value = mock_resp
        mock_client.__enter__.return_value = mock_client
        mock_client.__exit__.return_value = None
        mock_client_cls.return_value = mock_client

        with patch("autoclip.pipeline.source_acquisition.providers.http_api_provider.validate_media_gate", return_value=(mock_media_info, "sha256_mock")):
            result = provider.acquire("https://example.com/public-video", target_dir)

            assert result.success is True
            assert result.provider_name == "http-api"
            assert result.duration == 30.0
            assert result.sha256 == "sha256_mock"
            assert (target_dir / "source.mp4").exists()


# ---------------------------------------------------------------------------
# 5. Deterministic Fallback in SourceAcquisitionRegistry Tests
# ---------------------------------------------------------------------------

def test_registry_primary_succeeds_secondary_not_called(tmp_path):
    mock_primary = MagicMock(spec=SourceAcquisitionProvider)
    mock_primary.provider_name = "primary"
    mock_primary.is_configured.return_value = True

    fake_file = tmp_path / "source.mp4"
    fake_file.write_bytes(b"primary bytes")
    mock_primary.acquire.return_value = AcquisitionResult(
        success=True,
        local_media_path=fake_file,
        provider_name="primary",
        source_url="https://youtube.com/watch?v=1",
        duration=12.0,
        file_size=len(b"primary bytes"),
        sha256="primary_sha",
    )

    mock_secondary = MagicMock(spec=SourceAcquisitionProvider)
    mock_secondary.provider_name = "secondary"
    mock_secondary.is_configured.return_value = True

    registry = SourceAcquisitionRegistry(providers=[mock_primary, mock_secondary])
    result = registry.acquire("https://youtube.com/watch?v=1", tmp_path)

    assert result.success is True
    assert result.provider_name == "primary"
    mock_secondary.acquire.assert_not_called()


def test_registry_fallback_to_secondary_when_primary_fails(tmp_path):
    mock_primary = MagicMock(spec=SourceAcquisitionProvider)
    mock_primary.provider_name = "yt-dlp"
    mock_primary.is_configured.return_value = True
    mock_primary.acquire.side_effect = SourceAcquisitionError(
        "YouTube blocked cloud datacenter IP",
        code=SourceErrorCode.SOURCE_ACCESS_BLOCKED,
        provider_name="yt-dlp",
    )

    fake_file = tmp_path / "fallback.mp4"
    fake_file.write_bytes(b"secondary bytes")

    mock_secondary = MagicMock(spec=SourceAcquisitionProvider)
    mock_secondary.provider_name = "http-api"
    mock_secondary.is_configured.return_value = True
    mock_secondary.acquire.return_value = AcquisitionResult(
        success=True,
        local_media_path=fake_file,
        provider_name="http-api",
        source_url="https://youtube.com/watch?v=1",
        duration=25.0,
        file_size=len(b"secondary bytes"),
        sha256="secondary_sha",
    )

    registry = SourceAcquisitionRegistry(providers=[mock_primary, mock_secondary])
    result = registry.acquire("https://youtube.com/watch?v=1", tmp_path)

    assert result.success is True
    assert result.provider_name == "http-api"
    assert result.acquisition_attempts == 2
    assert result.provider_metadata["provenance"]["attempts"] == 2
    assert result.provider_metadata["provenance"]["attempts_history"][0]["provider"] == "yt-dlp"


def test_registry_raises_clean_operator_error_when_all_fail(tmp_path):
    mock_p1 = MagicMock(spec=SourceAcquisitionProvider)
    mock_p1.provider_name = "yt-dlp"
    mock_p1.is_configured.return_value = True
    mock_p1.acquire.side_effect = SourceAcquisitionError("Blocked", code=SourceErrorCode.SOURCE_ACCESS_BLOCKED)

    mock_p2 = MagicMock(spec=SourceAcquisitionProvider)
    mock_p2.provider_name = "http-api"
    mock_p2.is_configured.return_value = True
    mock_p2.acquire.side_effect = SourceAcquisitionError("Timeout", code=SourceErrorCode.SOURCE_PROVIDER_TIMEOUT)

    registry = SourceAcquisitionRegistry(providers=[mock_p1, mock_p2])

    with pytest.raises(SourceAcquisitionError) as exc_info:
        registry.acquire("https://youtube.com/watch?v=blocked_everywhere", tmp_path)

    err = exc_info.value
    assert "Please upload the video file directly" in err.hint
    assert err.attempts == 2


# ---------------------------------------------------------------------------
# 6. Ingest & Forensic Manifest Provenance Integration
# ---------------------------------------------------------------------------

def test_ingest_youtube_records_source_acquisition_provenance(tmp_path):
    settings = IngestSettings()
    fake_video = tmp_path / "source.mp4"
    fake_video.write_bytes(b"test video payload for provenance")

    mock_media_info = MediaInfo(path=fake_video, duration_s=18.0, has_video=True, has_audio=True, width=1280, height=720)
    mock_result = AcquisitionResult(
        success=True,
        local_media_path=fake_video,
        provider_name="yt-dlp",
        source_url="https://www.youtube.com/watch?v=test_prov",
        duration=18.0,
        file_size=len(b"test video payload for provenance"),
        sha256="test_sha256_hash",
        media_info=mock_media_info,
        provider_metadata={
            "provenance": {
                "provider": "yt-dlp",
                "attempts": 1,
                "sha256": "test_sha256_hash",
                "file_size": 33,
                "duration_s": 18.0,
            }
        },
    )

    with patch("autoclip.pipeline.ingest.get_default_registry") as mock_reg_get:
        mock_reg = MagicMock()
        mock_reg.acquire.return_value = mock_result
        mock_reg_get.return_value = mock_reg

        source = ingest.ingest_youtube("https://www.youtube.com/watch?v=test_prov", settings)

        assert source.type == "youtube"
        assert hasattr(source, "source_acquisition")
        assert source.source_acquisition["provider"] == "yt-dlp"
        assert source.source_acquisition["sha256"] == "test_sha256_hash"
