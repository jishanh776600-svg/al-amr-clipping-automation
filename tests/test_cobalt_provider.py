"""Unit tests for CobaltAcquisitionProvider and its integration with SourceAcquisitionRegistry."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from autoclip.pipeline.ffmpeg import MediaInfo
from autoclip.pipeline.source_acquisition import (
    AcquisitionResult,
    CobaltAcquisitionProvider,
    SourceAcquisitionError,
    SourceAcquisitionRegistry,
    SourceErrorCode,
)


def _fake_media_gate_success(target_file: Path):
    target_file.write_bytes(b"dummy mp4 media stream")
    fake_info = MediaInfo(
        path=target_file,
        duration_s=120.0,
        has_video=True,
        has_audio=True,
        video_codec="h264",
        audio_codec="aac",
        width=1920,
        height=1080,
    )
    result = MagicMock()
    result.detected_media_type = "video/mp4"
    result.duration = 120.0
    result.file_size = 23
    result.sha256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    result.media_info = fake_info
    return result


def test_cobalt_provider_default_init() -> None:
    provider = CobaltAcquisitionProvider()
    assert provider.provider_name == "cobalt"
    assert provider.is_configured() is True
    assert "https://api.cobalt.tools" in provider.instances


def test_cobalt_provider_custom_instances() -> None:
    provider = CobaltAcquisitionProvider(
        instances=["https://custom-cobalt.internal:9000"],
        api_key="secret-token",
    )
    assert provider.instances == ["https://custom-cobalt.internal:9000"]
    assert provider._api_key == "secret-token"


def test_cobalt_provider_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOCLIP_COBALT_ENDPOINT", "https://env-cobalt.internal")
    monkeypatch.setenv("AUTOCLIP_COBALT_API_KEY", "env-key-123")
    provider = CobaltAcquisitionProvider()
    assert provider.instances == ["https://env-cobalt.internal"]
    assert provider._api_key == "env-key-123"


def test_cobalt_provider_rejects_ssrf(tmp_path: Path) -> None:
    provider = CobaltAcquisitionProvider(instances=["https://valid-cobalt.tools"])
    # Disallowed private IP source URL
    with pytest.raises(SourceAcquisitionError) as exc_info:
        provider.acquire("http://192.168.1.100/video.mp4", tmp_path)
    assert exc_info.value.code == SourceErrorCode.SOURCE_ACCESS_BLOCKED


def test_cobalt_provider_successful_tunnel_acquisition(tmp_path: Path) -> None:
    provider = CobaltAcquisitionProvider(instances=["https://api.cobalt.tools"])
    target_dir = tmp_path / "cobalt_success"

    # Mock POST / returning tunnel URL
    mock_post_resp = MagicMock()
    mock_post_resp.status_code = 200
    mock_post_resp.json.return_value = {
        "status": "tunnel",
        "url": "https://cdn.cobalt.tools/tunnel/sample.mp4",
        "filename": "sample.mp4",
    }

    # Mock streaming GET on tunnel URL
    mock_stream_resp = MagicMock()
    mock_stream_resp.status_code = 200
    mock_stream_resp.headers = {"Content-Length": "1024"}
    mock_stream_resp.iter_bytes.return_value = [b"chunk1", b"chunk2"]

    progress_calls = []

    def _progress(fraction: float):
        progress_calls.append(fraction)

    with patch("httpx.Client.post", return_value=mock_post_resp), \
         patch("httpx.Client.stream") as mock_stream, \
         patch("autoclip.pipeline.source_acquisition.providers.cobalt_provider.validate_media_gate", side_effect=_fake_media_gate_success):
        
        mock_stream.return_value.__enter__.return_value = mock_stream_resp
        res = provider.acquire("https://www.youtube.com/watch?v=dQw4w9WgXcQ", target_dir, on_progress=_progress)

    assert isinstance(res, AcquisitionResult)
    assert res.local_media_path.name == "source.mp4"
    assert res.duration == 120.0
    assert res.provider_metadata["provider"] == "cobalt"
    assert res.provider_metadata["instance"] == "https://api.cobalt.tools"
    assert len(progress_calls) > 0


def test_cobalt_provider_handles_picker_response(tmp_path: Path) -> None:
    provider = CobaltAcquisitionProvider(instances=["https://api.cobalt.tools"])
    target_dir = tmp_path / "cobalt_picker"

    mock_post_resp = MagicMock()
    mock_post_resp.status_code = 200
    mock_post_resp.json.return_value = {
        "status": "picker",
        "picker": [{"type": "video", "url": "https://cdn.cobalt.tools/stream.mp4"}],
    }

    mock_stream_resp = MagicMock()
    mock_stream_resp.status_code = 200
    mock_stream_resp.headers = {"Content-Length": "512"}
    mock_stream_resp.iter_bytes.return_value = [b"video_bytes"]

    with patch("httpx.Client.post", return_value=mock_post_resp), \
         patch("httpx.Client.stream") as mock_stream, \
         patch("autoclip.pipeline.source_acquisition.providers.cobalt_provider.validate_media_gate", side_effect=_fake_media_gate_success):
        
        mock_stream.return_value.__enter__.return_value = mock_stream_resp
        res = provider.acquire("https://www.youtube.com/watch?v=dQw4w9WgXcQ", target_dir)

    assert res.local_media_path.exists()
    assert res.provider_metadata["provider"] == "cobalt"


def test_cobalt_provider_handles_error_response(tmp_path: Path) -> None:
    provider = CobaltAcquisitionProvider(instances=["https://api.cobalt.tools"])
    target_dir = tmp_path / "cobalt_error"

    mock_post_resp = MagicMock()
    mock_post_resp.status_code = 400
    mock_post_resp.json.return_value = {
        "status": "error",
        "error": {"code": "error.api.auth.jwt.missing"},
    }

    with patch("httpx.Client.post", return_value=mock_post_resp):
        with pytest.raises(SourceAcquisitionError) as exc_info:
            provider.acquire("https://www.youtube.com/watch?v=dQw4w9WgXcQ", target_dir)

    assert exc_info.value.code == SourceErrorCode.SOURCE_PROVIDER_UNAVAILABLE
    assert "error.api.auth.jwt.missing" in exc_info.value.message


def test_cobalt_provider_failover_across_instances(tmp_path: Path) -> None:
    provider = CobaltAcquisitionProvider(instances=["https://down.cobalt.tools", "https://backup.cobalt.tools"])
    target_dir = tmp_path / "cobalt_failover"

    # Instance 1 raises connection error
    # Instance 2 succeeds
    mock_post_resp2 = MagicMock()
    mock_post_resp2.status_code = 200
    mock_post_resp2.json.return_value = {
        "status": "tunnel",
        "url": "https://backup.cobalt.tools/tunnel.mp4",
    }

    mock_stream_resp = MagicMock()
    mock_stream_resp.status_code = 200
    mock_stream_resp.headers = {"Content-Length": "256"}
    mock_stream_resp.iter_bytes.return_value = [b"stream"]

    def mock_post(url, **kwargs):
        if "down" in url:
            raise httpx.ConnectError("Connection refused")
        return mock_post_resp2

    with patch("httpx.Client.post", side_effect=mock_post), \
         patch("httpx.Client.stream") as mock_stream, \
         patch("autoclip.pipeline.source_acquisition.providers.cobalt_provider.validate_media_gate", side_effect=_fake_media_gate_success):
        
        mock_stream.return_value.__enter__.return_value = mock_stream_resp
        res = provider.acquire("https://www.youtube.com/watch?v=dQw4w9WgXcQ", target_dir)

    assert res.provider_metadata["instance"] == "https://backup.cobalt.tools"


def test_registry_integration_with_cobalt() -> None:
    registry = SourceAcquisitionRegistry()
    provider_names = [p.provider_name for p in registry.providers]
    assert provider_names == ["cobalt", "piped", "invidious", "server-downloader", "yt-dlp", "http-api"]
