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
        return self.status in (ComplianceStatus.SEO_PASS, ComplianceStatus.SEO_WARN) and len(self.errors) == 0

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
