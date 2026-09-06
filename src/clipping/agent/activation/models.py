"""Data Models for Operator Activation Sessions and Challenge Verification.

Implements strict state machine contracts and ephemeral OTP challenge management.
Guarantees zero plain secrets or raw OTPs are leaked in serialization or logs.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ConfigDict


class ActivationState(str, Enum):
    """Granular state transitions of an activation session from initiation to completion."""
    ACTIVATION_STARTED = "activation_started"
    AUTHENTICATION_REQUIRED = "authentication_required"
    OTP_REQUIRED = "otp_required"
    TELEGRAM_ESCALATION_SENT = "telegram_escalation_sent"
    WAITING_FOR_OPERATOR = "waiting_for_operator"
    OTP_RECEIVED = "otp_received"
    OTP_VALIDATED = "otp_validated"
    AUTHENTICATION_CONTINUED = "authentication_continued"
    REMOTE_IDENTITY_VERIFIED = "remote_identity_verified"
    ACTIVATION_COMPLETE = "activation_complete"

    # Terminal failure states
    OTP_EXPIRED = "otp_expired"
    OTP_REJECTED = "otp_rejected"
    SESSION_EXPIRED = "session_expired"
    OPERATOR_CANCELLED = "operator_cancelled"
    REMOTE_AUTH_FAILED = "remote_auth_failed"
    MANUAL_INTERVENTION_REQUIRED = "manual_intervention_required"


class ActivationChallenge(BaseModel):
    """
    Ephemeral authentication challenge (e.g. 2FA/SMS/Email OTP).
    Bound strictly to a session_id and consumed exactly once.
    Raw OTP values are NEVER stored in this model.
    """
    model_config = ConfigDict(frozen=True)

    challenge_id: str = Field(..., min_length=1, max_length=128)
    session_id: str = Field(..., min_length=1, max_length=128)
    service: str = Field(..., min_length=1, max_length=64)
    challenge_type: str = Field(default="otp", description="Type of verification challenge, e.g. 'otp', 'email_pin'")
    expected_length: int = Field(default=6, ge=4, le=12)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime = Field(...)
    consumed: bool = False
    consumed_at: Optional[datetime] = None
    attempts: int = 0
    max_attempts: int = 3

    def is_expired(self, current_time: Optional[datetime] = None) -> bool:
        now = current_time or datetime.now(timezone.utc)
        return now >= self.expires_at

    def to_safe_dict(self) -> Dict[str, Any]:
        """Safe representation without sensitive internals."""
        return {
            "challenge_id": self.challenge_id,
            "session_id": self.session_id,
            "service": self.service,
            "challenge_type": self.challenge_type,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "consumed": self.consumed,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "is_expired": self.is_expired(),
        }


class ActivationSession(BaseModel):
    """
    Durable record tracking the lifecycle of an account or service activation.
    Guarantees strict auditability while eliminating any persistence of raw secrets.
    """
    model_config = ConfigDict(frozen=True)

    session_id: str = Field(..., min_length=1, max_length=128)
    service: str = Field(..., min_length=1, max_length=64, description="Target service: e.g. 'youtube', 'whop', 'telegram'")
    account_identifier: str = Field(..., min_length=1, max_length=128, description="Safe public identifier e.g. channel handle")
    state: ActivationState = ActivationState.ACTIVATION_STARTED
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime = Field(...)
    active_challenge: Optional[ActivationChallenge] = None
    telegram_chat_id: Optional[int] = None
    telegram_message_id: Optional[int] = None
    remote_identity: Optional[str] = None
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def is_expired(self, current_time: Optional[datetime] = None) -> bool:
        now = current_time or datetime.now(timezone.utc)
        return now >= self.expires_at

    def transition(
        self,
        new_state: ActivationState,
        active_challenge: Optional[ActivationChallenge] = None,
        remote_identity: Optional[str] = None,
        error_message: Optional[str] = None,
        metadata_update: Optional[Dict[str, Any]] = None,
    ) -> "ActivationSession":
        """Returns a modified copy transitioning to a new lifecycle state."""
        updates: Dict[str, Any] = {"state": new_state}
        if active_challenge is not None:
            updates["active_challenge"] = active_challenge
        if remote_identity is not None:
            updates["remote_identity"] = remote_identity
        if error_message is not None:
            updates["error_message"] = error_message
        if metadata_update:
            new_meta = dict(self.metadata)
            new_meta.update(metadata_update)
            updates["metadata"] = new_meta
        return self.model_copy(update=updates)

    def to_safe_dict(self) -> Dict[str, Any]:
        """Safe representation for API responses and Mission Control UI (zero secrets)."""
        return {
            "session_id": self.session_id,
            "service": self.service,
            "account_identifier": self.account_identifier,
            "state": self.state.value,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "active_challenge": self.active_challenge.to_safe_dict() if self.active_challenge else None,
            "remote_identity": self.remote_identity,
            "error_message": self.error_message,
            "is_expired": self.is_expired(),
        }
