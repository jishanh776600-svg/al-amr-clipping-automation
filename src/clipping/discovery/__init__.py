"""Clip discovery and selection package exports."""

from clipping.discovery.config import ClipDiscoveryConfig
from clipping.discovery.windows import CandidateWindowGenerator
from clipping.discovery.scoring import DeterministicClipScorer
from clipping.discovery.dedup import CandidateDeduplicator, calculate_temporal_iou, calculate_text_jaccard
from clipping.discovery.selection import ClipSelector
from clipping.discovery.engine import ClipDiscoveryEngine
from clipping.discovery.exceptions import (
    DiscoveryError,
    WindowGenerationError,
    ScoringError,
    SelectionError,
)

__all__ = [
    "ClipDiscoveryConfig",
    "CandidateWindowGenerator",
    "DeterministicClipScorer",
    "CandidateDeduplicator",
    "calculate_temporal_iou",
    "calculate_text_jaccard",
    "ClipSelector",
    "ClipDiscoveryEngine",
    "DiscoveryError",
    "WindowGenerationError",
    "ScoringError",
    "SelectionError",
]
