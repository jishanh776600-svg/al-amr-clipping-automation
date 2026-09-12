"""Tests for Web Console authentication, protected endpoints, and settings API."""

from __future__ import annotations

import os
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from autoclip import db, paths
from autoclip.app import create_app
from autoclip.db import store
from autoclip.db.models import Job, Source, new_id


@pytest.fixture
def auth_client_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Set up app with an active OPERATOR_TOKEN."""
    monkeypatch.setenv("AUTOCLIP_HOME", str(tmp_path))
    monkeypatch.setenv("AUTOCLIP_NO_WORKER", "1")
    monkeypatch.setenv("OPERATOR_TOKEN", "prod-operator-token-xyz-12345")
    paths.ensure_layout()
    db.init()
    app = create_app()
    with TestClient(app) as client:
        yield client, "prod-operator-token-xyz-12345"


def test_public_endpoints_accessible_without_auth(auth_client_env):
    """Probes, health checks, root SPA, and static assets do not require authentication."""
    client, _ = auth_client_env

    # 1. Health and ready probes
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 200
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/ready").status_code == 200

    # 2. Root SPA page
    r_root = client.get("/")
    assert r_root.status_code == 200
    assert "text/html" in r_root.headers.get("content-type", "")


def test_unauthenticated_api_requests_return_401(auth_client_env):
    """All private /api/* endpoints return 401 Unauthorized without credentials."""
    client, _ = auth_client_env

    endpoints = [
        "/api/settings",
        "/api/system",
        "/api/jobs",
        "/api/clips",
        "/api/campaigns",
        "/api/caption-styles",
        "/api/providers/status",
        "/api/sources",
    ]

    for ep in endpoints:
        resp = client.get(ep)
        assert resp.status_code == 401, f"{ep} did not return 401 when unauthenticated"
        data = resp.json()
        assert "Unauthorized" in data.get("detail", "")


def test_invalid_token_returns_401(auth_client_env):
    """Providing an invalid token via Bearer header or X-API-Key returns 401."""
    client, _ = auth_client_env

    # Bad Bearer token
    r1 = client.get("/api/settings", headers={"Authorization": "Bearer wrong-token-value"})
    assert r1.status_code == 401

    # Bad X-API-Key
    r2 = client.get("/api/settings", headers={"X-API-Key": "wrong-token-value"})
    assert r2.status_code == 401

    # Bad query token
    r3 = client.get("/api/settings?token=wrong-token-value")
    assert r3.status_code == 401


def test_valid_authenticated_settings_request_succeeds(auth_client_env):
    """Valid token allows fetching settings with complete configuration schema."""
    client, token = auth_client_env

    # 1. Via Authorization: Bearer <token>
    r_bearer = client.get("/api/settings", headers={"Authorization": f"Bearer {token}"})
    assert r_bearer.status_code == 200
    data = r_bearer.json()
    assert "active_provider" in data
    assert "whisper" in data
    assert "clips" in data
    assert "export" in data
    assert "keys_present" in data

    # 2. Via X-API-Key
    r_key = client.get("/api/settings", headers={"X-API-Key": token})
    assert r_key.status_code == 200

    # 3. Via query parameter token (used by EventSource SSE and media downloads)
    r_query = client.get(f"/api/settings?token={token}")
    assert r_query.status_code == 200


def test_all_private_endpoints_succeed_with_valid_auth(auth_client_env):
    """Protected resources succeed when authorized with valid operator token."""
    client, token = auth_client_env
    auth_headers = {"Authorization": f"Bearer {token}"}

    # /api/system
    r_sys = client.get("/api/system", headers=auth_headers)
    assert r_sys.status_code == 200

    # /api/jobs
    r_jobs = client.get("/api/jobs", headers=auth_headers)
    assert r_jobs.status_code == 200

    # /api/clips
    r_clips = client.get("/api/clips", headers=auth_headers)
    assert r_clips.status_code == 200

    # /api/campaigns
    r_camp = client.get("/api/campaigns", headers=auth_headers)
    assert r_camp.status_code == 200

    # /api/caption-styles
    r_cap = client.get("/api/caption-styles", headers=auth_headers)
    assert r_cap.status_code == 200

    # /api/providers/status
    r_prov = client.get("/api/providers/status", headers=auth_headers)
    assert r_prov.status_code == 200


def test_secrets_not_exposed_in_settings_response(auth_client_env):
    """Settings endpoint reports keys_present boolean flags but never exposes raw tokens."""
    client, token = auth_client_env
    auth_headers = {"Authorization": f"Bearer {token}"}

    resp = client.get("/api/settings", headers=auth_headers)
    assert resp.status_code == 200
    raw_text = resp.text

    # Ensure operator token itself is not returned in settings
    assert token not in raw_text
    assert "sk-" not in raw_text
