"""Structured Campaign Brief and Evaluation Models for AL AMR.

Defines the configuration contract for campaign-guided clipping,
individual rule verification results, and structured candidate evaluations.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal
from pydantic import BaseModel, Field


def generate_campaign_id() -> str:
    return uuid.uuid4().hex[:12]


class CampaignBrief(BaseModel):
    """Structured campaign brief defining requirements and ranking preferences."""

    # Identity
    campaign_id: str = Field(default_factory=generate_campaign_id)
    name: str = "Default Campaign"
    description: str = ""

    # Content
    topic_context: str = ""
    target_audience: str = ""
    required_topics: list[str] = Field(default_factory=list)
    required_concepts: list[str] = Field(default_factory=list)
    optional_keywords: list[str] = Field(default_factory=list)
    banned_words: list[str] = Field(default_factory=list)
    banned_topics: list[str] = Field(default_factory=list)

    # Clip Constraints
    minimum_duration: float = Field(default=20.0, gt=0)
    maximum_duration: float = Field(default=90.0, gt=0)
    preferred_duration: float | None = Field(default=None, gt=0)
    maximum_candidates: int = Field(default=10, ge=1, le=50)

    # Hook Rules
    hook_required: bool = True
    hook_window_seconds: float = Field(default=2.5, gt=0)
    minimum_hook_score: float = Field(default=6.0, ge=0.0, le=10.0)
    hook_types: list[str] = Field(default_factory=list)

    # Engagement & Virality Thresholds (0.0 to 10.0)
    minimum_viral_score: float = Field(default=5.0, ge=0.0, le=10.0)
    minimum_emotional_score: float = Field(default=0.0, ge=0.0, le=10.0)
    minimum_curiosity_score: float = Field(default=0.0, ge=0.0, le=10.0)
    minimum_clarity_score: float = Field(default=0.0, ge=0.0, le=10.0)

    # Call to Action (CTA) Rules
    cta_required: bool = False
    cta_types: list[str] = Field(default_factory=list)
    cta_window_seconds: float = Field(default=5.0, gt=0)
    minimum_cta_score: float = Field(default=5.0, ge=0.0, le=10.0)

    # Content Quality & Speech Density
    maximum_silence_seconds: float = Field(default=1.5, ge=0.1)
    minimum_transcript_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    minimum_content_density: float = Field(default=1.2, ge=0.1)  # words per second

    # Style & Delivery
    tone: str = "engaging"
    pacing: str = "dynamic"
    preferred_speaker_count: int | None = Field(default=None, ge=1)

    # Output Specs
    aspect_ratio: Literal["9:16", "1:1", "16:9"] = "9:16"
    caption_preset: str = "bold_pop"
    output_count: int = Field(default=5, ge=1, le=50)


class RuleResult(BaseModel):
    """Detailed result of a single rule check."""

    name: str
    passed: bool
    score: float = 10.0
    details: str = ""
    is_hard: bool = True


class CandidateEvaluation(BaseModel):
    """Full campaign evaluation outcome for a single candidate highlight."""

    candidate_id: str
    clip_id: str = ""
    campaign_id: str = ""
    approved: bool = True
    final_score: float = 0.0
    base_viral_score: float = 0.0
    hook_score: float = 0.0
    cta_score: float = 0.0
    density_score: float = 0.0
    hard_failures: list[str] = Field(default_factory=list)
    soft_warnings: list[str] = Field(default_factory=list)
    rule_results: dict[str, Any] = Field(default_factory=dict)

    @property
    def viral_score(self) -> float:
        return self.base_viral_score
