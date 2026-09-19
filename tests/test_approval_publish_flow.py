"""Integration tests for the complete Approve & Publish and Request Changes review workflow."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from autoclip.app import create_app
from autoclip.db import store
from autoclip.db.models import (
    Clip,
    ClipApprovalRecord,
    ClipMetadataRecord,
    FinalRenderRecord,
    Job,
    PublicationRecord,
    Source,
    new_id,
    utcnow,
)
from autoclip.publishing.service import PublishingService


def _auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-operator-token"}


@pytest.fixture
def api_client(initialised_db, monkeypatch):
    monkeypatch.setenv("OPERATOR_TOKEN", "test-operator-token")
    monkeypatch.delenv("AUTOCLIP_API_KEY", raising=False)
    monkeypatch.delenv("AL_AMR_MASTER_KEY", raising=False)
    monkeypatch.delenv("WORKER_CALLBACK_SECRET", raising=False)
    app = create_app()
    with TestClient(app) as client:
        yield client


def _setup_job_clip(tmp_path: Path):
    media_file = tmp_path / "clip.mp4"
    media_file.write_bytes(b"\x00" * 1024)

    src = Source(id=new_id(), type="upload", path=str(media_file), title="Test Source")
    store.create_source(src)
    job = Job(id=new_id(), source_id=src.id)
    store.create_job(job)
    clip = Clip(id=new_id(), job_id=job.id, start_s=0.0, end_s=30.0, rank=1, title="Test Clip", score=90)
    store.create_clip(clip)

    render = FinalRenderRecord(
        id=new_id(),
        job_id=job.id,
        clip_id=clip.id,
        output_path=str(media_file),
        package_dir=str(tmp_path),
        duration=30.0,
        width=1080,
        height=1920,
        fps=30.0,
        quality_status="RENDER_PASS",
        render_status="completed",
    )
    store.replace_final_renders(job.id, [render])

    meta = ClipMetadataRecord(
        id=new_id(),
        job_id=job.id,
        clip_id=clip.id,
        generated_title="Test Title",
        final_title="Test Title",
        generated_description="Test Desc",
        final_description="Test Desc",
        compliance_status="SEO_PASS",
        compliance_score=100.0,
    )
    store.create_clip_metadata(meta)

    return job, clip, media_file


class TestApprovalAndPublishingFlow:

    def test_approve_initiates_youtube_and_instagram_publishing(self, api_client, initialised_db, tmp_path):
        job, clip, _ = _setup_job_clip(tmp_path)

        # Initial approval state should be PENDING_REVIEW
        resp = api_client.get(f"/api/jobs/{job.id}/clips/{clip.id}/approval", headers=_auth_headers())
        assert resp.status_code == 200
        assert resp.json()["current_status"] == "PENDING_REVIEW"

        # Mock the background publisher to avoid external network calls during unit test
        with patch.object(PublishingService, "publish_clip_all_destinations", new_callable=AsyncMock) as mock_pub:
            mock_pub.return_value = [
                PublicationRecord(
                    id="pub-yt-1",
                    job_id=job.id,
                    clip_id=clip.id,
                    platform="youtube",
                    destination_id="dest-youtube-main",
                    idempotency_key=f"{job.id}:{clip.id}:youtube:dest-youtube-main",
                    status="PUBLISHED",
                    permalink="https://youtube.com/shorts/test123",
                ),
                PublicationRecord(
                    id="pub-ig-1",
                    job_id=job.id,
                    clip_id=clip.id,
                    platform="instagram",
                    destination_id="dest-instagram-main",
                    idempotency_key=f"{job.id}:{clip.id}:instagram:dest-instagram-main",
                    status="FAILED_PERMANENT",
                    error_message="Instagram OAuth token expired",
                ),
            ]

            post_resp = api_client.post(
                f"/api/jobs/{job.id}/clips/{clip.id}/approval",
                headers={**_auth_headers(), "Content-Type": "application/json"},
                json={"action": "APPROVE", "operator_note": "Approved for live publishing"},
            )
            assert post_resp.status_code == 200
            data = post_resp.json()
            assert data["current_status"] == "APPROVED"
            assert data["is_approved_for_publishing"] is True

            # Verify publications are seeded and returned in response
            pubs = data.get("publications")
            assert pubs is not None
            assert len(pubs) >= 2
            platforms = {p["platform"] for p in pubs}
            assert "youtube" in platforms
            assert "instagram" in platforms

    def test_request_changes_transitions_state_and_cancels_publishing(self, api_client, initialised_db, tmp_path):
        job, clip, _ = _setup_job_clip(tmp_path)

        # 1. First test request changes without note -> defaults note cleanly
        resp = api_client.post(
            f"/api/jobs/{job.id}/clips/{clip.id}/approval",
            headers={**_auth_headers(), "Content-Type": "application/json"},
            json={"action": "REQUEST_CHANGES", "operator_note": ""},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["current_status"] == "CHANGES_REQUESTED"
        assert data["is_approved_for_publishing"] is False
        assert len(data["history"]) >= 1
        assert data["history"][-1]["operator_note"] == "Changes requested by operator"

        # 2. Reset approval
        reset_resp = api_client.post(
            f"/api/jobs/{job.id}/clips/{clip.id}/approval/reset",
            headers={**_auth_headers(), "Content-Type": "application/json"},
            json={"operator_note": "Resetting for second check"},
        )
        assert reset_resp.status_code == 200
        assert reset_resp.json()["current_status"] == "PENDING_REVIEW"

        # 3. Test request changes with custom operator note
        resp2 = api_client.post(
            f"/api/jobs/{job.id}/clips/{clip.id}/approval",
            headers={**_auth_headers(), "Content-Type": "application/json"},
            json={"action": "REQUEST_CHANGES", "operator_note": "Adjust audio balance and crop 2s"},
        )
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["current_status"] == "CHANGES_REQUESTED"
        assert data2["history"][-1]["operator_note"] == "Adjust audio balance and crop 2s"

    @pytest.mark.asyncio
    async def test_publishing_service_multi_destination_handles_failures_independently(self, initialised_db, tmp_path):
        job, clip, _ = _setup_job_clip(tmp_path)

        # Approve the clip so publishing eligibility gate passes Step 24
        approval = ClipApprovalRecord(
            id=new_id(),
            job_id=job.id,
            clip_id=clip.id,
            current_status="APPROVED",
            publish_eligible=True,
            blocking_reasons=[],
        )
        store.create_clip_approval(approval)

        svc = PublishingService()

        # Without external credentials configured, both platforms execute independently
        results = await svc.publish_clip_all_destinations(
            job_id=job.id,
            clip_id=clip.id,
            platforms=["youtube", "instagram"],
            dry_run=False,
        )

        assert len(results) == 2
        plat_map = {r.platform: r for r in results}
        assert "youtube" in plat_map
        assert "instagram" in plat_map

        # Neither platform should crash or suppress the other, and error messages are captured
        for p, rec in plat_map.items():
            assert rec.status in ("FAILED", "FAILED_PERMANENT", "FAILED_RETRYABLE", "PUBLISHED")
            if "FAILED" in rec.status:
                assert rec.error_message != ""
