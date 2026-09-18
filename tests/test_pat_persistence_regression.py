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


def setup_test_env():
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
    db.reset_connections()
    shutil.rmtree(tmp_home, ignore_errors=True)


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
    print("ALL 9 PAT PERSISTENCE REGRESSION TESTS PASSED SUCCESSFULLY!")
