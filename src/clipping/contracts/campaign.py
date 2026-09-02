"""Campaign Specification and Rule Contracts."""

from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict
from clipping.core.constants import SCHEMA_VERSION


class CampaignRuleCategory(str, Enum):
    REQUIRED_THEME = "required_theme"
    PROHIBITED_TOPIC = "prohibited_topic"
    PROHIBITED_WORD = "prohibited_word"
    DURATION = "duration"
    CALL_TO_ACTION = "call_to_action"
    BRAND_VOICE = "brand_voice"
    VISUAL_REQUIREMENT = "visual_requirement"


class CampaignRuleSeverity(str, Enum):
    CRITICAL = "critical"  # Violating this fails QA immediately
    WARNING = "warning"    # Violating this lowers virality score but permits review


class BoundingBox(BaseModel):
    """Document bounding box for source-span provenance."""
    model_config = ConfigDict(frozen=True)

    page_no: int = Field(..., ge=1, description="1-indexed page number in the PDF")
    left: float = Field(..., ge=0.0, description="Left coordinate in normalized or points space")
    top: float = Field(..., ge=0.0, description="Top coordinate")
    right: float = Field(..., ge=0.0, description="Right coordinate")
    bottom: float = Field(..., ge=0.0, description="Bottom coordinate")


class CampaignRule(BaseModel):
    """Individual campaign requirement or constraint with provenance."""
    model_config = ConfigDict(frozen=True)

    rule_id: str = Field(..., min_length=1, max_length=64, description="Unique identifier for rule")
    category: CampaignRuleCategory
    severity: CampaignRuleSeverity = CampaignRuleSeverity.CRITICAL
    description: str = Field(..., min_length=3, description="Human-readable rule specification")
    exact_match_patterns: List[str] = Field(default_factory=list, description="Regex/exact words if applicable")
    provenance: Optional[BoundingBox] = None


class CampaignSpec(BaseModel):
    """Normalized structured specification extracted from Campaign PDF."""
    model_config = ConfigDict(frozen=True)

    schema_version: str = Field(default=SCHEMA_VERSION, description="Schema version")
    campaign_id: str = Field(..., min_length=1, max_length=128, description="Deterministic campaign ID")
    campaign_name: str = Field(..., min_length=1, max_length=256)
    target_audience: str = Field(default="General", max_length=512)
    min_duration_seconds: float = Field(default=30.0, gt=0.0, le=180.0)
    max_duration_seconds: float = Field(default=60.0, gt=0.0, le=180.0)
    rules: List[CampaignRule] = Field(default_factory=list)
    required_cta_text: Optional[str] = Field(default=None, max_length=256)
    raw_pdf_storage_key: Optional[str] = Field(default=None, description="Logical storage key for source PDF")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def validate_durations(self) -> None:
        if self.min_duration_seconds > self.max_duration_seconds:
            raise ValueError(
                f"min_duration_seconds ({self.min_duration_seconds}) cannot exceed "
                f"max_duration_seconds ({self.max_duration_seconds})"
            )
