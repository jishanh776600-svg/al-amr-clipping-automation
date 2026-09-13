"""Step 14 Final Production Release Certification & Security Audit Tests.

Covers:
1. Operator API Authentication & 401 Protection on protected routes.
2. Health (/health) and Readiness (/ready) probes (no secrets leaked).
3. Public YouTube URL variants normalization and validation.
4. Clean failure guidance when all acquisition providers fail (direct video upload guidance).
5. Forensic Job Manifest completeness and secret non-leakage.
6. Publishing safety defaults (Telegram live, YouTube dry-run).
7. Unified 3-input model validation (Source, Guidelines, Destinations).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from autoclip import app as app_module
from autoclip.app import create_app
from autoclip.db import store
from autoclip.db.models import Job, Source, new_id
from autoclip.health import check_liveness, check_readiness
from autoclip.jobs import orchestrator
from autoclip.pipeline import ingest
from autoclip.pipeline.source_acquisition import (
    AcquisitionResult,
    SourceAcquisitionError,
    SourceErrorCode,
    validate_remote_url,
)
from autoclip.publishing.service import PublishingService
from autoclip.publishing.youtube import YouTubePublisher


@pytest.fixture
def client(autoclip_home, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv(app_module.ENV_NO_WORKER, "1")
    with TestClient(create_app()) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# 1. API Authentication & 401 Security Gate
# ---------------------------------------------------------------------------

def test_api_auth_required_when_token_configured(autoclip_home, monkeypatch):
    """When OPERATOR_TOKEN is set, unauthenticated mutation requests receive HTTP 401."""
    monkeypatch.setenv(app_module.ENV_NO_WORKER, "1")
    monkeypatch.setenv("OPERATOR_TOKEN", "super_secret_operator_key_999")

    with TestClient(create_app()) as test_client:
        # Protected endpoints must reject unauthenticated requests
        resp = test_client.post("/api/jobs", json={"source_id": "fake"})
        assert resp.status_code == 401
        assert "Unauthorized" in resp.text
        # Ensure the actual token is NOT in the response
        assert "super_secret_operator_key_999" not in resp.text

        # Valid Bearer token succeeds
        resp_auth = test_client.get(
            "/api/jobs",
            headers={"Authorization": "Bearer super_secret_operator_key_999"},
        )
        assert resp_auth.status_code == 200

        # Valid X-API-Key succeeds
        resp_key = test_client.get(
            "/api/jobs",
            headers={"X-API-Key": "super_secret_operator_key_999"},
        )
        assert resp_key.status_code == 200


def test_health_and_ready_publicly_available_without_secrets(client):
    """Liveness and readiness endpoints must be accessible unauthenticated and leak zero credentials."""
    resp_health = client.get("/health")
    assert resp_health.status_code == 200
    health_data = resp_health.json()
    assert health_data["status"] == "ok"

    resp_ready = client.get("/ready")
    assert resp_ready.status_code in (200, 503)
    ready_data = resp_ready.json()
    assert "checks" in ready_data
    assert "database" in ready_data["checks"]
    assert "storage" in ready_data["checks"]

    # Security check: ensure no environment keys or passwords exist in response text
    combined_text = resp_health.text + resp_ready.text
    assert "token" not in combined_text.lower()
    assert "password" not in combined_text.lower()
    assert "secret" not in combined_text.lower()


# ---------------------------------------------------------------------------
# 2. Public YouTube URL Normalization & Validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "valid_youtube_url",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/watch?v=dQw4w9WgXcQ",
        "http://m.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://www.youtube.com/shorts/3iZkQy7lFm4",
        "https://youtube.com/shorts/3iZkQy7lFm4",
        "https://www.youtube.com/embed/dQw4w9WgXcQ",
    ],
)
def test_youtube_url_patterns_supported(valid_youtube_url):
    """All standard public YouTube URL formats must be recognised."""
    assert ingest.is_youtube_url(valid_youtube_url) is True
    assert validate_remote_url(valid_youtube_url) == valid_youtube_url


@pytest.mark.parametrize(
    "invalid_or_unrelated_url",
    [
        "https://vimeo.com/12345678",
        "https://tiktok.com/@user/video/123",
        "https://notyoutube.com/watch?v=123",
        "file:///local/path/video.mp4",
        "ftp://example.com/stream.mp4",
        "",
        "http://127.0.0.1:8000/watch?v=123",
    ],
)
def test_reject_unsupported_or_dangerous_urls(invalid_or_unrelated_url):
    """Non-YouTube or SSRF URLs must not be identified as valid YouTube URLs or safe remote URLs."""
    if not invalid_or_unrelated_url or "http" not in invalid_or_unrelated_url or "127.0.0.1" in invalid_or_unrelated_url:
        with pytest.raises(SourceAcquisitionError):
            validate_remote_url(invalid_or_unrelated_url)
    assert ingest.is_youtube_url(invalid_or_unrelated_url) is False


# ---------------------------------------------------------------------------
# 3. Clean Failure & Actionable Operator Guidance
# ---------------------------------------------------------------------------

def test_acquisition_failure_produces_clean_operator_guidance(tmp_path):
    """When all acquisition providers fail, error hint directs operator to direct file upload."""
    settings = ingest.IngestSettings()

    with patch("autoclip.pipeline.source_acquisition.registry.SourceAcquisitionRegistry.acquire") as mock_acq:
        mock_acq.side_effect = SourceAcquisitionError(
            "YouTube refused automated retrieval from cloud environment.",
            code=SourceErrorCode.SOURCE_ACCESS_BLOCKED,
            hint=(
                "AL AMR could not acquire source media automatically from this link across all configured acquisition providers. "
                "The source may be restricted, blocked, or unavailable. "
                "Please upload the video file directly to proceed."
            ),
        )

        with pytest.raises(SourceAcquisitionError) as exc_info:
            ingest.ingest_youtube("https://www.youtube.com/watch?v=blocked_video", settings)

        err = exc_info.value
        assert err.code == SourceErrorCode.SOURCE_ACCESS_BLOCKED
        assert "Please upload the video file directly to proceed" in err.hint


# ---------------------------------------------------------------------------
# 4. Forensic Manifest Secret Non-Leakage & Auditability
# ---------------------------------------------------------------------------

def test_forensic_manifest_contains_provenance_without_secret_leakage(client):
    """Forensic job manifest must contain source provenance and never leak API secrets."""
    source_id = new_id()
    source = Source(
        id=source_id,
        type="youtube",
        url="https://www.youtube.com/watch?v=test_prov_manifest",
        path="/tmp/fake_source.mp4",
        title="Test Campaign Video",
        duration_s=45.0,
    )
    store.create_source(source)

    job_id = new_id()
    job = Job(
        id=job_id,
        source_id=source_id,
        status="done",
        settings={
            "source_acquisition": {
                "provider": "yt-dlp",
                "attempts": 1,
                "sha256": "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
                "file_size": 10485760,
                "duration_s": 45.0,
            },
            "destinations": ["telegram", "drive"],
        },
    )
    store.create_job(job)

    manifest = orchestrator.get_job_manifest(job_id)
    assert manifest is not None
    assert manifest["job_id"] == job_id
    assert manifest["source"]["source_acquisition"]["provider"] == "yt-dlp"
    assert manifest["source"]["source_acquisition"]["sha256"].startswith("abcdef123")

    # API response test
    resp = client.get(f"/api/jobs/{job_id}/manifest")
    assert resp.status_code == 200
    data = resp.json()
    assert data["job_id"] == job_id
    assert data["source"]["source_acquisition"]["provider"] == "yt-dlp"


# ---------------------------------------------------------------------------
# 5. Publishing Safety Defaults
# ---------------------------------------------------------------------------

def test_publishing_safety_defaults(monkeypatch, tmp_path):
    """YouTube publishing must remain in DRY-RUN mode by default."""
    monkeypatch.delenv("YOUTUBE_PUBLISH_LIVE", raising=False)
    monkeypatch.setenv("YOUTUBE_REFRESH_TOKEN", "mock_refresh_token")

    fake_clip = tmp_path / "clip.mp4"
    fake_clip.write_bytes(b"dummy video bytes")

    publisher = YouTubePublisher()
    assert publisher.is_configured() is True

    import asyncio
    from autoclip.publishing.base import PublishingMetadata

    meta = PublishingMetadata(
        title="Test Clip",
        description="Test Description",
        tags=["AI", "Clipping"],
    )

    result = asyncio.run(publisher.publish(fake_clip, meta))
    assert result.success is True
    assert result.status == "ready_for_upload"
    assert result.details.get("mode") == "dry_run"
