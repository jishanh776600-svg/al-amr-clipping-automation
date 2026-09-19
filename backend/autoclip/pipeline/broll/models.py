"""Data models for Semantic Visual Matching, B-Roll, and Edit Decision Lists (EDL)."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class PresentationMode(str, Enum):
    FULL_SCREEN = "FULL_SCREEN"
    PARTIAL_OVERLAY = "PARTIAL_OVERLAY"


class VisualType(str, Enum):
    STOCK_VIDEO = "STOCK_VIDEO"
    STOCK_PHOTO = "STOCK_PHOTO"
    DASHBOARD = "DASHBOARD"
    CHART = "CHART"
    PRODUCT_LISTING = "PRODUCT_LISTING"
    DOCUMENT = "DOCUMENT"
    ARCHIVE_IMAGE = "ARCHIVE_IMAGE"
    SOURCE_FOOTAGE = "SOURCE_FOOTAGE"


@dataclass
class SemanticConceptDefinition:
    """Catalog entry defining how a concept should be understood and visually presented."""
    concept_id: str
    primary_keywords: list[str]
    context_indicators: list[str]
    forbidden_figurative_phrases: list[str]
    default_presentation_mode: PresentationMode
    default_visual_type: VisualType
    search_queries: list[str]
    description: str


@dataclass
class SemanticVisualCue:
    """A semantic moment identified from the transcript requiring visual evidence."""
    cue_id: str
    concept: str
    trigger_phrase: str
    trigger_word: str
    start_s: float
    end_s: float
    preferred_mode: PresentationMode
    visual_type: VisualType
    search_queries: list[str] = field(default_factory=list)
    is_literal: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_s(self) -> float:
        return max(0.0, self.end_s - self.start_s)


@dataclass
class VisualAsset:
    """A visual evidence asset candidate available for insertion."""
    asset_id: str
    file_path: Path
    visual_type: VisualType
    concept: str
    presentation_mode: PresentationMode
    width: int = 1080
    height: int = 1920
    duration_s: float = 5.0
    tags: list[str] = field(default_factory=list)
    source_provider: str = "local_vault"  # local_vault, generated_ui, pexels, pixabay
    license_type: str = "commercial_use"
    is_video: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RelevanceScore:
    """Multi-factor scoring breakdown for a candidate visual."""
    semantic_score: float      # 0.0 - 1.0: Contextual semantic relevance
    literal_score: float       # 0.0 - 1.0: Literal object presence
    quality_score: float       # 0.0 - 1.0: Resolution, clarity, absence of artifacts
    aspect_score: float        # 0.0 - 1.0: Suitability for 9:16 vertical presentation
    repetition_penalty: float  # 0.0 - 1.0: Penalty for recently used assets/concepts
    total_score: float         # Weighted combined score
    confidence_threshold: float = 0.75
    is_approved: bool = False
    rejection_reason: str = ""

    @property
    def overall_score(self) -> float:
        return self.total_score

    def to_dict(self) -> dict[str, Any]:
        return {
            "semantic_score": round(self.semantic_score, 3),
            "literal_score": round(self.literal_score, 3),
            "quality_score": round(self.quality_score, 3),
            "aspect_score": round(self.aspect_score, 3),
            "repetition_penalty": round(self.repetition_penalty, 3),
            "total_score": round(self.total_score, 3),
            "confidence_threshold": self.confidence_threshold,
            "is_approved": self.is_approved,
            "rejection_reason": self.rejection_reason,
        }


@dataclass
class EDLEntry:
    """A single timeline decision in the Edit Decision List."""
    entry_id: str
    start_s: float
    end_s: float
    presentation_mode: PresentationMode
    visual_type: VisualType
    asset_path: Path
    trigger_phrase: str
    concept: str
    relevance_score: RelevanceScore
    transition: str = "hard_cut"  # 100% hard cuts per reference style
    crop_x: int = 0
    crop_y: int = 0
    scale_w: int = 1080
    scale_h: int = 1920
    is_video: bool = False
    overlay_x_expr: str = "(W-w)/2"
    overlay_y_expr: str = "(H-h)*0.55"

    @property
    def duration_s(self) -> float:
        return max(0.0, self.end_s - self.start_s)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "start_s": round(self.start_s, 2),
            "end_s": round(self.end_s, 2),
            "duration_s": round(self.duration_s, 2),
            "presentation_mode": self.presentation_mode.value,
            "visual_type": self.visual_type.value,
            "asset_path": str(self.asset_path),
            "trigger_phrase": self.trigger_phrase,
            "concept": self.concept,
            "score": self.relevance_score.to_dict(),
            "transition": self.transition,
        }


@dataclass
class EditDecisionList:
    """The complete Edit Decision List for a clip."""
    clip_id: str
    clip_start_s: float
    clip_end_s: float
    entries: list[EDLEntry] = field(default_factory=list)
    fallbacks: list[dict[str, Any]] = field(default_factory=list)

    @property
    def total_evidence_duration(self) -> float:
        return sum(e.duration_s for e in self.entries)

    def to_dict(self) -> dict[str, Any]:
        return {
            "clip_id": self.clip_id,
            "clip_start_s": round(self.clip_start_s, 2),
            "clip_end_s": round(self.clip_end_s, 2),
            "entry_count": len(self.entries),
            "fallback_count": len(self.fallbacks),
            "total_evidence_duration": round(self.total_evidence_duration, 2),
            "entries": [e.to_dict() for e in self.entries],
            "fallbacks": self.fallbacks,
        }
