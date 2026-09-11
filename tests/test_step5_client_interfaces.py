"""Tests for AL AMR Step 5: Client Interfaces & Operator Control Surface."""

import os
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from autoclip.app import create_app
from autoclip import db, paths
from autoclip.db import store
from autoclip.db.models import new_id, utcnow, Job, Source, Clip, Export


@pytest.fixture
def client_env(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOCLIP_HOME", str(tmp_path))
    monkeypatch.setenv("AUTOCLIP_NO_WORKER", "1")
    paths.ensure_layout()
    db.init()
    app = create_app()
    with TestClient(app) as c:
        yield c, tmp_path


def test_campaign_presets_crud(client_env):
    client, _ = client_env

    # 1. Create Campaign
    payload = {
        "name": "Al Amr Viral Brand Q4",
        "brief": {
            "name": "Al Amr Viral Brand Q4",
            "topic_context": "Business strategy and automation in Middle East",
            "target_audience": "Arabic entrepreneurs",
            "required_topics": ["ROI", "Growth", "Automation"],
            "banned_topics": ["Politics", "Scams"],
            "minimum_duration": 25.0,
            "maximum_duration": 75.0,
            "hook_required": True,
            "hook_types": ["Controversial statement", "Question hook"],
            "output_count": 5
        }
    }
    resp = client.post("/api/campaigns", json=payload)
    assert resp.status_code == 201
    created = resp.json()
    assert created["name"] == "Al Amr Viral Brand Q4"
    assert created["id"] is not None
    assert created["brief"]["topic_context"] == "Business strategy and automation in Middle East"
    cid = created["id"]

    # 2. Get Campaign by ID
    resp_get = client.get(f"/api/campaigns/{cid}")
    assert resp_get.status_code == 200
    assert resp_get.json()["id"] == cid

    # 3. List Campaigns
    resp_list = client.get("/api/campaigns")
    assert resp_list.status_code == 200
    items = resp_list.json()
    assert len(items) >= 1
    assert any(item["id"] == cid for item in items)

    # 4. Update Campaign
    update_payload = {
        "id": cid,
        "name": "Al Amr Viral Brand Q4 Updated",
        "brief": {
            **created["brief"],
            "output_count": 8
        }
    }
    resp_update = client.post("/api/campaigns", json=update_payload)
    assert resp_update.status_code == 201
    updated = resp_update.json()
    assert updated["name"] == "Al Amr Viral Brand Q4 Updated"
    assert updated["brief"]["output_count"] == 8

    # 5. Delete Campaign
    resp_del = client.delete(f"/api/campaigns/{cid}")
    assert resp_del.status_code == 204

    # Verify 404 after delete
    resp_del_get = client.get(f"/api/campaigns/{cid}")
    assert resp_del_get.status_code == 404


def test_campaign_preset_invalid_brief(client_env):
    client, _ = client_env
    bad_payload = {
        "name": "Invalid Campaign",
        "brief": {
            "minimum_duration": -10.0  # Must be > 0
        }
    }
    resp = client.post("/api/campaigns", json=bad_payload)
    assert resp.status_code == 400


def test_cross_job_clips_endpoint(client_env):
    client, tmp_path = client_env

    # Seed source, jobs, and clips directly in DB
    source = Source(
        id=new_id(),
        type="upload",
        path=str(tmp_path / "test.mp4"),
        title="Test Clip Video",
        url=None,
        filename="test.mp4",
        channel="Al Amr",
        duration_s=120.0,
        width=1920,
        height=1080,
        fps=30.0,
        has_audio=True,
        has_video=True,
        created_at=utcnow()
    )
    store.create_source(source)

    job1 = Job(
        id=new_id(),
        source_id=source.id,
        status="done",
        current_stage="complete",
        progress=1.0,
        error=None,
        provider="dummy",
        created_at=utcnow(),
        updated_at=utcnow(),
        started_at=utcnow(),
        finished_at=utcnow()
    )
    store.create_job(job1)

    clip1 = Clip(
        id=new_id(),
        job_id=job1.id,
        start_s=5.0,
        end_s=35.0,
        rank=1,
        title="First High Scoring Clip",
        hook="Did you know this about scaling?",
        score=92,
        reason="Strong hook with immediate tension",
        status="kept",
    )

    clip2 = Clip(
        id=new_id(),
        job_id=job1.id,
        start_s=40.0,
        end_s=70.0,
        rank=2,
        title="Second Clip Candidate",
        hook="Another hook point",
        score=74,
        reason="Good insight",
        status="candidate",
    )
    store.replace_clips(job1.id, [clip1, clip2])

    # Test GET /api/clips
    resp = client.get("/api/clips?limit=10")
    assert resp.status_code == 200
    clips = resp.json()
    assert len(clips) >= 2
    assert any(c["id"] == clip1.id for c in clips)
    assert any(c["id"] == clip2.id for c in clips)

    # Test GET /api/clips?status=kept
    resp_kept = client.get("/api/clips?status=kept")
    assert resp_kept.status_code == 200
    kept_clips = resp_kept.json()
    assert all(c["status"] == "kept" for c in kept_clips)
    assert any(c["id"] == clip1.id for c in kept_clips)


def test_export_streaming_range_request(client_env):
    client, tmp_path = client_env

    # 1. Create DB dependencies (Source, Job, Clip) for Export FK
    source = Source(
        id=new_id(),
        type="upload",
        path=str(tmp_path / "stream_source.mp4"),
        title="Stream Test Source",
        duration_s=60.0,
        created_at=utcnow()
    )
    store.create_source(source)

    job = Job(
        id=new_id(),
        source_id=source.id,
        status="done",
        current_stage="complete",
        progress=1.0,
        provider="dummy",
        created_at=utcnow(),
        updated_at=utcnow(),
        started_at=utcnow(),
        finished_at=utcnow()
    )
    store.create_job(job)

    clip_id = new_id()
    clip = Clip(
        id=clip_id,
        job_id=job.id,
        start_s=0.0,
        end_s=20.0,
        rank=1,
        title="Streaming Clip",
        status="kept",
        created_at=utcnow()
    )
    store.replace_clips(job.id, [clip])

    # 2. Create dummy export file
    export_id = new_id()
    export_dir = paths.exports_dir()
    export_dir.mkdir(parents=True, exist_ok=True)
    export_file = export_dir / f"{export_id}.mp4"

    # Write 4096 bytes of dummy data
    sample_content = b"AL_AMR_STREAM_VIDEO_TEST_PAYLOAD_" * 128  # ~4KB
    export_file.write_bytes(sample_content)
    total_len = len(sample_content)

    rec = Export(
        id=export_id,
        clip_id=clip_id,
        ratio="9:16",
        style="bold_pop",
        path=str(export_file),
        size_bytes=total_len,
        created_at=utcnow()
    )
    store.create_export(rec)

    # 1. Full stream request (no Range header)
    resp_full = client.get(f"/api/exports/{export_id}/stream")
    assert resp_full.status_code == 200
    assert resp_full.headers["Accept-Ranges"] == "bytes"
    assert "inline" in resp_full.headers["Content-Disposition"]
    assert len(resp_full.content) == total_len

    # 2. Range request: bytes=0-199
    resp_range = client.get(
        f"/api/exports/{export_id}/stream",
        headers={"Range": "bytes=0-199"}
    )
    assert resp_range.status_code == 206
    assert resp_range.headers["Accept-Ranges"] == "bytes"
    assert resp_range.headers["Content-Range"] == f"bytes 0-199/{total_len}"
    assert len(resp_range.content) == 200
    assert resp_range.content == sample_content[0:200]

    # 3. Range request: bytes=200-499
    resp_range_mid = client.get(
        f"/api/exports/{export_id}/stream",
        headers={"Range": "bytes=200-499"}
    )
    assert resp_range_mid.status_code == 206
    assert resp_range_mid.headers["Content-Range"] == f"bytes 200-499/{total_len}"
    assert len(resp_range_mid.content) == 300
    assert resp_range_mid.content == sample_content[200:500]


def test_client_disconnect_reconnect_invariant(client_env):
    """Verifies that disconnecting clients (browser closed, phone slept)
    always reconnect to authoritative state from DB/API."""
    client, tmp_path = client_env

    # 1. Client creates a job
    source = Source(
        id=new_id(),
        type="youtube",
        path=str(tmp_path / "source.mp4"),
        title="Authoritative State Test",
        url="https://youtube.com/watch?v=mock123",
        filename=None,
        channel="Al Amr",
        duration_s=60.0,
        width=1920,
        height=1080,
        fps=30.0,
        has_audio=True,
        has_video=True,
        created_at=utcnow()
    )
    store.create_source(source)

    job = Job(
        id=new_id(),
        source_id=source.id,
        status="running",
        current_stage="transcribe",
        progress=0.35,
        error=None,
        provider="mock",
        created_at=utcnow(),
        updated_at=utcnow(),
        started_at=utcnow(),
        finished_at=None
    )
    store.create_job(job)

    # 2. Client queries initial state
    resp1 = client.get(f"/api/jobs/{job.id}")
    assert resp1.status_code == 200
    assert resp1.json()["progress"] == 0.35
    assert resp1.json()["current_stage"] == "transcribe"

    # 3. Client DISCONNECTS (simulated: client process sleeps or drops connection)
    # Background server continues processing independently:
    store.update_job(job.id, current_stage="highlight", progress=0.65)
    store.update_job(job.id, status="done", current_stage="complete", progress=1.0)

    # 4. Client RECONNECTS (e.g. phone wakes up or browser tab refreshes)
    resp2 = client.get(f"/api/jobs/{job.id}")
    assert resp2.status_code == 200
    reconnected_state = resp2.json()
    assert reconnected_state["status"] == "done"
    assert reconnected_state["progress"] == 1.0
    assert reconnected_state["current_stage"] == "complete"
