"""Tests for Step 2.2 Remote Runtime Hardening.

Covers:
1. YouTube cookie abstraction (cookies_file vs cookies_from_browser, AUTOCLIP_COOKIES_FILE env var).
2. Queue restart recovery (_requeue_interrupted source validation and corrupt artifact cleanup).
3. Export idempotency (reusing valid export files, avoiding duplicate DB rows, purging corrupted files).
4. Environment variable overrides for remote models and base URLs.
5. Models directory layout under AUTOCLIP_HOME.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autoclip import config, paths
from autoclip.config import IngestSettings, Settings
from autoclip.db import store
from autoclip.db.models import Clip, Export, Job, Source, new_id
from autoclip.jobs.queue import JobQueue
from autoclip.pipeline import ingest
from autoclip.pipeline.validator import ValidationResult


def test_models_dir_in_layout(autoclip_home):
    """Ensure models_dir is located under root and created by ensure_layout."""
    paths.ensure_layout()
    m_dir = paths.models_dir()
    assert m_dir == autoclip_home / "models"
    assert m_dir.is_dir()


def test_cookie_abstraction_precedence(monkeypatch, tmp_path):
    """Test cookies_file takes precedence over cookies_from_browser, and env var works."""
    dummy_cookie = tmp_path / "cookies.txt"
    dummy_cookie.write_text("# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\txyz\n")

    # Case 1: IngestSettings with cookies_file
    settings = IngestSettings(cookies_file=str(dummy_cookie), cookies_from_browser="chrome")
    with patch("yt_dlp.YoutubeDL") as mock_ydl:
        mock_instance = MagicMock()
        mock_instance.extract_info.return_value = {"id": "123"}
        mock_ydl.return_value.__enter__.return_value = mock_instance

        # Mock downloaded file finding
        with patch("autoclip.pipeline.ingest._find_downloaded_file", return_value=dummy_cookie):
            with patch("autoclip.pipeline.ingest._probe_and_validate", return_value=MagicMock()):
                ingest.ingest_youtube("https://youtube.com/watch?v=123", settings)

        opts = mock_ydl.call_args[0][0]
        assert opts.get("cookiefile") == str(dummy_cookie)
        assert "cookiesfrombrowser" not in opts

    # Case 2: Environment variable override AUTOCLIP_COOKIES_FILE
    monkeypatch.setenv("AUTOCLIP_COOKIES_FILE", str(dummy_cookie))
    settings_no_cookie = IngestSettings(cookies_from_browser="firefox")
    with patch("yt_dlp.YoutubeDL") as mock_ydl:
        mock_instance = MagicMock()
        mock_instance.extract_info.return_value = {"id": "123"}
        mock_ydl.return_value.__enter__.return_value = mock_instance

        with patch("autoclip.pipeline.ingest._find_downloaded_file", return_value=dummy_cookie):
            with patch("autoclip.pipeline.ingest._probe_and_validate", return_value=MagicMock()):
                ingest.ingest_youtube("https://youtube.com/watch?v=123", settings_no_cookie)

        opts = mock_ydl.call_args[0][0]
        assert opts.get("cookiefile") == str(dummy_cookie)
        assert "cookiesfrombrowser" not in opts

    # Case 3: No cookies_file, fallback to cookies_from_browser
    monkeypatch.delenv("AUTOCLIP_COOKIES_FILE", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
    settings_browser = IngestSettings(cookies_from_browser="edge")
    with patch("yt_dlp.YoutubeDL") as mock_ydl:
        mock_instance = MagicMock()
        mock_instance.extract_info.return_value = {"id": "123"}
        mock_ydl.return_value.__enter__.return_value = mock_instance

        with patch("autoclip.pipeline.ingest._find_downloaded_file", return_value=dummy_cookie):
            with patch("autoclip.pipeline.ingest._probe_and_validate", return_value=MagicMock()):
                ingest.ingest_youtube("https://youtube.com/watch?v=123", settings_browser)

        opts = mock_ydl.call_args[0][0]
        assert "cookiefile" not in opts
        assert opts.get("cookiesfrombrowser") == ("edge",)


def test_provider_env_overrides(monkeypatch):
    """Verify AUTOCLIP_{PROVIDER}_BASE_URL and AUTOCLIP_{PROVIDER}_MODEL override settings."""
    from autoclip.providers import build_provider

    monkeypatch.setenv("AUTOCLIP_OLLAMA_BASE_URL", "http://remote-gpu:11434")
    monkeypatch.setenv("AUTOCLIP_OLLAMA_MODEL", "qwen2.5:72b")

    p = build_provider("ollama")
    assert p.base_url == "http://remote-gpu:11434"
    assert p.model == "qwen2.5:72b"

    monkeypatch.setenv("AUTOCLIP_OPENAI_BASE_URL", "http://vllm-host:8000/v1")
    monkeypatch.setenv("AUTOCLIP_OPENAI_MODEL", "meta-llama/Llama-3-70b-Instruct")
    monkeypatch.setenv("AUTOCLIP_OPENAI_KEY", "dummy-secret-key")

    p2 = build_provider("openai")
    assert p2.base_url == "http://vllm-host:8000/v1"
    assert p2.model == "meta-llama/Llama-3-70b-Instruct"
    assert p2.api_key == "dummy-secret-key"


def test_requeue_interrupted_missing_source(initialised_db, autoclip_home):
    """If a job was running when server restarted, but source file is missing, fail it safely."""
    source_id = new_id()
    job_id = new_id()

    # Source registered with nonexistent path
    store.create_source(
        Source(
            id=source_id,
            type="upload",
            path=str(autoclip_home / "media" / "missing.mp4"),
            title="Lost File",
            duration_s=10.0,
        )
    )
    store.create_job(Job(id=job_id, source_id=source_id, status="running"))

    q = JobQueue()
    q._requeue_interrupted()

    updated = store.get_job(job_id)
    assert updated.status == "failed"
    assert "Source media file missing" in updated.error


def test_requeue_interrupted_valid_source_and_corrupt_artifact_cleanup(initialised_db, autoclip_home):
    """If source is valid, requeue job and delete any corrupt partial exports."""
    source_file = autoclip_home / "media" / "source.mp4"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_bytes(b"dummy source bytes")

    source_id = new_id()
    job_id = new_id()

    store.create_source(
        Source(
            id=source_id,
            type="upload",
            path=str(source_file),
            title="Good File",
            duration_s=10.0,
        )
    )
    store.create_job(Job(id=job_id, source_id=source_id, status="running"))

    # Create a corrupt export file for this job
    job_export_dir = paths.exports_dir() / job_id
    job_export_dir.mkdir(parents=True, exist_ok=True)
    corrupt_clip = job_export_dir / "corrupt_clip.mp4"
    corrupt_clip.write_bytes(b"bad mp4 bytes")
    corrupt_srt = job_export_dir / "corrupt_clip.srt"
    corrupt_srt.write_text("1\n00:00:01 --> 00:00:02\nhello\n")

    q = JobQueue()
    # Mock validate_media_output returning invalid for this corrupt file
    with patch(
        "autoclip.pipeline.validator.validate_media_output",
        return_value=ValidationResult(valid=False, path=corrupt_clip, errors=["FFmpeg decode check failed"]),
    ):
        q._requeue_interrupted()

    updated = store.get_job(job_id)
    assert updated.status == "queued"
    assert updated.error is None
    # Corrupt artifact should have been purged
    assert not corrupt_clip.exists()
    assert not corrupt_srt.exists()


def test_stage_export_idempotency(initialised_db, autoclip_home):
    """Verify _stage_export reuses existing valid exports, prevents re-rendering and avoids duplicate rows."""
    from autoclip.pipeline.runner import PipelineRunner
    from autoclip.pipeline.transcript import Transcript, Word
    from autoclip.pipeline.reframe.croppath import centre_crop

    source_file = autoclip_home / "media" / "source.mp4"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_bytes(b"dummy")

    source = Source(
        id=new_id(),
        type="upload",
        path=str(source_file),
        title="Test Source",
        duration_s=10.0,
        has_video=True,
    )
    store.create_source(source)

    job = Job(id=new_id(), source_id=source.id, status="running")
    store.create_job(job)

    clip = Clip(
        id=new_id(),
        job_id=job.id,
        rank=1,
        start_s=0.0,
        end_s=5.0,
        start_word=0,
        end_word=1,
        title="Clip One",
    )
    store.replace_clips(job.id, [clip])

    transcript = Transcript(
        words=[Word("hello", 0.0, 1.0), Word("world", 1.0, 2.0)],
        language="en",
    )

    crop_paths = {clip.id: centre_crop(1920, 1080, 5.0)}

    runner = PipelineRunner(job, source)

    # Pre-create the destination file as a valid rendered clip
    dest_dir = paths.exports_dir() / job.id
    dest_dir.mkdir(parents=True, exist_ok=True)
    from autoclip.pipeline import export
    existing_dest = dest_dir / export.output_filename(clip.title or f"clip-{clip.rank}", "9:16")
    existing_dest.write_bytes(b"dummy valid mp4")

    # Mock validate_media_output returning valid
    mock_valid = ValidationResult(valid=True, path=existing_dest, duration_s=5.0, width=1080, height=1920)
    with patch("autoclip.pipeline.runner.validate_media_output", return_value=mock_valid):
        with patch("autoclip.pipeline.export.export_clip") as mock_export_clip:
            runner._stage_export([clip], transcript, crop_paths)
            # Should NOT have called export_clip because valid file already exists
            mock_export_clip.assert_not_called()

    # Verify export record in database
    exports = store.list_exports(clip.id)
    assert len(exports) == 1
    assert exports[0].path == str(existing_dest)

    # Run _stage_export a second time (e.g. restart/rerun)
    with patch("autoclip.pipeline.runner.validate_media_output", return_value=mock_valid):
        with patch("autoclip.pipeline.export.export_clip") as mock_export_clip:
            runner._stage_export([clip], transcript, crop_paths)
            mock_export_clip.assert_not_called()

    # Verify NO duplicate export record was created
    exports2 = store.list_exports(clip.id)
    assert len(exports2) == 1
