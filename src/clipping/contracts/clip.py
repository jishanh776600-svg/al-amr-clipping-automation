"""Clip Discovery, Semantic Scoring, Ranking, and Selection Contracts."""

from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field, ConfigDict
from clipping.core.constants import SCHEMA_VERSION
from clipping.contracts.perception import WordTimestamp


class ClipBoundary(BaseModel):
    """Temporal and token boundary of a candidate clip."""
    model_config = ConfigDict(frozen=True)

    start_time: float = Field(..., ge=0.0)
    end_time: float = Field(..., ge=0.0)
    duration: float = Field(..., gt=0.0)
    start_word_idx: int = Field(ge=0)
    end_word_idx: int = Field(ge=0)


class ClipCandidate(BaseModel):
    """Discovered candidate clip window with transcript boundaries."""
    model_config = ConfigDict(frozen=True)

    schema_version: str = Field(default=SCHEMA_VERSION)
    candidate_id: str = Field(..., min_length=1, max_length=128)
    source_video_id: str = Field(..., min_length=1, max_length=128)
    campaign_id: str = Field(default="default_campaign", min_length=1, max_length=128)
    start_time: float = Field(..., ge=0.0)
    end_time: float = Field(..., ge=0.0)
    duration: float = Field(..., gt=0.0)
    transcript_text: str = Field(..., min_length=5)
    words: List[WordTimestamp] = Field(default_factory=list)
    hook_sentence: str = Field(..., min_length=3)
    primary_speaker_id: Optional[str] = None
    speaker_ids: List[str] = Field(default_factory=list)
    scene_ids: List[int] = Field(default_factory=list)
    boundary: Optional[ClipBoundary] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def validate_bounds(self) -> None:
        if self.start_time >= self.end_time:
            raise ValueError(f"start_time ({self.start_time}) must be < end_time ({self.end_time})")


class CandidateScoreBreakdown(BaseModel):
    """Granular explainable breakdown of candidate quality scoring."""
    model_config = ConfigDict(frozen=True)

    hook_score: float = Field(default=0.0, ge=0.0, le=100.0)
    completeness_score: float = Field(default=0.0, ge=0.0, le=100.0)
    curiosity_score: float = Field(default=0.0, ge=0.0, le=100.0)
    specificity_score: float = Field(default=0.0, ge=0.0, le=100.0)
    emotion_score: float = Field(default=0.0, ge=0.0, le=100.0)
    standalone_score: float = Field(default=0.0, ge=0.0, le=100.0)
    visual_score: float = Field(default=0.0, ge=0.0, le=100.0)
    
    # Penalties
    filler_penalty: float = Field(default=0.0, ge=0.0, le=50.0)
    silence_penalty: float = Field(default=0.0, ge=0.0, le=50.0)
    repetition_penalty: float = Field(default=0.0, ge=0.0, le=50.0)
    boundary_penalty: float = Field(default=0.0, ge=0.0, le=50.0)
    
    total_score: float = Field(..., ge=0.0, le=100.0, description="Weighted composite score 0-100")


class ClipScore(BaseModel):
    """Multi-dimensional engagement and candidate quality score."""
    model_config = ConfigDict(frozen=True)

    candidate_id: str = Field(..., min_length=1, max_length=128)
    hook_strength: float = Field(..., ge=0.0, le=100.0, description="Hook engagement 0-100")
    narrative_completeness: float = Field(..., ge=0.0, le=100.0)
    curiosity_factor: float = Field(..., ge=0.0, le=100.0)
    campaign_relevance: float = Field(default=100.0, ge=0.0, le=100.0)
    overall_virality_score: float = Field(..., ge=0.0, le=100.0, description="Candidate quality score 0-100")
    breakdown: Optional[CandidateScoreBreakdown] = None
    reasoning: str = Field(default="", max_length=2048)
    scored_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RankedCandidate(BaseModel):
    """Scored and ranked candidate clip with selection rationale."""
    model_config = ConfigDict(frozen=True)

    candidate: ClipCandidate
    score: ClipScore
    rank: int = Field(..., ge=1)
    selection_reason: str = Field(default="")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source_evidence: Dict[str, Any] = Field(default_factory=dict)
    is_selected: bool = False


class ClipSelectionResult(BaseModel):
    """Complete output of candidate generation, scoring, and diversity selection."""
    model_config = ConfigDict(frozen=True)

    schema_version: str = Field(default=SCHEMA_VERSION)
    source_video_id: str = Field(..., min_length=1)
    total_candidates_generated: int = Field(ge=0)
    quality_threshold: float = Field(ge=0.0, le=100.0)
    selected_clips: List[RankedCandidate] = Field(default_factory=list)
    rejected_clips: List[RankedCandidate] = Field(default_factory=list)
    selection_reasons: List[str] = Field(default_factory=list)
    coverage_metrics: Dict[str, Any] = Field(default_factory=dict)
    insufficient_candidate_warning: bool = False
    completed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ComplianceResult(BaseModel):
    """Compliance verification audit against CampaignSpec rules."""
    model_config = ConfigDict(frozen=True)

    candidate_id: str = Field(..., min_length=1, max_length=128)
    is_compliant: bool = Field(..., description="True if no CRITICAL rules are violated")
    violations: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    rule_references: List[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
