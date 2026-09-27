"""Data models for Step 23 Campaign-Aware SEO and Per-Short Metadata."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ComplianceStatus(StrEnum):
    SEO_PASS = "SEO_PASS"
    SEO_WARN = "SEO_WARN"
    SEO_REJECT = "SEO_REJECT"


@dataclass
class CampaignSEORequirements:
    """Normalized SEO rules extracted from authoritative CampaignSpecification."""

    campaign_id: str = ""
    campaign_title: str = ""
    brand_name: str = ""
    required_phrases: list[str] = field(default_factory=list)
    required_mentions: list[str] = field(default_factory=list)
    required_hashtags: list[str] = field(default_factory=list)
    prohibited_terms: list[str] = field(default_factory=list)
    cta_required: bool = False
    cta_instructions: list[str] = field(default_factory=list)
    campaign_url: str | None = None
    title_patterns: list[str] = field(default_factory=list)
    description_guidelines: list[str] = field(default_factory=list)
    platforms: list[str] = field(default_factory=lambda: ["youtube", "instagram", "telegram"])
    min_title_length: int = 5
    max_title_length: int = 100
    max_description_length: int = 2000

    def to_dict(self) -> dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "campaign_title": self.campaign_title,
            "brand_name": self.brand_name,
            "required_phrases": self.required_phrases,
            "required_mentions": self.required_mentions,
            "required_hashtags": self.required_hashtags,
            "prohibited_terms": self.prohibited_terms,
            "cta_required": self.cta_required,
            "cta_instructions": self.cta_instructions,
            "campaign_url": self.campaign_url,
            "title_patterns": self.title_patterns,
            "description_guidelines": self.description_guidelines,
            "platforms": self.platforms,
            "min_title_length": self.min_title_length,
            "max_title_length": self.max_title_length,
            "max_description_length": self.max_description_length,
        }


@dataclass
class ComplianceResult:
    """Outcome of evaluating metadata against Campaign Requirements and platform standards."""

    status: ComplianceStatus
    score: float
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    matched_requirements: dict[str, Any] = field(default_factory=dict)

    @property
    def is_publish_ready(self) -> bool:
        return self.status in (ComplianceStatus.SEO_PASS, ComplianceStatus.SEO_WARN)

    @property
    def is_compliant(self) -> bool:
        return self.is_publish_ready

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "score": self.score,
            "errors": self.errors,
            "warnings": self.warnings,
            "matched_requirements": self.matched_requirements,
            "is_publish_ready": self.is_publish_ready,
        }


@dataclass
class PlatformSEORules:
    """Platform-specific SEO rules extracted directly from the campaign document."""

    title_rules: list[str] = field(default_factory=list)
    description_rules: list[str] = field(default_factory=list)
    caption_rules: list[str] = field(default_factory=list)
    hashtag_rules: list[str] = field(default_factory=list)
    keyword_rules: list[str] = field(default_factory=list)
    tag_rules: list[str] = field(default_factory=list)
    link_rules: list[str] = field(default_factory=list)
    mention_rules: list[str] = field(default_factory=list)
    cta_rules: list[str] = field(default_factory=list)
    formatting_rules: list[str] = field(default_factory=list)
    character_limits: dict[str, int] = field(default_factory=dict)
    required_phrases: list[str] = field(default_factory=list)
    prohibited_terms: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title_rules": self.title_rules,
            "description_rules": self.description_rules,
            "caption_rules": self.caption_rules,
            "hashtag_rules": self.hashtag_rules,
            "keyword_rules": self.keyword_rules,
            "tag_rules": self.tag_rules,
            "link_rules": self.link_rules,
            "mention_rules": self.mention_rules,
            "cta_rules": self.cta_rules,
            "formatting_rules": self.formatting_rules,
            "character_limits": self.character_limits,
            "required_phrases": self.required_phrases,
            "prohibited_terms": self.prohibited_terms,
            "links": self.links,
        }


@dataclass
class GlobalSEORules:
    """Cross-platform overarching brand, language, and compliance rules from campaign document."""

    tone: str = ""
    language: str = "en"
    prohibited_terms: list[str] = field(default_factory=list)
    required_terms: list[str] = field(default_factory=list)
    brand_terms: list[str] = field(default_factory=list)
    cta_rules: list[str] = field(default_factory=list)
    link_rules: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tone": self.tone,
            "language": self.language,
            "prohibited_terms": self.prohibited_terms,
            "required_terms": self.required_terms,
            "brand_terms": self.brand_terms,
            "cta_rules": self.cta_rules,
            "link_rules": self.link_rules,
            "links": self.links,
        }


@dataclass
class CampaignSEOSpec:
    """Authoritative structured campaign SEO specification separating Global, YouTube, and Instagram."""

    campaign_id: str = ""
    campaign_title: str = ""
    brand_name: str = ""
    global_rules: GlobalSEORules = field(default_factory=GlobalSEORules)
    youtube_rules: PlatformSEORules = field(default_factory=PlatformSEORules)
    instagram_rules: PlatformSEORules = field(default_factory=PlatformSEORules)

    def to_dict(self) -> dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "campaign_title": self.campaign_title,
            "brand_name": self.brand_name,
            "global_rules": self.global_rules.to_dict(),
            "youtube_rules": self.youtube_rules.to_dict(),
            "instagram_rules": self.instagram_rules.to_dict(),
        }


@dataclass
class YouTubeMetadata:
    """Independently generated and verified YouTube Shorts metadata package."""

    title: str = ""
    description: str = ""
    hashtags: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    cta: str = ""
    compliance_score: float = 100.0  # Document compliance (0-100)
    optimization_score: float = 100.0  # Search/discoverability optimization (0-100)
    compliance_status: str = "PASS"  # PASS or FAIL
    rule_evaluations: dict[str, bool] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_compliant(self) -> bool:
        return self.compliance_status == "PASS" and len(self.errors) == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "description": self.description,
            "hashtags": self.hashtags,
            "tags": self.tags,
            "links": self.links,
            "cta": self.cta,
            "compliance_score": self.compliance_score,
            "optimization_score": self.optimization_score,
            "compliance_status": self.compliance_status,
            "rule_evaluations": self.rule_evaluations,
            "errors": self.errors,
            "warnings": self.warnings,
            "is_compliant": self.is_compliant,
        }


@dataclass
class InstagramMetadata:
    """Independently generated and verified Instagram Reels metadata package."""

    caption: str = ""
    first_line_hook: str = ""
    hashtags: list[str] = field(default_factory=list)
    mentions: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    cta: str = ""
    compliance_score: float = 100.0  # Document compliance (0-100)
    optimization_score: float = 100.0  # Feed retention/engagement optimization (0-100)
    compliance_status: str = "PASS"  # PASS or FAIL
    rule_evaluations: dict[str, bool] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_compliant(self) -> bool:
        return self.compliance_status == "PASS" and len(self.errors) == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "caption": self.caption,
            "first_line_hook": self.first_line_hook,
            "hashtags": self.hashtags,
            "mentions": self.mentions,
            "links": self.links,
            "cta": self.cta,
            "compliance_score": self.compliance_score,
            "optimization_score": self.optimization_score,
            "compliance_status": self.compliance_status,
            "rule_evaluations": self.rule_evaluations,
            "errors": self.errors,
            "warnings": self.warnings,
            "is_compliant": self.is_compliant,
        }


@dataclass
class DualPlatformMetadata:
    """Dual platform metadata container guaranteeing independent YouTube and Instagram metadata."""

    youtube: YouTubeMetadata = field(default_factory=YouTubeMetadata)
    instagram: InstagramMetadata = field(default_factory=InstagramMetadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "youtube": self.youtube.to_dict(),
            "instagram": self.instagram.to_dict(),
        }

