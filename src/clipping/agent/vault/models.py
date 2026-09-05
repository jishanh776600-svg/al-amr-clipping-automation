"""Account and Credential Vault Data Models."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ConfigDict


class AccountPlatform(str, Enum):
    YOUTUBE = "youtube"
    INSTAGRAM = "instagram"
    TIKTOK = "tiktok"
    TWITTER = "twitter"


class AccountStatus(str, Enum):
    ACTIVE = "active"
    RESTRICTED = "restricted"
    SUSPENDED = "suspended"
    PENDING_VERIFICATION = "pending_verification"
    DEPRECATED = "deprecated"


class AccountMetadata(BaseModel):
    """
    Public, safe account metadata.
    Contains NO passwords, session tokens, or private keys. Safe for dashboard APIs and audit logs.
    """
    model_config = ConfigDict(frozen=True)

    platform: AccountPlatform
    account_id: str = Field(..., min_length=1, max_length=128)
    username: str = Field(..., min_length=1, max_length=128)
    display_name: Optional[str] = None
    campaign_association: Optional[str] = None  # Associated campaign_id
    status: AccountStatus = AccountStatus.ACTIVE
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    reuse_eligibility: bool = True
    campaign_restrictions: List[str] = Field(default_factory=list)
    session_ref: Optional[str] = None  # Storage key of encrypted session state
    tags: List[str] = Field(default_factory=list)

    def to_safe_dict(self) -> Dict[str, Any]:
        """Dictionary representation safe for public APIs and audit logs."""
        return self.model_dump(mode="json")


class EncryptedSecretRecord(BaseModel):
    """Envelope containing Fernet-encrypted ciphertext of sensitive secrets."""
    model_config = ConfigDict(frozen=True)

    key_id: str
    algorithm: str = "fernet-aes128cbc-hmacsha256"
    ciphertext: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
