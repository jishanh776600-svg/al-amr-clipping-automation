"""Campaign Requirements Data Models and Structured Schema (Step 2/5)."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ConfigDict


class RequirementModality(str, Enum):
    REQUIRED = "REQUIRED"
    OPTIONAL = "OPTIONAL"
    PREFERRED = "PREFERRED"
    PROHIBITED = "PROHIBITED"
    UNKNOWN = "UNKNOWN"


class RequirementOverride(BaseModel):
    """Audit record capturing an operator override of an extracted requirement."""
    model_config = ConfigDict(frozen=True)

    field_path: str = Field(..., description="Dot-notated field path e.g. 'clips.min_duration_seconds'")
    original_value: Any = Field(..., description="Original extracted value from the brief")
    override_value: Any = Field(..., description="New value specified by the operator")
    operator: str = Field(..., description="Identifier of operator who performed the override")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    reason: Optional[str] = Field(default=None, description="Operator reason or rationale")


class ExtractionMetadata(BaseModel):
    """Provenance and execution metrics of the extraction process."""
    extracted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    engine: str = Field(default="deterministic_hybrid", description="Extraction engine used (e.g. 'deterministic', 'llm_validated')")
    source_filename: Optional[str] = None
    source_format: str = Field(default="txt", description="Format of source document: pdf, txt, or md")
    num_pages: int = Field(default=1, ge=1)
    is_image_only: bool = False
    extraction_status: str = Field(default="SUCCESS", description="SUCCESS, NEEDS_REVIEW, or FAILED")
    review_flag: Optional[str] = None
    model_used: Optional[str] = None
    error_message: Optional[str] = None
    confidence_score: float = Field(default=1.0, ge=0.0, le=1.0)



class CampaignIdentityRequirements(BaseModel):
    """1. Campaign identity requirements."""
    campaign_name: Optional[str] = None
    campaign_id: Optional[str] = None
    campaign_description: Optional[str] = None
    raw_title: Optional[str] = None


class SourceRequirements(BaseModel):
    """2. Source video requirements."""
    permitted_source_videos: List[str] = Field(default_factory=list)
    permitted_source_urls: List[str] = Field(default_factory=list)
    source_urls: List[str] = Field(default_factory=list)
    source_footage_restrictions: List[str] = Field(default_factory=list)
    source_restrictions: List[str] = Field(default_factory=list)
    prohibited_content: List[str] = Field(default_factory=list)
    specific_footage_required: bool = False
    raw_source_text: Optional[str] = None


class ClipRequirements(BaseModel):
    """3. Clip duration, count, and framing requirements."""
    clip_count_required: Optional[int] = Field(default=None, ge=1)
    min_duration_seconds: Optional[float] = Field(default=None, ge=1.0)
    max_duration_seconds: Optional[float] = Field(default=None, ge=1.0)
    preferred_duration_seconds: Optional[float] = Field(default=None, ge=1.0)
    target_duration_seconds: Optional[float] = Field(default=None, ge=1.0)
    aspect_ratio: str = Field(default="9:16", description="Target aspect ratio e.g. 9:16")
    resolution: Optional[str] = Field(default="1080x1920")
    resolution_min: Optional[str] = Field(default=None)
    fps: Optional[int] = Field(default=None, ge=1)
    duration_modality: RequirementModality = RequirementModality.UNKNOWN


class ContentRequirements(BaseModel):
    """4. Content topics, claims, and hooks requirements."""
    allowed_topics: List[str] = Field(default_factory=list)
    prohibited_topics: List[str] = Field(default_factory=list)
    required_talking_points: List[str] = Field(default_factory=list)
    prohibited_claims: List[str] = Field(default_factory=list)
    required_hooks_or_formats: List[str] = Field(default_factory=list)
    raw_content_rules: List[str] = Field(default_factory=list)


class BrandingRequirements(BaseModel):
    """5. Branding, watermark, and caption requirements."""
    required_logo: Optional[str] = None
    watermark_requirements: Optional[str] = None
    required_watermark: Optional[str] = None
    branding_rules: List[str] = Field(default_factory=list)
    caption_subtitle_requirements: Optional[str] = None
    watermark_modality: RequirementModality = RequirementModality.UNKNOWN


class TextRequirements(BaseModel):
    """6. Text, hashtags, and CTA requirements."""
    required_hashtags: List[str] = Field(default_factory=list)
    prohibited_hashtags: List[str] = Field(default_factory=list)
    caption_requirements: Optional[str] = None
    title_requirements: Optional[str] = None
    call_to_action: Optional[str] = None
    cta_modality: RequirementModality = RequirementModality.UNKNOWN
    required_keywords: List[str] = Field(default_factory=list)
    mention_handles: List[str] = Field(default_factory=list)


class PlatformRequirements(BaseModel):
    """7. Target platform requirements."""
    platforms: List[str] = Field(default_factory=list, description="Target platforms e.g. ['youtube_shorts', 'instagram_reels']")
    target_platforms: List[str] = Field(default_factory=list)
    preferred_platform: Optional[str] = None
    raw_platform_notes: Optional[str] = None


class SubmissionRequirements(BaseModel):
    """8. Submission metadata, caps, and deadlines."""
    submission_count: Optional[int] = Field(default=None, ge=1)
    deadline: Optional[str] = None
    submission_deadline: Optional[str] = None
    submission_platform: Optional[str] = None
    submission_url_or_process: Optional[str] = None
    naming_requirements: Optional[str] = None
    campaign_specific_metadata: Dict[str, Any] = Field(default_factory=dict)



class MonetizationRequirements(BaseModel):
    """9. Monetization, CPM, and budget constraints."""
    cpm_rate: Optional[float] = Field(default=None, ge=0.0)
    payout_info: Optional[str] = None
    total_budget: Optional[float] = Field(default=None, ge=0.0)
    min_views_required: Optional[int] = Field(default=None, ge=0)
    eligibility_rules: List[str] = Field(default_factory=list)


class AdditionalRules(BaseModel):
    """10. Other explicit campaign rules."""
    rules: List[str] = Field(default_factory=list)
    unclassified_notes: List[str] = Field(default_factory=list)


class CampaignRequirements(BaseModel):
    """
    Unified structured representation of extracted campaign requirements from Whop / briefs.
    Supports provenance, ambiguity preservation, and operator overrides with audit trail.
    """
    identity: CampaignIdentityRequirements = Field(default_factory=CampaignIdentityRequirements)
    source: SourceRequirements = Field(default_factory=SourceRequirements)
    clips: ClipRequirements = Field(default_factory=ClipRequirements)
    content: ContentRequirements = Field(default_factory=ContentRequirements)
    branding: BrandingRequirements = Field(default_factory=BrandingRequirements)
    text: TextRequirements = Field(default_factory=TextRequirements)
    platform: PlatformRequirements = Field(default_factory=PlatformRequirements)
    submission: SubmissionRequirements = Field(default_factory=SubmissionRequirements)
    monetization: MonetizationRequirements = Field(default_factory=MonetizationRequirements)
    additional_rules: AdditionalRules = Field(default_factory=AdditionalRules)
    metadata: ExtractionMetadata = Field(default_factory=ExtractionMetadata)

    # Operator overrides and audit history
    overrides: List[RequirementOverride] = Field(default_factory=list)

    def apply_override(
        self,
        field_path: str,
        override_value: Any,
        operator: str,
        reason: Optional[str] = None
    ) -> None:
        """
        Records an operator override without destroying the original extracted value.
        Updates the active field and logs the audit record.
        """
        original_value = self._get_nested_field(field_path)
        override = RequirementOverride(
            field_path=field_path,
            original_value=original_value,
            override_value=override_value,
            operator=operator,
            reason=reason,
        )
        self.overrides.append(override)
        self._set_nested_field(field_path, override_value)

    def _get_nested_field(self, field_path: str) -> Any:
        parts = field_path.split(".")
        current: Any = self
        for part in parts:
            if hasattr(current, part):
                current = getattr(current, part)
            elif isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        return current

    def _set_nested_field(self, field_path: str, value: Any) -> None:
        parts = field_path.split(".")
        current: Any = self
        for part in parts[:-1]:
            if hasattr(current, part):
                current = getattr(current, part)
            elif isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return
        final_attr = parts[-1]
        if hasattr(current, final_attr):
            setattr(current, final_attr, value)
        elif isinstance(current, dict):
            current[final_attr] = value
