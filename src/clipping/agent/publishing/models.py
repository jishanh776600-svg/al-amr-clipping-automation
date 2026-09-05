"""Autonomous Campaign Publishing and Submission Data Models."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field

from clipping.agent.vault.models import AccountPlatform


class SubmissionStatus(str, Enum):
    """Granular state transitions of a campaign content submission."""
    PENDING = "pending"
    VALIDATING = "validating"
    QUEUED = "queued"
    UPLOADING = "uploading"
    SUBMITTED = "submitted"
    SCHEDULED = "scheduled"
    PUBLISHED = "published"
    REJECTED = "rejected"
    FAILED = "failed"
    RETRY_PENDING = "retry_pending"
    BLOCKED = "blocked"
    RECONCILING = "reconciling"
    ESCALATED = "escalated"
    CANCELLED = "cancelled"


class PublishingMode(str, Enum):
    """Target publication and submission mode."""
    DRAFT = "draft"                          # Private / staging preparation
    SCHEDULED = "scheduled"                  # Scheduled future publication
    IMMEDIATE = "immediate"                  # Immediate public publication
    CAMPAIGN_SUBMISSION_ONLY = "campaign_submission_only" # Submit proof to campaign hub without publishing directly


class SubmissionStateTransition(BaseModel):
    """Immutable audit entry for submission state changes."""
    model_config = ConfigDict(frozen=True)

    from_status: Optional[SubmissionStatus] = None
    to_status: SubmissionStatus
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    reason: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)


class PublishingContentMetadata(BaseModel):
    """Campaign-compliant metadata for publication."""
    model_config = ConfigDict(frozen=True)

    title: str = Field(..., max_length=100)
    description: str = Field(default="", max_length=5000)
    hashtags: List[str] = Field(default_factory=list)
    mentions: List[str] = Field(default_factory=list)
    campaign_identifiers: Dict[str, Any] = Field(default_factory=dict)
    platform_specific: Dict[str, Any] = Field(default_factory=dict)
    privacy_status: str = Field(default="private")
    scheduled_publish_at: Optional[datetime] = None


class CampaignSubmissionRecord(BaseModel):
    """
    Durable, auditable submission entity tracking content from real clip production
    through platform publishing and campaign payout reconciliation.
    """
    model_config = ConfigDict(frozen=True)

    submission_id: str = Field(..., min_length=1, max_length=128)
    campaign_id: str = Field(..., min_length=1, max_length=128)
    account_id: str = Field(..., min_length=1, max_length=128)
    platform: AccountPlatform
    clip_id: str = Field(..., min_length=1, max_length=128)
    source_video_id: str = Field(default="")
    task_id: str = Field(default="")
    platform_post_id: Optional[str] = None
    platform_url: Optional[str] = None
    publishing_mode: PublishingMode = PublishingMode.DRAFT
    current_status: SubmissionStatus = SubmissionStatus.PENDING
    state_history: List[SubmissionStateTransition] = Field(default_factory=list)
    content_metadata: PublishingContentMetadata
    media_path: Optional[str] = None
    idempotency_key: str
    attempt_count: int = 0
    last_error: Optional[str] = None
    failure_classification: Optional[str] = None
    reconciliation_status: str = "unreconciled"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def transition_to(
        self,
        new_status: SubmissionStatus,
        reason: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        platform_post_id: Optional[str] = None,
        platform_url: Optional[str] = None,
    ) -> "CampaignSubmissionRecord":
        """Returns a copy of the submission record with the new state recorded in history."""
        now = datetime.now(timezone.utc)
        transition = SubmissionStateTransition(
            from_status=self.current_status,
            to_status=new_status,
            timestamp=now,
            reason=reason,
            details=details or {},
        )
        new_history = list(self.state_history) + [transition]
        updates: Dict[str, Any] = {
            "current_status": new_status,
            "state_history": new_history,
            "updated_at": now,
        }
        if platform_post_id:
            updates["platform_post_id"] = platform_post_id
        if platform_url:
            updates["platform_url"] = platform_url
        if reason:
            updates["last_error"] = reason

        return self.model_copy(update=updates)
