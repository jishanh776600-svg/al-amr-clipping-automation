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

