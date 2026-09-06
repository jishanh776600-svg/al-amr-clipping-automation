"""Secure Encrypted Account and Credential Vault for Durable Cloud Storage."""

import base64
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from clipping.agent.vault.models import (
    AccountMetadata,
    AccountPlatform,
    AccountStatus,
    EncryptedSecretRecord,
)
from clipping.logging.logger import get_logger
from clipping.storage.base import StorageDriver

logger = get_logger("clipping.agent.vault")


class EncryptedCredentialVault:
    """
    Zero-leakage, authenticated encrypted credential vault operating over abstract Cloud StorageDriver.
    Ensures account passwords, OAuth tokens, and session cookies are encrypted with Fernet (AES-128-CBC + HMAC)
    and separated from public account metadata.
    """

    DEFAULT_SALT = b"al_amr_clipping_vault_salt_v1_secure"

    def __init__(self, storage_driver: StorageDriver, master_key: Optional[str] = None):
        self.storage = storage_driver
        self._fernet = self._init_fernet(master_key)

    @classmethod
    def _init_fernet(cls, master_key: Optional[str] = None) -> Fernet:
        secret_source = (
            master_key
            or os.environ.get("ENCRYPTION_MASTER_KEY")
            or os.environ.get("VAULT_MASTER_KEY")
            or os.environ.get("OPERATOR_TOKEN")
            or "al_amr_default_cloud_vault_key_fallback"
        )
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=cls.DEFAULT_SALT,
            iterations=100000,
        )
        derived_key = base64.urlsafe_b64encode(kdf.derive(secret_source.encode("utf-8")))
        return Fernet(derived_key)

    def _encrypt(self, data: Dict[str, Any]) -> str:
        raw_json = json.dumps(data, ensure_ascii=False).encode("utf-8")
        token = self._fernet.encrypt(raw_json)
        return token.decode("utf-8")

    def _decrypt(self, ciphertext: str) -> Dict[str, Any]:
        decrypted_bytes = self._fernet.decrypt(ciphertext.encode("utf-8"))
        return json.loads(decrypted_bytes.decode("utf-8"))

    async def save_account(
        self,
        metadata: AccountMetadata,
        sensitive_credentials: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Stores public metadata and separately encrypts sensitive credentials."""
        platform_str = metadata.platform.value if isinstance(metadata.platform, AccountPlatform) else str(metadata.platform)
        base_dir = f"vault/accounts/{platform_str}/{metadata.account_id}"

        # 1. Save metadata
        meta_json = json.dumps(metadata.to_safe_dict(), indent=2).encode("utf-8")
        await self.storage.upload_bytes(meta_json, f"{base_dir}/metadata.json", content_type="application/json")

        # 2. Save encrypted secrets if provided
        if sensitive_credentials:
            ciphertext = self._encrypt(sensitive_credentials)
            record = EncryptedSecretRecord(
                key_id="default_master",
                ciphertext=ciphertext,
                updated_at=datetime.now(timezone.utc),
            )
            enc_json = json.dumps(record.model_dump(mode="json"), indent=2).encode("utf-8")
            await self.storage.upload_bytes(enc_json, f"{base_dir}/secret.enc", content_type="application/json")

        # 3. Update account index
        await self._register_in_index(platform_str, metadata.account_id)
        logger.info("Saved account to encrypted vault", platform=platform_str, account_id=metadata.account_id)

    async def get_account_metadata(
        self,
        platform: AccountPlatform | str,
        account_id: str,
    ) -> Optional[AccountMetadata]:
        """Loads non-sensitive metadata for dashboard/audit operations."""
        platform_str = platform.value if isinstance(platform, AccountPlatform) else str(platform)
        key = f"vault/accounts/{platform_str}/{account_id}/metadata.json"
        if not await self.storage.exists(key):
            return None
        data = await self.storage.download_bytes(key)
        return AccountMetadata.model_validate_json(data.decode("utf-8"))

    async def get_account_credentials(
        self,
        platform: AccountPlatform | str,
        account_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Decrypts and returns sensitive credentials in memory for authorized capabilities."""
        platform_str = platform.value if isinstance(platform, AccountPlatform) else str(platform)
        key = f"vault/accounts/{platform_str}/{account_id}/secret.enc"
        if not await self.storage.exists(key):
            return None
        data = await self.storage.download_bytes(key)
        record = EncryptedSecretRecord.model_validate_json(data.decode("utf-8"))
        return self._decrypt(record.ciphertext)

    async def list_accounts(
        self,
        platform: Optional[AccountPlatform | str] = None,
        status: Optional[AccountStatus] = None,
        reuse_eligible: Optional[bool] = None,
    ) -> List[AccountMetadata]:
        """Returns safe account list filtered by criteria."""
        index = await self._load_index()
        results: List[AccountMetadata] = []

        platform_filter = (platform.value if isinstance(platform, AccountPlatform) else platform) if platform else None

        for item in index:
            p_str, acc_id = item["platform"], item["account_id"]
            if platform_filter and p_str != platform_filter:
                continue
            meta = await self.get_account_metadata(p_str, acc_id)
            if not meta:
                continue
            if status and meta.status != status:
                continue
            if reuse_eligible is not None and meta.reuse_eligibility != reuse_eligible:
                continue
            results.append(meta)

        return results

    async def update_account_status(
        self,
        platform: AccountPlatform | str,
        account_id: str,
        new_status: AccountStatus,
    ) -> bool:
        meta = await self.get_account_metadata(platform, account_id)
        if not meta:
            return False
        updated = meta.model_copy(update={"status": new_status})
        await self.save_account(updated)
        return True

    async def save_campaign_secret(self, campaign_id: str, sensitive_data: Dict[str, Any]) -> None:
        ciphertext = self._encrypt(sensitive_data)
        record = EncryptedSecretRecord(
            key_id="default_master",
            ciphertext=ciphertext,
            updated_at=datetime.now(timezone.utc),
        )
        enc_json = json.dumps(record.model_dump(mode="json"), indent=2).encode("utf-8")
        await self.storage.upload_bytes(enc_json, f"vault/campaigns/{campaign_id}/secret.enc", content_type="application/json")

    async def get_campaign_secret(self, campaign_id: str) -> Optional[Dict[str, Any]]:
        key = f"vault/campaigns/{campaign_id}/secret.enc"
        if not await self.storage.exists(key):
            return None
        data = await self.storage.download_bytes(key)
        record = EncryptedSecretRecord.model_validate_json(data.decode("utf-8"))
        return self._decrypt(record.ciphertext)

    async def _register_in_index(self, platform: str, account_id: str) -> None:
        index = await self._load_index()
        for entry in index:
            if entry["platform"] == platform and entry["account_id"] == account_id:
                return
        index.append({"platform": platform, "account_id": account_id})
        index_json = json.dumps(index, indent=2).encode("utf-8")
        await self.storage.upload_bytes(index_json, "vault/accounts/index.json", content_type="application/json")

    async def _load_index(self) -> List[Dict[str, str]]:
        key = "vault/accounts/index.json"
        if not await self.storage.exists(key):
            return []
        data = await self.storage.download_bytes(key)
        try:
            return json.loads(data.decode("utf-8"))
        except Exception:
            return []
