"""Autonomous Campaign Data Models and Lifecycle Contracts."""

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ConfigDict


class CampaignStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    DRAFT = "draft"
    EXPIRED = "expired"


class CampaignPlatform(str, Enum):
    YOUTUBE_SHORTS = "youtube_shorts"
    INSTAGRAM_REELS = "instagram_reels"
    TIKTOK = "tiktok"


class PostingRequirements(BaseModel):
    model_config = ConfigDict(frozen=True)

    min_duration_seconds: float = Field(default=30.0, ge=5.0, le=300.0)
    max_duration_seconds: float = Field(default=60.0, ge=5.0, le=300.0)
    required_hashtags: List[str] = Field(default_factory=list)
    required_mentions: List[str] = Field(default_factory=list)
    required_title_keywords: List[str] = Field(default_factory=list)
    daily_post_limit: int = Field(default=3, ge=1, le=20)


class AccountRequirements(BaseModel):
    model_config = ConfigDict(frozen=True)

    allowed_platforms: List[CampaignPlatform] = Field(default_factory=lambda: [CampaignPlatform.YOUTUBE_SHORTS])
    allow_account_reuse: bool = True
    min_subscribers: int = 0
    verified_only: bool = False
    required_niche: Optional[str] = None


class CampaignRecord(BaseModel):
    """
    Durable, normalized representation of a discovered campaign and its strict execution boundaries.
    """
    model_config = ConfigDict(frozen=True)

    campaign_id: str = Field(..., min_length=1, max_length=128)
    name: str = Field(..., min_length=1, max_length=256)
    source: str = Field(..., description="Origin source URL or ingestion channel")
    description: str = Field(default="")
    status: CampaignStatus = CampaignStatus.ACTIVE
    required_platforms: List[CampaignPlatform] = Field(default_factory=lambda: [CampaignPlatform.YOUTUBE_SHORTS])
    allowed_content_rules: List[str] = Field(default_factory=list)
    prohibited_content_rules: List[str] = Field(default_factory=list)
    posting_requirements: PostingRequirements = Field(default_factory=PostingRequirements)
    account_requirements: AccountRequirements = Field(default_factory=AccountRequirements)
    reuse_restrictions: Optional[str] = None
    seo_requirements: Dict[str, Any] = Field(default_factory=dict)
    payment_info: Optional[Dict[str, Any]] = None
    source_video_requirements: Dict[str, Any] = Field(default_factory=dict)
    discovered_source_uris: List[str] = Field(default_factory=list)
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def validate_rules(self) -> Optional[str]:
        """Detects contradictions or impossible constraints."""
        if self.posting_requirements.min_duration_seconds > self.posting_requirements.max_duration_seconds:
            return f"Contradictory duration: min ({self.posting_requirements.min_duration_seconds}s) exceeds max ({self.posting_requirements.max_duration_seconds}s)"

        # Check for direct contradictions between allowed and prohibited rules
        allowed_set = {r.strip().lower() for r in self.allowed_content_rules}
        prohibited_set = {r.strip().lower() for r in self.prohibited_content_rules}
        intersection = allowed_set.intersection(prohibited_set)
        if intersection:
            return f"Contradictory content rules: {list(intersection)} is both allowed and prohibited"

        return None

    def is_eligible_source_url(self, url: str) -> bool:
        """Validates if a source URL satisfies campaign source video requirements."""
        if not url or not (url.startswith("http://") or url.startswith("https://")):
            return False
        # If campaign has strict source channel/domain requirements
        required_channel = self.source_video_requirements.get("required_channel")
        if required_channel and required_channel.lower() not in url.lower():
            return False
        return True
