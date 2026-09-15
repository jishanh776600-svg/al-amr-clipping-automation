"""Modular Campaign-Aware SEO and Per-Short Metadata Engine."""

from .models import CampaignSEORequirements, ComplianceResult, ComplianceStatus
from .extractor import extract_campaign_seo_requirements
from .quality_gate import MetadataQualityGate
from .engine import SEOEngine

__all__ = [
    "CampaignSEORequirements",
    "ComplianceResult",
    "ComplianceStatus",
    "extract_campaign_seo_requirements",
    "MetadataQualityGate",
    "SEOEngine",
]
