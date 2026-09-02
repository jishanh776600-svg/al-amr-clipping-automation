"""Subtitle Typography Styles, Presets, and Safe-Zone Configuration."""

from enum import Enum
from pydantic import BaseModel, Field, ConfigDict


class SubtitlePreset(str, Enum):
    KINETIC_GOLD = "kinetic_gold"
    NEON_CYAN = "neon_cyan"
    CLASSIC_WHITE = "classic_white"
    BOLD_YELLOW = "bold_yellow"


class SubtitleStyleConfig(BaseModel):
    """Configuration for ASS subtitle styling, karaoke effects, and safe zones."""
    model_config = ConfigDict(frozen=True)

    preset: SubtitlePreset = SubtitlePreset.KINETIC_GOLD
    font_name: str = "Arial Black"
    font_size: int = Field(default=68, ge=24, le=140)
    primary_color: str = Field(default="&H00FFFFFF", description="ASS BGR Hex for inactive words (White)")
    highlight_color: str = Field(default="&H0000D7FF", description="ASS BGR Hex for active spoken words (Gold #FFD700)")
    outline_color: str = Field(default="&H00000000", description="ASS BGR Hex for outline (Black)")
    outline_width: float = Field(default=4.5, ge=0.0, le=15.0)
    shadow_color: str = Field(default="&H80000000", description="ASS BGR Hex with 50% alpha")
    shadow_depth: float = Field(default=2.5, ge=0.0, le=10.0)
    alignment: int = Field(default=2, description="ASS alignment: 2 = Bottom-Center")
    margin_v: int = Field(default=420, ge=100, le=800, description="Vertical bottom margin for safe-zone")
    margin_l: int = Field(default=60, ge=20, le=300)
    margin_r: int = Field(default=60, ge=20, le=300)
    words_per_card: int = Field(default=3, ge=1, le=8, description="Maximum words displayed per subtitle card")

    @classmethod
    def from_preset(cls, preset: SubtitlePreset) -> "SubtitleStyleConfig":
        if preset == SubtitlePreset.KINETIC_GOLD:
            return cls(
                preset=preset,
                primary_color="&H00FFFFFF",  # White
                highlight_color="&H0000D7FF",  # Gold (#FFD700 in BGR)
                font_size=68,
                outline_width=4.5,
            )
        elif preset == SubtitlePreset.NEON_CYAN:
            return cls(
                preset=preset,
                primary_color="&H00FFFFFF",
                highlight_color="&H00FFFF00",  # Cyan (#00FFFF in BGR)
                font_size=68,
                outline_width=4.5,
            )
        elif preset == SubtitlePreset.BOLD_YELLOW:
            return cls(
                preset=preset,
                primary_color="&H00FFFFFF",
                highlight_color="&H0000FFFF",  # Yellow (#FFFF00 in BGR)
                font_size=68,
                outline_width=4.5,
            )
        else:  # CLASSIC_WHITE
            return cls(
                preset=preset,
                primary_color="&H00FFFFFF",
                highlight_color="&H00E0E0E0",
                font_size=60,
                outline_width=3.5,
            )
