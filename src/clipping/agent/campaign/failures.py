"""Deterministic Execution Failure Classification and Retry Architecture."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field, ConfigDict


class FailureCategory(str, Enum):
    """Deterministic failure category classification."""
    RETRYABLE = "retryable"
    OPERATOR_REQUIRED = "operator_required"
    PERMANENT_FAILURE = "permanent_failure"


class ExecutionErrorCode(str, Enum):
    """Machine-readable error codes for campaign execution and source ingestion."""
    # RETRYABLE
    NETWORK_TIMEOUT = "ERR_NET_TIMEOUT"
    PROVIDER_UNAVAILABLE = "ERR_PROVIDER_UNAVAILABLE"
    DOWNLOAD_TRANSIENT_FAILURE = "ERR_DOWNLOAD_TRANSIENT"
    BROWSER_TRANSIENT_FAILURE = "ERR_BROWSER_TRANSIENT"
    RATE_LIMIT_EXCEEDED = "ERR_RATE_LIMIT"

    # OPERATOR_REQUIRED
    CAPTCHA_CHALLENGE = "ERR_CAPTCHA_CHALLENGE"
    AUTHENTICATION_EXPIRED = "ERR_AUTH_EXPIRED"
    AMBIGUOUS_CAMPAIGN_REQUIREMENT = "ERR_AMBIGUOUS_REQUIREMENT"
    MANUAL_SOURCE_SELECTION_REQUIRED = "ERR_MANUAL_SOURCE_REQUIRED"
    PLATFORM_AUTHORIZATION_REQUIRED = "ERR_PLATFORM_AUTH_REQUIRED"
    UNRESOLVED_ESCALATION = "ERR_UNRESOLVED_ESCALATION"

    # PERMANENT_FAILURE
    INVALID_SOURCE_URI = "ERR_INVALID_SOURCE"
    UNSUPPORTED_MEDIA_CONTAINER = "ERR_UNSUPPORTED_MEDIA"
    CORRUPTED_MEDIA = "ERR_CORRUPTED_MEDIA"
    PROHIBITED_SOURCE = "ERR_PROHIBITED_SOURCE"
    SPECIFIC_FOOTAGE_NOT_PROVIDED = "ERR_SPECIFIC_FOOTAGE_MISSING"
    CAMPAIGN_EXPIRED = "ERR_CAMPAIGN_EXPIRED"
    INVALID_DESTINATION_PLATFORM = "ERR_INVALID_DESTINATION"
    UNVERIFIED_OR_INACTIVE_ACCOUNT = "ERR_UNVERIFIED_ACCOUNT"
    HTML_MASQUERADING_AS_VIDEO = "ERR_HTML_SPOOFED_VIDEO"
    SYSTEM_EMERGENCY_STOPPED = "ERR_EMERGENCY_STOPPED"


class ExecutionFailure(BaseModel):
    """Structured execution failure diagnostic record."""
    model_config = ConfigDict(frozen=True)

    category: FailureCategory
    error_code: ExecutionErrorCode
    message: str = Field(..., description="Human-readable root cause explanation")
    retryable: bool = False
    retry_after_seconds: Optional[int] = None
    retry_count: int = 0
    max_retries: int = 3
    context: Dict[str, Any] = Field(default_factory=dict, description="Safe diagnostic context (no secrets)")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_operator_required(self) -> bool:
        return self.category == FailureCategory.OPERATOR_REQUIRED

    @property
    def is_permanent(self) -> bool:
        return self.category == FailureCategory.PERMANENT_FAILURE


class ExecutionFailureClassifier:
    """Classifies exceptions and system error states into deterministic failure categories."""

    @classmethod
    def classify_exception(cls, exc: Exception, context: Optional[Dict[str, Any]] = None) -> ExecutionFailure:
        safe_ctx = context or {}
        msg = str(exc)
        msg_lower = msg.lower()

        # 1. HTML Spoofing
        if "html markup" in msg_lower or "pretending to be video" in msg_lower or "html" in msg_lower and "video" in msg_lower:
            return ExecutionFailure(
                category=FailureCategory.PERMANENT_FAILURE,
                error_code=ExecutionErrorCode.HTML_MASQUERADING_AS_VIDEO,
                message=f"Remote URL returned HTML page instead of binary video container: {msg}",
                retryable=False,
                context=safe_ctx,
            )

        # 2. Corrupted Media
        if "corrupted" in msg_lower or "container integrity check" in msg_lower:
            return ExecutionFailure(
                category=FailureCategory.PERMANENT_FAILURE,
                error_code=ExecutionErrorCode.CORRUPTED_MEDIA,
                message=f"Media file failed container integrity check or is corrupted: {msg}",
                retryable=False,
                context=safe_ctx,
            )

        # 3. Prohibited Content / Campaign Restrictions
        if "prohibited" in msg_lower or "violates campaign restriction" in msg_lower:
            return ExecutionFailure(
                category=FailureCategory.PERMANENT_FAILURE,
                error_code=ExecutionErrorCode.PROHIBITED_SOURCE,
                message=f"Source violates campaign brief restrictions: {msg}",
                retryable=False,
                context=safe_ctx,
            )

        # 4. Specific Footage Missing
        if "specific permitted footage" in msg_lower or "does not match permitted" in msg_lower:
            return ExecutionFailure(
                category=FailureCategory.PERMANENT_FAILURE,
                error_code=ExecutionErrorCode.SPECIFIC_FOOTAGE_NOT_PROVIDED,
                message=f"Campaign requires specific footage not provided: {msg}",
                retryable=False,
                context=safe_ctx,
            )

        # 5. Invalid / Inactive Account
        if "not active" in msg_lower or "unverified" in msg_lower or "not found in vault" in msg_lower:
            return ExecutionFailure(
                category=FailureCategory.PERMANENT_FAILURE,
                error_code=ExecutionErrorCode.UNVERIFIED_OR_INACTIVE_ACCOUNT,
                message=f"Target account is not verified and active: {msg}",
                retryable=False,
                context=safe_ctx,
            )

        # 6. CAPTCHA / Anti-Bot Challenges
        if "captcha" in msg_lower or "cloudflare" in msg_lower or "bot protection" in msg_lower:
            return ExecutionFailure(
                category=FailureCategory.OPERATOR_REQUIRED,
                error_code=ExecutionErrorCode.CAPTCHA_CHALLENGE,
                message=f"Security challenge encountered; human operator intervention required: {msg}",
                retryable=False,
                context=safe_ctx,
            )

        # 7. Transient Network & Timeout Errors
        if "timeout" in msg_lower or "timed out" in msg_lower:
            return ExecutionFailure(
                category=FailureCategory.RETRYABLE,
                error_code=ExecutionErrorCode.NETWORK_TIMEOUT,
                message=f"Transient network timeout encountered: {msg}",
                retryable=True,
                retry_after_seconds=15,
                context=safe_ctx,
            )

        if "connection" in msg_lower or "network error" in msg_lower or "503" in msg_lower or "502" in msg_lower:
            return ExecutionFailure(
                category=FailureCategory.RETRYABLE,
                error_code=ExecutionErrorCode.PROVIDER_UNAVAILABLE,
                message=f"Transient connection/provider failure: {msg}",
                retryable=True,
                retry_after_seconds=30,
                context=safe_ctx,
            )

        # Default fallback: Permanent Failure to fail closed
        return ExecutionFailure(
            category=FailureCategory.PERMANENT_FAILURE,
            error_code=ExecutionErrorCode.INVALID_SOURCE_URI,
            message=f"Execution failed with unrecoverable error: {msg}",
            retryable=False,
            context=safe_ctx,
        )
