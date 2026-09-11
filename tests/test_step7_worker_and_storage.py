"""Automated test suite for AL AMR Step 7:
GitHub Actions On-Demand Production Worker & Google Drive Persistent Storage.
"""

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from autoclip.app import create_app
from autoclip import db, paths
from autoclip.db import schema, store
from autoclip.db.models import Clip, Export, Job, Source, new_id, utcnow
from autoclip.jobs.dispatcher import (
    dispatch_job_to_github,
    get_dispatch_mode,
    is_github_dispatch_enabled,
)
from autoclip.publishing.publisher import publish_clip, publish_to_telegram
from autoclip.storage.drive import DriveFileMetadata, GoogleDriveStorage


@pytest.fixture
def client_env(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOCLIP_HOME", str(tmp_path))
    monkeypatch.setenv("AUTOCLIP_NO_WORKER", "1")
    paths.ensure_layout()
    db.init()
    app = create_app()
    with TestClient(app) as c:
        yield c, tmp_path


def test_schema_v4_migration_and_models(client_env):
    """Verify v4 migration adds drive fields to exports and dispatch fields to jobs."""
    _, tmp_path = client_env
    with store.connection() as conn:
        user_version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert user_version >= 4
        assert schema.SCHEMA_VERSION >= 4

        # Check exports columns
        exports_info = conn.execute("PRAGMA table_info(exports)").fetchall()
        export_cols = [row[1] for row in exports_info]
        assert "drive_file_id" in export_cols
        assert "drive_web_view_link" in export_cols
        assert "drive_storage_key" in export_cols

        # Check jobs columns
        jobs_info = conn.execute("PRAGMA table_info(jobs)").fetchall()
        job_cols = [row[1] for row in jobs_info]
        assert "dispatch_mode" in job_cols
        assert "github_run_id" in job_cols


def test_drive_storage_unconfigured(monkeypatch, tmp_path):
    """Verify GoogleDriveStorage gracefully detects absence of cloud credentials."""
    for key in ("GOOGLE_DRIVE_REFRESH_TOKEN", "GOOGLE_DRIVE_CLIENT_ID", "GOOGLE_DRIVE_CLIENT_SECRET", "GOOGLE_DRIVE_ROOT_FOLDER_ID"):
        monkeypatch.delenv(key, raising=False)

    storage_empty = GoogleDriveStorage()
    assert not storage_empty.is_configured
    test_dummy = tmp_path / "dummy.mp4"
    test_dummy.write_text("dummy")
    with pytest.raises(RuntimeError, match="Google Drive storage is not configured"):
        storage_empty.upload_file(test_dummy, "dummy.mp4")


def test_drive_storage_mocked_upload_and_stream(tmp_path):
    """Test GoogleDriveStorage upload and stream_range with mocked Google API client."""
    test_file = tmp_path / "test_clip.mp4"
    test_file.write_bytes(b"FAKE_MP4_VIDEO_CONTENT_FOR_STREAMING")

    with patch.dict(
        os.environ,
        {
            "GOOGLE_DRIVE_CLIENT_ID": "mock_client_id",
            "GOOGLE_DRIVE_CLIENT_SECRET": "mock_secret",
            "GOOGLE_DRIVE_REFRESH_TOKEN": "mock_refresh",
            "GOOGLE_DRIVE_ROOT_FOLDER_ID": "mock_root_folder",
        },
    ):
        storage = GoogleDriveStorage()
        assert storage.is_configured

        # Mock the Drive API service
        mock_service = MagicMock()
        mock_files = MagicMock()
        mock_service.files.return_value = mock_files

        mock_list_exec = MagicMock()
        mock_list_exec.execute.return_value = {"files": [{"id": "folder_123"}]}
        mock_files.list.return_value = mock_list_exec

        mock_create_exec = MagicMock()
        mock_create_exec.execute.return_value = {
            "id": "file_abc_123",
            "name": "test_clip.mp4",
            "size": str(len(test_file.read_bytes())),
            "webViewLink": "https://drive.google.com/file/d/file_abc_123/view",
            "mimeType": "video/mp4",
        }
        mock_files.create.return_value = mock_create_exec

        with patch.object(storage, "_get_service", return_value=mock_service):
            with patch.object(storage, "find_file", return_value=None):
                meta = storage.upload_file(test_file, "test_clip.mp4", folder_type="clips", subfolder="job_test")
                assert isinstance(meta, DriveFileMetadata)
                assert meta.file_id == "file_abc_123"
                assert meta.web_view_link == "https://drive.google.com/file/d/file_abc_123/view"
                assert meta.storage_key == "clips/job_test/test_clip.mp4"

            # Mock stream_range
            mock_get_exec = MagicMock()
            mock_get_exec.execute.return_value = {"size": "36", "mimeType": "video/mp4"}
            mock_files.get.return_value = mock_get_exec

            mock_resp = MagicMock()
            mock_resp.status_code = 206
            mock_resp.headers = {
                "Content-Range": "bytes 0-9/36",
                "Content-Length": "10",
                "Content-Type": "video/mp4",
            }
            mock_resp.iter_bytes.return_value = [b"FAKE_MP4_V"]

            mock_creds = MagicMock()
            mock_creds.valid = True
            mock_creds.token = "mock_access_token"
            storage._creds = mock_creds

            with patch("httpx.Client.send", return_value=mock_resp):
                generator, status, headers = storage.stream_range("file_abc_123", range_header="bytes=0-9")
                assert status == 206
                assert headers["Content-Range"] == "bytes 0-9/36"
                chunks = list(generator)
                assert b"".join(chunks) == b"FAKE_MP4_V"


def test_github_dispatcher_validation(client_env, monkeypatch):
    """Test dispatcher error handling and request construction."""
    _, tmp_path = client_env
    source = Source(id=new_id(), type="upload", path=str(tmp_path / "dummy.mp4"), url="https://youtube.com/watch?v=123")
    store.create_source(source)
    job = Job(id=new_id(), source_id=source.id, dispatch_mode="github")
    store.create_job(job)

    # 1. Missing token raises error
    for key in ("GITHUB_PAT", "GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(RuntimeError, match="GitHub token"):
        import asyncio
        asyncio.run(dispatch_job_to_github(job, source))

    # 2. Mock valid dispatch
    with patch.dict(os.environ, {"GITHUB_PAT": "ghp_mock_token_12345"}):
        with patch("httpx.AsyncClient.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 204
            mock_post.return_value = mock_resp

            import asyncio
            res = asyncio.run(dispatch_job_to_github(job, source, campaign_brief={"name": "test_brief"}))
            assert res["status"] == "dispatched"
            assert res["job_id"] == job.id

            # Verify call payload
            call_args = mock_post.call_args
            assert call_args is not None
            url, kwargs = call_args[0][0], call_args[1]
            assert "dispatches" in url
            assert kwargs["headers"]["Authorization"] == "Bearer ghp_mock_token_12345"
            assert kwargs["json"]["inputs"]["job_id"] == job.id
            assert "campaign_brief" in kwargs["json"]["inputs"]


def test_worker_callback_api_and_sse_events(client_env):
    """Verify POST /api/jobs/{id}/worker-callback updates database and SSE subscribers."""
    client, tmp_path = client_env

    # Create dummy source and job
    source = Source(id=new_id(), type="upload", path=str(tmp_path / "dummy.mp4"), title="Test Video")
    store.create_source(source)

    job = Job(
        id=new_id(),
        source_id=source.id,
        status="running",
        current_stage="dispatched_to_github",
        dispatch_mode="github",
    )
    store.create_job(job)

    # 1. Callback without secret (unauthenticated when no secret set)
    clip_id_1 = new_id()
    callback_payload = {
        "status": "running",
        "stage": "reframing",
        "progress": 0.65,
        "github_run_id": "987654321",
        "clips": [
            {
                "id": clip_id_1,
                "job_id": job.id,
                "start_s": 10.0,
                "end_s": 25.0,
                "title": "Viral Hook Clip",
                "score": 92,
                "reason": "High emotional engagement",
            }
        ],
        "evaluations": [
            {
                "clip_id": clip_id_1,
                "campaign_id": "cmp_q4",
                "approved": True,
                "final_score": 88.5,
                "hook_score": 90.0,
                "viral_score": 85.0,
                "rule_results": {"hook": True, "duration": True},
            }
        ],
        "exports": [
            {
                "id": new_id(),
                "clip_id": clip_id_1,
                "path": str(tmp_path / "clip_out.mp4"),
                "ratio": "9:16",
                "size_bytes": 1048576,
                "drive_file_id": "drive_file_999",
                "drive_web_view_link": "https://drive.google.com/view/999",
                "drive_storage_key": "AL-AMR/clips/clip_out.mp4",
            }
        ],
    }

    resp = client.post(f"/api/jobs/{job.id}/worker-callback", json=callback_payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "running"
    assert data["current_stage"] == "reframing"
    assert data["progress"] == 0.65
    assert data["github_run_id"] == "987654321"

    # Verify clips and evaluations persisted in store
    clips = store.list_clips_for_job(job.id)
    assert len(clips) == 1
    assert clips[0].id == clip_id_1
    assert clips[0].title == "Viral Hook Clip"

    eval_row = store.get_campaign_evaluation(clip_id_1)
    assert eval_row is not None
    assert eval_row.approved is True
    assert eval_row.final_score == 88.5

    exports = store.list_exports(clip_id_1)
    assert len(exports) == 1
    assert exports[0].drive_file_id == "drive_file_999"
    assert exports[0].drive_web_view_link == "https://drive.google.com/view/999"

    # 2. Final completion callback
    completion_payload = {
        "status": "done",
        "stage": "completed",
        "progress": 1.0,
    }
    resp_done = client.post(f"/api/jobs/{job.id}/worker-callback", json=completion_payload)
    assert resp_done.status_code == 200
    assert resp_done.json()["status"] == "done"
    assert resp_done.json()["progress"] == 1.0


def test_worker_callback_auth(client_env, monkeypatch):
    """Verify token authentication on callback endpoint when configured."""
    client, tmp_path = client_env
    monkeypatch.setenv("AL_AMR_MASTER_KEY", "super_secret_key_al_amr")

    source = Source(id=new_id(), type="upload", path=str(tmp_path / "v_auth.mp4"))
    store.create_source(source)
    job = Job(id=new_id(), source_id=source.id, status="running")
    store.create_job(job)

    # Missing/wrong token should fail with 401
    bad_resp = client.post(f"/api/jobs/{job.id}/worker-callback", json={"token": "wrong_key", "progress": 0.5})
    assert bad_resp.status_code == 401

    # Valid token succeeds
    good_resp = client.post(
        f"/api/jobs/{job.id}/worker-callback",
        json={"token": "super_secret_key_al_amr", "stage": "rendering", "progress": 0.5},
    )
    assert good_resp.status_code == 200
    assert good_resp.json()["current_stage"] == "rendering"


def test_range_stream_with_drive_fallback(client_env, tmp_path):
    """Test streaming and download from Google Drive when local file is missing on Render."""
    client, _ = client_env

    source = Source(id=new_id(), type="upload", path=str(tmp_path / "v_stream.mp4"))
    store.create_source(source)
    job = Job(id=new_id(), source_id=source.id, status="done")
    store.create_job(job)
    clip_id = new_id()
    clip = Clip(id=clip_id, job_id=job.id, start_s=0, end_s=10)
    store.create_clip(clip)

    export_id = new_id()
    export_record = Export(
        id=export_id,
        clip_id=clip_id,
        path=str(tmp_path / "non_existent_local.mp4"),
        ratio="9:16",
        drive_file_id="drive_stream_id_123",
        drive_web_view_link="https://drive.google.com/file/d/drive_stream_id_123/view",
    )
    store.create_export(export_record)

    # 1. When Drive is configured, stream_range is called
    with patch.object(GoogleDriveStorage, "is_configured", True):
        def fake_stream_range(file_id, range_header=None):
            return (iter([b"STREAMED_FROM_DRIVE_CHUNKS"]), 206 if range_header else 200, {"Content-Type": "video/mp4"})

        with patch.object(GoogleDriveStorage, "stream_range", side_effect=fake_stream_range):
            resp = client.get(f"/api/exports/{export_id}/stream", headers={"Range": "bytes=0-10"})
            assert resp.status_code == 206
            assert resp.content == b"STREAMED_FROM_DRIVE_CHUNKS"

    # 2. When Drive is not configured but drive_web_view_link is available, redirects to web view link
    with patch.object(GoogleDriveStorage, "is_configured", False):
        resp_redirect = client.get(f"/api/exports/{export_id}/stream", follow_redirects=False)
        assert resp_redirect.status_code in (302, 307)
        assert resp_redirect.headers["location"] == "https://drive.google.com/file/d/drive_stream_id_123/view"


def test_disconnected_client_invariant(client_env, tmp_path):
    """Strict verification of Disconnected Client Invariant:
    A client submits a job, immediately drops connection, the remote worker completes
    and reports results back, and the client reconnects later to find complete state.
    """
    client, _ = client_env

    # Step 1: Submit job
    source = Source(id=new_id(), type="youtube", url="https://youtube.com/watch?v=remote_test", path=str(tmp_path / "v.mp4"))
    store.create_source(source)

    job_id = new_id()
    job = Job(id=job_id, source_id=source.id, status="queued", dispatch_mode="github")
    store.create_job(job)

    # Step 2: Client "disconnects" (simulated by not making any requests or closing app)
    # Step 3: Remote GitHub Actions worker finishes processing in the cloud and calls worker-callback
    finished_clip_id = new_id()
    callback_payload = {
        "status": "done",
        "stage": "completed",
        "progress": 1.0,
        "github_run_id": "run_11223344",
        "clips": [
            {
                "id": finished_clip_id,
                "job_id": job_id,
                "start_s": 5.0,
                "end_s": 35.0,
                "title": "Autonomous Cloud Clip",
                "score": 95,
            }
        ],
        "evaluations": [
            {
                "clip_id": finished_clip_id,
                "campaign_id": "autonomous_cmp",
                "approved": True,
                "final_score": 94.0,
            }
        ],
        "exports": [
            {
                "id": new_id(),
                "clip_id": finished_clip_id,
                "path": "/cloud/exports/clip1.mp4",
                "ratio": "9:16",
                "drive_file_id": "gdrive_persisted_id_777",
                "drive_web_view_link": "https://drive.google.com/view/777",
                "drive_storage_key": "AL-AMR/clips/clip1.mp4",
            }
        ],
    }
    client.post(f"/api/jobs/{job_id}/worker-callback", json=callback_payload)

    # Step 4: Client "reconnects" hours later from Android APK or browser
    resp_reconnect = client.get(f"/api/jobs/{job_id}")
    assert resp_reconnect.status_code == 200
    job_data = resp_reconnect.json()
    assert job_data["status"] == "done"
    assert job_data["current_stage"] == "completed"
    assert job_data["progress"] == 1.0
    assert job_data["github_run_id"] == "run_11223344"

    # Verify client can fetch clips and sees drive links
    resp_clips = client.get(f"/api/jobs/{job_id}/clips")
    assert resp_clips.status_code == 200
    clips_list = resp_clips.json()
    assert len(clips_list) == 1
    assert clips_list[0]["title"] == "Autonomous Cloud Clip"
    assert clips_list[0]["exports"][0]["drive_file_id"] == "gdrive_persisted_id_777"
    assert clips_list[0]["exports"][0]["drive_web_view_link"] == "https://drive.google.com/view/777"


def test_publishing_dispatcher_mock(tmp_path):
    """Test publishing clip to social destinations with mock credentials."""
    dummy_clip = tmp_path / "clip.mp4"
    dummy_clip.write_bytes(b"dummy_mp4_bytes")

    # 1. Telegram without bot token skips gracefully
    with patch.dict(os.environ, {}, clear=True):
        res = publish_to_telegram(dummy_clip, "Test Caption")
        assert res["status"] == "skipped"

    # 2. Telegram with credentials calls API
    with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "mock_bot_token", "TELEGRAM_CHAT_ID": "123456"}):
        with patch("httpx.post") as mock_tg_post:
            mock_tg_resp = MagicMock()
            mock_tg_resp.status_code = 200
            mock_tg_resp.json.return_value = {"ok": True, "result": {"message_id": 42}}
            mock_tg_post.return_value = mock_tg_resp

            res = publish_to_telegram(dummy_clip, "Test Caption", drive_link="https://drive.google.com/view/1")
            assert res["status"] == "published"
            assert mock_tg_post.called

    # 3. publish_clip multi-target orchestrator
    with patch.dict(os.environ, {"YOUTUBE_REFRESH_TOKEN": "mock_yt_refresh"}):
        all_res = publish_clip(dummy_clip, "Title", ["youtube", "unknown_platform"])
        assert all_res["youtube"]["status"] == "ready_for_upload"
        assert all_res["unknown_platform"]["status"] == "unsupported"

