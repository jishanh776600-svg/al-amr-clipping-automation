"""Escalation Data Models and Context Payloads for Human-in-the-Loop Governance."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ConfigDict


class EscalationSeverity(str, Enum):
    """Urgency level of an escalation requiring human operator attention."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class EscalationReason(str, Enum):
    """Categorical root cause necessitating human intervention."""
    CAPTCHA_CHALLENGE = "captcha_challenge"
    IDENTITY_VERIFICATION = "identity_verification"
    MFA_REQUIRED = "mfa_required"
    CONTRADICTORY_INSTRUCTIONS = "contradictory_instructions"
    PLATFORM_BLOCKED = "platform_blocked"
    POLICY_VIOLATION = "policy_violation"
    IRREVERSIBLE_ACTION = "irreversible_action"
    LEGAL_COMPLIANCE_AMBIGUITY = "legal_compliance_ambiguity"
    RESOURCE_EXHAUSTION = "resource_exhaustion"
    UNCLASSIFIED_FAILURE = "unclassified_failure"


class EscalationStatus(str, Enum):
    """Lifecycle status of an escalation request."""
    OPEN = "open"
    RESOLVED = "resolved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class EscalationContext(BaseModel):
    """
    Rich diagnostic context explaining the exact situation to the operator.
    Enables zero-guesswork decisions via Telegram or Mission Control dashboard.
    """
    model_config = ConfigDict(frozen=True)

    what_happened: str = Field(..., description="Clear explanation of the incident/event")
    why_it_happened: str = Field(..., description="Diagnosed root cause or trigger condition")
    what_was_attempted: List[str] = Field(default_factory=list, description="Automated remedies tried by agent")
    decision_required: str = Field(..., description="Exact question or choice needed from human operator")
    available_options: List[str] = Field(default_factory=list, description="Actionable options available to the operator")
    reason: Optional[EscalationReason] = None
    severity: Optional[EscalationSeverity] = None
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Diagnostic payload (secrets strictly omitted)")


class EscalationRecord(BaseModel):
    """Durable record representing an active or historical escalation."""
    model_config = ConfigDict(frozen=True)

    escalation_id: str = Field(..., min_length=1, max_length=128)
    task_id: str = Field(..., min_length=1, max_length=128)
    campaign_id: Optional[str] = None
    reason: EscalationReason
    severity: EscalationSeverity = EscalationSeverity.MEDIUM
    status: EscalationStatus = EscalationStatus.OPEN

    context: EscalationContext

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: Optional[datetime] = None
    resolved_by: Optional[str] = None
    resolution_action: Optional[str] = None
    resolution_notes: Optional[str] = None

    def resolve(self, operator: str, action: str, notes: Optional[str] = None) -> "EscalationRecord":
        """Returns a resolved copy of this escalation record."""
        now = datetime.now(timezone.utc)
        return self.model_copy(update={
            "status": EscalationStatus.RESOLVED,
            "resolved_at": now,
            "resolved_by": operator,
            "resolution_action": action,
            "resolution_notes": notes,
        })

    def reject(self, operator: str, notes: Optional[str] = None) -> "EscalationRecord":
        """Returns a rejected copy of this escalation record."""
        now = datetime.now(timezone.utc)
        return self.model_copy(update={
            "status": EscalationStatus.REJECTED,
            "resolved_at": now,
            "resolved_by": operator,
            "resolution_notes": notes,
        })
