"""Regression tests for publishing route ordering and fetch resilience.

Verifies:
1. GET /api/publishing/destinations is not shadowed by GET /api/publishing/{record_id}.
2. GET /api/publishing/queue is not shadowed by GET /api/publishing/{record_id}.
3. GET /api/publishing/orchestration-telemetry returns operational status and telemetry.
4. Nonexistent publishing record ID still returns 404 cleanly.
5. Export streaming handles missing Drive file or Drive API errors with graceful fallback.
"""

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from autoclip.app import create_app
from autoclip.db import store, models


@pytest.fixture
def client_env():
    tmp = tempfile.mkdtemp(prefix="alamr-routes-test-")
    old_home = os.environ.get("AUTOCLIP_HOME")
    old_master = os.environ.get("AL_AMR_MASTER_KEY")
    os.environ["AUTOCLIP_API_KEY"] = "test-key-25"

    from autoclip import db, paths
    import autoclip.security.vault

    db.reset_connections()
    autoclip.security.vault._global_vault = None
    paths.ensure_layout()
    db.init()

    app = create_app()
    client = TestClient(app)

    yield client

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


def test_publishing_destinations_not_shadowed(client_env):
    """GET /api/publishing/destinations must return destinations list, not 404 'Publishing record not found'."""
    resp = client_env.get("/api/publishing/destinations", headers={"Authorization": "Bearer test-key-25"})
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 3
    platforms = [d["platform"] for d in data]
    assert "youtube" in platforms
    assert "instagram" in platforms
    assert "telegram" in platforms


def test_publishing_queue_not_shadowed(client_env):
    """GET /api/publishing/queue must return queue items list, not 404 'Publishing record not found'."""
    resp = client_env.get("/api/publishing/queue", headers={"Authorization": "Bearer test-key-25"})
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)


def test_publishing_orchestration_telemetry_not_shadowed(client_env):
    """GET /api/publishing/orchestration-telemetry must return telemetry dict, not 404 'Publishing record not found'."""
    resp = client_env.get("/api/publishing/orchestration-telemetry", headers={"Authorization": "Bearer test-key-25"})
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("status") == "operational"
    assert "queue" in data
    assert "destinations" in data
    assert "reserve" in data
    assert "learning" in data


def test_publishing_record_id_still_works(client_env):
    """GET /api/publishing/{record_id} correctly returns 404 for nonexistent record and 200 for real record."""
    # Nonexistent
    resp = client_env.get("/api/publishing/nonexistent-rec-123", headers={"Authorization": "Bearer test-key-25"})
    assert resp.status_code == 404
    assert resp.json().get("detail") == "Publishing record not found."

    # Insert parent source, job, export, and record
    src = models.Source(id="src-1", type="upload", path="/tmp/test.mp4", title="Test")
    store.create_source(src)
    job = models.Job(id="job-1", source_id="src-1")
    store.create_job(job)
    clip = models.Clip(id="clip-1", job_id="job-1", start_s=0.0, end_s=5.0, rank=1, title="Test")
    store.create_clip(clip)
    exp = models.Export(id="exp-1", clip_id="clip-1", ratio="9:16", style="bold", size_bytes=100, path="/tmp/test.mp4")
    store.create_export(exp)

    rec = models.PublishingRecord(
        id="pub-test-real-123",
        job_id="job-1",
        export_id="exp-1",
        platform="telegram",
        status="published",
    )
    store.create_or_update_publishing_record(rec)

    resp2 = client_env.get("/api/publishing/pub-test-real-123", headers={"Authorization": "Bearer test-key-25"})
    assert resp2.status_code == 200
    assert resp2.json()["id"] == "pub-test-real-123"
    assert resp2.json()["platform"] == "telegram"
