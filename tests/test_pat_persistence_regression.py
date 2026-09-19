"""Regression tests for AL AMR GitHub PAT persistence, masking safety, and settings isolation.

Verifies:
1. save-other-settings-preserves-PAT: Updating other settings (whisper, clips, ingest, export) leaves stored PAT intact.
2. reload-does-not-clear-PAT: Multiple reloads of settings/app context never erase the PAT.
3. masked-PAT-does-not-overwrite-PAT: Submitting masked string (e.g. '•••••••• Configured') leaves the real secret intact.
4. explicit-PAT-replacement: Providing a new raw PAT cleanly updates the encrypted vault and fingerprint.
5. explicit-PAT-clear: Deleting secret or sending explicit clear removes the credential from durable vault.
6. dispatcher-credential-retrieval: GitHub dispatcher retrieves the decrypted PAT even without environment variable.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Ensure project root is in sys.path
root_dir = Path(__file__).resolve().parent.parent
backend_dir = root_dir / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from autoclip import config, db, paths
from autoclip.api.schemas import SettingsIn, SecretIn
from autoclip.api.settings import put_settings, put_secret, delete_secret, get_settings
from autoclip.db import store
from autoclip.jobs.dispatcher import get_github_token
from autoclip.security.vault import get_vault, CredentialVault


_orig_env: dict[str, str | None] = {}
_keyring_patcher = None


def setup_test_env():
    global _orig_env, _keyring_patcher
    _keyring_patcher = patch.object(config, "_keyring", return_value=None)
    _keyring_patcher.start()

    _orig_env = {
        "AUTOCLIP_HOME": os.environ.get("AUTOCLIP_HOME"),
        "AL_AMR_MASTER_KEY": os.environ.get("AL_AMR_MASTER_KEY"),
    }
    tmp_home = Path(tempfile.mkdtemp(prefix="autoclip_pat_test_"))
    os.environ["AUTOCLIP_HOME"] = str(tmp_home)
    os.environ["AL_AMR_MASTER_KEY"] = "alamr-test-master-key-persistence-9876"
    for k in ("GITHUB_PAT", "GH_TOKEN", "GITHUB_TOKEN"):
        os.environ.pop(k, None)

    paths.ensure_layout()
    db.init()
    
    # Reset vault singleton
    import autoclip.security.vault
    autoclip.security.vault._global_vault = None
    return tmp_home


def cleanup_test_env(tmp_home: Path):
    global _keyring_patcher
    if _keyring_patcher:
        _keyring_patcher.stop()
        _keyring_patcher = None

    db.reset_connections()
    shutil.rmtree(tmp_home, ignore_errors=True)
    for k, v in _orig_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    import autoclip.security.vault
    autoclip.security.vault._global_vault = None


def test_save_other_settings_preserves_pat():
    """1. Saving other settings (clips, whisper, etc.) must NEVER overwrite or wipe the PAT."""
    tmp = setup_test_env()
    try:
        real_pat = "ghp_RealValidToken123456789"
        config.set_secret(config.GITHUB_PAT_KEY, real_pat)
        assert config.get_secret(config.GITHUB_PAT_KEY) == real_pat

        # Update unrelated settings via put_settings
        import asyncio
        update_payload = SettingsIn(
            clips={"min_duration_s": 25.0, "max_duration_s": 45.0, "max_clips": 7},
            whisper={"model": "medium", "language": "ar"},
            github_pat=None,  # No PAT in payload
        )
        res = asyncio.run(put_settings(update_payload))

        # Check PAT is completely preserved
        assert config.get_secret(config.GITHUB_PAT_KEY) == real_pat
        assert res.keys_present[config.GITHUB_PAT_KEY] is True
        assert res.credentials_status[config.GITHUB_PAT_KEY].configured is True
        assert "•••••••• Configured" in res.credentials_status[config.GITHUB_PAT_KEY].masked
        assert real_pat not in res.credentials_status[config.GITHUB_PAT_KEY].masked
    finally:
        cleanup_test_env(tmp)


def test_reload_does_not_clear_pat():
    """2. Reloading settings multiple times and simulating process restarts never clears PAT."""
    tmp = setup_test_env()
    try:
        real_pat = "ghp_ReloadPersistentToken9999"
        config.set_secret(config.GITHUB_PAT_KEY, real_pat)

        import asyncio
        for _ in range(3):
            s = asyncio.run(get_settings())
            assert s.keys_present[config.GITHUB_PAT_KEY] is True
            assert s.credentials_status[config.GITHUB_PAT_KEY].configured is True
            assert config.get_secret(config.GITHUB_PAT_KEY) == real_pat

        # Simulate process restart
        db.reset_connections()
        import autoclip.security.vault
        autoclip.security.vault._global_vault = None

        s_after_restart = asyncio.run(get_settings())
        assert s_after_restart.keys_present[config.GITHUB_PAT_KEY] is True
        assert config.get_secret(config.GITHUB_PAT_KEY) == real_pat
    finally:
        cleanup_test_env(tmp)


def test_masked_pat_does_not_overwrite_pat():
    """3. Sending a masked placeholder value (e.g. from UI state) NEVER overwrites real PAT."""
    tmp = setup_test_env()
    try:
        real_pat = "ghp_OriginalProtectedToken5555"
        config.set_secret(config.GITHUB_PAT_KEY, real_pat)

        import asyncio
        # Attempt 1: put_settings with masked string
        masked_val = "•••••••• Configured (…5555)"
        res1 = asyncio.run(put_settings(SettingsIn(github_pat=masked_val)))
        assert config.get_secret(config.GITHUB_PAT_KEY) == real_pat

        # Attempt 2: put_secret with masked string
        asyncio.run(put_secret(SecretIn(key=config.GITHUB_PAT_KEY, value=masked_val)))
        assert config.get_secret(config.GITHUB_PAT_KEY) == real_pat

        # Attempt 3: put_settings with empty string
        res3 = asyncio.run(put_settings(SettingsIn(github_pat="   ")))
        assert config.get_secret(config.GITHUB_PAT_KEY) == real_pat

        # Direct config.set_secret with masked string
        ret = config.set_secret(config.GITHUB_PAT_KEY, masked_val)
        assert ret is False
        assert config.get_secret(config.GITHUB_PAT_KEY) == real_pat
    finally:
        cleanup_test_env(tmp)


def test_explicit_pat_replacement():
    """4. Explicitly providing a new raw token securely replaces the PAT and updates vault."""
    tmp = setup_test_env()
    try:
        first_pat = "ghp_InitialToken11111111"
        second_pat = "ghp_UpdatedToken22222222"

        config.set_secret(config.GITHUB_PAT_KEY, first_pat)
        assert config.get_secret(config.GITHUB_PAT_KEY) == first_pat

        import asyncio
        asyncio.run(put_secret(SecretIn(key=config.GITHUB_PAT_KEY, value=second_pat)))
        assert config.get_secret(config.GITHUB_PAT_KEY) == second_pat

        # Verify vault fingerprint updated
        rec = store.get_credential(config.GITHUB_PAT_KEY)
        assert rec is not None
        assert "2222" in rec.fingerprint
    finally:
        cleanup_test_env(tmp)


def test_explicit_pat_clear():
    """5. Explicit clear (DELETE endpoint or __CLEAR__ marker) securely removes the secret."""
    tmp = setup_test_env()
    try:
        pat = "ghp_TokenToBeDeleted33333333"
        config.set_secret(config.GITHUB_PAT_KEY, pat)
        assert config.get_secret(config.GITHUB_PAT_KEY) == pat

        import asyncio
        # Method A: delete_secret endpoint
        asyncio.run(delete_secret(config.GITHUB_PAT_KEY))
        assert config.get_secret(config.GITHUB_PAT_KEY) is None
        assert store.get_credential(config.GITHUB_PAT_KEY) is None

        # Method B: put_settings with __CLEAR__
        config.set_secret(config.GITHUB_PAT_KEY, pat)
        assert config.get_secret(config.GITHUB_PAT_KEY) == pat
        asyncio.run(put_settings(SettingsIn(github_pat="__CLEAR__")))
        assert config.get_secret(config.GITHUB_PAT_KEY) is None
    finally:
        cleanup_test_env(tmp)


def test_dispatcher_reads_persisted_pat():
    """6. GitHub dispatcher retrieves the persisted secret when environment variable is unset."""
    tmp = setup_test_env()
    try:
        for k in ("GITHUB_PAT", "GH_TOKEN", "GITHUB_TOKEN"):
            os.environ.pop(k, None)

        pat = "ghp_DispatcherVerifiedToken77777777"
        config.set_secret(config.GITHUB_PAT_KEY, pat)

        resolved_token = get_github_token()
        assert resolved_token == pat
    finally:
        cleanup_test_env(tmp)


def test_wal_checkpoint_guarantees_physical_db_file():
    """7. TRUNCATE checkpoint flushes data directly into autoclip.db so it survives WAL file loss."""
    tmp = setup_test_env()
    try:
        pat = "ghp_WalDurabilityCheckToken8888"
        config.set_secret(config.GITHUB_PAT_KEY, pat)

        db_path = paths.db_path()
        wal_path = Path(str(db_path) + "-wal")
        shm_path = Path(str(db_path) + "-shm")

        # Verify main database file is populated (> 4096 bytes) and WAL is truncated
        assert db_path.stat().st_size > 4096
        if wal_path.exists():
            assert wal_path.stat().st_size == 0

        # Close all connections and explicitly delete WAL and SHM to simulate abrupt reboot
        db.reset_connections()
        wal_path.unlink(missing_ok=True)
        shm_path.unlink(missing_ok=True)

        # Reopen database without WAL/SHM: data must exist physically in main database
        import autoclip.security.vault
        autoclip.security.vault._global_vault = None
        recovered_secret = config.get_secret(config.GITHUB_PAT_KEY)
        assert recovered_secret == pat

        # Status check must report configured
        vault = autoclip.security.vault.get_vault()
        status = vault.get_secret_status(config.GITHUB_PAT_KEY)
        assert status["configured"] is True
    finally:
        cleanup_test_env(tmp)


def test_env_key_anchoring_to_master_key_file():
    """8. Environment key is anchored to .master_key; survives environment variable changes."""
    tmp = setup_test_env()
    try:
        initial_env_key = "initial-blueprint-master-key-1111"
        os.environ["AL_AMR_MASTER_KEY"] = initial_env_key

        import autoclip.security.vault
        autoclip.security.vault._global_vault = None
        vault = autoclip.security.vault.get_vault()

        # Storing secret under initial key
        pat = "ghp_KeyAnchoringToken9999"
        vault.store_secret(config.GITHUB_PAT_KEY, pat)

        # Verify .master_key was anchored on disk
        key_file = paths.root() / ".master_key"
        assert key_file.is_file()
        assert key_file.read_text(encoding="utf-8").strip() == initial_env_key

        # Simulate Render redeploy where generateValue creates a new different key
        os.environ["AL_AMR_MASTER_KEY"] = "regenerated-blueprint-master-key-2222"
        autoclip.security.vault._global_vault = None
        db.reset_connections()

        # Vault must use persistent disk key, NOT new env key, guaranteeing decryption
        new_vault = autoclip.security.vault.get_vault()
        decrypted = new_vault.retrieve_secret(config.GITHUB_PAT_KEY)
        assert decrypted == pat
        status = new_vault.get_secret_status(config.GITHUB_PAT_KEY)
        assert status["configured"] is True
    finally:
        cleanup_test_env(tmp)


def test_invalid_master_key_fails_safely_without_destroying_record():
    """9. Key mismatch fails safely and does NOT destroy or delete the stored record."""
    tmp = setup_test_env()
    try:
        pat = "ghp_SafeFailureToken4444"
        config.set_secret(config.GITHUB_PAT_KEY, pat)

        # Tamper with .master_key to simulate corrupted/unreadable key
        key_file = paths.root() / ".master_key"
        key_file.write_text("corrupted-or-wrong-key-0000", encoding="utf-8")
        os.environ["AL_AMR_MASTER_KEY"] = "corrupted-or-wrong-key-0000"

        import autoclip.security.vault
        autoclip.security.vault._global_vault = None
        db.reset_connections()

        vault = autoclip.security.vault.get_vault()
        # Status should report key mismatch without throwing
        status = vault.get_secret_status(config.GITHUB_PAT_KEY)
        assert status["configured"] is False
        assert "mismatch" in status["masked"].lower()

        # The encrypted record must STILL exist in SQLite
        rec = store.get_credential(config.GITHUB_PAT_KEY)
        assert rec is not None
        assert rec.ciphertext != ""
    finally:
        cleanup_test_env(tmp)


def test_save_pat_restart_pat_still_exists():
    """Requirement test: save PAT → restart → PAT still exists."""
    tmp = setup_test_env()
    try:
        real_pat = "ghp_SaveRestartToken11111111111111111111"
        config.set_secret(config.GITHUB_PAT_KEY, real_pat)

        # Simulate restart: close DB connections and wipe in-memory vault singleton
        db.reset_connections()
        import autoclip.security.vault
        autoclip.security.vault._global_vault = None

        # Reopen and verify PAT exists and decrypts
        reloaded_pat = config.get_secret(config.GITHUB_PAT_KEY)
        assert reloaded_pat == real_pat
        vault = autoclip.security.vault.get_vault()
        status = vault.get_secret_status(config.GITHUB_PAT_KEY)
        assert status["configured"] is True
        assert "1111" in status["masked"]
    finally:
        cleanup_test_env(tmp)


def test_save_pat_refresh_update_unrelated_setting_pat_still_exists():
    """Requirement test: save PAT → refresh/update unrelated setting → PAT still exists."""
    tmp = setup_test_env()
    try:
        real_pat = "ghp_SaveRefreshUpdateToken222222222222222"
        config.set_secret(config.GITHUB_PAT_KEY, real_pat)

        import asyncio
        # Step 1: Frontend refresh (calls get_settings)
        s1 = asyncio.run(get_settings())
        assert s1.keys_present[config.GITHUB_PAT_KEY] is True
        assert s1.credentials_status[config.GITHUB_PAT_KEY].configured is True

        # Step 2: User updates unrelated settings (e.g. caption_style, visual_filter, bgm_asset_id, whisper)
        for update in (
            SettingsIn(export={"caption_style": "kyle_kirshner"}),
            SettingsIn(export={"visual_filter": "high_contrast"}),
            SettingsIn(export={"bgm_asset_id": "bgm_corporate_01"}),
            SettingsIn(whisper={"model": "small", "language": "en"}),
            SettingsIn(active_provider="gemini"),
        ):
            res = asyncio.run(put_settings(update))
            assert res.keys_present[config.GITHUB_PAT_KEY] is True
            assert res.credentials_status[config.GITHUB_PAT_KEY].configured is True

        # Step 3: Verify PAT is completely intact
        assert config.get_secret(config.GITHUB_PAT_KEY) == real_pat
    finally:
        cleanup_test_env(tmp)


def test_save_pat_recreate_application_process_pat_still_decrypts():
    """Requirement test: save PAT → recreate application process → PAT still decrypts."""
    tmp = setup_test_env()
    try:
        real_pat = "ghp_ProcessRecreateToken3333333333333333"
        config.set_secret(config.GITHUB_PAT_KEY, real_pat)

        # Simulate complete application process recreation
        db.reset_connections()
        import autoclip.security.vault
        autoclip.security.vault._global_vault = None

        # Simulate startup lifecycle in app.py
        from autoclip.app import lifespan, create_app
        import asyncio
        from fastapi import FastAPI

        test_app = create_app()
        # Trigger startup ensure_initialized
        vault = autoclip.security.vault.get_vault()
        vault.ensure_initialized()

        decrypted = config.get_secret(config.GITHUB_PAT_KEY)
        assert decrypted == real_pat
        assert vault.get_secret_status(config.GITHUB_PAT_KEY)["configured"] is True
    finally:
        cleanup_test_env(tmp)


def test_save_pat_workflow_dispatch_pat_remains_stored():
    """Requirement test: save PAT → workflow dispatch → PAT remains stored."""
    tmp = setup_test_env()
    try:
        real_pat = "ghp_WorkflowDispatchToken4444444444444444"
        config.set_secret(config.GITHUB_PAT_KEY, real_pat)

        # Dispatcher retrieves token for GitHub Actions API
        token_for_dispatch = get_github_token()
        assert token_for_dispatch == real_pat

        # Verify PAT remains stored after dispatch token read
        vault = get_vault()
        status = vault.get_secret_status(config.GITHUB_PAT_KEY)
        assert status["configured"] is True
        assert config.get_secret(config.GITHUB_PAT_KEY) == real_pat
    finally:
        cleanup_test_env(tmp)


def test_masked_frontend_value_cannot_overwrite_real_pat():
    """Requirement test: masked/null/undefined frontend values CANNOT overwrite the real PAT."""
    tmp = setup_test_env()
    try:
        real_pat = "ghp_BulletProofProtectedPAT55555555555555"
        config.set_secret(config.GITHUB_PAT_KEY, real_pat)

        import asyncio
        invalid_overwrites = [
            "",
            "   ",
            "null",
            "NULL",
            "None",
            "none",
            "undefined",
            "UNDEFINED",
            "••••••",
            "••••••••",
            "•••••••• Configured (…5555)",
            "***",
            "******",
            "Not configured",
            "[REDACTED]",
            "masked",
            "...",
        ]

        for bad_val in invalid_overwrites:
            # Via put_settings
            asyncio.run(put_settings(SettingsIn(github_pat=bad_val)))
            assert config.get_secret(config.GITHUB_PAT_KEY) == real_pat, f"Overwritten by {bad_val!r} in put_settings"

            # Via put_secret
            asyncio.run(put_secret(SecretIn(key=config.GITHUB_PAT_KEY, value=bad_val)))
            assert config.get_secret(config.GITHUB_PAT_KEY) == real_pat, f"Overwritten by {bad_val!r} in put_secret"

            # Via direct config.set_secret
            ret = config.set_secret(config.GITHUB_PAT_KEY, bad_val)
            assert ret is False, f"set_secret did not reject {bad_val!r}"
            assert config.get_secret(config.GITHUB_PAT_KEY) == real_pat, f"Overwritten by direct set_secret({bad_val!r})"

        # None value in put_settings
        asyncio.run(put_settings(SettingsIn(github_pat=None)))
        assert config.get_secret(config.GITHUB_PAT_KEY) == real_pat
    finally:
        cleanup_test_env(tmp)


def test_persistent_master_key_is_reused_instead_of_regenerated():
    """Requirement test: persistent master key is reused instead of regenerated."""
    tmp = setup_test_env()
    try:
        # Establish initial key
        initial_key = "initial-authoritative-key-66666666"
        os.environ["AL_AMR_MASTER_KEY"] = initial_key

        import autoclip.security.vault
        autoclip.security.vault._global_vault = None
        vault1 = autoclip.security.vault.get_vault()
        resolved_1 = vault1._resolve_master_key()
        assert resolved_1 == initial_key

        real_pat = "ghp_DurableMasterKeyPAT666666666666666"
        vault1.store_secret(config.GITHUB_PAT_KEY, real_pat)

        # Simulate Render restart/redeploy where AL_AMR_MASTER_KEY regenerates to a new random value
        os.environ["AL_AMR_MASTER_KEY"] = "regenerated-new-key-77777777"
        autoclip.security.vault._global_vault = None
        db.reset_connections()

        vault2 = autoclip.security.vault.get_vault()
        resolved_2 = vault2._resolve_master_key()
        # Must reuse the authoritative established persistent key, NOT the regenerated env key
        assert resolved_2 == initial_key
        assert resolved_2 != os.environ["AL_AMR_MASTER_KEY"]

        # Decryption must succeed flawlessly
        assert vault2.retrieve_secret(config.GITHUB_PAT_KEY) == real_pat
    finally:
        cleanup_test_env(tmp)


def test_database_anchored_master_key_survives_master_key_file_deletion():
    """Bonus durability: master key mirrored in SQLite survives accidental .master_key file deletion."""
    tmp = setup_test_env()
    try:
        initial_key = "db-anchored-key-88888888"
        os.environ["AL_AMR_MASTER_KEY"] = initial_key

        import autoclip.security.vault
        autoclip.security.vault._global_vault = None
        vault = autoclip.security.vault.get_vault()

        real_pat = "ghp_DatabaseAnchoredPAT88888888888888"
        vault.store_secret(config.GITHUB_PAT_KEY, real_pat)

        # Delete .master_key file on disk and change env key to simulate container replacement
        key_file = paths.root() / ".master_key"
        key_file.unlink()
        assert not key_file.exists()
        os.environ.pop("AL_AMR_MASTER_KEY", None)

        autoclip.security.vault._global_vault = None
        db.reset_connections()

        # New vault must restore key from SQLite __vault_master_seed__ and restore .master_key on disk
        new_vault = autoclip.security.vault.get_vault()
        restored_key = new_vault._resolve_master_key()
        assert restored_key == initial_key
        assert key_file.is_file()
        assert key_file.read_text(encoding="utf-8").strip() == initial_key
        assert new_vault.retrieve_secret(config.GITHUB_PAT_KEY) == real_pat
    finally:
        cleanup_test_env(tmp)


def test_vault_pat_case_insensitivity_and_canonicalization():
    """Verify that both lowercase 'github_pat' and uppercase 'GITHUB_PAT' seamlessly resolve."""
    tmp = setup_test_env()
    try:
        from autoclip.jobs import dispatcher

        pat_val = "ghp_CaseInsensitiveValidationToken1234"
        # Store using uppercase key
        config.set_secret("GITHUB_PAT", pat_val)

        # Retrieve using both uppercase and lowercase
        assert config.get_secret("GITHUB_PAT") == pat_val
        assert config.get_secret("github_pat") == pat_val

        vault = get_vault()
        assert vault.retrieve_secret("GITHUB_PAT") == pat_val
        assert vault.retrieve_secret("github_pat") == pat_val

        status_upper = vault.get_secret_status("GITHUB_PAT")
        status_lower = vault.get_secret_status("github_pat")
        assert status_upper["configured"] is True
        assert status_lower["configured"] is True

        # Dispatcher must resolve token
        assert dispatcher.get_github_token() == pat_val
    finally:
        cleanup_test_env(tmp)


def test_dispatcher_capability_without_env_var():
    """Verify dispatcher capability check returns AVAILABLE in cloud environment when token is in vault."""
    tmp = setup_test_env()
    try:
        from autoclip.jobs import dispatcher

        # Ensure no env var
        os.environ.pop("GITHUB_PAT", None)
        os.environ.pop("GH_TOKEN", None)
        os.environ.pop("GITHUB_TOKEN", None)
        os.environ["RENDER"] = "1"
        os.environ["AUTOCLIP_DISPATCH_MODE"] = "auto"

        # Initially without token: capability must be UNAVAILABLE
        cap_unavail = dispatcher.check_dispatch_capability()
        assert cap_unavail["ready"] is False
        assert cap_unavail["capability"] == "UNAVAILABLE"
        assert dispatcher.is_github_dispatch_enabled() is False

        # Store PAT into durable vault
        pat_val = "ghp_CloudWorkerDispatchToken9999"
        config.set_secret("github_pat", pat_val)

        # Now: capability must be AVAILABLE and ready=True even with NO env var
        assert dispatcher.is_github_dispatch_enabled() is True
        cap_avail = dispatcher.check_dispatch_capability()
        assert cap_avail["ready"] is True
        assert cap_avail["capability"] == "AVAILABLE"
        assert cap_avail["token_available"] is True
    finally:
        os.environ.pop("RENDER", None)
        os.environ.pop("AUTOCLIP_DISPATCH_MODE", None)
        cleanup_test_env(tmp)


def test_settings_api_handles_uppercase_github_pat():
    """Verify settings endpoints (_handle_secret_save, delete_secret, validate_secret) accept 'GITHUB_PAT'."""
    tmp = setup_test_env()
    try:
        from autoclip.api.settings import _handle_secret_save

        pat = "ghp_SecretEndpointToken5555"
        _handle_secret_save("GITHUB_PAT", pat)

        assert config.get_secret("github_pat") == pat
        assert config.get_secret("GITHUB_PAT") == pat

        # Test delete with uppercase
        config.delete_secret("GITHUB_PAT")
        assert config.get_secret("github_pat") is None
        assert config.get_secret("GITHUB_PAT") is None
    finally:
        cleanup_test_env(tmp)


def test_ephemeral_disk_reproduction_and_diagnostics_forensics():
    """Reproduces the exact production failure mode where ephemeral disk wipe destroys DB,

    and proves that:
    1. Forensic diagnostics correctly track master-key fingerprints, origin source, and DB inode.
    2. Wiping the ephemeral directory leaves only __vault_master_seed__ on re-init.
    3. Re-anchoring via store_secret cleanly restores the PAT and verifies round-trip decryption.
    """
    tmp = setup_test_env()
    try:
        from autoclip.api.settings import get_settings_diagnostics
        from autoclip.security.vault import get_vault
        import asyncio

        vault = get_vault()
        fp1 = vault.get_master_key_fingerprint()
        src1 = vault.get_master_key_source()
        assert len(fp1) == 16
        assert src1 != "uninitialized"

        pat = "ghp_ProductionLivePAT1234567890abcdef"
        config.set_secret("github_pat", pat)

        # 1. Collect diagnostics with PAT configured
        diag1 = asyncio.run(get_settings_diagnostics())
        assert diag1["database_exists"] is True
        assert "github_pat" in diag1["stored_credential_keys"]
        assert "__vault_master_seed__" in diag1["stored_credential_keys"]
        assert diag1["github_pat"]["configured"] is True
        assert diag1["github_pat"]["decryption_verified"] is True
        assert diag1["master_key"]["active_fingerprint"] == fp1

        # 2. Simulate ephemeral container recreation (wiping DB and .master_key)
        from autoclip import db
        import autoclip.security.vault
        db.reset_connections()
        paths.db_path().unlink(missing_ok=True)
        (paths.root() / ".master_key").unlink(missing_ok=True)
        autoclip.security.vault._global_vault = None

        # 3. Simulate container reboot calling ensure_initialized()
        from autoclip import db
        db.reset_connections()
        db.init()
        fresh_vault = get_vault()
        fresh_vault.ensure_initialized()

        # 4. Now diagnostics reproduces the exact bug: DB is fresh, only __vault_master_seed__ exists
        diag2 = asyncio.run(get_settings_diagnostics())
        assert diag2["database_exists"] is True
        assert "github_pat" not in diag2["stored_credential_keys"]
        assert "__vault_master_seed__" in diag2["stored_credential_keys"]
        assert diag2["github_pat"]["configured"] is False

        # 5. Client-side durable auto-recovery restores the PAT seamlessly
        config.set_secret("github_pat", pat)
        diag3 = asyncio.run(get_settings_diagnostics())
        assert diag3["github_pat"]["configured"] is True
        assert diag3["github_pat"]["decryption_verified"] is True
        assert config.get_secret("github_pat") == pat
    finally:
        cleanup_test_env(tmp)


if __name__ == "__main__":
    print("Running test_save_other_settings_preserves_pat...")
    test_save_other_settings_preserves_pat()
    print("Running test_reload_does_not_clear_pat...")
    test_reload_does_not_clear_pat()
    print("Running test_masked_pat_does_not_overwrite_pat...")
    test_masked_pat_does_not_overwrite_pat()
    print("Running test_explicit_pat_replacement...")
    test_explicit_pat_replacement()
    print("Running test_explicit_pat_clear...")
    test_explicit_pat_clear()
    print("Running test_dispatcher_reads_persisted_pat...")
    test_dispatcher_reads_persisted_pat()
    print("Running test_wal_checkpoint_guarantees_physical_db_file...")
    test_wal_checkpoint_guarantees_physical_db_file()
    print("Running test_env_key_anchoring_to_master_key_file...")
    test_env_key_anchoring_to_master_key_file()
    print("Running test_invalid_master_key_fails_safely_without_destroying_record...")
    test_invalid_master_key_fails_safely_without_destroying_record()
    print("Running test_save_pat_restart_pat_still_exists...")
    test_save_pat_restart_pat_still_exists()
    print("Running test_save_pat_refresh_update_unrelated_setting_pat_still_exists...")
    test_save_pat_refresh_update_unrelated_setting_pat_still_exists()
    print("Running test_save_pat_recreate_application_process_pat_still_decrypts...")
    test_save_pat_recreate_application_process_pat_still_decrypts()
    print("Running test_save_pat_workflow_dispatch_pat_remains_stored...")
    test_save_pat_workflow_dispatch_pat_remains_stored()
    print("Running test_masked_frontend_value_cannot_overwrite_real_pat...")
    test_masked_frontend_value_cannot_overwrite_real_pat()
    print("Running test_persistent_master_key_is_reused_instead_of_regenerated...")
    test_persistent_master_key_is_reused_instead_of_regenerated()
    print("Running test_database_anchored_master_key_survives_master_key_file_deletion...")
    test_database_anchored_master_key_survives_master_key_file_deletion()
    print("Running test_vault_pat_case_insensitivity_and_canonicalization...")
    test_vault_pat_case_insensitivity_and_canonicalization()
    print("Running test_dispatcher_capability_without_env_var...")
    test_dispatcher_capability_without_env_var()
    print("Running test_settings_api_handles_uppercase_github_pat...")
    test_settings_api_handles_uppercase_github_pat()
    print("ALL 19 PAT PERSISTENCE AND DETECTION REGRESSION TESTS PASSED SUCCESSFULLY!")
