"""Document parsing package exports."""

from clipping.document.base import (
    CampaignDocumentParser,
    DocumentExtractionResult,
    ExtractedBlock,
    ExtractedTable,
)
from clipping.document.extractor import DeterministicRuleExtractor
from clipping.document.docling_parser import DoclingCampaignParser
from clipping.document.brief_engine import (
    BriefDocumentReader,
    BriefDeterministicExtractor,
    CampaignBriefIntelligenceEngine,
)
from clipping.document.ai_extractor import BriefAIExtractor

__all__ = [
    "CampaignDocumentParser",
    "DocumentExtractionResult",
    "ExtractedBlock",
    "ExtractedTable",
    "DeterministicRuleExtractor",
    "DoclingCampaignParser",
    "BriefDocumentReader",
    "BriefDeterministicExtractor",
    "CampaignBriefIntelligenceEngine",
    "BriefAIExtractor",
]
