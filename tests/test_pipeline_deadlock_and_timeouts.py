"""Tests for pipeline deadlock prevention, timeouts, idempotency, and transcription resilience."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autoclip.config import WhisperSettings
from autoclip.jobs.dispatcher import get_dispatch_mode, is_github_dispatch_enabled
from autoclip.pipeline import ffmpeg, prepare, transcribe
from autoclip.pipeline.ffmpeg import FFmpegError


def test_dispatcher_default_mode_and_auto_detection(monkeypatch):
    monkeypatch.delenv("AUTOCLIP_DISPATCH_MODE", raising=False)
    monkeypatch.delenv("GITHUB_PAT", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    # Defaults to auto
    assert get_dispatch_mode() == "auto"
    assert is_github_dispatch_enabled() is False

    # With GITHUB_PAT, auto enables github dispatch
    monkeypatch.setenv("GITHUB_PAT", "ghp_test123456789")
    assert is_github_dispatch_enabled() is True

    # Explicit local override wins
    monkeypatch.setenv("AUTOCLIP_DISPATCH_MODE", "local")
    assert get_dispatch_mode() == "local"
    assert is_github_dispatch_enabled() is False


def test_ffmpeg_probe_timeout():
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["ffprobe"], timeout=1.0)

        with pytest.raises(FFmpegError, match="timed out"):
            ffmpeg.probe(Path("nonexistent.mp4"), timeout_s=1.0)


def test_ffmpeg_run_timeout_watchdog():
    with patch("subprocess.Popen") as mock_popen:
        proc = MagicMock()
        proc.poll.return_value = None  # Process never finishes on its own
        proc.wait.side_effect = subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=0.1)
        proc.stdout = []
        proc.stderr = []
        mock_popen.return_value = proc

        with pytest.raises(FFmpegError, match="timed out"):
            ffmpeg.run(["ffmpeg", "-i", "in.mp4", "out.mp4"], timeout_s=0.1)

        assert proc.kill.called or proc.terminate.called


def test_prepare_extract_audio_atomic_failure(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"dummy source")
    dest = tmp_path / "dest.wav"

    # Simulate failure in ffmpeg.run
    with patch("autoclip.pipeline.ffmpeg.run", side_effect=FFmpegError("Simulated FFmpeg failure", command=["ffmpeg"])):
        with pytest.raises(FFmpegError):
            prepare.extract_audio(source, dest)

    # Neither dest nor temporary file should remain
    assert not dest.exists()
    assert not (tmp_path / "dest.tmp.wav").exists()


def test_prepare_generate_thumbnails_idempotency(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"dummy source")
    thumbs_dir = tmp_path / "thumbnails"
    thumbs_dir.mkdir()

    # Pre-populate a thumbnail
    (thumbs_dir / "thumb_0001.jpg").write_bytes(b"fake thumb")

    with patch("autoclip.pipeline.ffmpeg.run") as mock_run:
        prepare.generate_thumbnails(source, thumbs_dir)
        # Should not have called ffmpeg because thumbnails already exist
        mock_run.assert_not_called()


def test_transcribe_fallback_on_oom(tmp_path):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"fake audio")
    settings = WhisperSettings(model="small")

    mock_segment = MagicMock()
    mock_segment.words = [MagicMock(word="hello", start=0.0, end=0.5)]
    mock_segment.text = "hello"
    mock_segment.start = 0.0
    mock_segment.end = 0.5

    mock_info = MagicMock()
    mock_info.duration = 1.0
    mock_info.language = "en"

    with patch("faster_whisper.WhisperModel") as mock_whisper:
        def side_effect(model_name, **kwargs):
            if model_name == "small":
                raise MemoryError("CUDA out of memory")
            mock_inst = MagicMock()
            mock_inst.transcribe.return_value = ([mock_segment], mock_info)
            return mock_inst

        mock_whisper.side_effect = side_effect

        transcript = transcribe.transcribe(audio, settings, duration_s=1.0)
        assert transcript.model == "base"
        assert len(transcript.words) == 1
        assert transcript.words[0].text == "hello"
