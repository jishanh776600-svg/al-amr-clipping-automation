"""Tests for durable encrypted credential persistence across application restarts/redeployments."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from autoclip import config, db, paths
from autoclip.app import app
from autoclip.db import store
from autoclip.jobs.dispatcher import get_github_token
from autoclip.security.vault import CredentialVault, get_vault


@pytest.fixture
def client(autoclip_home: Path, initialised_db: int) -> TestClient:
    return TestClient(app)


def test_save_and_retrieve_pat_internally(monkeypatch: pytest.MonkeyPatch, autoclip_home: Path) -> None:
    """Requirement 1 & 2: Save PAT and retrieve internally."""
    monkeypatch.delenv("GITHUB_PAT", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("AL_AMR_MASTER_KEY", "test-master-key-alamr-production-1234")

    raw_token = "ghp_TestToken1234567890abcdef"
    saved = config.set_secret("github_pat", raw_token)
    assert saved is True

    retrieved = config.get_secret("github_pat")
    assert retrieved == raw_token


def test_stored_value_is_encrypted_at_rest(monkeypatch: pytest.MonkeyPatch, autoclip_home: Path) -> None:
    """Requirement 3: Verify the stored value in SQLite is encrypted and not plaintext."""
    monkeypatch.delenv("GITHUB_PAT", raising=False)
    monkeypatch.setenv("AL_AMR_MASTER_KEY", "test-master-key-alamr-production-1234")

    raw_token = "ghp_HighlySensitivePlaintextSecret987654321"
    config.set_secret("github_pat", raw_token)

    # Query the SQLite database directly
    with db.connection() as conn:
        row = conn.execute(
            "SELECT key, ciphertext, fingerprint FROM app_credentials WHERE key = 'github_pat'"
        ).fetchone()

    assert row is not None
    assert row["key"] == "github_pat"
    # The raw token must NOT exist in the ciphertext or fingerprint
    assert raw_token not in row["ciphertext"]
    # Ciphertext must be Fernet AES-256 token (begins with standard Fernet token header)
    assert row["ciphertext"].startswith("gAAAAA")
    # Fingerprint must be properly masked
    assert "4321" in row["fingerprint"]
    assert "HighlySensitive" not in row["fingerprint"]
    assert "•••••••• Configured" in row["fingerprint"]


def test_api_responses_never_contain_raw_pat(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement 4 & 5: Verify API responses never leak raw PAT and frontend receives masked status."""
    monkeypatch.delenv("GITHUB_PAT", raising=False)
    monkeypatch.setenv("AL_AMR_MASTER_KEY", "test-master-key-alamr-production-1234")

    raw_token = "ghp_SecretThatMustNeverBeInJsonResponse1234"
    config.set_secret("github_pat", raw_token)

    # 1. GET /api/settings (with operator auth header)
    headers = {"Authorization": "Bearer test-master-key-alamr-production-1234"}
    resp = client.get("/api/settings", headers=headers)
    assert resp.status_code == 200
    assert raw_token not in resp.text

    body = resp.json()
    assert body["keys_present"]["github_pat"] is True
    cred_status = body["credentials_status"]["github_pat"]
    assert cred_status["configured"] is True
    assert raw_token not in cred_status["masked"]
    assert "•••••••• Configured" in cred_status["masked"]


def test_github_dispatch_retrieves_persisted_pat(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement 6: Verify GitHub Actions dispatcher retrieves the persisted PAT."""
    monkeypatch.delenv("GITHUB_PAT", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("AL_AMR_MASTER_KEY", "test-master-key-alamr-production-1234")

    raw_token = "ghp_DispatcherResolvedToken123456"
    config.set_secret("github_pat", raw_token)

    # get_github_token() must fetch and decrypt the stored PAT
    resolved = get_github_token()
    assert resolved == raw_token


def test_credential_survives_application_restart_semantics(monkeypatch: pytest.MonkeyPatch, autoclip_home: Path) -> None:
    """Requirement 7: Verify credential remains available after full process restart simulation."""
    master_key = "durable-render-master-key-persisted-in-env"
    monkeypatch.delenv("GITHUB_PAT", raising=False)
    monkeypatch.setenv("AL_AMR_MASTER_KEY", master_key)

    raw_token = "ghp_DurableAcrossRenderContainerRestarts999"
    config.set_secret("github_pat", raw_token)

    # --- SIMULATE PROCESS RESTART / CONTAINER REBOOT ---
    # 1. Terminate open SQLite connections
    db.reset_connections()
    # 2. Reset global in-memory vault singletons
    import autoclip.security.vault
    autoclip.security.vault._global_vault = None
    # 3. Simulate new container starting with the same persistent /data volume and env var
    assert (autoclip_home / "autoclip.db").is_file()

    # Verify that in a fresh execution context, the token is cleanly decrypted
    restarted_vault = get_vault()
    recovered = config.get_secret("github_pat")
    assert recovered == raw_token
    assert restarted_vault.retrieve_secret("github_pat") == raw_token


def test_replacing_pat_updates_vault(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement 8: Verify replacing the PAT updates the store cleanly."""
    monkeypatch.delenv("GITHUB_PAT", raising=False)
    monkeypatch.setenv("AL_AMR_MASTER_KEY", "test-master-key-alamr-production-1234")

    config.set_secret("github_pat", "ghp_FirstToken11111111")
    assert config.get_secret("github_pat") == "ghp_FirstToken11111111"

    config.set_secret("github_pat", "ghp_SecondToken22222222")
    assert config.get_secret("github_pat") == "ghp_SecondToken22222222"

    rec = store.get_credential("github_pat")
    assert rec is not None
    assert "2222" in rec.fingerprint


def test_deleting_pat_removes_from_vault(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement 9: Verify deleting/removing the PAT purges it from SQLite vault."""
    monkeypatch.delenv("GITHUB_PAT", raising=False)
    monkeypatch.setenv("AL_AMR_MASTER_KEY", "test-master-key-alamr-production-1234")

    config.set_secret("github_pat", "ghp_TokenToBeDeleted5555")
    assert config.get_secret("github_pat") == "ghp_TokenToBeDeleted5555"

    config.delete_secret("github_pat")
    assert config.get_secret("github_pat") is None
    assert store.get_credential("github_pat") is None


def test_no_raw_pat_appears_in_logs(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement 10: Verify no raw PAT appears in application logs."""
    monkeypatch.delenv("GITHUB_PAT", raising=False)
    monkeypatch.setenv("AL_AMR_MASTER_KEY", "test-master-key-alamr-production-1234")

    secret_pattern = "ghp_UltraSecretTokenNeverToAppearInAnyLogString"
    with caplog.at_level(logging.DEBUG):
        config.set_secret("github_pat", secret_pattern)
        _ = config.get_secret("github_pat")
        _ = get_github_token()
        config.delete_secret("github_pat")

    assert secret_pattern not in caplog.text


def test_migration_from_existing_keyring(monkeypatch: pytest.MonkeyPatch, fake_keyring) -> None:
    """Requirement 11: Verify automatic migration from existing OS keyring to durable SQLite vault."""
    monkeypatch.delenv("GITHUB_PAT", raising=False)
    monkeypatch.setenv("AL_AMR_MASTER_KEY", "test-master-key-alamr-production-1234")

    legacy_token = "ghp_ExistingTokenInOSKeyringToMigrate888"
    # Seed OS keyring directly
    fake_keyring.set_password("autoclip", "github_pat", legacy_token)

    # Assert SQLite vault does NOT have the credential yet
    assert store.get_credential("github_pat") is None

    # First get_secret triggers seamless migration
    retrieved = config.get_secret("github_pat")
    assert retrieved == legacy_token

    # Assert credential is now securely persisted in SQLite
    rec = store.get_credential("github_pat")
    assert rec is not None
    assert rec.ciphertext.startswith("gAAAAA")

    # Now destroy the keyring entirely to simulate headless Render environment
    monkeypatch.setattr(config, "_keyring", lambda: None)

    # Retrieval must continue to succeed from the durable SQLite vault
    vault_retrieved = config.get_secret("github_pat")
    assert vault_retrieved == legacy_token


def test_validate_pat_endpoint_live_mock(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test the POST /api/settings/secrets/github_pat/validate endpoint."""
    monkeypatch.delenv("GITHUB_PAT", raising=False)
    monkeypatch.setenv("AL_AMR_MASTER_KEY", "test-master-key-alamr-production-1234")

    config.set_secret("github_pat", "ghp_MockValidationToken1234")

    headers_auth = {"Authorization": "Bearer test-master-key-alamr-production-1234"}

    from unittest.mock import MagicMock

    # Mock successful GitHub API response
    with patch("httpx.AsyncClient.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"login": "alamr-operator"}
        mock_resp.headers = {"x-oauth-scopes": "repo, workflow, actions:write"}
        mock_get.return_value = mock_resp

        resp = client.post("/api/settings/secrets/github_pat/validate", headers=headers_auth)
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert data["username"] == "alamr-operator"
        assert "actions:write" in data["scopes"]
        assert "Connected successfully to GitHub as @alamr-operator" in data["message"]

    # Mock invalid credentials (HTTP 401)
    with patch("httpx.AsyncClient.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Bad credentials"
        mock_get.return_value = mock_resp

        resp = client.post("/api/settings/secrets/github_pat/validate", headers=headers_auth)
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is False
        assert "Bad credentials" in data["message"]
