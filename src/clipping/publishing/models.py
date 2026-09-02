"""Data models for YouTube Publishing, Scheduling, and Audit Trails."""

from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict


class PublishStatus(str, Enum):
    """Lifecycle states for video publication."""
    READY = "ready"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"
    SKIPPED = "skipped"    # Clip rejected or QA failed
    DEFERRED = "deferred"  # Awaiting approval or scheduled for the future


class PrivacyStatus(str, Enum):
    """YouTube video privacy visibility modes."""
    PRIVATE = "private"
    UNLISTED = "unlisted"
    PUBLIC = "public"


class FailureClassification(str, Enum):
    """Classification of errors determining retry eligibility."""
    RETRYABLE = "retryable"          # Rate limit (429), quotaExceeded, uploadLimitExceeded, 5xx server error, network timeout
    NON_RETRYABLE = "non_retryable"  # 400 Bad Request, 401 Unauthorized, 403 Forbidden (accessNotConfigured, insufficientPermissions)


class YouTubeVideoMetadata(BaseModel):
    """Sanitized and validated metadata sent to YouTube Data API."""
    model_config = ConfigDict(frozen=True)

    title: str = Field(..., min_length=1, max_length=100)
    description: str = Field(default="", max_length=5000)
    tags: List[str] = Field(default_factory=list)
    privacy_status: PrivacyStatus = PrivacyStatus.PRIVATE
    publish_at: Optional[datetime] = None  # Scheduled release in UTC


class YouTubeVideoReference(BaseModel):
    """Receipt returned after successful YouTube upload confirmation."""
    model_config = ConfigDict(frozen=True)

    video_id: str = Field(..., min_length=1, max_length=64)
    watch_url: str = Field(..., min_length=1)
    channel_id: str = Field(..., min_length=1)
    published_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PublishRequest(BaseModel):
    """Durable record tracking publication status for an individual clip."""
    model_config = ConfigDict(frozen=True)

    job_id: str = Field(..., min_length=1, max_length=128)
    clip_id: str = Field(..., min_length=1, max_length=128)
    approval_request_id: str = Field(..., min_length=1, max_length=128)
    idempotency_key: str = Field(..., min_length=1, max_length=256)
    video_storage_key: str = Field(..., min_length=1)
    metadata: YouTubeVideoMetadata
    status: PublishStatus = PublishStatus.READY
    youtube_video_id: Optional[str] = None
    youtube_url: Optional[str] = None
    channel_id: Optional[str] = None
    attempt_count: int = Field(default=0, ge=0)
    failure_reason: Optional[str] = None
    failure_type: Optional[FailureClassification] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    published_at: Optional[datetime] = None
    scheduled_publish_at: Optional[datetime] = None
    version: int = Field(default=1, ge=1)


class PublishAuditRecord(BaseModel):
    """Immutable audit record logging an attempt or state change in publishing."""
    model_config = ConfigDict(frozen=True)

    audit_id: str
    job_id: str
    clip_id: str
    approval_request_id: str
    idempotency_key: str
    attempt_number: int
    previous_status: PublishStatus
    new_status: PublishStatus
    youtube_video_id: Optional[str] = None
    error_message: Optional[str] = None
    failure_type: Optional[FailureClassification] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    schema_version: str = "1.0"


class PublishSummary(BaseModel):
    """Aggregate metrics across clips for a job or schedule execution."""
    model_config = ConfigDict(frozen=True)

    job_id: str
    total_clips: int
    published_count: int
    skipped_count: int
    deferred_count: int
    failed_count: int
    all_processed: bool
