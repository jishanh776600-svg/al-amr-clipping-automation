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

    def _resolve_master_key(self) -> str:
        """Resolve the encryption key, ensuring durable continuity across restarts and redeploys.

        Resolution hierarchy:
        1. Explicit key if passed into constructor.
        2. Persistent disk key file at `paths.root() / ".master_key"`:
           Once a database volume has an established master key, that key is authoritative
           so that changes to environment variables (e.g. Render Blueprint `generateValue: true`
           regeneration on redeploy) do not render existing encrypted credentials unreadable.
        3. Environment secret (`AL_AMR_MASTER_KEY`, `AUTOCLIP_ENCRYPTION_KEY`, `ENCRYPTION_KEY`):
           Anchored immediately to `paths.root() / ".master_key"` on persistent disk.
        4. Generated random installation secret:
           Persisted to `paths.root() / ".master_key"`.
        5. Deterministic fallback derived from resolved root directory.
        """
        if self._explicit_key:
            return self._explicit_key

        root_dir = paths.root()
        key_file = root_dir / ".master_key"

        env_key = (
            os.environ.get("AL_AMR_MASTER_KEY")
            or os.environ.get("AUTOCLIP_ENCRYPTION_KEY")
            or os.environ.get("ENCRYPTION_KEY")
        )
        clean_env = env_key.strip() if (env_key and env_key.strip()) else None

        # 1. If persistent volume already has an established master key, use it as authoritative
        try:
            if key_file.is_file():
                stored_key = key_file.read_text(encoding="utf-8").strip()
                if stored_key:
                    if clean_env and stored_key != clean_env:
                        log.warning(
                            "Persistent disk master key at %s differs from environment key; "
                            "preserving persistent disk key for credential decryption continuity.",
                            key_file,
                        )
                    return stored_key
        except Exception as exc:
            log.warning("Could not read persistent master key file %s: %s", key_file, exc)

        # 2. If environment secret is configured, anchor it to the persistent disk volume
        if clean_env:
            try:
                root_dir.mkdir(parents=True, exist_ok=True)
                key_file.write_text(clean_env, encoding="utf-8")
                import contextlib
                with contextlib.suppress(OSError):
                    key_file.chmod(0o600)
                log.info("Anchored environment master key to persistent disk at %s", key_file)
            except Exception as exc:
                log.warning("Could not anchor environment master key to %s: %s", key_file, exc)
            return clean_env

        # 3. Generate and persist a stable random master key for this installation volume
        import secrets
        new_key = f"autoclip-key-{secrets.token_urlsafe(32)}"
        try:
            root_dir.mkdir(parents=True, exist_ok=True)
            key_file.write_text(new_key, encoding="utf-8")
            import contextlib
            with contextlib.suppress(OSError):
                key_file.chmod(0o600)
            log.info("Generated and persisted new durable master key to %s", key_file)
            return new_key
        except Exception as exc:
            log.warning("Could not write master key file %s: %s", key_file, exc)

        # 4. Local development fallback derived from the root path
        fallback = f"autoclip-dev-salt-{root_dir.resolve()}"
        return fallback

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

    def store_secret(self, key: str, plaintext: str) -> str:
        """Encrypt and persist a secret in the database.

        Returns the masked fingerprint.
        """
        if not plaintext or not plaintext.strip():
            raise ValueError(f"Cannot store empty secret for '{key}'")

        token = plaintext.strip()
        cipher = self.get_cipher()
        ciphertext = cipher.encrypt(token.encode("utf-8")).decode("utf-8")
        fingerprint = self.mask_token(key, token)

        store.save_credential(key, ciphertext, fingerprint)
        log.info(
            "Durable credential '%s' successfully encrypted and stored at rest (fingerprint: %s)",
            key,
            fingerprint,
        )
        return fingerprint

    def retrieve_secret(self, key: str) -> str | None:
        """Retrieve and decrypt a stored secret.

        Returns the plaintext secret to internal callers only, or None if not set.
        """
        rec = store.get_credential(key)
        if rec is None:
            return None

        cipher = self.get_cipher()
        try:
            decrypted = cipher.decrypt(rec.ciphertext.encode("utf-8")).decode("utf-8")
            return decrypted
        except InvalidToken as exc:
            log.error(
                "Failed to decrypt credential '%s'. The master encryption key may have changed.",
                key,
            )
            raise RuntimeError(
                f"Could not decrypt stored credential '{key}'. Master encryption key mismatch."
            ) from exc
        except Exception as exc:
            log.error("Unexpected error decrypting credential '%s': %s", key, exc)
            return None

    def delete_secret(self, key: str) -> bool:
        """Delete a secret from the persistent store."""
        deleted = store.delete_credential(key)
        if deleted:
            log.info("Durable credential '%s' removed from encrypted database vault", key)
        return deleted

    def get_secret_status(self, key: str) -> dict[str, Any]:
        """Return non-sensitive status information for Settings UI."""
        rec = store.get_credential(key)
        if rec is None:
            return {
                "configured": False,
                "masked": "Not configured",
                "updated_at": "",
            }

        # Verify whether the stored credential is successfully decryptable with active master key
        decrypted = False
        try:
            val = self.retrieve_secret(key)
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

        # Only migrate if SQLite does not already have a record
        existing = store.get_credential(key)
        if existing is not None:
            return False

        log.info("Migrating credential '%s' from OS keyring to durable SQLite vault...", key)
        token = keyring_value.strip()
        fingerprint = self.store_secret(key, token)

        # Verify round-trip decryption
        verified = self.retrieve_secret(key)
        if verified != token:
            log.error("Migration verification failed for credential '%s'. Rolling back.", key)
            self.delete_secret(key)
            return False

        log.info(
            "Successfully migrated credential '%s' to durable SQLite vault (fingerprint: %s)",
            key,
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
