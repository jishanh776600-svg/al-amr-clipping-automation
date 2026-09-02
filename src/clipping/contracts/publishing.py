"""Telegram Approval & YouTube Publishing Contracts."""

from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional, Literal
from pydantic import BaseModel, Field, ConfigDict
from clipping.core.constants import SCHEMA_VERSION


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    REGENERATE_REQUESTED = "regenerate_requested"


class ApprovalRequest(BaseModel):
    """Payload sent to human reviewer via Telegram."""
    model_config = ConfigDict(frozen=True)

    schema_version: str = Field(default=SCHEMA_VERSION)
    clip_id: str = Field(..., min_length=1, max_length=128)
    campaign_id: str = Field(..., min_length=1, max_length=128)
    video_storage_key: str = Field(..., description="Logical storage key of rendered MP4")
    thumbnail_storage_key: str = Field(..., description="Logical storage key of thumbnail JPG")
    title_suggestion: str = Field(..., min_length=3, max_length=100)
    description_suggestion: str = Field(default="", max_length=5000)
    virality_score: float = Field(..., ge=0.0, le=100.0)
    compliance_passed: bool = True
    approval_status: ApprovalStatus = ApprovalStatus.PENDING
    telegram_message_id: Optional[int] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    decided_at: Optional[datetime] = None


class PublishingStatus(str, Enum):
    QUEUED = "queued"
    UPLOADING = "uploading"
    PUBLISHED = "published"
    FAILED = "failed"


class PublishingJob(BaseModel):
    """Specification for YouTube upload and scheduling."""
    model_config = ConfigDict(frozen=True)

    schema_version: str = Field(default=SCHEMA_VERSION)
    job_id: str = Field(..., min_length=1, max_length=128)
    clip_id: str = Field(..., min_length=1, max_length=128)
    channel_id: str = Field(..., min_length=1, max_length=128)
    video_storage_key: str = Field(..., description="Logical storage key for final video")
    title: str = Field(..., min_length=1, max_length=100)
    description: str = Field(default="", max_length=5000)
    tags: List[str] = Field(default_factory=list)
    scheduled_time: Optional[datetime] = None
    privacy_status: Literal["public", "private", "unlisted"] = "public"
    publishing_status: PublishingStatus = PublishingStatus.QUEUED
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PublishingResult(BaseModel):
    """Receipt after YouTube upload and scheduling confirmation."""
    model_config = ConfigDict(frozen=True)

    job_id: str = Field(..., min_length=1, max_length=128)
    clip_id: str = Field(..., min_length=1, max_length=128)
    channel_id: str = Field(..., min_length=1, max_length=128)
    youtube_video_id: str = Field(..., min_length=1, max_length=64)
    youtube_watch_url: str = Field(...)
    quota_units_used: int = Field(default=0, ge=0, description="Estimated quota units consumed depending on endpoint bucket")
    published_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
