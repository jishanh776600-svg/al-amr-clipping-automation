"""Rendering and Subtitle Data Contracts."""

from datetime import datetime, timezone
from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict
from clipping.core.constants import SCHEMA_VERSION, TARGET_WIDTH, TARGET_HEIGHT
from clipping.contracts.perception import WordTimestamp
from clipping.contracts.director import ReframePlan


class SubtitleEvent(BaseModel):
    """Timed subtitle display event with word-level highlight intervals."""
    model_config = ConfigDict(frozen=True)

    start_time: float = Field(..., ge=0.0, description="Start timestamp relative to clip")
    end_time: float = Field(..., ge=0.0, description="End timestamp relative to clip")
    text: str = Field(..., min_length=1)
    words: List[WordTimestamp] = Field(default_factory=list)
    style_preset: str = Field(default="kinetic_gold", max_length=64)
    pos_x: int = Field(default=540, ge=0, le=TARGET_WIDTH)
    pos_y: int = Field(default=1500, ge=0, le=TARGET_HEIGHT)


class RenderJob(BaseModel):
    """Specification for rendering a finished 9:16 vertical short."""
    model_config = ConfigDict(frozen=True)

    schema_version: str = Field(default=SCHEMA_VERSION)
    job_id: str = Field(..., min_length=1, max_length=128)
    clip_id: str = Field(..., min_length=1, max_length=128)
    source_video_storage_key: str = Field(..., description="Logical key for master video")
    reframe_plan: ReframePlan
    subtitle_ass_storage_key: Optional[str] = Field(default=None, description="Logical key for ASS script")
    output_storage_key: str = Field(..., description="Logical destination key for final MP4")
    target_width: int = Field(default=TARGET_WIDTH, gt=0)
    target_height: int = Field(default=TARGET_HEIGHT, gt=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RenderOutput(BaseModel):
    """Result of a completed rendering operation."""
    model_config = ConfigDict(frozen=True)

    clip_id: str = Field(..., min_length=1, max_length=128)
    output_storage_key: str = Field(..., description="Logical key where rendered MP4 is stored")
    duration_seconds: float = Field(..., gt=0.0)
    file_size_bytes: int = Field(..., ge=0)
    render_time_seconds: float = Field(..., ge=0.0)
    completed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
