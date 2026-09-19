"""Semantic Visual Matching & Contextual Evidence B-Roll Engine."""
from .card_generator import EvidenceCardGenerator
from .engine import SemanticBrollEngine
from .models import (
    EDLEntry,
    EditDecisionList,
    PresentationMode,
    RelevanceScore,
    SemanticVisualCue,
    VisualAsset,
    VisualType,
)
from .scorer import VisualRelevanceScorer
from .semantic_parser import ContextualSemanticParser
from .vault import VisualAssetVault

MultiFactorRelevanceScorer = VisualRelevanceScorer

__all__ = [
    "SemanticBrollEngine",
    "ContextualSemanticParser",
    "VisualRelevanceScorer",
    "MultiFactorRelevanceScorer",
    "EvidenceCardGenerator",
    "VisualAssetVault",
    "PresentationMode",
    "VisualType",
    "SemanticVisualCue",
    "VisualAsset",
    "RelevanceScore",
    "EDLEntry",
    "EditDecisionList",
]
