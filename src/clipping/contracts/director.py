"""Virtual Camera Director & Reframe Data Contracts."""

from enum import Enum
from typing import List
from pydantic import BaseModel, Field, ConfigDict
from clipping.core.constants import SCHEMA_VERSION, TARGET_WIDTH, TARGET_HEIGHT


class SpeakerLayout(str, Enum):
    SOLO = "solo"
    TWO_PERSON_SPLIT = "two_person_split"
    DYNAMIC_SWITCH = "dynamic_switch"
    WIDE_SHOT = "wide_shot"


class ReframeCropKeyframe(BaseModel):
    """Discrete crop coordinate keyframe for virtual camera director."""
    model_config = ConfigDict(frozen=True)

    timestamp: float = Field(..., ge=0.0, description="Timestamp in seconds relative to clip start")
    crop_x: int = Field(..., ge=0, description="Top-left pixel X in source coordinates")
    crop_y: int = Field(..., ge=0, description="Top-left pixel Y in source coordinates")
    crop_w: int = Field(..., gt=0, description="Crop box width in source coordinates")
    crop_h: int = Field(..., gt=0, description="Crop box height in source coordinates")
    layout_mode: SpeakerLayout = SpeakerLayout.SOLO


class ReframePlan(BaseModel):
    """Complete 9:16 reframing and virtual camera motion plan for a clip."""
    model_config = ConfigDict(frozen=True)

    schema_version: str = Field(default=SCHEMA_VERSION)
    clip_id: str = Field(..., min_length=1, max_length=128)
    source_width: int = Field(..., gt=0)
    source_height: int = Field(..., gt=0)
    target_width: int = Field(default=TARGET_WIDTH, gt=0)
    target_height: int = Field(default=TARGET_HEIGHT, gt=0)
    keyframes: List[ReframeCropKeyframe] = Field(..., min_length=1)
