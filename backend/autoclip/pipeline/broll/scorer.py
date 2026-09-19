"""Multi-Factor Visual Relevance Scorer & Confidence Gate.

Evaluates candidate visuals across semantic relevance, literal object matching,
image/video quality, aspect-ratio suitability, and visual repetition penalties.
Enforces a configurable confidence gate (default 0.75) where low-confidence
candidates are rejected in favor of A-roll + punch-in.
"""
from __future__ import annotations

import logging
from typing import Any

from .models import (
    PresentationMode,
    RelevanceScore,
    SemanticVisualCue,
    VisualAsset,
)

log = logging.getLogger(__name__)

DEFAULT_CONFIDENCE_THRESHOLD = 0.75


class VisualRelevanceScorer:
    """Evaluates candidate visuals against semantic cues and historical usage."""

    def __init__(
        self,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
        weights: dict[str, float] | None = None,
    ) -> None:
        self.confidence_threshold = confidence_threshold
        # Weights normalized: 0.35 + 0.25 + 0.20 + 0.20 = 1.00
        self.weights = weights or {
            "semantic": 0.35,
            "literal": 0.25,
            "quality": 0.20,
            "aspect": 0.20,
        }

    def score_asset(
        self,
        asset: VisualAsset,
        cue: SemanticVisualCue,
        recent_usages: list[Any] | None = None,
    ) -> RelevanceScore:
        """Alias for score_candidate accepting (asset, cue, recent_usages) argument order."""
        recent_ids = [u[0] if isinstance(u, (tuple, list)) else str(u) for u in (recent_usages or [])]
        return self.score_candidate(cue=cue, asset=asset, recent_asset_ids=recent_ids)

    def score_candidate(
        self,
        cue: SemanticVisualCue,
        asset: VisualAsset,
        recent_asset_ids: list[str] | None = None,
        recent_concepts: list[str] | None = None,
    ) -> RelevanceScore:
        """Scores a single candidate visual against a semantic cue."""
        recent_asset_ids = recent_asset_ids or []
        recent_concepts = recent_concepts or []

        # 1. Semantic Similarity Score
        # Match between cue concept/keywords and asset concept/tags
        semantic_score = 0.5
        if asset.concept == cue.concept:
            semantic_score = 1.0
        else:
            # Check overlap between asset tags and cue search queries/trigger phrase
            phrase_tokens = set(cue.trigger_phrase.lower().split())
            matching_tags = sum(1 for tag in asset.tags if tag.lower() in phrase_tokens)
            if matching_tags > 0:
                semantic_score = min(0.9, 0.5 + (matching_tags * 0.15))

        # 2. Literal Relevance Score
        # Does the visual literally depict the concrete concept?
        literal_score = 0.6
        if cue.is_literal:
            if asset.concept == cue.concept:
                literal_score = 1.0
            elif any(cue.trigger_word.lower() in tag.lower() for tag in asset.tags):
                literal_score = 0.85
        else:
            # Figurative / metaphorical visuals receive lower literal score
            literal_score = 0.4

        # 3. Visual Quality Score
        quality_score = 0.7
        if asset.width >= 1080 and asset.height >= 1920:
            quality_score = 1.0
        elif asset.width >= 720 and asset.height >= 1280:
            quality_score = 0.85
        elif asset.width < 720 or asset.height < 720:
            quality_score = 0.5

        # 4. Aspect Ratio Suitability Score
        aspect_score = 0.6
        if asset.height > asset.width:  # Native vertical 9:16
            aspect_score = 1.0
        elif asset.width == asset.height:  # 1:1 square
            aspect_score = 0.8
        elif asset.width > asset.height:  # 16:9 landscape
            # Landscape is acceptable if reframed/centered or partial overlay
            if cue.preferred_mode == PresentationMode.PARTIAL_OVERLAY:
                aspect_score = 0.95
            else:
                aspect_score = 0.75

        # 5. Visual Repetition Penalty
        repetition_penalty = 0.0
        # If the exact same asset was used recently in this clip
        if asset.asset_id in recent_asset_ids:
            repetition_penalty += 0.40

        # If the same concept was shown very recently
        if recent_concepts and recent_concepts[-1:] == [cue.concept]:
            repetition_penalty += 0.20

        # Weighted combination
        raw_score = (
            self.weights["semantic"] * semantic_score
            + self.weights["literal"] * literal_score
            + self.weights["quality"] * quality_score
            + self.weights["aspect"] * aspect_score
        )

        total_score = max(0.0, min(1.0, raw_score - repetition_penalty))

        # Confidence Gate Evaluation
        is_approved = total_score >= self.confidence_threshold
        rejection_reason = ""
        if not is_approved:
            if raw_score < self.confidence_threshold:
                rejection_reason = f"Low relevance score ({total_score:.2f} < {self.confidence_threshold:.2f})"
            else:
                rejection_reason = f"Penalized due to repetition ({repetition_penalty:.2f} penalty applied)"

        return RelevanceScore(
            semantic_score=semantic_score,
            literal_score=literal_score,
            quality_score=quality_score,
            aspect_score=aspect_score,
            repetition_penalty=repetition_penalty,
            total_score=total_score,
            confidence_threshold=self.confidence_threshold,
            is_approved=is_approved,
            rejection_reason=rejection_reason,
        )
