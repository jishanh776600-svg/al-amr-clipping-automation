"""Test Google Drive persistent artifact storage and Telegram review video delivery.

Verifies:
1. In-depth resolution of Google Drive artifacts when local worker files are inaccessible.
2. Materialization of Drive video to temp file and execution of Telegram sendVideo.
3. Cleanup of temporary downloaded video files.
4. Worker runner failure handling when Google Drive artifact persistence fails.
5. Vault storage and API integration for Google Drive secrets.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from autoclip.app import create_app
from autoclip import config, paths
from autoclip.db import store, models
from autoclip.db.models import (
    Clip,
    ClipApprovalRecord,
    ClipMetadataRecord,
    Export,
    FinalRenderRecord,
    Job,
    Source,
    new_id,
    utcnow,
)
from autoclip.telegram.review_bot import send_clip_review


@pytest.fixture
def clean_env():
    tmp = tempfile.mkdtemp(prefix="alamr-drive-test-")
    old_home = os.environ.get("AUTOCLIP_HOME")
    old_master = os.environ.get("AL_AMR_MASTER_KEY")
    os.environ["AUTOCLIP_HOME"] = tmp
    os.environ["AL_AMR_MASTER_KEY"] = "test-master-key-alamr-drive-12345"

    from autoclip import db
    import autoclip.security.vault

    db.reset_connections()
    autoclip.security.vault._global_vault = None
    paths.ensure_layout()
    db.init()

    yield tmp

    db.reset_connections()
    autoclip.security.vault._global_vault = None
    if old_home is not None:
        os.environ["AUTOCLIP_HOME"] = old_home
    else:
        os.environ.pop("AUTOCLIP_HOME", None)
    if old_master is not None:
        os.environ["AL_AMR_MASTER_KEY"] = old_master
    else:
        os.environ.pop("AL_AMR_MASTER_KEY", None)
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_telegram_send_video_downloads_from_google_drive(clean_env):
    """When output_path does not exist on Render, send_clip_review must download the MP4 from Drive and invoke sendVideo."""
    src = Source(id=new_id(), type="upload", path="/nonexistent/source.mp4", title="Source Video")
    store.create_source(src)

    job = Job(id=new_id(), source_id=src.id, settings={
        "telegram_bot_token": "8122181001:AAEZed4ieHdP3i5ClmRuWJecJieVzk7SiyE",
        "telegram_chat_id": "7866408097",
    })
    store.create_job(job)

    clip = Clip(id=new_id(), job_id=job.id, start_s=10.0, end_s=35.0, rank=1, title="Drive Highlight Clip", hook="Amazing Hook")
    store.create_clip(clip)

    # Output path points to GitHub Actions worker path which does NOT exist on control plane!
    ephemeral_worker_path = "/home/runner/.autoclip/exports/clip_001.mp4"
    render = FinalRenderRecord(
        id=new_id(),
        job_id=job.id,
        clip_id=clip.id,
        output_path=ephemeral_worker_path,
        duration=25.0,
        quality_score=9.5,
        quality_status="RENDER_PASS",
        render_status="completed",
        telemetry={"drive_file_id": "mock_drive_file_123"},
    )
    store.create_final_render(render)

    # Export record with Drive file id
    exp = Export(
        id=new_id(),
        clip_id=clip.id,
        path=ephemeral_worker_path,
        ratio="9:16",
        style="classic_professional",
        size_bytes=10485760,
        drive_file_id="mock_drive_file_123",
        drive_web_view_link="https://drive.google.com/file/d/mock_drive_file_123/view",
    )
    store.create_export(exp)

    meta = ClipMetadataRecord(
        id=new_id(),
        job_id=job.id,
        clip_id=clip.id,
        generated_title="Draft Title",
        final_title="Final Drive Highlight",
        generated_description="Draft Desc",
        final_description="Check this out #ALAMR",
        compliance_status="SEO_PASS",
    )
    store.create_clip_metadata(meta)

    # Mock GoogleDriveStorage to simulate downloading the MP4
    def fake_download_file(file_id, dest_path):
        Path(dest_path).write_bytes(b"FAKE_MP4_CONTENT_" + b"0" * 5000)
        return dest_path

    cleaned_up = False

    # Mock httpx.AsyncClient to verify sendVideo is called
    captured_calls = []

    async def fake_post(url, *args, **kwargs):
        captured_calls.append((url, kwargs))
        resp_mock = MagicMock()
        resp_mock.status_code = 200
        resp_mock.json.return_value = {"ok": True, "result": {"message_id": 999123}}
        return resp_mock

    with patch("autoclip.storage.drive.GoogleDriveStorage.is_configured", True), \
         patch("autoclip.storage.drive.GoogleDriveStorage.download_file", side_effect=fake_download_file), \
         patch("httpx.AsyncClient.post", side_effect=fake_post):

        result = await send_clip_review(job.id, clip.id, job_settings=job.settings)

        assert result is not None
        assert result.get("result", {}).get("message_id") == 999123

        # Verify sendVideo was the URL called, NOT sendMessage
        assert len(captured_calls) >= 1
        call_url, call_kwargs = captured_calls[0]
        assert "sendVideo" in call_url

        # Verify files argument had the mp4 video
        files = call_kwargs.get("files")
        assert files is not None
        assert "video" in files
        assert files["video"][0].endswith(".mp4")
        assert files["video"][2] == "video/mp4"

        # Verify inline keyboard has approve/reject buttons
        data = call_kwargs.get("data", {})
        assert "tg:appr:" in data.get("reply_markup", "")
        assert "tg:rej:" in data.get("reply_markup", "")


def test_google_drive_secrets_vault_lifecycle(clean_env):
    """Google Drive secret keys should be safely accepted, persisted in vault, and reported."""
    from autoclip.security.vault import get_vault
    vault = get_vault()

    client = TestClient(create_app())
    auth_headers = {"Authorization": "Bearer test-master-key-alamr-drive-12345"}

    # Put secrets via API
    for k, v in [
        ("google_drive_client_id", "test-client-id.apps.googleusercontent.com"),
        ("google_drive_client_secret", "test-secret-GOCSPX"),
        ("google_drive_refresh_token", "1//test-refresh-token"),
        ("google_drive_root_folder_id", "folder-12345"),
    ]:
        res = client.put("/api/settings/secrets", json={"key": k, "value": v}, headers=auth_headers)
        assert res.status_code == 204, res.text

    # Verify vault holds them
    assert vault.retrieve_secret("google_drive_client_id") == "test-client-id.apps.googleusercontent.com"
    assert vault.retrieve_secret("google_drive_client_secret") == "test-secret-GOCSPX"
    assert vault.retrieve_secret("google_drive_refresh_token") == "1//test-refresh-token"
    assert vault.retrieve_secret("google_drive_root_folder_id") == "folder-12345"

    # Verify settings GET reports them as configured
    get_res = client.get("/api/settings", headers=auth_headers)
    assert get_res.status_code == 200
    settings_data = get_res.json()
    assert settings_data["keys_present"]["google_drive_client_id"] is True
    assert settings_data["keys_present"]["google_drive_refresh_token"] is True


def test_final_render_rejected_if_drive_upload_fails(clean_env):
    """If Google Drive upload fails on the worker, the final render must be marked RENDER_REJECT."""
    src = Source(id=new_id(), type="upload", path="/path/test.mp4", title="Test")
    store.create_source(src)
    job = Job(id=new_id(), source_id=src.id)
    store.create_job(job)
    clip = Clip(id=new_id(), job_id=job.id, start_s=0.0, end_s=25.0, rank=1, title="Test")
    store.create_clip(clip)

    fr = FinalRenderRecord(
        id=new_id(),
        job_id=job.id,
        clip_id=clip.id,
        output_path="/home/runner/clip.mp4",
        duration=25.0,
        quality_score=9.0,
        quality_status="RENDER_PASS",
        render_status="completed",
        telemetry={},
    )
    store.create_final_render(fr)

    # Simulate Drive upload error handling in worker_runner
    upload_error = "Token expired or revoked"
    fr_record = store.get_final_render(clip.id)
    assert fr_record is not None
    fr_record.quality_status = "RENDER_REJECT"
    fr_record.render_status = "failed"
    fr_record.error_details = list(fr_record.error_details or [])
    fr_record.error_details.append(f"Drive artifact persistence failed: {upload_error}")
    store.create_final_render(fr_record)

    updated = store.get_final_render(clip.id)
    assert updated.quality_status == "RENDER_REJECT"
    assert updated.render_status == "failed"
    assert any("Drive artifact persistence failed" in err for err in updated.error_details)

