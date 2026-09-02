"""Configuration model for Clip Discovery, Scoring & Selection."""

from pydantic import BaseModel, Field, ConfigDict


class ClipDiscoveryConfig(BaseModel):
    """Configuration parameters for candidate windowing, heuristic scoring, and selection."""
    model_config = ConfigDict(frozen=True)

    # Duration Bounds (Seconds)
    min_duration_seconds: float = Field(default=20.0, ge=10.0, le=45.0)
    max_duration_seconds: float = Field(default=60.0, ge=30.0, le=180.0)
    preferred_min_duration: float = Field(default=25.0, ge=15.0)
    preferred_max_duration: float = Field(default=50.0, le=90.0)

    # Selection Yield Policy (5-10 Clips)
    min_clips_target: int = Field(default=5, ge=1, le=20)
    max_clips_target: int = Field(default=10, ge=1, le=50)
    quality_threshold: float = Field(default=55.0, ge=0.0, le=100.0, description="Minimum score to be considered publishable")

    # Deduplication & Spacing
    iou_dedup_threshold: float = Field(default=0.50, ge=0.1, le=0.9, description="Temporal IoU overlap suppression threshold")
    text_sim_dedup_threshold: float = Field(default=0.65, ge=0.2, le=0.95, description="Token Jaccard similarity suppression threshold")
    temporal_spacing_seconds: float = Field(default=15.0, ge=0.0, description="Minimum separation between clip start times")

    # Component Scoring Weights (Sum = 1.0)
    weight_hook: float = Field(default=0.25, ge=0.0, le=1.0)
    weight_completeness: float = Field(default=0.20, ge=0.0, le=1.0)
    weight_curiosity: float = Field(default=0.15, ge=0.0, le=1.0)
    weight_specificity: float = Field(default=0.15, ge=0.0, le=1.0)
    weight_emotion: float = Field(default=0.10, ge=0.0, le=1.0)
    weight_standalone: float = Field(default=0.10, ge=0.0, le=1.0)
    weight_visual: float = Field(default=0.05, ge=0.0, le=1.0)

    # Penalties Scaling
    penalty_filler_scale: float = Field(default=4.0, ge=0.0, le=20.0)
    penalty_silence_scale: float = Field(default=5.0, ge=0.0, le=20.0)
    penalty_repetition_scale: float = Field(default=4.0, ge=0.0, le=20.0)
    penalty_boundary_scale: float = Field(default=8.0, ge=0.0, le=25.0)
