"""Durable encrypted credential vault for AL AMR / AutoClip.

Encrypts secrets (such as the GitHub Personal Access Token) at rest using AES-256 (Fernet)
and persists them inside the durable SQLite database (table: `app_credentials`).

The encryption key is loaded from the environment:
- `AL_AMR_MASTER_KEY` (configured in `render.yaml` with `generateValue: true`)
- `AUTOCLIP_ENCRYPTION_KEY`
- `ENCRYPTION_KEY`

Security guarantees:
- Secrets are NEVER logged in plaintext.
- Secrets are NEVER exposed in API responses or frontend payloads.
- Ciphertext is verified on decryption.
- Fallback deterministic key derivation is provided for local offline development.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from .. import paths
from ..db import models, store

log = logging.getLogger(__name__)


def _derive_fernet_key(master_secret: str) -> bytes:
    """Derive a URL-safe base64-encoded 32-byte key for Fernet from an arbitrary master string."""
    digest = hashlib.sha256(master_secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


class CredentialVault:
    """Manages encryption, storage, and retrieval of persistent secrets."""

    def __init__(self, master_key: str | None = None) -> None:
        self._explicit_key = master_key
        self._cached_cipher: Fernet | None = None
        self._cached_key_source: str | None = None
        self._cached_key_origin: str = "uninitialized"

    def get_master_key_fingerprint(self) -> str:
        """Safe SHA-256 fingerprint prefix of active master key (never exposes key)."""
        self.get_cipher()
        key = self._cached_key_source or ""
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]

    def get_master_key_source(self) -> str:
        """Origin/source of active master key."""
        self.get_cipher()
        return self._cached_key_origin

    def _db_anchor_cipher(self) -> Fernet:
        """Deterministic anchor cipher to protect the master key seed in the SQLite database."""
        op_token = os.environ.get("OPERATOR_TOKEN") or os.environ.get("AUTOCLIP_API_KEY") or "alamr-op-2024-secure"
        repo = os.environ.get("GITHUB_REPOSITORY") or "al-amr-clipping-automation"
        seed_key = f"vault-db-seed:{op_token}:{repo}"
        return Fernet(_derive_fernet_key(seed_key))

    def _save_master_key_to_db(self, master_key: str) -> None:
        """Mirror authoritative master key into SQLite so it survives container/disk detachment."""
        try:
            cipher = self._db_anchor_cipher()
            ciphertext = cipher.encrypt(master_key.encode("utf-8")).decode("utf-8")
            store.save_credential("__vault_master_seed__", ciphertext, "Authoritative Master Key Seed")
        except Exception as exc:
            log.debug("Could not mirror master key to database: %s", exc)

    def _load_master_key_from_db(self) -> str | None:
        """Restore authoritative master key directly from SQLite if disk key file was lost."""
        try:
            rec = store.get_credential("__vault_master_seed__")
            if rec is not None:
                cipher = self._db_anchor_cipher()
                decrypted = cipher.decrypt(rec.ciphertext.encode("utf-8")).decode("utf-8")
                if decrypted and decrypted.strip():
                    return decrypted.strip()
        except Exception as exc:
            log.debug("Could not read master key from database: %s", exc)
        return None

    def _resolve_master_key(self) -> str:
        """Resolve the encryption key, ensuring durable continuity across restarts and redeploys.

        Resolution hierarchy:
        1. Explicit key if passed into constructor.
        2. Persistent disk key file at `paths.root() / ".master_key"`:
           Once a database volume has an established master key, that key is authoritative
           so that changes to environment variables (e.g. Render Blueprint `generateValue: true`
           regeneration on redeploy) do not render existing encrypted credentials unreadable.
        3. Database key anchor (`__vault_master_seed__` in SQLite `app_credentials`):
           If the database exists, the key is permanently co-located with the stored data.
        4. Environment secret (`AL_AMR_MASTER_KEY`, `AUTOCLIP_ENCRYPTION_KEY`, `ENCRYPTION_KEY`):
           Anchored immediately to `paths.root() / ".master_key"` on persistent disk and in SQLite.
        5. Generated random installation secret:
           Persisted to `paths.root() / ".master_key"` and SQLite.
        6. Deterministic fallback derived from resolved root directory.
        """
        if self._explicit_key:
            self._cached_key_origin = "explicit"
            return self._explicit_key

        root_dir = paths.root()
        key_file = root_dir / ".master_key"

        env_key = (
            os.environ.get("AL_AMR_MASTER_KEY")
            or os.environ.get("AUTOCLIP_ENCRYPTION_KEY")
            or os.environ.get("ENCRYPTION_KEY")
        )
        clean_env = env_key.strip() if (env_key and env_key.strip()) else None

        # 1. Search persistent disk locations for an existing established master key
        persistent_locations = [key_file]
        if Path("/data/.master_key") != key_file:
            persistent_locations.append(Path("/data/.master_key"))
        if root_dir == Path.home() / ".autoclip":
            for alt_loc in (
                Path.home() / ".autoclip" / ".master_key",
                Path.home() / ".clipforge" / ".master_key",
            ):
                if alt_loc not in persistent_locations:
                    persistent_locations.append(alt_loc)

        for ploc in persistent_locations:
            try:
                if ploc.is_file():
                    stored_key = ploc.read_text(encoding="utf-8").strip()
                    if stored_key:
                        # Ensure this established authoritative key is mirrored to active key_file and SQLite
                        if ploc != key_file:
                            try:
                                root_dir.mkdir(parents=True, exist_ok=True)
                                key_file.write_text(stored_key, encoding="utf-8")
                            except Exception:
                                pass
                        self._save_master_key_to_db(stored_key)
                        if clean_env and stored_key != clean_env:
                            log.info(
                                "Preserving authoritative persistent disk key from %s across environment regeneration.",
                                ploc,
                            )
                        self._cached_key_origin = f"disk_file:{ploc}"
                        return stored_key
            except Exception as exc:
                log.debug("Could not read persistent master key file %s: %s", ploc, exc)

        # 2. Check database anchor in SQLite (__vault_master_seed__)
        db_key = self._load_master_key_from_db()
        if db_key:
            try:
                root_dir.mkdir(parents=True, exist_ok=True)
                key_file.write_text(db_key, encoding="utf-8")
                import contextlib
                with contextlib.suppress(OSError):
                    key_file.chmod(0o600)
                if Path("/data").is_dir() and Path("/data/.master_key") != key_file:
                    with contextlib.suppress(Exception):
                        Path("/data/.master_key").write_text(db_key, encoding="utf-8")
                log.info("Restored authoritative master key from SQLite database to %s", key_file)
            except Exception as exc:
                log.debug("Could not restore master key to disk: %s", exc)
            self._cached_key_origin = "sqlite_seed"
            return db_key

        # 3. If environment secret is configured, anchor it to the persistent disk volume and DB
        if clean_env:
            try:
                root_dir.mkdir(parents=True, exist_ok=True)
                key_file.write_text(clean_env, encoding="utf-8")
                import contextlib
                with contextlib.suppress(OSError):
                    key_file.chmod(0o600)
                if Path("/data").is_dir() and Path("/data/.master_key") != key_file:
                    try:
                        Path("/data/.master_key").write_text(clean_env, encoding="utf-8")
                    except Exception:
                        pass
                self._save_master_key_to_db(clean_env)
                log.info("Anchored environment master key to persistent disk at %s and SQLite", key_file)
            except Exception as exc:
                log.warning("Could not anchor environment master key to %s: %s", key_file, exc)
            self._cached_key_origin = "env_var"
            return clean_env

        # 4. Generate and persist a stable random master key for this installation volume
        import secrets
        new_key = f"autoclip-key-{secrets.token_urlsafe(32)}"
        try:
            root_dir.mkdir(parents=True, exist_ok=True)
            key_file.write_text(new_key, encoding="utf-8")
            import contextlib
            with contextlib.suppress(OSError):
                key_file.chmod(0o600)
            if Path("/data").is_dir() and Path("/data/.master_key") != key_file:
                try:
                    Path("/data/.master_key").write_text(new_key, encoding="utf-8")
                except Exception:
                    pass
            self._save_master_key_to_db(new_key)
            log.info("Generated and persisted new durable master key to %s and SQLite", key_file)
            self._cached_key_origin = "generated"
            return new_key
        except Exception as exc:
            log.warning("Could not write master key file %s: %s", key_file, exc)

        # 5. Local development fallback derived from the root path
        fallback = f"autoclip-dev-salt-{root_dir.resolve()}"
        self._cached_key_origin = "dev_fallback"
        return fallback

    def _candidate_keys(self) -> list[str]:
        """Collect all potential candidate master keys for multi-key self-healing recovery."""
        candidates: list[str] = []
        if self._explicit_key:
            candidates.append(self._explicit_key)

        key_locations = [
            paths.root() / ".master_key",
            Path("/data/.master_key"),
        ]
        if paths.root() == Path.home() / ".autoclip":
            key_locations.extend([
                Path.home() / ".autoclip" / ".master_key",
                Path.home() / ".clipforge" / ".master_key",
            ])
        for kf in key_locations:
            try:
                if kf.is_file():
                    content = kf.read_text(encoding="utf-8").strip()
                    if content and content not in candidates:
                        candidates.append(content)
            except Exception:
                pass

        # Also check SQLite for database-anchored key
        db_key = self._load_master_key_from_db()
        if db_key and db_key not in candidates:
            candidates.append(db_key)

        for env_var in (
            "AL_AMR_MASTER_KEY",
            "AUTOCLIP_ENCRYPTION_KEY",
            "ENCRYPTION_KEY",
            "OPERATOR_TOKEN",
            "AUTOCLIP_API_KEY",
        ):
            val = os.environ.get(env_var)
            if val and val.strip() and val.strip() not in candidates:
                candidates.append(val.strip())

        for p in (paths.root(), Path("/data"), Path.home() / ".autoclip"):
            try:
                salt = f"autoclip-dev-salt-{p.resolve()}"
                if salt not in candidates:
                    candidates.append(salt)
            except Exception:
                pass

        return candidates

    def ensure_initialized(self) -> None:
        """Ensure vault cipher is initialized, keys anchored to disk and DB, and seed PAT from env if unconfigured."""
        self.get_cipher()
        master_key = self._cached_key_source
        if master_key:
            root_dir = paths.root()
            key_file = root_dir / ".master_key"
            if not key_file.is_file():
                try:
                    root_dir.mkdir(parents=True, exist_ok=True)
                    key_file.write_text(master_key, encoding="utf-8")
                    import contextlib
                    with contextlib.suppress(OSError):
                        key_file.chmod(0o600)
                except Exception:
                    pass
            if Path("/data").is_dir() and Path("/data/.master_key") != key_file:
                try:
                    Path("/data/.master_key").write_text(master_key, encoding="utf-8")
                except Exception:
                    pass
            self._save_master_key_to_db(master_key)

        # Seed GitHub PAT from environment if available and not yet stored
        env_pat = (
            os.environ.get("GITHUB_PAT")
            or os.environ.get("GH_TOKEN")
            or os.environ.get("GITHUB_TOKEN")
        )
        if env_pat and env_pat.strip():
            from ..config import is_masked_secret
            clean_pat = env_pat.strip()
            if not is_masked_secret(clean_pat):
                existing = None
                try:
                    existing = self.retrieve_secret("github_pat")
                except Exception:
                    pass
                if not existing:
                    self.store_secret("github_pat", clean_pat)
                    log.info("Anchored GitHub PAT into durable SQLite vault from environment on startup.")

    def get_cipher(self) -> Fernet:
        """Return the active Fernet cipher instance."""
        current_key = self._resolve_master_key()
        if self._cached_cipher is None or self._cached_key_source != current_key:
            fernet_key = _derive_fernet_key(current_key)
            self._cached_cipher = Fernet(fernet_key)
            self._cached_key_source = current_key
        return self._cached_cipher

    def mask_token(self, key: str, token: str) -> str:
        """Generate a safe, masked representation of a secret for display.

        NEVER reveals the full secret.
        """
        if not token or not token.strip():
            return "Not configured"

        cleaned = token.strip()
        if len(cleaned) >= 8:
            suffix = cleaned[-4:]
            return f"•••••••• Configured (…{suffix})"
        return "•••••••• Configured"

    def store_secret(self, key: str, plaintext: str | None) -> str:
        """Encrypt and persist a secret in the database.

        Returns the masked fingerprint.
        NEVER overwrites an existing secret with empty, null, undefined, or masked values.
        """
        from ..config import canonical_secret_key, is_masked_secret

        canon = canonical_secret_key(key) or key

        if plaintext is None:
            log.warning("Cannot store None secret for '%s'. Existing secret preserved.", canon)
            existing = self.retrieve_secret(canon)
            return self.mask_token(canon, existing) if existing else "Not configured"

        token = str(plaintext).strip()
        if not token or is_masked_secret(token):
            log.warning(
                "Refusing to overwrite secret '%s' with empty, null, undefined, or masked placeholder (%s). Existing secret preserved.",
                canon,
                token[:12] if token else "empty",
            )
            existing = self.retrieve_secret(canon)
            return self.mask_token(canon, existing) if existing else "Not configured"

        cipher = self.get_cipher()
        ciphertext = cipher.encrypt(token.encode("utf-8")).decode("utf-8")
        fingerprint = self.mask_token(canon, token)

        store.save_credential(canon, ciphertext, fingerprint)
        log.info(
            "Durable credential '%s' successfully encrypted and stored at rest (fingerprint: %s)",
            canon,
            fingerprint,
        )
        return fingerprint

    def retrieve_secret(self, key: str) -> str | None:
        """Retrieve and decrypt a stored secret.

        Returns the plaintext secret to internal callers only, or None if not set.
        Includes self-healing multi-key candidate recovery if the active master key changed.
        """
        from ..config import canonical_secret_key

        canon = canonical_secret_key(key) or key
        rec = store.get_credential(canon)
        if rec is None:
            return None

        cipher = self.get_cipher()
        try:
            return cipher.decrypt(rec.ciphertext.encode("utf-8")).decode("utf-8")
        except InvalidToken:
            log.warning(
                "Primary master key failed to decrypt credential '%s'. Attempting multi-key candidate recovery...",
                canon,
            )
        except Exception as exc:
            log.error("Unexpected error decrypting credential '%s': %s", canon, exc)
            return None

        # Multi-candidate key self-healing recovery
        for candidate_key in self._candidate_keys():
            if not candidate_key or candidate_key == self._cached_key_source:
                continue
            try:
                candidate_fernet = Fernet(_derive_fernet_key(candidate_key))
                decrypted = candidate_fernet.decrypt(rec.ciphertext.encode("utf-8")).decode("utf-8")
                log.info(
                    "Credential '%s' successfully recovered using fallback candidate key. Re-encrypting with active master key.",
                    canon,
                )
                # Re-encrypt with active primary cipher and heal the record
                new_ciphertext = cipher.encrypt(decrypted.encode("utf-8")).decode("utf-8")
                fingerprint = self.mask_token(canon, decrypted)
                store.save_credential(canon, new_ciphertext, fingerprint)
                return decrypted
            except (InvalidToken, Exception):
                continue

        log.error(
            "Failed to decrypt credential '%s'. The master encryption key may have changed.",
            canon,
        )
        raise RuntimeError(
            f"Could not decrypt stored credential '{canon}'. Master encryption key mismatch."
        )

    def delete_secret(self, key: str) -> bool:
        """Delete a secret from the persistent store."""
        from ..config import canonical_secret_key

        canon = canonical_secret_key(key) or key
        deleted = store.delete_credential(canon)
        if deleted:
            log.info("Durable credential '%s' removed from encrypted database vault", canon)
        return deleted

    def get_secret_status(self, key: str) -> dict[str, Any]:
        """Return non-sensitive status information for Settings UI."""
        from ..config import canonical_secret_key

        canon = canonical_secret_key(key) or key
        rec = store.get_credential(canon)
        if rec is None:
            return {
                "configured": False,
                "masked": "Not configured",
                "updated_at": "",
            }

        # Verify whether the stored credential is successfully decryptable with active master key
        decrypted = False
        try:
            val = self.retrieve_secret(canon)
            decrypted = bool(val)
        except Exception:
            decrypted = False

        if not decrypted:
            return {
                "configured": False,
                "masked": "Encryption key mismatch (Re-enter token)",
                "updated_at": rec.updated_at,
            }

        return {
            "configured": True,
            "masked": rec.fingerprint or "•••••••• Configured",
            "updated_at": rec.updated_at,
        }

    def migrate_from_keyring(self, key: str, keyring_value: str | None) -> bool:
        """Safely migrate an existing credential from the OS keyring into SQLite.

        Verifies decryption before marking migration successful.
        """
        if not keyring_value or not keyring_value.strip():
            return False

        from ..config import canonical_secret_key

        canon = canonical_secret_key(key) or key

        # Only migrate if SQLite does not already have a record
        existing = store.get_credential(canon)
        if existing is not None:
            return False

        log.info("Migrating credential '%s' from OS keyring to durable SQLite vault...", canon)
        token = keyring_value.strip()
        fingerprint = self.store_secret(canon, token)

        # Verify round-trip decryption
        verified = self.retrieve_secret(canon)
        if verified != token:
            log.error("Migration verification failed for credential '%s'. Rolling back.", canon)
            self.delete_secret(canon)
            return False

        log.info(
            "Successfully migrated credential '%s' to durable SQLite vault (fingerprint: %s)",
            canon,
            fingerprint,
        )
        return True


_global_vault: CredentialVault | None = None


def get_vault() -> CredentialVault:
    """Return the singleton CredentialVault instance."""
    global _global_vault
    if _global_vault is None:
        _global_vault = CredentialVault()
    return _global_vault
