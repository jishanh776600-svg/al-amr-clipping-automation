"""AL AMR Campaign Rule & Hook Enforcement Engine.

Integrates structured campaign briefs, deterministic token/pause validation,
hook quality scoring, Call-to-Action detection, and explainable candidate ranking.
"""

from __future__ import annotations

from .candidate_discovery import (
    CandidateDiscoveryEngine,
    CandidateScore,
    NarrativeMilestones,
    candidates_to_clips,
)
from .clip_assembly import (
    BoundaryOptimization,
    ClipAssemblyEngine,
    PreRenderQualityGate,
    QualityGateResult,
    SmartBoundaryEngine,
    specifications_to_clips,
)
from .cta import CtaAnalysis, analyze_cta
from .density import DensityAnalysis, analyze_speech_density_and_silence
from .evaluator import CampaignEvaluator
from .hook import HookAnalysis, analyze_hook
from .models import CampaignBrief, CandidateEvaluation, RuleResult
from .models_intelligence import (
    CampaignConflict,
    CampaignSpecification,
    IngestedDocument,
    RequirementItem,
)
from .normalizer import CampaignNormalizer
from .ranking import rank_and_filter_candidates
from .url_extractor import ExtractedUrlContent, extract_campaign_url

__all__ = [
    "BoundaryOptimization",
    "CampaignBrief",
    "CandidateDiscoveryEngine",
    "CandidateEvaluation",
    "CandidateScore",
    "CampaignEvaluator",
    "CampaignConflict",
    "CampaignNormalizer",
    "CampaignSpecification",
    "ClipAssemblyEngine",
    "CtaAnalysis",
    "DensityAnalysis",
    "ExtractedUrlContent",
    "HookAnalysis",
    "IngestedDocument",
    "NarrativeMilestones",
    "PreRenderQualityGate",
    "QualityGateResult",
    "RequirementItem",
    "RuleResult",
    "SmartBoundaryEngine",
    "analyze_cta",
    "analyze_hook",
    "analyze_speech_density_and_silence",
    "candidates_to_clips",
    "extract_campaign_url",
    "rank_and_filter_candidates",
    "specifications_to_clips",
]
