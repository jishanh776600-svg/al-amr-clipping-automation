"""Pipeline state and publishing orchestration contracts (Step 5/5)."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ConfigDict

from clipping.contracts.production import ProductionArtifact, ReviewStatus


class PipelineStage(str, Enum):
    """Authoritative execution stages for the end-to-end production orchestrator."""
    INPUT_RECEIVED = "INPUT_RECEIVED"
    BRIEF_ANALYZED = "BRIEF_ANALYZED"
    SOURCE_RESOLVED = "SOURCE_RESOLVED"
    SOURCE_VALIDATED = "SOURCE_VALIDATED"
    REQUIREMENTS_VALIDATED = "REQUIREMENTS_VALIDATED"
    MOMENTS_SELECTED = "MOMENTS_SELECTED"
    CLIPS_RENDERED = "CLIPS_RENDERED"
    COMPLIANCE_CHECKED = "COMPLIANCE_CHECKED"
    TELEGRAM_REVIEW_SENT = "TELEGRAM_REVIEW_SENT"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    REVISION_REQUIRED = "REVISION_REQUIRED"
    APPROVED = "APPROVED"
    PUBLISHING = "PUBLISHING"
    YOUTUBE_PUBLISHED = "YOUTUBE_PUBLISHED"
    INSTAGRAM_PUBLISHED = "INSTAGRAM_PUBLISHED"
    PUBLICATION_VERIFIED = "PUBLICATION_VERIFIED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    OPERATOR_INTERVENTION = "OPERATOR_INTERVENTION"

    @property
    def is_terminal(self) -> bool:
        return self in (PipelineStage.COMPLETED, PipelineStage.FAILED)


class OverallStatus(str, Enum):
    """High-level campaign status exposed to Mission Control and operators."""
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    REVIEW_READY = "REVIEW_READY"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    REVISION_REQUIRED = "REVISION_REQUIRED"
    APPROVED = "APPROVED"
    PUBLISHING = "PUBLISHING"
    PARTIALLY_PUBLISHED = "PARTIALLY_PUBLISHED"
    PUBLISHED = "PUBLISHED"
    OPERATOR_ACTION_REQUIRED = "OPERATOR_ACTION_REQUIRED"
    FAILED = "FAILED"
    COMPLETED = "COMPLETED"


class PlatformPublishStatus(str, Enum):
    """Lifecycle states for individual platform publication attempts."""
    PENDING = "PENDING"
    PUBLISHING = "PUBLISHING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class PlatformPublicationResult(BaseModel):
    """Durable receipt and verification record for a platform upload."""
    model_config = ConfigDict(frozen=True)

    platform: str = Field(..., description="Target platform e.g. 'youtube_shorts', 'instagram_reels'")
    status: PlatformPublishStatus = PlatformPublishStatus.PENDING
    publication_id: Optional[str] = Field(default=None, description="Platform-assigned ID e.g. videoId or mediaId")
    publication_url: Optional[str] = Field(default=None, description="Authoritative watch/view URL")
    error_message: Optional[str] = None
    attempt_count: int = Field(default=0, ge=0)
    published_at: Optional[datetime] = None
    verified_at: Optional[datetime] = None
    metadata_snapshot: Dict[str, Any] = Field(default_factory=dict)

    @property
    def is_success(self) -> bool:
        return self.status == PlatformPublishStatus.SUCCESS and bool(self.publication_id)


class PipelineCheckpoint(BaseModel):
    """Immutable checkpoint recorded along pipeline state transitions."""
    model_config = ConfigDict(frozen=True)

    stage: PipelineStage
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    details: Dict[str, Any] = Field(default_factory=dict)


class CampaignPipelineState(BaseModel):
    """
    Durable, serializable state tracking the complete lifecycle of a campaign run.
    Enables zero-loss crash recovery, idempotent publishing, and detailed telemetry.
    """
    campaign_id: str
    job_id: str
    current_stage: PipelineStage = PipelineStage.INPUT_RECEIVED
    overall_status: OverallStatus = OverallStatus.QUEUED
    artifacts: List[ProductionArtifact] = Field(default_factory=list)
    approved_artifact_ids: List[str] = Field(default_factory=list)
    publication_results: Dict[str, PlatformPublicationResult] = Field(default_factory=dict)
    target_platforms: List[str] = Field(default_factory=list)
    retry_count: int = 0
    failure_reason: Optional[str] = None
    failure_category: Optional[str] = None
    checkpoints: List[PipelineCheckpoint] = Field(default_factory=list)
    operator_intervention_id: Optional[str] = None
    resumable: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def record_stage(
        self,
        new_stage: PipelineStage,
        details: Optional[Dict[str, Any]] = None,
        overall_status: Optional[OverallStatus] = None,
    ) -> "CampaignPipelineState":
        """Transitions state machine to new stage and logs immutable checkpoint."""
        now = datetime.now(timezone.utc)
        payload = details or {}
        checkpoint = PipelineCheckpoint(stage=new_stage, timestamp=now, details=payload)

        updates: Dict[str, Any] = {
            "current_stage": new_stage,
            "checkpoints": [*self.checkpoints, checkpoint],
            "updated_at": now,
        }
        if overall_status:
            updates["overall_status"] = overall_status

        return self.model_copy(update=updates)
