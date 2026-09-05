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
    UNAVAILABLE = "unavailable"


class CampaignLifecycleState(str, Enum):
    """Granular state transitions of an autonomous campaign from discovery to closure."""
    DISCOVERED = "discovered"
    EVALUATING = "evaluating"
    SELECTED = "selected"
    ACCOUNT_ASSIGNED = "account_assigned"
    ACCOUNT_CREATING = "account_creating"
    ACCOUNT_CONFIGURING = "account_configuring"
    CONTENT_PRODUCTION = "content_production"
    CONTENT_READY = "content_ready"
    SUBMISSION_ACTIVE = "submission_active"
    CAMPAIGN_ACTIVE = "campaign_active"
    CAMPAIGN_COMPLETED = "campaign_completed"
    PAYMENT_PENDING = "payment_pending"
    PAYMENT_CONFIRMED = "payment_confirmed"
    REUSE_ELIGIBLE = "reuse_eligible"
    REUSE_PROHIBITED = "reuse_prohibited"
    CLOSED = "closed"
    BLOCKED = "blocked"
    ESCALATED = "escalated"



class CampaignPlatform(str, Enum):
    YOUTUBE_SHORTS = "youtube_shorts"
    INSTAGRAM_REELS = "instagram_reels"
    TIKTOK = "tiktok"


class PayoutModel(str, Enum):
    CPM = "cpm"
    FIXED_PER_CLIP = "fixed_per_clip"
    REV_SHARE = "rev_share"
    BOUNTY = "bounty"


class PayoutTerms(BaseModel):
    """Normalized payout terms, rates, and budget constraints."""
    model_config = ConfigDict(frozen=True)

    model: PayoutModel = PayoutModel.CPM
    cpm_rate: Optional[float] = Field(default=None, ge=0.0, description="Rate per 1,000 views in USD")
    fixed_amount: Optional[float] = Field(default=None, ge=0.0, description="Fixed payout per accepted clip")
    min_payout: Optional[float] = Field(default=None, ge=0.0)
    max_payout: Optional[float] = Field(default=None, ge=0.0)
    currency: str = "USD"
    total_budget: Optional[float] = Field(default=None, ge=0.0)
    remaining_budget: Optional[float] = Field(default=None, ge=0.0)
    budget_exhausted: bool = False

    def is_healthy_budget(self) -> bool:
        if self.budget_exhausted:
            return False
        if self.remaining_budget is not None and self.remaining_budget <= 0.0:
            return False
        return True


class CampaignDuration(BaseModel):
    """Timeline, duration boundaries, and expiration tracking."""
    model_config = ConfigDict(frozen=True)

    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    deadline: Optional[datetime] = None
    timezone_name: str = "UTC"
    is_expired: bool = False

    def check_expired_at(self, current_time: datetime) -> bool:
        if self.is_expired:
            return True
        effective_end = self.deadline or self.end_date
        if effective_end and current_time >= effective_end:
            return True
        return False


class SourceMaterial(BaseModel):
    """Source video footage, drive links, and approved creator material."""
    model_config = ConfigDict(frozen=True)

    video_urls: List[str] = Field(default_factory=list)
    google_drive_folder: Optional[str] = None
    podcast_stream_url: Optional[str] = None
    allowed_channel_patterns: List[str] = Field(default_factory=list)
    preferred_segments: List[str] = Field(default_factory=list)


class QuotasAndCaps(BaseModel):
    """Submission frequencies, creator limits, and campaign caps."""
    model_config = ConfigDict(frozen=True)

    daily_creator_limit: int = Field(default=3, ge=1, le=50)
    max_submissions_per_creator: Optional[int] = Field(default=None, ge=1)
    campaign_total_clip_cap: Optional[int] = Field(default=None, ge=1)
    current_total_submissions: int = Field(default=0, ge=0)

    def is_creator_cap_reached(self, creator_submissions_today: int) -> bool:
        return creator_submissions_today >= self.daily_creator_limit


class PostingRequirements(BaseModel):
    model_config = ConfigDict(frozen=True)

    min_duration_seconds: float = Field(default=30.0, ge=5.0, le=300.0)
    max_duration_seconds: float = Field(default=60.0, ge=5.0, le=300.0)
    required_hashtags: List[str] = Field(default_factory=list)
    required_mentions: List[str] = Field(default_factory=list)
    required_title_keywords: List[str] = Field(default_factory=list)
    daily_post_limit: int = Field(default=3, ge=1, le=20)
    caption_template: Optional[str] = None
    pinned_comment: Optional[str] = None


class AccountRequirements(BaseModel):
    model_config = ConfigDict(frozen=True)

    allowed_platforms: List[CampaignPlatform] = Field(default_factory=lambda: [CampaignPlatform.YOUTUBE_SHORTS])
    allow_account_reuse: bool = True
    min_subscribers: int = 0
    min_account_age_days: int = 0
    verified_only: bool = False
    required_niche: Optional[str] = None
    disallowed_regions: List[str] = Field(default_factory=list)


class TermChangeRecord(BaseModel):
    """Audit record capturing changes in campaign terms over time."""
    model_config = ConfigDict(frozen=True)

    field_name: str
    old_value: Any
    new_value: Any
    changed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    impact_summary: str = ""


class PostCampaignRules(BaseModel):
    """Rules governing channel, video, and credential state after a campaign concludes."""
    model_config = ConfigDict(frozen=True)

    allow_account_reuse_after_campaign: bool = True
    privatize_videos_on_completion: bool = False
    delete_videos_on_completion: bool = False
    cooldown_days_before_reuse: int = Field(default=0, ge=0)
    retain_branding: bool = True


class CampaignRecord(BaseModel):
    """
    Durable, normalized representation of a discovered campaign and its strict execution boundaries.
    Enriched with full autonomous campaign intelligence, Whop source integration, economics & ranking.
    """
    model_config = ConfigDict(frozen=True)

    campaign_id: str = Field(..., min_length=1, max_length=128)
    name: str = Field(..., min_length=1, max_length=256)
    source: str = Field(..., description="Origin source URL or ingestion channel (e.g. 'whop', 'https://whop.com/...')")
    description: str = Field(default="")
    status: CampaignStatus = CampaignStatus.ACTIVE
    lifecycle_state: CampaignLifecycleState = CampaignLifecycleState.DISCOVERED
    required_platforms: List[CampaignPlatform] = Field(default_factory=lambda: [CampaignPlatform.YOUTUBE_SHORTS])
    allowed_content_rules: List[str] = Field(default_factory=list)
    prohibited_content_rules: List[str] = Field(default_factory=list)
    posting_requirements: PostingRequirements = Field(default_factory=PostingRequirements)
    account_requirements: AccountRequirements = Field(default_factory=AccountRequirements)
    post_campaign_rules: PostCampaignRules = Field(default_factory=PostCampaignRules)
    reuse_restrictions: Optional[str] = None
    seo_requirements: Dict[str, Any] = Field(default_factory=dict)
    payment_info: Optional[Dict[str, Any]] = None
    source_video_requirements: Dict[str, Any] = Field(default_factory=dict)
    discovered_source_uris: List[str] = Field(default_factory=list)
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Campaign Intelligence Additions
    payout_terms: PayoutTerms = Field(default_factory=PayoutTerms)
    duration_terms: CampaignDuration = Field(default_factory=CampaignDuration)
    source_material: SourceMaterial = Field(default_factory=SourceMaterial)
    quotas: QuotasAndCaps = Field(default_factory=QuotasAndCaps)
    term_changes: List[TermChangeRecord] = Field(default_factory=list)
    opportunity_score: Optional[float] = None
    opportunity_tier: Optional[str] = None
    canonical_url: Optional[str] = None
    creator_community: Optional[str] = None
    external_source_id: Optional[str] = None


    def validate_rules(self) -> Optional[str]:
        """Detects contradictions or impossible constraints in the campaign brief."""
        if self.posting_requirements.min_duration_seconds > self.posting_requirements.max_duration_seconds:
            return f"Contradictory duration: min ({self.posting_requirements.min_duration_seconds}s) exceeds max ({self.posting_requirements.max_duration_seconds}s)"

        # Check for direct contradictions between allowed and prohibited rules
        allowed_set = {r.strip().lower() for r in self.allowed_content_rules}
        prohibited_set = {r.strip().lower() for r in self.prohibited_content_rules}
        intersection = allowed_set.intersection(prohibited_set)
        if intersection:
            return f"Contradictory content rules: {list(intersection)} is both allowed and prohibited"

        # Check for contradictory payout bounds
        if (
            self.payout_terms.min_payout is not None
            and self.payout_terms.max_payout is not None
            and self.payout_terms.min_payout > self.payout_terms.max_payout
        ):
            return f"Contradictory payout: min payout (${self.payout_terms.min_payout}) exceeds max payout (${self.payout_terms.max_payout})"

        # Check duration dates
        if (
            self.duration_terms.start_date is not None
            and self.duration_terms.end_date is not None
            and self.duration_terms.start_date > self.duration_terms.end_date
        ):
            return f"Contradictory dates: start date ({self.duration_terms.start_date}) is after end date ({self.duration_terms.end_date})"

        return None

    def is_eligible_source_url(self, url: str) -> bool:
        """Validates if a source URL satisfies campaign source video requirements."""
        if not url or not (url.startswith("http://") or url.startswith("https://")):
            return False

        # Check source video requirements dictionary
        required_channel = self.source_video_requirements.get("required_channel")
        if required_channel and required_channel.lower() not in url.lower():
            return False

        # Check source material allowed channel patterns if specified
        if self.source_material.allowed_channel_patterns:
            matches_pattern = any(p.lower() in url.lower() for p in self.source_material.allowed_channel_patterns)
            if not matches_pattern:
                return False

        return True
