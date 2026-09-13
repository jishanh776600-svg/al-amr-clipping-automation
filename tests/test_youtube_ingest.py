"""Tests for upstream yt-dlp YouTube ingestion.

Covers:
1. URL pattern detection (standard watch, youtu.be, shorts, embed, non-YouTube URLs).
2. Headless yt-dlp invocation (never requests Chrome/browser profile cookies).
3. JS runtime challenge discovery (deno, node).
4. Cookie file handling (explicit file, YOUTUBE_COOKIES_TEXT env, temporary file cleanup).
5. Error translation and classification (all YOUTUBE_* error codes).
6. Post-download media validation (zero-byte, HTML response, FFprobe stream check).
7. Preservation of non-YouTube URL and local file paths.
8. Orchestrator retry policy (transient network errors vs permanent extraction errors).
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yt_dlp

from autoclip.config import IngestSettings
from autoclip.jobs.orchestrator import is_retryable_error
from autoclip.pipeline import ingest
from autoclip.pipeline.ingest import (
    YOUTUBE_AUTH_REQUIRED,
    YOUTUBE_DOWNLOAD_FAILED,
    YOUTUBE_EXTRACTION_BLOCKED,
    YOUTUBE_FORMAT_ERROR,
    YOUTUBE_INVALID_URL,
    YOUTUBE_MEDIA_INVALID,
    YOUTUBE_NETWORK_ERROR,
    YOUTUBE_VIDEO_UNAVAILABLE,
    YouTubeErrorCode,
    YouTubeIngestError,
    is_youtube_url,
    validate_downloaded_media,
)


# ---------------------------------------------------------------------------
# 1. URL Pattern Detection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", True),
        ("http://youtube.com/watch?v=dQw4w9WgXcQ", True),
        ("https://youtu.be/dQw4w9WgXcQ", True),
        ("https://www.youtube.com/shorts/3iZkQy7lFm4", True),
        ("https://youtube.com/shorts/3iZkQy7lFm4?feature=share", True),
        ("https://www.youtube.com/embed/dQw4w9WgXcQ", True),
        ("https://www.youtube.com/v/dQw4w9WgXcQ", True),
        ("https://www.youtube.com/live/dQw4w9WgXcQ", True),
        ("https://example.com/video.mp4", False),
        ("https://vimeo.com/12345678", False),
        ("/local/path/to/video.mp4", False),
        ("C:\\Users\\video.mp4", False),
        ("", False),
        (None, False),
    ],
)
def test_is_youtube_url(url, expected):
    assert is_youtube_url(url) is expected


# ---------------------------------------------------------------------------
# 2. Headless yt-dlp Invocation & Cookie Handling
# ---------------------------------------------------------------------------

def test_ytdlp_opts_headless_no_chrome_profiles(tmp_path):
    """yt-dlp must never attempt desktop browser profile extraction in headless environments."""
    settings = IngestSettings()
    fake_video = tmp_path / "video.mp4"
    fake_video.write_bytes(b"dummy mp4")

    with patch("yt_dlp.YoutubeDL") as mock_ydl:
        mock_inst = MagicMock()
        mock_inst.extract_info.return_value = {"id": "test1", "title": "Test"}
        mock_ydl.return_value.__enter__.return_value = mock_inst

        with patch("autoclip.pipeline.ingest._find_downloaded_file", return_value=fake_video):
            with patch("autoclip.pipeline.ingest.validate_downloaded_media", return_value=MagicMock()):
                ingest.ingest_youtube("https://www.youtube.com/watch?v=test1", settings)

        opts = mock_ydl.call_args[0][0]
        # Critical: no browser profile extraction
        assert "cookiesfrombrowser" not in opts
        assert "cookiefile" not in opts
        # Format string must be resilient
        assert "bestvideo" in opts["format"]
        assert "1080" in opts["format"]


def test_ytdlp_explicit_cookie_file(tmp_path):
    """Explicit Netscape cookies file should be passed cleanly to yt-dlp."""
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("# Netscape HTTP Cookie File\n")
    settings = IngestSettings(cookies_file=str(cookie_file))

    fake_video = tmp_path / "video.mp4"
    fake_video.write_bytes(b"dummy mp4")

    with patch("yt_dlp.YoutubeDL") as mock_ydl:
        mock_inst = MagicMock()
        mock_inst.extract_info.return_value = {"id": "test2"}
        mock_ydl.return_value.__enter__.return_value = mock_inst

        with patch("autoclip.pipeline.ingest._find_downloaded_file", return_value=fake_video):
            with patch("autoclip.pipeline.ingest.validate_downloaded_media", return_value=MagicMock()):
                ingest.ingest_youtube("https://www.youtube.com/watch?v=test2", settings)

        opts = mock_ydl.call_args[0][0]
        assert opts.get("cookiefile") == str(cookie_file)


def test_ytdlp_cookies_text_env_cleaned_up(monkeypatch, tmp_path):
    """YOUTUBE_COOKIES_TEXT secret is written to temporary file and safely unlinked after download."""
    cookie_text = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tsecret\n"
    monkeypatch.setenv("YOUTUBE_COOKIES_TEXT", cookie_text)

    fake_video = tmp_path / "video.mp4"
    fake_video.write_bytes(b"dummy mp4")

    captured_cookie_path = None

    def fake_extract_info(url, download=True):
        nonlocal captured_cookie_path
        captured_cookie_path = Path(mock_ydl.call_args[0][0]["cookiefile"])
        # The temporary cookie file should exist during execution
        assert captured_cookie_path.exists()
        assert captured_cookie_path.read_text() == cookie_text
        return {"id": "test3"}

    with patch("yt_dlp.YoutubeDL") as mock_ydl:
        mock_inst = MagicMock()
        mock_inst.extract_info.side_effect = fake_extract_info
        mock_ydl.return_value.__enter__.return_value = mock_inst

        with patch("autoclip.pipeline.ingest._find_downloaded_file", return_value=fake_video):
            with patch("autoclip.pipeline.ingest.validate_downloaded_media", return_value=MagicMock()):
                ingest.ingest_youtube("https://www.youtube.com/watch?v=test3", IngestSettings())

    # After execution, the temporary cookie file must be wiped
    assert captured_cookie_path is not None
    assert not captured_cookie_path.exists()


# ---------------------------------------------------------------------------
# 3. JS Runtime Challenge Discovery
# ---------------------------------------------------------------------------

def test_ytdlp_js_runtime_discovery_deno(tmp_path):
    """When Deno is on PATH, js_engine is configured for challenge handling."""
    fake_video = tmp_path / "video.mp4"
    fake_video.write_bytes(b"dummy mp4")

    with patch("shutil.which", side_effect=lambda cmd: "/usr/bin/deno" if cmd == "deno" else None):
        with patch("yt_dlp.YoutubeDL") as mock_ydl:
            mock_inst = MagicMock()
            mock_inst.extract_info.return_value = {"id": "test_deno"}
            mock_ydl.return_value.__enter__.return_value = mock_inst

            with patch("autoclip.pipeline.ingest._find_downloaded_file", return_value=fake_video):
                with patch("autoclip.pipeline.ingest.validate_downloaded_media", return_value=MagicMock()):
                    ingest.ingest_youtube("https://www.youtube.com/watch?v=test_deno", IngestSettings())

            opts = mock_ydl.call_args[0][0]
            assert opts.get("js_engine") == "deno"


def test_ytdlp_js_runtime_discovery_node(tmp_path):
    """When Node is on PATH and Deno is absent, js_engine falls back to node."""
    fake_video = tmp_path / "video.mp4"
    fake_video.write_bytes(b"dummy mp4")

    with patch("shutil.which", side_effect=lambda cmd: "/usr/bin/node" if cmd == "node" else None):
        with patch("yt_dlp.YoutubeDL") as mock_ydl:
            mock_inst = MagicMock()
            mock_inst.extract_info.return_value = {"id": "test_node"}
            mock_ydl.return_value.__enter__.return_value = mock_inst

            with patch("autoclip.pipeline.ingest._find_downloaded_file", return_value=fake_video):
                with patch("autoclip.pipeline.ingest.validate_downloaded_media", return_value=MagicMock()):
                    ingest.ingest_youtube("https://www.youtube.com/watch?v=test_node", IngestSettings())

            opts = mock_ydl.call_args[0][0]
            assert opts.get("js_engine") == "node"


# ---------------------------------------------------------------------------
# 4. Error Code Classifications
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "exc_msg,expected_code",
    [
        ("Video unavailable. This video has been removed by the uploader", YOUTUBE_VIDEO_UNAVAILABLE),
        ("HTTP Error 404: Not Found", YOUTUBE_VIDEO_UNAVAILABLE),
        ("Sign in to confirm your age", YOUTUBE_AUTH_REQUIRED),
        ("Private video. Sign in if you've been granted access", YOUTUBE_AUTH_REQUIRED),
        ("Join this channel to get access to members-only content", YOUTUBE_AUTH_REQUIRED),
        ("Sign in to confirm you're not a bot", YOUTUBE_AUTH_REQUIRED),
        ("HTTP Error 429: Too Many Requests", YOUTUBE_EXTRACTION_BLOCKED),
        ("HTTP Error 403: Forbidden", YOUTUBE_EXTRACTION_BLOCKED),
        ("YouTube is blocking automated requests from this IP", YOUTUBE_EXTRACTION_BLOCKED),
        ("Connection timed out after 30 seconds", YOUTUBE_NETWORK_ERROR),
        ("Remote end closed connection without response", YOUTUBE_NETWORK_ERROR),
        ("Requested format is not available", YOUTUBE_FORMAT_ERROR),
        ("No video formats found", YOUTUBE_FORMAT_ERROR),
        ("Unknown internal yt-dlp failure occurred", YOUTUBE_DOWNLOAD_FAILED),
    ],
)
def test_error_translation_classification(exc_msg, expected_code):
    exc = yt_dlp.utils.DownloadError(exc_msg)
    ingest_err = ingest._translate_ytdlp_error(exc, IngestSettings())
    assert isinstance(ingest_err, YouTubeIngestError)
    assert ingest_err.code == expected_code
    # Never leak internal /root/ Chrome database paths
    assert "chrome" not in ingest_err.message.lower()


# ---------------------------------------------------------------------------
# 5. Media Validation
# ---------------------------------------------------------------------------

def test_media_validation_zero_byte(tmp_path):
    empty_file = tmp_path / "empty.mp4"
    empty_file.write_bytes(b"")

    with pytest.raises(YouTubeIngestError) as exc_info:
        validate_downloaded_media(empty_file)
    assert exc_info.value.code == YOUTUBE_MEDIA_INVALID
    assert "0 bytes" in str(exc_info.value)


def test_media_validation_html_disguised(tmp_path):
    html_file = tmp_path / "blocked.mp4"
    html_file.write_text("<!DOCTYPE html><html><body>Error 403 Access Denied</body></html>")

    with pytest.raises(YouTubeIngestError) as exc_info:
        validate_downloaded_media(html_file)
    assert exc_info.value.code == YOUTUBE_MEDIA_INVALID
    assert "HTML document" in str(exc_info.value)


def test_media_validation_corrupt_stream(tmp_path):
    corrupt_file = tmp_path / "corrupt.mp4"
    corrupt_file.write_bytes(b"\x00\x00\x00\x20ftypmp42" + b"bad content" * 20)

    # Mock ffprobe returning duration 0
    with patch("autoclip.pipeline.ffmpeg.probe") as mock_probe:
        mock_probe.return_value = MagicMock(duration_s=0.0, has_video=True, has_audio=True)
        with pytest.raises(YouTubeIngestError) as exc_info:
            validate_downloaded_media(corrupt_file)
        assert exc_info.value.code == YOUTUBE_MEDIA_INVALID

    # Mock ffprobe returning no video stream
    with patch("autoclip.pipeline.ffmpeg.probe") as mock_probe:
        mock_probe.return_value = MagicMock(duration_s=10.0, has_video=False, has_audio=True)
        with pytest.raises(YouTubeIngestError) as exc_info:
            validate_downloaded_media(corrupt_file)
        assert exc_info.value.code == YOUTUBE_MEDIA_INVALID


def test_media_validation_valid(tmp_path):
    valid_file = tmp_path / "valid.mp4"
    valid_file.write_bytes(b"\x00\x00\x00\x20ftypisom" + b"\x00" * 1024)

    mock_media = MagicMock(duration_s=42.5, has_video=True, has_audio=True)
    with patch("autoclip.pipeline.ffmpeg.probe", return_value=mock_media):
        res = validate_downloaded_media(valid_file)
        assert res == mock_media


# ---------------------------------------------------------------------------
# 6. Preservation of Direct URL and Local Ingestion
# ---------------------------------------------------------------------------

def test_ingest_url_dispatches_youtube_vs_direct():
    with patch("autoclip.pipeline.ingest.ingest_youtube") as mock_yt:
        mock_yt.return_value = MagicMock()
        ingest.ingest_url("https://www.youtube.com/watch?v=abc")
        mock_yt.assert_called_once()

    with patch("autoclip.pipeline.ingest.ingest_direct_url") as mock_direct:
        mock_direct.return_value = MagicMock()
        ingest.ingest_url("https://example.com/raw_video.mp4")
        mock_direct.assert_called_once()


# ---------------------------------------------------------------------------
# 7. Orchestrator Retry Semantics
# ---------------------------------------------------------------------------

def test_retry_semantics_youtube_errors():
    """Transient network errors can retry; permanent extraction errors must fail immediately."""
    assert is_retryable_error("youtube_network_error: connection timed out") is True
    assert is_retryable_error("youtube_invalid_url: malformed id") is False
    assert is_retryable_error("youtube_video_unavailable: 404 removed") is False
    assert is_retryable_error("youtube_auth_required: age restricted") is False
    assert is_retryable_error("youtube_extraction_blocked: 403 forbidden") is False
    assert is_retryable_error("youtube_format_error: no formats") is False
    assert is_retryable_error("youtube_media_invalid: 0 bytes") is False
    assert is_retryable_error("youtube_download_failed: fatal exception") is False
