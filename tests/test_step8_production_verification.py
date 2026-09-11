"""Automated test suite for AL AMR Step 8:
Real Production End-to-End Integration & Live Operator Verification.
"""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from autoclip import db, paths
from autoclip.app import create_app
from autoclip.db import store
from autoclip.db.models import CampaignEvaluationRow, Clip, Export, Job, Source, new_id
from autoclip.jobs.dispatcher import (
    dispatch_job_to_github,
    get_dispatch_mode,
    is_github_dispatch_enabled,
)
from autoclip.jobs.worker_runner import send_callback
from autoclip.storage.drive import GoogleDriveStorage


@pytest.fixture
def test_app_client(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOCLIP_HOME", str(tmp_path))
    monkeypatch.setenv("AUTOCLIP_NO_WORKER", "1")
    paths.ensure_layout()
    db.init()
    app = create_app()
    with TestClient(app) as client:
        yield client, tmp_path


def test_multi_key_and_query_token_auth(test_app_client, monkeypatch):
    """Verify all production key variants and query token authenticate correctly."""
    client, tmp_path = test_app_client
    monkeypatch.setenv("OPERATOR_TOKEN", "alamr-op-tok-123")
    monkeypatch.setenv("AL_AMR_MASTER_KEY", "master-secret-456")
    monkeypatch.setenv("AUTOCLIP_API_KEY", "api-key-789")
    monkeypatch.setenv("WORKER_CALLBACK_SECRET", "worker-sec-000")

    # 1. Unauthenticated request to private endpoint must fail with 401
    resp_unauth = client.get("/api/jobs")
    assert resp_unauth.status_code == 401

    # 2. Bearer token with OPERATOR_TOKEN succeeds
    resp_op = client.get("/api/jobs", headers={"Authorization": "Bearer alamr-op-tok-123"})
    assert resp_op.status_code == 200

    # 3. Bearer token with AL_AMR_MASTER_KEY succeeds
    resp_master = client.get("/api/jobs", headers={"Authorization": "Bearer master-secret-456"})
    assert resp_master.status_code == 200

    # 4. X-API-Key with AUTOCLIP_API_KEY succeeds
    resp_api = client.get("/api/jobs", headers={"X-API-Key": "api-key-789"})
    assert resp_api.status_code == 200

    # 5. Query parameter token with WORKER_CALLBACK_SECRET succeeds
    resp_query = client.get("/api/jobs?token=worker-sec-000")
    assert resp_query.status_code == 200

    # 6. Invalid token fails with 401
    resp_bad = client.get("/api/jobs", headers={"Authorization": "Bearer invalid-token"})
    assert resp_bad.status_code == 401


def test_dispatch_mode_auto_resolution(monkeypatch):
    """Verify AUTOCLIP_DISPATCH_MODE=auto correctly activates GitHub dispatch when PAT is present."""
    monkeypatch.setenv("AUTOCLIP_DISPATCH_MODE", "auto")

    monkeypatch.delenv("GITHUB_PAT", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert not is_github_dispatch_enabled()

    monkeypatch.setenv("GITHUB_PAT", "ghp_test_token_abc")
    assert is_github_dispatch_enabled()

    monkeypatch.setenv("AUTOCLIP_DISPATCH_MODE", "github")
    assert is_github_dispatch_enabled()

    monkeypatch.setenv("AUTOCLIP_DISPATCH_MODE", "local")
    assert not is_github_dispatch_enabled()


def test_source_media_serving_with_token(test_app_client, monkeypatch):
    """Verify remote worker or media player can stream source file with query token when auth is active."""
    client, tmp_path = test_app_client
    monkeypatch.setenv("OPERATOR_TOKEN", "secure-op-token")

    dummy_media = tmp_path / "sources" / "test_source.mp4"
    dummy_media.parent.mkdir(parents=True, exist_ok=True)
    dummy_media.write_bytes(b"fake_source_mp4_bytes_0123456789")

    source = Source(id=new_id(), type="upload", path=str(dummy_media), title="Test Video")
    store.create_source(source)

    # Without token: 401
    resp_no_token = client.get(f"/api/sources/{source.id}/file")
    assert resp_no_token.status_code == 401

    # With query token: 200
    resp_token = client.get(f"/api/sources/{source.id}/file?token=secure-op-token")
    assert resp_token.status_code == 200
    assert resp_token.content == b"fake_source_mp4_bytes_0123456789"


def test_worker_runner_send_callback_headers():
    """Verify send_callback sends Authorization Bearer header to control plane."""
    with patch("httpx.post") as mock_post:
        mock_post.return_value.status_code = 200
        send_callback(
            "https://control-plane.example.com/api/jobs/job_1/worker-callback",
            "test_auth_token_xyz",
            status="running",
            stage="whisper_transcription",
            progress=0.25,
        )
        assert mock_post.called
        call_args = mock_post.call_args
        headers = call_args.kwargs.get("headers", {})
        assert headers.get("Authorization") == "Bearer test_auth_token_xyz"
        assert headers.get("X-API-Key") == "test_auth_token_xyz"
        payload = call_args.kwargs.get("json", {})
        assert payload.get("status") == "running"
        assert payload.get("stage") == "whisper_transcription"
        assert payload.get("progress") == 0.25


def test_production_end_to_end_worker_sync_and_stream(test_app_client, monkeypatch):
    """Full end-to-end integration:
    1. Job is created in GitHub mode
    2. Cloud worker sends multi-stage progress and results via callback
    3. Clips, campaign evaluations, and exports are synchronized
    4. Operator verifies job details, clip lists, and Google Drive streaming
    """
    client, tmp_path = test_app_client
    monkeypatch.setenv("OPERATOR_TOKEN", "prod-operator-key")
    auth_headers = {"Authorization": "Bearer prod-operator-key"}

    source = Source(id=new_id(), type="youtube", url="https://youtube.com/watch?v=prod_test", path=str(tmp_path / "prod.mp4"))
    store.create_source(source)

    job_id = new_id()
    job = Job(
        id=job_id,
        source_id=source.id,
        status="queued",
        dispatch_mode="github",
        settings={"campaign": {"brand_name": "Al Amr", "required_hook_words": ["secret"]}},
    )
    store.create_job(job)

    # Worker reports running
    resp_cb1 = client.post(
        f"/api/jobs/{job_id}/worker-callback",
        json={"token": "prod-operator-key", "status": "running", "stage": "face_tracking", "progress": 0.5},
    )
    assert resp_cb1.status_code == 200

    # Worker reports completion with clip, evaluation, and Drive export
    clip_id = new_id()
    export_id = new_id()
    resp_cb2 = client.post(
        f"/api/jobs/{job_id}/worker-callback",
        json={
            "token": "prod-operator-key",
            "status": "done",
            "stage": "completed",
            "progress": 1.0,
            "github_run_id": "run_998877",
            "clips": [
                {
                    "id": clip_id,
                    "job_id": job_id,
                    "start_s": 12.0,
                    "end_s": 42.0,
                    "title": "Al Amr Viral Hook",
                    "score": 96,
                }
            ],
            "evaluations": [
                {
                    "clip_id": clip_id,
                    "campaign_id": "campaign_al_amr",
                    "approved": True,
                    "final_score": 95.5,
                    "hook_score": 98.0,
                    "rule_results": {"hook": True, "brand": True},
                }
            ],
            "exports": [
                {
                    "id": export_id,
                    "clip_id": clip_id,
                    "path": "/cloud/work/clip.mp4",
                    "ratio": "9:16",
                    "size_bytes": 5242880,
                    "drive_file_id": "gdrive_file_abc123",
                    "drive_web_view_link": "https://drive.google.com/file/d/abc123/view",
                    "drive_storage_key": "AL-AMR/clips/clip.mp4",
                }
            ],
        },
    )
    assert resp_cb2.status_code == 200

    # Operator client checks job
    job_resp = client.get(f"/api/jobs/{job_id}", headers=auth_headers)
    assert job_resp.status_code == 200
    job_json = job_resp.json()
    assert job_json["status"] == "done"
    assert job_json["github_run_id"] == "run_998877"

    # Operator client queries clips
    clips_resp = client.get(f"/api/jobs/{job_id}/clips", headers=auth_headers)
    assert clips_resp.status_code == 200
    clips_json = clips_resp.json()
    assert len(clips_json) == 1
    assert clips_json[0]["id"] == clip_id
    assert clips_json[0]["evaluation"]["approved"] is True
    assert clips_json[0]["exports"][0]["drive_file_id"] == "gdrive_file_abc123"

    # Operator client streams export (Google Drive mock)
    with patch.object(GoogleDriveStorage, "is_configured", True):
        with patch.object(GoogleDriveStorage, "stream_range") as mock_stream:
            mock_stream.return_value = (
                iter([b"streamed_chunk"]),
                206,
                {"Content-Range": "bytes 0-13/14", "Content-Length": "14", "Accept-Ranges": "bytes"},
            )
            stream_resp = client.get(
                f"/api/exports/{export_id}/stream?token=prod-operator-key",
                headers={"Range": "bytes=0-13"},
            )
            assert stream_resp.status_code == 206
            assert stream_resp.content == b"streamed_chunk"
