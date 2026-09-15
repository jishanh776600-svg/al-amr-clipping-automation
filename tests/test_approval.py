"""Step 24: Focused tests for Clip Approval & Publishing Control Workflow.

Tests cover:
- DB migration V19 (clip_approvals table created)
- ClipApprovalRecord model: state transitions, audit history, idempotency
- Store CRUD: create, get, list, update, reset, publishing guard
- API endpoints: GET/POST approval, reset, telemetry, review-package
- Publishing endpoint 404 guard (no export)
- Optimistic concurrency (stale version rejection with HTTP 409)
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from autoclip.app import create_app
from autoclip.db import store
from autoclip.db.models import (
    Clip,
    ClipApprovalRecord,
    Job,
    Source,
    new_id,
    utcnow,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_source() -> Source:
    return Source(id=new_id(), type="upload", path="/tmp/s.mp4", title="T")


def _make_job(source: Source) -> Job:
    return Job(id=new_id(), source_id=source.id)


def _make_clip(job: Job) -> Clip:
    return Clip(id=new_id(), job_id=job.id, start_s=0.0, end_s=30.0, rank=1, title="T", score=80)


def _make_approval(clip: Clip, job: Job, eligible: bool = True) -> ClipApprovalRecord:
    return ClipApprovalRecord(
        id=new_id(),
        job_id=job.id,
        clip_id=clip.id,
        current_status="PENDING_REVIEW",
        publish_eligible=eligible,
        blocking_reasons=[],
    )


def _create_fixtures(initialised_db):
    src = _make_source()
    store.create_source(src)
    job = _make_job(src)
    store.create_job(job)
    clip = _make_clip(job)
    store.create_clip(clip)
    return src, job, clip


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestClipApprovalModel:

    def test_initial_state(self):
        r = ClipApprovalRecord(id="x", job_id="j", clip_id="c")
        assert r.current_status == "PENDING_REVIEW"
        assert r.version == 1
        assert r.history == []

    def test_approve_transition(self):
        r = ClipApprovalRecord(id="x", job_id="j", clip_id="c", publish_eligible=True)
        r.apply_action("APPROVED", "APPROVE", "Looks great")
        assert r.current_status == "APPROVED"
        assert r.previous_status == "PENDING_REVIEW"
        assert r.version == 2
        assert len(r.history) == 1
        assert r.history[0]["operator_note"] == "Looks great"

    def test_reject_transition(self):
        r = ClipApprovalRecord(id="x", job_id="j", clip_id="c")
        r.apply_action("REJECTED", "REJECT", "Poor quality")
        assert r.current_status == "REJECTED"

    def test_changes_requested_transition(self):
        r = ClipApprovalRecord(id="x", job_id="j", clip_id="c")
        r.apply_action("CHANGES_REQUESTED", "REQUEST_CHANGES", "Fix ending")
        assert r.current_status == "CHANGES_REQUESTED"

    def test_invalid_transition_raises(self):
        r = ClipApprovalRecord(id="x", job_id="j", clip_id="c")
        with pytest.raises(ValueError, match="Invalid transition"):
            r.apply_action("PENDING_REVIEW", "RESET", "")

    def test_is_approved_for_publishing_requires_eligible_and_approved(self):
        r = ClipApprovalRecord(id="x", job_id="j", clip_id="c", publish_eligible=True)
        assert not r.is_approved_for_publishing
        r.apply_action("APPROVED", "APPROVE", "")
        assert r.is_approved_for_publishing

    def test_approved_but_not_eligible_false(self):
        r = ClipApprovalRecord(id="x", job_id="j", clip_id="c", publish_eligible=False)
        r.apply_action("APPROVED", "APPROVE", "")
        assert not r.is_approved_for_publishing

    def test_can_transition_to(self):
        r = ClipApprovalRecord(id="x", job_id="j", clip_id="c")
        assert r.can_transition_to("APPROVED")
        assert r.can_transition_to("REJECTED")
        assert not r.can_transition_to("PENDING_REVIEW")

    def test_multi_step_history(self):
        r = ClipApprovalRecord(id="x", job_id="j", clip_id="c")
        r.apply_action("REJECTED", "REJECT", "Bad audio")
        r.apply_action("PENDING_REVIEW", "RESET", "")
        assert len(r.history) == 2
        assert r.history[0]["to_status"] == "REJECTED"
        assert r.history[1]["to_status"] == "PENDING_REVIEW"

    def test_to_dict_has_is_approved_for_publishing(self):
        r = ClipApprovalRecord(id="x", job_id="j", clip_id="c")
        d = r.to_dict()
        assert "is_approved_for_publishing" in d
        assert d["current_status"] == "PENDING_REVIEW"


# ---------------------------------------------------------------------------
# Store tests
# ---------------------------------------------------------------------------


class TestClipApprovalStore:

    def test_create_and_get(self, initialised_db):
        _, job, clip = _create_fixtures(initialised_db)
        store.create_clip_approval(_make_approval(clip, job))
        fetched = store.get_clip_approval(clip.id)
        assert fetched is not None
        assert fetched.clip_id == clip.id
        assert fetched.current_status == "PENDING_REVIEW"

    def test_get_nonexistent_returns_none(self, initialised_db):
        assert store.get_clip_approval("no-such-id") is None

    def test_list_for_job(self, initialised_db):
        _, job, clip = _create_fixtures(initialised_db)
        store.create_clip_approval(_make_approval(clip, job))
        records = store.list_clip_approvals_for_job(job.id)
        assert len(records) == 1
        assert records[0].clip_id == clip.id

    def test_update_approval(self, initialised_db):
        _, job, clip = _create_fixtures(initialised_db)
        store.create_clip_approval(_make_approval(clip, job))
        fetched = store.get_clip_approval(clip.id)
        fetched.apply_action("APPROVED", "APPROVE", "Looks good")
        store.update_clip_approval(fetched)
        updated = store.get_clip_approval(clip.id)
        assert updated.current_status == "APPROVED"
        assert updated.version == 2
        assert len(updated.history) == 1

    def test_reset_approval_preserves_history(self, initialised_db):
        _, job, clip = _create_fixtures(initialised_db)
        store.create_clip_approval(_make_approval(clip, job))
        fetched = store.get_clip_approval(clip.id)
        fetched.apply_action("APPROVED", "APPROVE", "")
        store.update_clip_approval(fetched)
        reset = store.reset_clip_approval(clip.id)
        assert reset.current_status == "PENDING_REVIEW"
        assert reset.version == 3
        assert any(e["to_status"] == "PENDING_REVIEW" for e in reset.history)
        assert any(e["to_status"] == "APPROVED" for e in reset.history)

    def test_is_clip_approved_for_publishing_not_approved(self, initialised_db):
        _, job, clip = _create_fixtures(initialised_db)
        store.create_clip_approval(_make_approval(clip, job))
        ok, reasons = store.is_clip_approved_for_publishing(clip.id)
        assert not ok
        assert reasons

    def test_is_clip_approved_for_publishing_no_record(self, initialised_db):
        _, _, clip = _create_fixtures(initialised_db)
        ok, reasons = store.is_clip_approved_for_publishing(clip.id)
        assert not ok
        assert any("not been reviewed" in r for r in reasons)

    def test_is_clip_approved_for_publishing_approved_and_eligible(self, initialised_db):
        _, job, clip = _create_fixtures(initialised_db)
        approval = _make_approval(clip, job, eligible=True)
        store.create_clip_approval(approval)
        fetched = store.get_clip_approval(clip.id)
        fetched.apply_action("APPROVED", "APPROVE", "")
        store.update_clip_approval(fetched)
        ok, reasons = store.is_clip_approved_for_publishing(clip.id)
        assert ok
        assert reasons == []

    def test_review_package_assembled_correctly(self, initialised_db):
        _, job, clip = _create_fixtures(initialised_db)
        pkg = store.build_clip_approval_review_package(clip.id, job.id)
        assert pkg["clip_id"] == clip.id
        assert pkg["job_id"] == job.id
        assert "approval_status" in pkg
        assert "publish_eligible" in pkg

    def test_upsert_replaces_on_duplicate_clip_id(self, initialised_db):
        _, job, clip = _create_fixtures(initialised_db)
        store.create_clip_approval(_make_approval(clip, job))
        second = ClipApprovalRecord(
            id=new_id(),
            job_id=job.id,
            clip_id=clip.id,
            current_status="APPROVED",
            publish_eligible=True,
        )
        store.create_clip_approval(second)
        fetched = store.get_clip_approval(clip.id)
        assert fetched.current_status == "APPROVED"


# ---------------------------------------------------------------------------
# API tests
# ---------------------------------------------------------------------------


@pytest.fixture
def client(initialised_db):
    os.environ.setdefault("AUTOCLIP_API_KEY", "test-key-24")
    return TestClient(create_app())


def _h():
    return {"Authorization": "Bearer test-key-24"}


def _api_fixtures(client):
    tf = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    tf.write(b"\x00" * 1024)
    tf.close()
    src = Source(id=new_id(), type="upload", path=tf.name, title="T")
    store.create_source(src)
    job = Job(id=new_id(), source_id=src.id)
    store.create_job(job)
    clip = Clip(id=new_id(), job_id=job.id, start_s=0.0, end_s=30.0, rank=1, title="T")
    store.create_clip(clip)
    try:
        os.unlink(tf.name)
    except Exception:
        pass
    return src.id, job.id, clip.id


class TestApprovalAPI:

    def test_get_approval_auto_init(self, client, initialised_db):
        _, job_id, clip_id = _api_fixtures(client)
        r = client.get(f"/api/jobs/{job_id}/clips/{clip_id}/approval", headers=_h())
        assert r.status_code == 200
        d = r.json()
        assert d["current_status"] == "PENDING_REVIEW"
        assert d["clip_id"] == clip_id

    def test_approve_action(self, client, initialised_db):
        _, job_id, clip_id = _api_fixtures(client)
        client.get(f"/api/jobs/{job_id}/clips/{clip_id}/approval", headers=_h())
        r = client.post(
            f"/api/jobs/{job_id}/clips/{clip_id}/approval",
            headers={**_h(), "Content-Type": "application/json"},
            json={"action": "APPROVE"},
        )
        assert r.status_code == 200
        d = r.json()
        assert d["current_status"] == "APPROVED"
        assert d["version"] == 2

    def test_reject_without_note_returns_422(self, client, initialised_db):
        _, job_id, clip_id = _api_fixtures(client)
        client.get(f"/api/jobs/{job_id}/clips/{clip_id}/approval", headers=_h())
        r = client.post(
            f"/api/jobs/{job_id}/clips/{clip_id}/approval",
            headers={**_h(), "Content-Type": "application/json"},
            json={"action": "REJECT", "operator_note": ""},
        )
        assert r.status_code == 422

    def test_reject_with_note_succeeds(self, client, initialised_db):
        _, job_id, clip_id = _api_fixtures(client)
        client.get(f"/api/jobs/{job_id}/clips/{clip_id}/approval", headers=_h())
        r = client.post(
            f"/api/jobs/{job_id}/clips/{clip_id}/approval",
            headers={**_h(), "Content-Type": "application/json"},
            json={"action": "REJECT", "operator_note": "Bad audio quality"},
        )
        assert r.status_code == 200
        assert r.json()["current_status"] == "REJECTED"

    def test_request_changes_with_note_succeeds(self, client, initialised_db):
        _, job_id, clip_id = _api_fixtures(client)
        client.get(f"/api/jobs/{job_id}/clips/{clip_id}/approval", headers=_h())
        r = client.post(
            f"/api/jobs/{job_id}/clips/{clip_id}/approval",
            headers={**_h(), "Content-Type": "application/json"},
            json={"action": "REQUEST_CHANGES", "operator_note": "Trim the ending"},
        )
        assert r.status_code == 200
        assert r.json()["current_status"] == "CHANGES_REQUESTED"

    def test_idempotent_approve(self, client, initialised_db):
        _, job_id, clip_id = _api_fixtures(client)
        client.get(f"/api/jobs/{job_id}/clips/{clip_id}/approval", headers=_h())
        client.post(
            f"/api/jobs/{job_id}/clips/{clip_id}/approval",
            headers={**_h(), "Content-Type": "application/json"},
            json={"action": "APPROVE"},
        )
        r2 = client.post(
            f"/api/jobs/{job_id}/clips/{clip_id}/approval",
            headers={**_h(), "Content-Type": "application/json"},
            json={"action": "APPROVE"},
        )
        assert r2.status_code == 200
        assert r2.json()["current_status"] == "APPROVED"

    def test_stale_version_returns_409(self, client, initialised_db):
        _, job_id, clip_id = _api_fixtures(client)
        client.get(f"/api/jobs/{job_id}/clips/{clip_id}/approval", headers=_h())
        client.post(
            f"/api/jobs/{job_id}/clips/{clip_id}/approval",
            headers={**_h(), "Content-Type": "application/json"},
            json={"action": "APPROVE"},
        )
        r = client.post(
            f"/api/jobs/{job_id}/clips/{clip_id}/approval",
            headers={**_h(), "Content-Type": "application/json"},
            json={"action": "REJECT", "operator_note": "stale", "expected_version": 1},
        )
        assert r.status_code == 409

    def test_unknown_action_returns_422(self, client, initialised_db):
        _, job_id, clip_id = _api_fixtures(client)
        client.get(f"/api/jobs/{job_id}/clips/{clip_id}/approval", headers=_h())
        r = client.post(
            f"/api/jobs/{job_id}/clips/{clip_id}/approval",
            headers={**_h(), "Content-Type": "application/json"},
            json={"action": "PUBLISH_NOW"},
        )
        assert r.status_code == 422

    def test_reset_approval(self, client, initialised_db):
        _, job_id, clip_id = _api_fixtures(client)
        client.get(f"/api/jobs/{job_id}/clips/{clip_id}/approval", headers=_h())
        client.post(
            f"/api/jobs/{job_id}/clips/{clip_id}/approval",
            headers={**_h(), "Content-Type": "application/json"},
            json={"action": "APPROVE"},
        )
        r = client.post(
            f"/api/jobs/{job_id}/clips/{clip_id}/approval/reset", headers=_h()
        )
        assert r.status_code == 200
        assert r.json()["current_status"] == "PENDING_REVIEW"

    def test_list_job_approvals(self, client, initialised_db):
        _, job_id, clip_id = _api_fixtures(client)
        client.get(f"/api/jobs/{job_id}/clips/{clip_id}/approval", headers=_h())
        r = client.get(f"/api/jobs/{job_id}/approvals", headers=_h())
        assert r.status_code == 200
        assert isinstance(r.json(), list)
        assert len(r.json()) >= 1

    def test_review_package_endpoint(self, client, initialised_db):
        _, job_id, clip_id = _api_fixtures(client)
        r = client.get(
            f"/api/jobs/{job_id}/clips/{clip_id}/approval/review-package",
            headers=_h(),
        )
        assert r.status_code == 200
        pkg = r.json()
        assert pkg["clip_id"] == clip_id
        assert "approval_status" in pkg

    def test_approval_for_unknown_clip_returns_404(self, client, initialised_db):
        _, job_id, _ = _api_fixtures(client)
        r = client.get(
            f"/api/jobs/{job_id}/clips/unknown-clip/approval", headers=_h()
        )
        assert r.status_code == 404

    def test_publishing_missing_export_returns_404(self, client, initialised_db):
        r = client.post(
            "/api/exports/nonexistent-export/publish",
            headers={**_h(), "Content-Type": "application/json"},
            json={"platforms": ["telegram"], "dry_run": True},
        )
        assert r.status_code == 404

    def test_real_media_smoke_approval_flow(self, client, initialised_db):
        """Smoke test with real media asset felix_speech.mp4."""
        media_path = Path(__file__).parent / "test_media" / "felix_speech.mp4"
        assert media_path.exists(), f"Real media missing: {media_path}"

        # 1. Setup Source, Job, Clip
        src = Source(id=new_id(), type="upload", path=str(media_path), title="Felix Speech")
        store.create_source(src)
        job = Job(id=new_id(), source_id=src.id)
        store.create_job(job)
        clip = Clip(id=new_id(), job_id=job.id, start_s=0.0, end_s=5.0, rank=1, title="Felix Intro")
        store.create_clip(clip)

        # 2. Setup Step 22 Final Render record pointing to real media
        from autoclip.db.models import FinalRenderRecord, ClipMetadataRecord
        render = FinalRenderRecord(
            id=new_id(),
            job_id=job.id,
            clip_id=clip.id,
            output_path=str(media_path),
            package_dir=str(media_path.parent),
            duration=5.0,
            width=1080,
            height=1920,
            fps=30.0,
            quality_status="RENDER_PASS",
            render_status="completed",
        )
        store.replace_final_renders(job.id, [render])

        # 3. Setup Step 23 SEO Metadata record
        meta = ClipMetadataRecord(
            id=new_id(),
            job_id=job.id,
            clip_id=clip.id,
            generated_title="Felix Highlights",
            final_title="Felix Highlights",
            generated_description="Official Felix clip #ALAMR",
            final_description="Official Felix clip #ALAMR",
            compliance_status="SEO_PASS",
            compliance_score=100.0,
        )
        store.create_clip_metadata(meta)

        # 4. Fetch approval endpoint -> auto-inits and runs readiness gate
        r = client.get(f"/api/jobs/{job.id}/clips/{clip.id}/approval", headers=_h())
        assert r.status_code == 200
        data = r.json()
        assert data["current_status"] == "PENDING_REVIEW"
        assert data["publish_eligible"] is True
        assert data["blocking_reasons"] == []

        # 5. Operator approves via API
        r_post = client.post(
            f"/api/jobs/{job.id}/clips/{clip.id}/approval",
            headers={**_h(), "Content-Type": "application/json"},
            json={"action": "APPROVE", "operator_note": "Smoke test approved"},
        )
        assert r_post.status_code == 200
        approved_data = r_post.json()
        assert approved_data["current_status"] == "APPROVED"
        assert approved_data["is_approved_for_publishing"] is True

        # 6. Verify publishing guard passes
        ok, reasons = store.is_clip_approved_for_publishing(clip.id)
        assert ok is True
        assert reasons == []

        # 7. Check review package
        r_pkg = client.get(
            f"/api/jobs/{job.id}/clips/{clip.id}/approval/review-package",
            headers=_h(),
        )
        assert r_pkg.status_code == 200
        pkg = r_pkg.json()
        assert pkg["output_path"] == str(media_path)
        assert pkg["approval_status"] == "APPROVED"
        assert pkg["publish_eligible"] is True