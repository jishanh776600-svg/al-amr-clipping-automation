"""Unit and Integration tests for AL AMR Clipping Automation Console UI Backend."""

import pytest
from httpx import AsyncClient, ASGITransport
from clipping.approval.models import ApprovalRequest, ApprovalStatus
from clipping.approval.repository import ApprovalRepository
from clipping.contracts.qa import QAReport, QACheckStatus
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


@pytest.mark.asyncio
async def test_ui_system_status():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/system/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["system_name"] == "AL AMR Clipping Automation"
        assert data["status"] == "OPERATIONAL"
        assert "subsystems" in data
        assert "pipeline_engine" in data["subsystems"]
        assert "approval_gateway" in data["subsystems"]


@pytest.mark.asyncio
async def test_ui_serve_index():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "AL AMR" in resp.text


@pytest.mark.asyncio
async def test_ui_get_clips_and_submit_decision(ui_test_env):
    storage = ui_test_env["storage"]
    app_repo = ui_test_env["app_repo"]

    job_id = "job_ui_test_01"
    clip_id = "clip_ui_01"
    req_id = "req_ui_01"

    # Seed approved request & QA report
    req = ApprovalRequest(
        approval_request_id=req_id,
        job_id=job_id,
        source_video_id="src_01",
        clip_id=clip_id,
        clip_index=1,
        title="Zero-Cost Architecture",
        start_time=10.0,
        end_time=42.0,
        duration=32.0,
        score=94.5,
        video_storage_key=f"clips/{clip_id}/final.mp4",
        status=ApprovalStatus.AWAITING_APPROVAL,
    )
    await app_repo.save_request(req)

    qa = QAReport(
        clip_id=clip_id,
        source_video_id="src_01",
        overall_status=QACheckStatus.PASS,
        can_publish=True,
    )
    await storage.upload_bytes(qa.model_dump_json().encode("utf-8"), f"clips/{clip_id}/qa_report.json")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Fetch clips
        resp = await client.get(f"/api/jobs/{job_id}/clips")
        assert resp.status_code == 200
        clips = resp.json()
        assert len(clips) == 1
        assert clips[0]["clip_id"] == clip_id
        assert clips[0]["score"] == 94.5
        assert clips[0]["qa_status"] == "PASS"
        assert clips[0]["approval_status"] == "awaiting_approval"
        assert "score_breakdown" in clips[0]

        # 2. Make approval decision via Console API
        dec_resp = await client.post(
            f"/api/jobs/{job_id}/clips/{clip_id}/decision",
            json={"action": "approve", "reviewer": "Lead Director", "notes": "Approved for YouTube Shorts"},
        )
        assert dec_resp.status_code == 200
        dec_data = dec_resp.json()
        assert dec_data["new_status"] == "approved"

        # 3. Verify canonical Google Drive state was mutated
        updated_req = await app_repo.get_request_by_id(req_id)
        assert updated_req.status == ApprovalStatus.APPROVED

        # 4. Verify immutable audit trail was appended
        audits = await app_repo.list_audits_for_job(job_id)
        assert len(audits) >= 1
        assert "Approved for YouTube Shorts" in audits[-1].reason


@pytest.mark.asyncio
async def test_ui_spa_routes_and_static_assets():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Verify SPA direct route entrypoints
        spa_routes = [
            "/dashboard", "/agent", "/campaigns", "/accounts",
            "/clipping", "/approvals", "/publishing", "/tasks",
            "/workers", "/escalations", "/activity", "/system"
        ]
        for route in spa_routes:
            resp = await client.get(route)
            assert resp.status_code == 200, f"Route {route} failed with {resp.status_code}"
            assert "text/html" in resp.headers["content-type"]
            assert "AL AMR CLIPPING" in resp.text

        # Verify static asset accessibility
        assets = [
            "/static/css/dashboard.css",
            "/static/js/api.js",
            "/static/js/shell.js",
            "/static/js/app.js",
        ]
        for asset in assets:
            resp = await client.get(asset)
            assert resp.status_code == 200, f"Asset {asset} failed with {resp.status_code}"


@pytest.mark.asyncio
async def test_ui_media_streaming(ui_test_env):
    storage = ui_test_env["storage"]
    test_bytes = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2mp41"
    await storage.upload_bytes(test_bytes, "clips/test_clip_01/final_1080x1920.mp4")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/media/clips/test_clip_01/final_1080x1920.mp4")
        assert resp.status_code == 200
        assert resp.content == test_bytes


@pytest.mark.asyncio
async def test_ui_job_live_status(ui_test_env):
    storage = ui_test_env["storage"]
    from clipping.state.remote import RemoteStorageStateRepository
    from clipping.state.models import PipelineStage, JobState

    state_repo = RemoteStorageStateRepository(storage_driver=storage)
    job_id = "job_live_test_01"
    await state_repo.create_job(
        job_id=job_id,
        campaign_id="camp_test",
        source_video_id="src_test",
        idempotency_key=f"idemp_{job_id}",
    )
    await state_repo.update_job_state(
        job_id=job_id,
        new_state=JobState.REFRAMING_AND_RENDERING,
        new_stage=PipelineStage.RENDERING,
        reason="Rendering 1080x1920 clips",
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/jobs/{job_id}/live")
        assert resp.status_code == 200
        data = resp.json()
        assert data["job_id"] == job_id
        assert data["current_stage"] == "06_RENDER"
        assert data["progress_percent"] == 88


@pytest.mark.asyncio
async def test_ui_create_and_run_campaign(ui_test_env, monkeypatch):
    from unittest.mock import AsyncMock
    from clipping.agent.vault.vault import EncryptedCredentialVault
    from clipping.agent.vault.models import AccountMetadata, AccountPlatform, AccountStatus

    storage = ui_test_env["storage"]
    vault = EncryptedCredentialVault(storage_driver=storage)
    yt_meta = AccountMetadata(
        platform=AccountPlatform.YOUTUBE,
        account_id="UC_live_test",
        username="al_amr_creator",
        display_name="AL AMR Official Shorts",
        status=AccountStatus.ACTIVE,
    )
    await vault.save_account(yt_meta, sensitive_credentials={"client_id": "cid", "client_secret": "csec"})

    mock_run = AsyncMock(return_value=0)
    monkeypatch.setattr("clipping.cli.pipeline_runner.run_pipeline", mock_run)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/campaigns/create-and-run",
            json={
                "name": "Live Test Campaign",
                "source_uri": "https://www.youtube.com/watch?v=sample12345",
                "requirements_text": "Must be 30s to 60s clips",
                "target_platforms": ["youtube_shorts"],
                "target_account_id": "UC_live_test",
                "cpm_rate": 2.0,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert "campaign_id" in data
        assert "job_id" in data


@pytest.mark.asyncio
async def test_ui_publish_clip_lifecycle(ui_test_env, monkeypatch):
    storage = ui_test_env["storage"]
    app_repo = ui_test_env["app_repo"]

    job_id = "job_pub_test_01"
    clip_id = "clip_pub_01"
    media_key = f"clips/{clip_id}/final_1080x1920.mp4"
    await storage.upload_bytes(b"dummy mp4 content", media_key)

    # Enroll active YouTube creator channel in test vault
    from clipping.agent.vault.vault import EncryptedCredentialVault
    from clipping.agent.vault.models import AccountMetadata, AccountPlatform, AccountStatus
    vault = EncryptedCredentialVault(storage_driver=storage)
    await vault.save_account(
        AccountMetadata(
            platform=AccountPlatform.YOUTUBE,
            account_id="UC_channel_real",
            username="al_amr_creator",
            display_name="AL AMR Official Shorts",
            status=AccountStatus.ACTIVE,
        ),
        sensitive_credentials={"client_id": "test_id", "client_secret": "test_secret"},
    )

    # 1. Unapproved clip fails closed
    req = ApprovalRequest(
        approval_request_id="app_req_pub_01",
        job_id=job_id,
        source_video_id="src_pub",
        clip_id=clip_id,
        clip_index=1,
        title="Publish Test Clip",
        start_time=0.0,
        end_time=30.0,
        duration=30.0,
        score=95.0,
        video_storage_key=media_key,
        status=ApprovalStatus.AWAITING_APPROVAL,
    )
    await app_repo.save_request(req)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Fails closed because not approved
        resp = await client.post(f"/api/jobs/{job_id}/clips/{clip_id}/publish")
        assert resp.status_code == 400
        assert "must be 'approved'" in resp.json()["detail"]

        # Approve clip
        dec_resp = await client.post(
            f"/api/jobs/{job_id}/clips/{clip_id}/decision",
            json={"action": "approve"},
        )
        assert dec_resp.status_code == 200

        # Mock publishing adapter
        from clipping.agent.publishing.adapters.base import PlatformPublishResult
        from clipping.agent.publishing.models import SubmissionStatus
        from unittest.mock import AsyncMock

        mock_publish = AsyncMock(
            return_value=PlatformPublishResult(
                success=True,
                status=SubmissionStatus.PUBLISHED,
                platform_post_id="yt_short_12345",
                platform_url="https://youtube.com/shorts/yt_short_12345",
            )
        )
        monkeypatch.setattr(
            "clipping.agent.publishing.adapters.youtube.YouTubePublishingAdapter.publish",
            mock_publish,
        )

        # Publish succeeds
        pub_resp = await client.post(f"/api/jobs/{job_id}/clips/{clip_id}/publish")
        assert pub_resp.status_code == 200
        pub_data = pub_resp.json()
        assert pub_data["status"] == "success"
        assert pub_data["platform_post_id"] == "yt_short_12345"


@pytest.mark.asyncio
async def test_ui_verify_account_live_meta_and_youtube(ui_test_env, monkeypatch):
    from unittest.mock import AsyncMock
    from clipping.preflight.service_verifier import ServiceVerificationResult

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Unconfigured Instagram returns unconfigured / unverified cleanly
        resp_unconf = await client.post(
            "/api/accounts/verify",
            json={"platform": "instagram", "credentials": {}},
        )
        assert resp_unconf.status_code == 200
        data_unconf = resp_unconf.json()
        assert data_unconf["configured"] is False
        assert data_unconf["verified"] is False
        assert "not configured" in data_unconf["message"]

        # 2. Simulate genuine successful Meta verification
        mock_ig_res = ServiceVerificationResult(
            service="instagram",
            configured=True,
            verified=True,
            status_code=200,
            account_identity="@alamr_official (178414001)",
            message="Instagram Graph API verified: Authenticated as @alamr_official",
            why_required="Automated Instagram Reels publishing",
            configuration_requirement="Meta Graph API access token",
            blocks_dry_run=False,
            blocks_live_operation=False,
        )
        monkeypatch.setattr(
            "clipping.preflight.service_verifier.RealServiceVerifier.verify_instagram",
            AsyncMock(return_value=mock_ig_res),
        )

        resp_ver = await client.post(
            "/api/accounts/verify",
            json={
                "platform": "instagram",
                "account_id": "178414001",
                "credentials": {"access_token": "EAA...", "instagram_account_id": "178414001"},
            },
        )
        assert resp_ver.status_code == 200
        data_ver = resp_ver.json()
        assert data_ver["verified"] is True
        assert data_ver["account_identity"] == "@alamr_official (178414001)"

        # 3. Register account with verify_connection: True
        reg_resp = await client.post(
            "/api/accounts",
            json={
                "platform": "instagram",
                "account_id": "178414001",
                "username": "alamr_official",
                "display_name": "AL AMR Official Reels",
                "credentials": {"access_token": "EAA..."},
                "verify_connection": True,
            },
        )
        assert reg_resp.status_code == 200
        reg_data = reg_resp.json()
        assert reg_data["status"] == "success"
        assert reg_data["account"]["status"] == "active"
        assert reg_data["verification"]["verified"] is True

        # 4. Re-verify enrolled account
        enrolled_ver = await client.get("/api/accounts/instagram/178414001/verify")
        assert enrolled_ver.status_code == 200
        assert enrolled_ver.json()["verified"] is True


@pytest.mark.asyncio
async def test_ui_connect_meta_token_lifecycle_and_fail_closed_publishing(ui_test_env, monkeypatch):
    """
    Validates:
    1. Enrolling Instagram account as pending_verification.
    2. Fail-closed publishing when target account is unverified/pending.
    3. Token connection failure preserves pending status and sanitized error.
    4. Successful token connection transitions account to ACTIVE with encrypted vault credentials.
    5. Live publishing succeeds once account is active.
    6. Deletion of account cleans vault index and storage safely.
    """
    from unittest.mock import AsyncMock
    from clipping.agent.vault.vault import EncryptedCredentialVault
    from clipping.agent.vault.models import AccountMetadata, AccountPlatform, AccountStatus
    from clipping.preflight.service_verifier import ServiceVerificationResult
    from clipping.agent.publishing.adapters.base import PlatformPublishResult
    from clipping.agent.publishing.models import SubmissionStatus

    storage = ui_test_env["storage"]
    app_repo = ui_test_env["app_repo"]
    vault = EncryptedCredentialVault(storage_driver=storage)

    # 1. Enroll al_amr_official in pending_verification status
    meta = AccountMetadata(
        platform=AccountPlatform.INSTAGRAM,
        account_id="al_amr_official",
        username="al_amr_official",
        display_name="AL AMR Official Reels",
        status=AccountStatus.PENDING_VERIFICATION,
    )
    await vault.save_account(meta, sensitive_credentials={})

    # Prepare approved clip
    job_id = "job_ig_test_01"
    clip_id = "clip_ig_01"
    media_key = f"clips/{clip_id}/final_1080x1920.mp4"
    await storage.upload_bytes(b"dummy mp4 content", media_key)
    req = ApprovalRequest(
        approval_request_id="app_req_ig_01",
        job_id=job_id,
        source_video_id="src_ig",
        clip_id=clip_id,
        clip_index=1,
        title="Instagram Reels Test Clip",
        start_time=0.0,
        end_time=25.0,
        duration=25.0,
        score=96.0,
        video_storage_key=media_key,
        status=ApprovalStatus.APPROVED,
    )
    await app_repo.save_request(req)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 2. Attempting to publish to pending Instagram account FAILS CLOSED
        pub_fail = await client.post(
            f"/api/jobs/{job_id}/clips/{clip_id}/publish?target_account_id=al_amr_official&target_platform=instagram"
        )
        assert pub_fail.status_code == 400
        assert "Publishing blocked" in pub_fail.json()["detail"]
        assert "PENDING_VERIFICATION" in pub_fail.json()["detail"]

        # 3. Test connect failure with invalid token
        mock_fail_res = ServiceVerificationResult(
            service="instagram",
            configured=True,
            verified=False,
            status_code=400,
            message="Meta Graph API token verification failed (400): Token expired or invalid",
            why_required="Automated Instagram Reels publishing",
            configuration_requirement="Generate new token",
            blocks_dry_run=False,
            blocks_live_operation=True,
            details={"error": "Malformed access token"},
        )
        monkeypatch.setattr(
            "clipping.preflight.service_verifier.RealServiceVerifier.verify_instagram",
            AsyncMock(return_value=mock_fail_res),
        )

        conn_fail = await client.post(
            "/api/accounts/instagram/al_amr_official/connect",
            json={"credentials": {"access_token": "EAAB_bad_token"}},
        )
        assert conn_fail.status_code == 200
        assert conn_fail.json()["success"] is False
        assert conn_fail.json()["verified"] is False
        assert conn_fail.json()["status"] == "pending_verification"

        # Verify vault still has it as pending
        meta_after_fail = await vault.get_account_metadata(AccountPlatform.INSTAGRAM, "al_amr_official")
        assert meta_after_fail.status == AccountStatus.PENDING_VERIFICATION

        # 4. Test connect success with valid token
        mock_success_res = ServiceVerificationResult(
            service="instagram",
            configured=True,
            verified=True,
            status_code=200,
            account_identity="@al_amr_official (178414058)",
            message="Instagram Graph API verified: Authenticated as @al_amr_official",
            why_required="Automated Instagram Reels publishing",
            configuration_requirement="Meta Graph API access token",
            blocks_dry_run=False,
            blocks_live_operation=False,
        )
        monkeypatch.setattr(
            "clipping.preflight.service_verifier.RealServiceVerifier.verify_instagram",
            AsyncMock(return_value=mock_success_res),
        )

        conn_success = await client.post(
            "/api/accounts/instagram/al_amr_official/connect",
            json={"credentials": {"access_token": "EAAB_real_token_secure"}},
        )
        assert conn_success.status_code == 200
        assert conn_success.json()["success"] is True
        assert conn_success.json()["verified"] is True
        assert conn_success.json()["status"] == "active"

        # Verify vault status transitioned to ACTIVE and credentials encrypted
        meta_active = await vault.get_account_metadata(AccountPlatform.INSTAGRAM, "al_amr_official")
        assert meta_active.status == AccountStatus.ACTIVE
        stored_creds = await vault.get_account_credentials(AccountPlatform.INSTAGRAM, "al_amr_official")
        assert stored_creds["access_token"] == "EAAB_real_token_secure"

        # 5. Live publishing now SUCCEEDS targeting active Instagram account
        mock_ig_publish = AsyncMock(
            return_value=PlatformPublishResult(
                success=True,
                status=SubmissionStatus.PUBLISHED,
                platform_post_id="ig_reel_998877",
                platform_url="https://www.instagram.com/reel/ig_reel_998877",
            )
        )
        monkeypatch.setattr(
            "clipping.agent.publishing.adapters.instagram.InstagramPublishingAdapter.publish",
            mock_ig_publish,
        )

        pub_success = await client.post(
            f"/api/jobs/{job_id}/clips/{clip_id}/publish?target_account_id=al_amr_official&target_platform=instagram"
        )
        assert pub_success.status_code == 200
        assert pub_success.json()["status"] == "success"
        assert pub_success.json()["platform"] == "instagram"
        assert pub_success.json()["platform_post_id"] == "ig_reel_998877"

        # 6. Delete account safely
        del_resp = await client.delete("/api/accounts/instagram/al_amr_official")
        assert del_resp.status_code == 200
        assert del_resp.json()["status"] == "success"

        # Verify removed from vault
        accounts_after_del = await vault.list_accounts(platform=AccountPlatform.INSTAGRAM)
        assert len(accounts_after_del) == 0


