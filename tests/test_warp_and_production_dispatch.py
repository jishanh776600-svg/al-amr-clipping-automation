import os
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from autoclip.config import GITHUB_PAT_KEY, load as load_settings, set_secret, delete_secret
from autoclip.jobs import dispatcher
from autoclip.pipeline.source_acquisition import warp_checker
from autoclip.pipeline.source_acquisition.registry import SourceAcquisitionRegistry
from autoclip.pipeline.source_acquisition.base import (
    AcquisitionResult,
    JobContext,
    SourceAcquisitionError,
    SourceAcquisitionProvider,
    SourceErrorCode,
)
from autoclip.pipeline.ffmpeg import MediaInfo


def test_warp_checker_resolution(monkeypatch):
    monkeypatch.delenv("AUTOCLIP_PROXY", raising=False)
    monkeypatch.delenv("YTDLP_PROXY", raising=False)
    monkeypatch.delenv("ALL_PROXY", raising=False)

    # 1. Explicit proxy takes precedence
    assert warp_checker.resolve_egress_proxy("socks5://custom:1080") == "socks5://custom:1080"

    # 2. Env proxy
    monkeypatch.setenv("AUTOCLIP_PROXY", "socks5://env:1080")
    assert warp_checker.resolve_egress_proxy() == "socks5://env:1080"

    # 3. Port check fallback when closed
    monkeypatch.delenv("AUTOCLIP_PROXY", raising=False)
    with patch("autoclip.pipeline.source_acquisition.warp_checker.is_port_open", return_value=False):
        assert warp_checker.resolve_egress_proxy() == ""

    # 4. Port check fallback when open
    with patch("autoclip.pipeline.source_acquisition.warp_checker.is_port_open", return_value=True):
        assert warp_checker.resolve_egress_proxy() == warp_checker.DEFAULT_WARP_PROXY


def test_warp_checker_status_probe(monkeypatch):
    # Mock httpx client response
    mock_resp = MagicMock()
    mock_resp.text = "ip=1.2.3.4\nloc=US\nwarp=plus\nvisit_scheme=https\n"

    with patch("httpx.Client") as mock_client:
        mock_instance = MagicMock()
        mock_instance.__enter__.return_value = mock_instance
        mock_instance.get.return_value = mock_resp
        mock_client.return_value = mock_instance

        status = warp_checker.check_warp_status("socks5://127.0.0.1:1080")
        assert status["active"] is True
        assert status["client_ip"] == "1.2.3.4"
        assert status["location"] == "US"
        assert status["warp_status"] == "plus"
        assert status["warp_on"] is True
        assert status["error"] is None


def test_dispatcher_token_and_dispatch_mode(monkeypatch):
    monkeypatch.delenv("GITHUB_PAT", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    delete_secret(GITHUB_PAT_KEY)

    # Initially without token
    monkeypatch.setenv("AUTOCLIP_DISPATCH_MODE", "auto")
    assert dispatcher.get_github_token() is None
    assert dispatcher.is_github_dispatch_enabled() is False

    # Stored in secrets
    set_secret(GITHUB_PAT_KEY, "ghp_stored_secret_token_xyz")
    assert dispatcher.get_github_token() == "ghp_stored_secret_token_xyz"
    assert dispatcher.is_github_dispatch_enabled() is True

    # Environment overrides stored secret
    monkeypatch.setenv("GITHUB_PAT", "ghp_env_override_token_123")
    assert dispatcher.get_github_token() == "ghp_env_override_token_123"

    # Cleanup
    delete_secret(GITHUB_PAT_KEY)


def test_cloud_youtube_guard_rejects_unproxied_cloud_job(monkeypatch, autoclip_home):
    from autoclip.app import create_app
    from autoclip.db import store, models

    monkeypatch.delenv("GITHUB_PAT", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("AUTOCLIP_PROXY", raising=False)
    delete_secret(GITHUB_PAT_KEY)

    # Simulate cloud host
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://al-amr-test.onrender.com")

    with TestClient(create_app()) as client:
        # 1. Quick launch with YouTube URL in cloud without worker token or proxy fails fast
        resp = client.post(
            "/api/jobs/create-autonomous",
            data={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
            headers={"Authorization": "Bearer alamr-op-2024-secure"},
        )
        assert resp.status_code == 400
        assert "Cloud YouTube acquisition requires either GitHub Actions worker dispatch" in resp.json()["detail"]

        # 2. When GITHUB_PAT secret is set, it passes and dispatches
        set_secret(GITHUB_PAT_KEY, "ghp_valid_test_token_456")
        with patch("autoclip.jobs.dispatcher.dispatch_job_to_github") as mock_dispatch:
            resp2 = client.post(
                "/api/jobs/create-autonomous",
                data={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
                headers={"Authorization": "Bearer alamr-op-2024-secure"},
            )
            assert resp2.status_code == 201
            data = resp2.json()
            assert data["dispatch_mode"] == "github"

    delete_secret(GITHUB_PAT_KEY)
