"""Unit and integration tests for Piped and Invidious source acquisition providers."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autoclip.pipeline.ffmpeg import MediaInfo
from autoclip.pipeline.source_acquisition import (
    AcquisitionResult,
    InvidiousAcquisitionProvider,
    PipedAcquisitionProvider,
    ServerDownloaderProvider,
    SourceAcquisitionError,
    SourceAcquisitionRegistry,
    SourceErrorCode,
    extract_youtube_id,
    is_youtube_url,
)


# ---------------------------------------------------------------------------
# 1. URL parsing and identification tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected_id"),
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://m.youtube.com/watch?v=dQw4w9WgXcQ&feature=share", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ?t=42", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtube.com/shorts/dQw4w9WgXcQ?feature=share", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/v/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://vimeo.com/12345678", None),
        ("https://example.com/video.mp4", None),
        ("not-a-url", None),
        ("", None),
    ],
)
def test_extract_youtube_id(url: str, expected_id: str | None) -> None:
    assert extract_youtube_id(url) == expected_id


def test_is_youtube_url() -> None:
    assert is_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ") is True
    assert is_youtube_url("https://youtu.be/dQw4w9WgXcQ") is True
    assert is_youtube_url("https://example.com/video.mp4") is False
    assert is_youtube_url("https://vimeo.com/12345") is False


# ---------------------------------------------------------------------------
# 2. Piped Provider Tests
# ---------------------------------------------------------------------------


def _dummy_media_gate_result(target_file: Path) -> tuple[MediaInfo, str]:
    target_file.write_bytes(b"\x00\x00\x00\x20ftypisom" + b"\x00" * 200)
    info = MediaInfo(
        path=target_file,
        duration_s=60.0,
        has_video=True,
        has_audio=True,
        video_codec="h264",
        audio_codec="aac",
        width=1920,
        height=1080,
        fps=30.0,
    )
    return info, "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


def test_piped_provider_non_youtube_skips(tmp_path: Path) -> None:
    provider = PipedAcquisitionProvider(instances=["https://pipedapi.example.com"])
    with pytest.raises(SourceAcquisitionError) as exc_info:
        provider.acquire("https://vimeo.com/12345", tmp_path)
    assert exc_info.value.code == SourceErrorCode.SOURCE_PROVIDER_UNAVAILABLE


def test_piped_provider_progressive_stream(tmp_path: Path) -> None:
    provider = PipedAcquisitionProvider(instances=["https://pipedapi.example.com"])

    mock_streams_resp = {
        "title": "Test Video",
        "duration": 60,
        "videoStreams": [
            {
                "url": "https://video.example.com/progressive.mp4",
                "format": "MPEG-4",
                "quality": "720p",
                "mimeType": "video/mp4",
                "videoOnly": False,
            }
        ],
        "audioStreams": [],
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_streams_resp

    mock_stream_resp = MagicMock()
    mock_stream_resp.status_code = 200
    mock_stream_resp.headers = {"content-length": "1000"}
    mock_stream_resp.iter_bytes.return_value = [b"mockmp4content" * 20]
    mock_stream_resp.__enter__.return_value = mock_stream_resp
    mock_stream_resp.__exit__.return_value = None

    client_mock = MagicMock()
    client_mock.get.return_value = mock_resp
    client_mock.stream.return_value = mock_stream_resp
    client_mock.__enter__.return_value = client_mock
    client_mock.__exit__.return_value = None

    with (
        patch("httpx.Client", return_value=client_mock),
        patch(
            "autoclip.pipeline.source_acquisition.providers.piped_provider.validate_media_gate",
            side_effect=lambda p, **kw: _dummy_media_gate_result(p),
        ),
    ):
        result = provider.acquire("https://www.youtube.com/watch?v=dQw4w9WgXcQ", tmp_path)

    assert result.success is True
    assert result.provider_name == "piped"
    assert result.duration == 60.0


def test_piped_provider_failover_to_second_instance(tmp_path: Path) -> None:
    provider = PipedAcquisitionProvider(
        instances=["https://piped-down.example.com", "https://piped-up.example.com"]
    )

    resp_down = MagicMock()
    resp_down.status_code = 502

    resp_up = MagicMock()
    resp_up.status_code = 200
    resp_up.json.return_value = {
        "videoStreams": [
            {
                "url": "https://stream.example.com/v.mp4",
                "mimeType": "video/mp4",
                "videoOnly": False,
                "quality": "720p",
            }
        ],
        "audioStreams": [],
    }

    stream_resp = MagicMock()
    stream_resp.status_code = 200
    stream_resp.headers = {"content-length": "500"}
    stream_resp.iter_bytes.return_value = [b"streamdata"]
    stream_resp.__enter__.return_value = stream_resp
    stream_resp.__exit__.return_value = None

    client_mock = MagicMock()
    client_mock.get.side_effect = [resp_down, resp_up]
    client_mock.stream.return_value = stream_resp
    client_mock.__enter__.return_value = client_mock
    client_mock.__exit__.return_value = None

    with (
        patch("httpx.Client", return_value=client_mock),
        patch(
            "autoclip.pipeline.source_acquisition.providers.piped_provider.validate_media_gate",
            side_effect=lambda p, **kw: _dummy_media_gate_result(p),
        ),
    ):
        result = provider.acquire("https://youtu.be/dQw4w9WgXcQ", tmp_path)

    assert result.success is True
    assert client_mock.get.call_count == 2


def test_piped_provider_all_instances_fail(tmp_path: Path) -> None:
    provider = PipedAcquisitionProvider(instances=["https://piped1.example.com"])

    resp = MagicMock()
    resp.status_code = 500

    client_mock = MagicMock()
    client_mock.get.return_value = resp
    client_mock.__enter__.return_value = client_mock
    client_mock.__exit__.return_value = None

    with patch("httpx.Client", return_value=client_mock):
        with pytest.raises(SourceAcquisitionError) as exc_info:
            provider.acquire("https://youtu.be/dQw4w9WgXcQ", tmp_path)

    assert exc_info.value.code in (
        SourceErrorCode.SOURCE_PROVIDER_UNAVAILABLE,
        SourceErrorCode.SOURCE_ALL_PROVIDERS_FAILED,
    )


# ---------------------------------------------------------------------------
# 3. Invidious Provider Tests
# ---------------------------------------------------------------------------


def test_invidious_provider_non_youtube_skips(tmp_path: Path) -> None:
    provider = InvidiousAcquisitionProvider(instances=["https://invidious.example.com"])
    with pytest.raises(SourceAcquisitionError) as exc_info:
        provider.acquire("https://dailymotion.com/video/123", tmp_path)
    assert exc_info.value.code == SourceErrorCode.SOURCE_PROVIDER_UNAVAILABLE


def test_invidious_provider_format_streams(tmp_path: Path) -> None:
    provider = InvidiousAcquisitionProvider(instances=["https://invidious.example.com"])

    mock_resp_json = {
        "title": "Invidious Video",
        "lengthSeconds": 120,
        "formatStreams": [
            {
                "url": "https://invidious.example.com/videoplayback?id=1",
                "type": "video/mp4; codecs=\"avc1.42001E, mp4a.40.2\"",
                "quality": "medium",
                "qualityLabel": "720p",
            }
        ],
        "adaptiveFormats": [],
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_resp_json

    mock_stream_resp = MagicMock()
    mock_stream_resp.status_code = 200
    mock_stream_resp.headers = {"content-length": "2000"}
    mock_stream_resp.iter_bytes.return_value = [b"invidiousvideodata"]
    mock_stream_resp.__enter__.return_value = mock_stream_resp
    mock_stream_resp.__exit__.return_value = None

    client_mock = MagicMock()
    client_mock.get.return_value = mock_resp
    client_mock.stream.return_value = mock_stream_resp
    client_mock.__enter__.return_value = client_mock
    client_mock.__exit__.return_value = None

    with (
        patch("httpx.Client", return_value=client_mock),
        patch(
            "autoclip.pipeline.source_acquisition.providers.invidious_provider.validate_media_gate",
            side_effect=lambda p, **kw: _dummy_media_gate_result(p),
        ),
    ):
        result = provider.acquire("https://youtu.be/dQw4w9WgXcQ", tmp_path)

    assert result.success is True
    assert result.provider_name == "invidious"


# ---------------------------------------------------------------------------
# 4. Multi-Provider Fallback Registry Tests
# ---------------------------------------------------------------------------


def test_registry_fallback_chain(tmp_path: Path) -> None:
    """Piped fails -> Invidious fails -> ServerDownloader succeeds."""
    piped = PipedAcquisitionProvider(instances=["https://piped.example.com"])
    invidious = InvidiousAcquisitionProvider(instances=["https://invidious.example.com"])
    server_dl = ServerDownloaderProvider()

    # Mock Piped failure
    piped_client = MagicMock()
    resp_500 = MagicMock()
    resp_500.status_code = 500
    piped_client.get.return_value = resp_500
    piped_client.__enter__.return_value = piped_client
    piped_client.__exit__.return_value = None

    # Mock Invidious failure
    invidious_client = MagicMock()
    resp_502 = MagicMock()
    resp_502.status_code = 502
    invidious_client.get.return_value = resp_502
    invidious_client.__enter__.return_value = invidious_client
    invidious_client.__exit__.return_value = None

    # Mock ServerDownloader success
    def mock_server_acquire(source_url, target_dir, **kwargs):
        media_file = Path(target_dir) / "source.mp4"
        media_file.write_bytes(b"\x00\x00\x00\x20ftypisom" + b"\x00" * 100)
        return AcquisitionResult(
            success=True,
            local_media_path=media_file,
            provider_name="server_downloader",
            source_url=source_url,
            detected_media_type="video",
            duration=30.0,
            file_size=media_file.stat().st_size,
            sha256="abcdef123456",
        )

    registry = SourceAcquisitionRegistry(providers=[piped, invidious, server_dl])

    with (
        patch("autoclip.pipeline.source_acquisition.providers.piped_provider.httpx.Client", return_value=piped_client),
        patch("autoclip.pipeline.source_acquisition.providers.invidious_provider.httpx.Client", return_value=invidious_client),
        patch.object(server_dl, "acquire", side_effect=mock_server_acquire),
    ):
        result = registry.acquire("https://youtu.be/dQw4w9WgXcQ", tmp_path)

    assert result.success is True
    assert result.provider_name == "server-downloader"
    assert result.acquisition_attempts == 3
    assert "provenance" in result.provider_metadata
    provenance = result.provider_metadata["provenance"]
    assert provenance["provider"] == "server-downloader"
    assert provenance["attempts"] == 3
    assert len(provenance["fallback_history"]) == 2
    assert provenance["fallback_history"][0]["provider"] == "piped"
    assert provenance["fallback_history"][1]["provider"] == "invidious"


def test_registry_non_youtube_skips_piped_and_invidious(tmp_path: Path) -> None:
    """Non-YouTube URL skips Piped and Invidious without aborting the chain."""
    piped = PipedAcquisitionProvider()
    invidious = InvidiousAcquisitionProvider()
    server_dl = ServerDownloaderProvider()

    def mock_server_acquire(source_url, target_dir, **kwargs):
        media_file = Path(target_dir) / "source.mp4"
        media_file.write_bytes(b"\x00\x00\x00\x20ftypisom" + b"\x00" * 100)
        return AcquisitionResult(
            success=True,
            local_media_path=media_file,
            provider_name="server-downloader",
            source_url=source_url,
            duration=15.0,
            file_size=media_file.stat().st_size,
            sha256="feedbeef1234",
        )

    registry = SourceAcquisitionRegistry(providers=[piped, invidious, server_dl])

    with patch.object(server_dl, "acquire", side_effect=mock_server_acquire):
        result = registry.acquire("https://vimeo.com/987654321", tmp_path)

    assert result.success is True
    assert result.provider_name == "server-downloader"
    assert result.acquisition_attempts == 3
