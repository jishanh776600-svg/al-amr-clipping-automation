"""AL AMR Campaign Rule & Hook Enforcement Engine.

Integrates structured campaign briefs, deterministic token/pause validation,
hook quality scoring, Call-to-Action detection, and explainable candidate ranking.
"""

from __future__ import annotations

from .cta import CtaAnalysis, analyze_cta
from .density import DensityAnalysis, analyze_speech_density_and_silence
from .evaluator import CampaignEvaluator
from .hook import HookAnalysis, analyze_hook
from .models import CampaignBrief, CandidateEvaluation, RuleResult
from .ranking import rank_and_filter_candidates

__all__ = [
    "CampaignBrief",
    "CandidateEvaluation",
    "CampaignEvaluator",
    "CtaAnalysis",
    "DensityAnalysis",
    "HookAnalysis",
    "RuleResult",
    "analyze_cta",
    "analyze_hook",
    "analyze_speech_density_and_silence",
    "rank_and_filter_candidates",
]
