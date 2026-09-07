"""Data Contracts for Autonomous Production, Strict Compliance, and Human Approval."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ConfigDict


class ProductionStatus(str, Enum):
    """Lifecycle state of an autonomous clipping production run."""
    QUEUED = "QUEUED"
    ANALYZING = "ANALYZING"
    SELECTING = "SELECTING"
    RENDERING = "RENDERING"
    COMPLIANCE_CHECK = "COMPLIANCE_CHECK"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    REVISION_REQUIRED = "REVISION_REQUIRED"
    APPROVED = "APPROVED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    OPERATOR_ACTION_REQUIRED = "OPERATOR_ACTION_REQUIRED"


class ReviewStatus(str, Enum):
    """Human operator approval gate status. Publishing is prohibited unless APPROVED."""
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REVISION_REQUIRED = "REVISION_REQUIRED"
    BLOCKED = "BLOCKED"
    REJECTED = "REJECTED"


class ProductionComplianceResult(BaseModel):
    """Authoritative compliance audit result before Telegram dispatch."""
    model_config = ConfigDict(frozen=True)

    is_compliant: bool = Field(..., description="True only if zero hard blockers exist")
    blockers: List[str] = Field(default_factory=list, description="Hard compliance violations preventing review")
    warnings: List[str] = Field(default_factory=list, description="Non-fatal warnings or items flagged for operator attention")
    checks: Dict[str, Dict[str, Any]] = Field(default_factory=dict, description="Detailed sub-check audit details")
    evidence: Dict[str, Any] = Field(default_factory=dict, description="Audit evidence collected during checks")
    requirement_coverage: float = Field(default=1.0, ge=0.0, le=1.0, description="Ratio of verified requirements")
    generated_metadata_validation: Dict[str, Any] = Field(default_factory=dict, description="Validation of hashtags, titles, captions")
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def passed(self) -> bool:
        """Alias for is_compliant."""
        return self.is_compliant


class ProductionArtifact(BaseModel):
    """Authoritative production artifact contract for completed 9:16 vertical media."""
    model_config = ConfigDict(frozen=True)

    artifact_id: str = Field(..., min_length=1, max_length=128)
    campaign_id: str = Field(..., min_length=1, max_length=128)
    source_id: str = Field(..., min_length=1, max_length=128)
    clip_number: int = Field(default=1, ge=1)
    local_output_path: str = Field(..., description="Path to finished MP4 on local disk")
    duration: float = Field(..., gt=0.0)
    resolution: str = Field(default="1080x1920")
    aspect_ratio: str = Field(default="9:16")
    fps: float = Field(default=30.0)
    transcript: Optional[str] = Field(default=None, description="Transcript or speech text reference")
    selected_start_time: float = Field(..., ge=0.0)
    selected_end_time: float = Field(..., gt=0.0)
    hook: Optional[str] = Field(default=None, max_length=500)
    title: str = Field(..., min_length=1, max_length=300)
    description: str = Field(default="", max_length=5000)
    caption: str = Field(default="", max_length=3000)
    hashtags: List[str] = Field(default_factory=list)
    mentions: List[str] = Field(default_factory=list)
    branding_status: str = Field(default="not_required")
    compliance_result: Optional[ProductionComplianceResult] = Field(default=None)
    compliance_blockers: List[str] = Field(default_factory=list)
    compliance_warnings: List[str] = Field(default_factory=list)
    production_status: ProductionStatus = Field(default=ProductionStatus.PENDING_APPROVAL)
    review_status: ReviewStatus = Field(default=ReviewStatus.PENDING_APPROVAL)
    revision_count: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: Optional[datetime] = None
    operator_feedback: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @property
    def is_publishing_ready(self) -> bool:
        """Publishing is strictly prohibited unless human operator has explicitly approved."""
        return self.review_status == ReviewStatus.APPROVED


class OperatorInterventionRecord(BaseModel):
    """Preserves execution checkpoint and actionable instructions during human escalation."""
    model_config = ConfigDict(frozen=True)

    intervention_id: str = Field(..., min_length=1, max_length=64)
    campaign_id: str = Field(..., min_length=1, max_length=128)
    job_id: str = Field(..., min_length=1, max_length=128)
    challenge_type: str = Field(default="CAPTCHA")
    checkpoint: str = Field(default="SOURCE_ACCESS")
    actionable_url: Optional[str] = Field(default=None, description="Actual browser challenge URL if available; never fabricated")
    human_steps: List[str] = Field(default_factory=list)
    status: str = Field(default="PENDING_OPERATOR")
    session_preserved: bool = Field(default=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    resumed_at: Optional[datetime] = None


class RevisionRecord(BaseModel):
    """Immutable audit record storing operator rejection feedback and requested modifications."""
    model_config = ConfigDict(frozen=True)

    revision_id: str = Field(..., min_length=1, max_length=64)
    original_artifact_id: str = Field(..., min_length=1, max_length=128)
    revised_artifact_id: Optional[str] = None
    revision_number: int = Field(..., ge=1)
    operator_id: str = Field(..., min_length=1)
    operator_feedback: str = Field(..., min_length=1)
    requested_changes: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
