"""Domain models for Autonomous Campaign Orchestration in AL AMR CLIPPING."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class OrchestrationStage(str, Enum):
    """Execution stages for end-to-end autonomous campaign orchestration."""
    DISCOVERY = "discovery"
    EVALUATING = "evaluating"
    OPPORTUNITY_SELECTED = "opportunity_selected"
    ACCOUNT_ASSIGNED = "account_assigned"
    SOURCE_ACQUISITION = "source_acquisition"
    PRODUCTION_DISPATCHED = "production_dispatched"
    PRODUCTION_COMPLETED = "production_completed"
    QA_VERIFIED = "qa_verified"
    SUBMISSION_PENDING = "submission_pending"
    SUBMISSION_COMPLETED = "submission_completed"
    PUBLISHED = "published"
    RECONCILED = "reconciled"
    FINALIZED = "finalized"
    BLOCKED = "blocked"
    ESCALATED = "escalated"

    @property
    def is_terminal(self) -> bool:
        return self in (
            OrchestrationStage.FINALIZED,
            OrchestrationStage.BLOCKED,
        )

    @property
    def is_active(self) -> bool:
        return not self.is_terminal and self != OrchestrationStage.ESCALATED


class OrchestrationCheckpoint(BaseModel):
    """Historical checkpoint for crash recovery and resume validation."""
    model_config = ConfigDict(frozen=True)

    stage: OrchestrationStage
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    details: Dict[str, Any] = Field(default_factory=dict)


class CampaignOrchestrationRecord(BaseModel):
    """
    Durable lifecycle state record for a campaign moving through
    the autonomous orchestration state machine.
    """
    orchestration_id: str
    campaign_id: str
    account_id: Optional[str] = None
    source_video_id: Optional[str] = None
    source_uri: Optional[str] = None
    production_task_id: Optional[str] = None
    submission_id: Optional[str] = None
    platform: Optional[str] = None
    current_stage: OrchestrationStage = OrchestrationStage.DISCOVERY
    opportunity_score: Optional[float] = None
    opportunity_tier: Optional[str] = None
    attempt_count: int = 0
    checkpoints: List[OrchestrationCheckpoint] = Field(default_factory=list)
    last_checkpoint: Dict[str, Any] = Field(default_factory=dict)
    blocking_reason: Optional[str] = None
    skip_reason: Optional[str] = None
    escalation_id: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def record_stage(
        self,
        new_stage: OrchestrationStage,
        details: Optional[Dict[str, Any]] = None,
    ) -> "CampaignOrchestrationRecord":
        """Transitions record to new stage and appends an immutable checkpoint."""
        now = datetime.now(timezone.utc)
        payload = details or {}
        checkpoint = OrchestrationCheckpoint(stage=new_stage, timestamp=now, details=payload)
        
        return self.model_copy(
            update={
                "current_stage": new_stage,
                "checkpoints": [*self.checkpoints, checkpoint],
                "last_checkpoint": payload,
                "updated_at": now,
            }
        )


class OrchestrationCycleSummary(BaseModel):
    """Durable telemetry summary of an autonomous orchestration cycle."""
    cycle_id: str
    status: str  # "completed", "partial", "failed", "safety_paused", "emergency_stopped"
    campaigns_discovered: int = 0
    campaigns_evaluated: int = 0
    opportunities_selected: int = 0
    accounts_provisioned_or_assigned: int = 0
    production_tasks_dispatched: int = 0
    submissions_processed: int = 0
    reconciliations_run: int = 0
    campaigns_finalized: int = 0
    escalations_raised: int = 0
    skipped_reasons: Dict[str, int] = Field(default_factory=dict)
    errors: List[str] = Field(default_factory=list)
    duration_seconds: float = 0.0
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
