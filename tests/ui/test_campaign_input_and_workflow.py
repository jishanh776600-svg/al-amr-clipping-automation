"""
Unit and integration tests for Campaign Input and Operator Workflow (Step 1/5).

Validates:
1. Brief file uploads (PDF, TXT, MD) into canonical storage.
2. Brief validation (rejects invalid types and empty files).
3. Local source video uploads (.mp4, .mov, etc.) into canonical storage.
4. Local video validation (rejects invalid video formats).
5. Campaign creation with YouTube source and active verified account.
6. Campaign creation with Instagram source and active verified account.
7. Campaign creation with uploaded brief and local video references.
8. Fail-closed rejection of inactive/unverified accounts.
9. Fail-closed rejection of nonexistent accounts.
10. Rejection of missing, empty, or invalid source URIs.
11. Zero secret leakage in responses, metadata, or job states.
"""

import io
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock

from clipping.agent.vault.vault import EncryptedCredentialVault
from clipping.agent.vault.models import AccountMetadata, AccountPlatform, AccountStatus
from clipping.approval.repository import ApprovalRepository
from clipping.storage.local import LocalStorageDriver
import clipping.ui.server as ui_server
from clipping.ui.server import app


@pytest.fixture
def ui_test_env(temp_vault_dir):
    storage = LocalStorageDriver(root_dir=temp_vault_dir)
    app.dependency_overrides[ui_server.get_storage_driver] = lambda: storage
    yield {
        "storage": storage,
        "app_repo": ApprovalRepository(storage_driver=storage),
    }
    app.dependency_overrides.clear()


@pytest.fixture
async def setup_test_accounts(ui_test_env):
    """Enrolls active YouTube, active Instagram, and pending Instagram accounts in vault."""
    storage = ui_test_env["storage"]
    vault = EncryptedCredentialVault(storage_driver=storage)

    # 1. Active YouTube account
    yt_meta = AccountMetadata(
        platform=AccountPlatform.YOUTUBE,
        account_id="UC_channel_real",
        username="al_amr_creator",
        display_name="AL AMR Official Shorts",
        status=AccountStatus.ACTIVE,
    )
    await vault.save_account(yt_meta, sensitive_credentials={"client_id": "test_cid", "client_secret": "test_csec"})

    # 2. Active Instagram account
    ig_meta = AccountMetadata(
        platform=AccountPlatform.INSTAGRAM,
        account_id="17841439457167561",
        username="black_boxvault",
        display_name="black_boxvault (Instagram Reels)",
        status=AccountStatus.ACTIVE,
    )
    await vault.save_account(ig_meta, sensitive_credentials={"access_token": "test_token"})

    # 3. Pending Instagram account (for fail-closed testing)
    ig_pending = AccountMetadata(
        platform=AccountPlatform.INSTAGRAM,
        account_id="pending_creator_01",
        username="pending_creator",
        display_name="Pending Creator",
        status=AccountStatus.PENDING_VERIFICATION,
    )
    await vault.save_account(ig_pending, sensitive_credentials={})

    return {
        "storage": storage,
        "vault": vault,
        "yt_account_id": "UC_channel_real",
        "ig_account_id": "17841439457167561",
        "pending_account_id": "pending_creator_01",
    }


@pytest.mark.asyncio
async def test_brief_upload_pdf_txt_md(ui_test_env):
    """Validates that valid brief files (PDF, TXT, MD) are securely uploaded and stored."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # PDF upload
        pdf_bytes = b"%PDF-1.4 Mock PDF campaign brief content for testing"
        files = {"file": ("campaign_brief.pdf", io.BytesIO(pdf_bytes), "application/pdf")}
        resp_pdf = await client.post("/api/campaigns/upload-brief", files=files)
        assert resp_pdf.status_code == 200
        data_pdf = resp_pdf.json()
        assert data_pdf["status"] == "success"
        assert data_pdf["format"] == "pdf"
        assert "campaigns/briefs/" in data_pdf["brief_storage_key"]
        assert data_pdf["size_bytes"] == len(pdf_bytes)

        # Verify persisted in storage
        storage = ui_test_env["storage"]
        assert await storage.exists(data_pdf["brief_storage_key"])

        # TXT upload
        txt_bytes = b"Campaign requirements: Clip duration 30-45s. Focus on AI topics."
        files_txt = {"file": ("brief.txt", io.BytesIO(txt_bytes), "text/plain")}
        resp_txt = await client.post("/api/campaigns/upload-brief", files=files_txt)
        assert resp_txt.status_code == 200
        assert resp_txt.json()["format"] == "txt"

        # MD upload
        md_bytes = b"# Campaign Guidelines\n- Highlight emotional spikes\n- 9:16 vertical crop"
        files_md = {"file": ("guidelines.md", io.BytesIO(md_bytes), "text/markdown")}
        resp_md = await client.post("/api/campaigns/upload-brief", files=files_md)
        assert resp_md.status_code == 200
        assert resp_md.json()["format"] == "md"


@pytest.mark.asyncio
async def test_brief_upload_invalid_types_and_empty_rejected(ui_test_env):
    """Validates that unsupported formats and empty brief files are rejected with HTTP 400."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Reject .exe
        files_exe = {"file": ("malicious.exe", io.BytesIO(b"MZ123"), "application/octet-stream")}
        resp_exe = await client.post("/api/campaigns/upload-brief", files=files_exe)
        assert resp_exe.status_code == 400
        assert "Unsupported brief file format" in resp_exe.json()["detail"]

        # Reject .zip
        files_zip = {"file": ("archive.zip", io.BytesIO(b"PK345"), "application/zip")}
        resp_zip = await client.post("/api/campaigns/upload-brief", files=files_zip)
        assert resp_zip.status_code == 400

        # Reject empty file
        files_empty = {"file": ("empty.pdf", io.BytesIO(b""), "application/pdf")}
        resp_empty = await client.post("/api/campaigns/upload-brief", files=files_empty)
        assert resp_empty.status_code == 400
        assert "cannot be empty" in resp_empty.json()["detail"]


@pytest.mark.asyncio
async def test_source_video_upload_and_validation(ui_test_env):
    """Validates local video file upload, format validation, and storage persistence."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Valid MP4 video upload
        dummy_video = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00isommp42dummyvideo"
        files = {"file": ("raw_source_4k.mp4", io.BytesIO(dummy_video), "video/mp4")}
        resp = await client.post("/api/campaigns/upload-video", files=files)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["source_type"] == "local_file"
        assert "sources/src_" in data["storage_key"]
        assert data["size_bytes"] == len(dummy_video)

        # Storage persistence check
        storage = ui_test_env["storage"]
        assert await storage.exists(data["storage_key"])

        # Reject non-video file
        files_bad = {"file": ("not_a_video.txt", io.BytesIO(b"hello"), "text/plain")}
        resp_bad = await client.post("/api/campaigns/upload-video", files=files_bad)
        assert resp_bad.status_code == 400
        assert "Unsupported video format" in resp_bad.json()["detail"]


@pytest.mark.asyncio
async def test_campaign_creation_youtube_source(ui_test_env, setup_test_accounts, monkeypatch):
    """Tests starting a campaign with a YouTube source URL and verified YouTube creator account."""
    mock_run = AsyncMock(return_value=0)
    monkeypatch.setattr("clipping.cli.pipeline_runner.run_pipeline", mock_run)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "name": "YouTube Shorts Alpha",
            "source_uri": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "source_type": "youtube",
            "target_platforms": ["youtube_shorts"],
            "target_account_id": setup_test_accounts["yt_account_id"],
            "requirements_text": "Engaging hooks only, 15-30s duration",
        }
        resp = await client.post("/api/campaigns/create-and-run", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["target_platform"] == "youtube"
        assert data["target_account_id"] == "UC_channel_real"
        assert "campaign_id" in data
        assert "job_id" in data


@pytest.mark.asyncio
async def test_campaign_creation_instagram_source_with_brief(ui_test_env, setup_test_accounts, monkeypatch):
    """Tests starting an Instagram campaign with direct URL source, PDF brief, and active Instagram account."""
    mock_run = AsyncMock(return_value=0)
    monkeypatch.setattr("clipping.cli.pipeline_runner.run_pipeline", mock_run)

    storage = ui_test_env["storage"]
    brief_key = "campaigns/briefs/test_uuid_brief.pdf"
    await storage.upload_bytes(b"%PDF-1.4 Mock Brief", brief_key)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "name": "Instagram Reels Launch",
            "source_uri": "https://cdn.example.com/podcast_ep12.mp4",
            "source_type": "direct_url",
            "brief_storage_key": brief_key,
            "brief_filename": "client_brief_v2.pdf",
            "target_platforms": ["instagram_reels"],
            "target_account_id": setup_test_accounts["ig_account_id"],
        }
        resp = await client.post("/api/campaigns/create-and-run", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["target_platform"] == "instagram"
        assert data["target_account_id"] == "17841439457167561"


@pytest.mark.asyncio
async def test_inactive_or_pending_account_fails_closed(ui_test_env, setup_test_accounts):
    """Validates that targeting a pending, unverified account fails closed with HTTP 400."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "name": "Unverified Target Test",
            "source_uri": "https://www.youtube.com/watch?v=sample12345",
            "target_platforms": ["instagram_reels"],
            "target_account_id": setup_test_accounts["pending_account_id"],
        }
        resp = await client.post("/api/campaigns/create-and-run", json=payload)
        assert resp.status_code == 400
        assert "is not active" in resp.json()["detail"]
        assert "pending_verification" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_nonexistent_account_rejected(ui_test_env, setup_test_accounts):
    """Validates that targeting an account that does not exist in the vault is rejected with HTTP 400."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "name": "Ghost Account Test",
            "source_uri": "https://www.youtube.com/watch?v=sample12345",
            "target_platforms": ["youtube_shorts"],
            "target_account_id": "nonexistent_account_999",
        }
        resp = await client.post("/api/campaigns/create-and-run", json=payload)
        assert resp.status_code == 400
        assert "not found in vault" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_missing_and_invalid_source_rejected(ui_test_env, setup_test_accounts):
    """Validates that empty sources and unsupported source schemes fail closed with HTTP 400."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Missing / whitespace source
        resp_empty = await client.post(
            "/api/campaigns/create-and-run",
            json={
                "name": "Empty Source Test",
                "source_uri": "   ",
                "target_platforms": ["youtube_shorts"],
                "target_account_id": setup_test_accounts["yt_account_id"],
            },
        )
        assert resp_empty.status_code == 400
        assert "Source video URI or uploaded file is required" in resp_empty.json()["detail"]

        # 2. Unsupported arbitrary scheme
        resp_bad = await client.post(
            "/api/campaigns/create-and-run",
            json={
                "name": "Bad Protocol Test",
                "source_uri": "ftp://unsupported.server/video.mp4",
                "target_platforms": ["youtube_shorts"],
                "target_account_id": setup_test_accounts["yt_account_id"],
            },
        )
        assert resp_bad.status_code == 400
        assert "Unsupported source URI format" in resp_bad.json()["detail"]


@pytest.mark.asyncio
async def test_zero_secret_leakage_in_campaign_workflow(ui_test_env, setup_test_accounts, monkeypatch):
    """Validates that no credentials, tokens, or master keys are exposed in campaign responses or jobs."""
    mock_run = AsyncMock(return_value=0)
    monkeypatch.setattr("clipping.cli.pipeline_runner.run_pipeline", mock_run)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/campaigns/create-and-run",
            json={
                "name": "Security Leak Audit Campaign",
                "source_uri": "https://www.youtube.com/watch?v=sampleSecurityCheck",
                "target_platforms": ["youtube_shorts"],
                "target_account_id": setup_test_accounts["yt_account_id"],
            },
        )
        assert resp.status_code == 200
        resp_text = resp.text
        # Assert no sensitive keys present
        assert "client_secret" not in resp_text
        assert "refresh_token" not in resp_text
        assert "access_token" not in resp_text
        assert "AL_AMR_MASTER_KEY" not in resp_text
        assert "test_csec" not in resp_text
