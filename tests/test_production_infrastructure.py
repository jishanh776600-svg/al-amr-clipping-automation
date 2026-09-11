"""Tests for AL AMR Step 4: Production Infrastructure & Remote Deployment Foundation."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from autoclip.app import create_app
from autoclip.config import load as load_settings
from autoclip.health import check_liveness, check_readiness
from autoclip import paths


def test_health_liveness():
    res = check_liveness()
    assert res["status"] == "ok"
    assert res["service"] == "autoclip"
    assert "version" in res


def test_health_readiness(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOCLIP_HOME", str(tmp_path))
    from autoclip import db
    paths.ensure_layout()
    db.init()

    ready, res = check_readiness()
    assert ready is True
    assert res["status"] == "ok"
    assert res["checks"]["database"] == "ok"
    assert res["checks"]["storage"] == "ok"
    assert res["checks"]["ffmpeg"] == "ok"
    assert "worker" in res["checks"]


def test_health_readiness_degraded_on_storage_error(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOCLIP_HOME", str(tmp_path))
    paths.ensure_layout()

    # Make work dir read-only or mock write failure
    with patch("pathlib.Path.write_text", side_effect=PermissionError("Mock write denied")):
        ready, res = check_readiness()
        assert ready is False
        assert res["status"] == "degraded"
        assert res["checks"]["storage"] == "write_failed"


def test_app_health_endpoints(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOCLIP_HOME", str(tmp_path))
    monkeypatch.setenv("AUTOCLIP_NO_WORKER", "1")
    app = create_app()

    with TestClient(app) as client:
        # Test root /health
        res_h = client.get("/health")
        assert res_h.status_code == 200
        assert res_h.json()["status"] == "ok"

        # Test root /ready
        res_r = client.get("/ready")
        assert res_r.status_code == 200
        assert res_r.json()["status"] == "ok"

        # Test /api/health and /api/ready
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/ready").status_code == 200


def test_auth_permissive_when_no_key_set(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOCLIP_HOME", str(tmp_path))
    monkeypatch.setenv("AUTOCLIP_NO_WORKER", "1")
    monkeypatch.delenv("AUTOCLIP_API_KEY", raising=False)
    monkeypatch.delenv("OPERATOR_TOKEN", raising=False)

    app = create_app()
    with TestClient(app) as client:
        # Should succeed without any credentials
        resp = client.get("/api/jobs")
        assert resp.status_code == 200


def test_auth_enforced_when_key_set(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOCLIP_HOME", str(tmp_path))
    monkeypatch.setenv("AUTOCLIP_NO_WORKER", "1")
    monkeypatch.setenv("AUTOCLIP_API_KEY", "secret-production-token-12345")

    app = create_app()
    with TestClient(app) as client:
        # Public health endpoints remain accessible without auth
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 200
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/ready").status_code == 200

        # Protected API route fails without auth
        resp_unauth = client.get("/api/jobs")
        assert resp_unauth.status_code == 401
        assert "Unauthorized" in resp_unauth.json()["detail"]

        # Protected API route fails with bad token
        resp_bad = client.get("/api/jobs", headers={"Authorization": "Bearer wrong-key"})
        assert resp_bad.status_code == 401

        # Protected API route succeeds with Bearer token
        resp_bearer = client.get("/api/jobs", headers={"Authorization": "Bearer secret-production-token-12345"})
        assert resp_bearer.status_code == 200

        # Protected API route succeeds with X-API-Key header
        resp_header = client.get("/api/jobs", headers={"X-API-Key": "secret-production-token-12345"})
        assert resp_header.status_code == 200


def test_operator_token_alias(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOCLIP_HOME", str(tmp_path))
    monkeypatch.setenv("AUTOCLIP_NO_WORKER", "1")
    monkeypatch.delenv("AUTOCLIP_API_KEY", raising=False)
    monkeypatch.setenv("OPERATOR_TOKEN", "alamr-op-token-777")

    app = create_app()
    with TestClient(app) as client:
        resp_unauth = client.get("/api/jobs")
        assert resp_unauth.status_code == 401

        resp_auth = client.get("/api/jobs", headers={"Authorization": "Bearer alamr-op-token-777"})
        assert resp_auth.status_code == 200


def test_whisper_model_caching_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOCLIP_HOME", str(tmp_path))
    whisper_dir = paths.models_dir() / "whisper"
    assert str(tmp_path) in str(whisper_dir)


def test_config_env_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOCLIP_HOME", str(tmp_path))
    monkeypatch.setenv("AUTOCLIP_ACTIVE_PROVIDER", "ollama")
    monkeypatch.setenv("AUTOCLIP_OLLAMA_BASE_URL", "http://remote-ollama:11434")
    monkeypatch.setenv("AUTOCLIP_OLLAMA_MODEL", "qwen2.5:14b")
    monkeypatch.setenv("AUTOCLIP_COOKIES_FILE", "/etc/secrets/cookies.txt")

    settings = load_settings()
    assert settings.active_provider == "ollama"
    assert settings.ingest.cookies_file == "/etc/secrets/cookies.txt"
    provider_settings = settings.provider("ollama")
    assert provider_settings.base_url == "http://remote-ollama:11434"
    assert provider_settings.model == "qwen2.5:14b"
