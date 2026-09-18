"""Canonical Visual Filter Registry for AL AMR.

Provides 20+ production visual filters applied in FFmpeg before captions
without modifying source media.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class VisualFilter:
    id: str
    name: str
    description: str
    ffmpeg_expr: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
        }


# Canonical registry of 20+ visual filters
FILTERS: list[VisualFilter] = [
    VisualFilter(
        id="original",
        name="Original",
        description="Source natural colors without grading",
        ffmpeg_expr="null",
    ),
    VisualFilter(
        id="black_and_white",
        name="Black & White",
        description="Balanced black and white conversion",
        ffmpeg_expr="hue=s=0",
    ),
    VisualFilter(
        id="grayscale",
        name="Grayscale",
        description="Linear luminance grayscale conversion",
        ffmpeg_expr="format=gray,format=yuv420p",
    ),
    VisualFilter(
        id="vintage",
        name="Vintage",
        description="Warm nostalgic tones with subtle fade",
        ffmpeg_expr="curves=vintage,colorchannelmixer=rr=0.95:gg=0.88:bb=0.78",
    ),
    VisualFilter(
        id="warm",
        name="Warm",
        description="Golden sun-drenched warm color temperature",
        ffmpeg_expr="colorbalance=rs=0.12:gs=0.04:bs=-0.12:rm=0.08:gm=0.02:bm=-0.08",
    ),
    VisualFilter(
        id="cool",
        name="Cool",
        description="Crisp modern bluish cool color temperature",
        ffmpeg_expr="colorbalance=rs=-0.08:gs=0.02:bs=0.12:rm=-0.06:gm=0.02:bm=0.10",
    ),
    VisualFilter(
        id="high_contrast",
        name="High Contrast",
        description="Punchy shadows and vibrant highlights",
        ffmpeg_expr="eq=contrast=1.3:brightness=-0.02:saturation=1.1",
    ),
    VisualFilter(
        id="low_contrast",
        name="Low Contrast",
        description="Soft cinematic shadows with relaxed highlight roll-off",
        ffmpeg_expr="eq=contrast=0.82:brightness=0.03:saturation=0.92",
    ),
    VisualFilter(
        id="cinematic",
        name="Cinematic",
        description="Teal-and-orange cinematic tone curve with rich blacks",
        ffmpeg_expr="eq=contrast=1.15:saturation=1.08,colorbalance=rs=0.06:bs=-0.05:rh=0.06:bh=-0.06",
    ),
    VisualFilter(
        id="faded",
        name="Faded",
        description="Lifted shadows with matte film look",
        ffmpeg_expr="curves=lighter,eq=contrast=0.88:saturation=0.85",
    ),
    VisualFilter(
        id="sepia",
        name="Sepia",
        description="Antique brown-tinted monochrome",
        ffmpeg_expr="colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131",
    ),
    VisualFilter(
        id="noir",
        name="Noir",
        description="High-contrast dramatic monochrome with deep crushed blacks",
        ffmpeg_expr="hue=s=0,eq=contrast=1.45:brightness=-0.04",
    ),
    VisualFilter(
        id="bright",
        name="Bright",
        description="Airy, high-key bright lighting boost",
        ffmpeg_expr="eq=brightness=0.07:contrast=1.05:saturation=1.05",
    ),
    VisualFilter(
        id="dark",
        name="Dark",
        description="Moody, low-key underexposed shadow depth",
        ffmpeg_expr="eq=brightness=-0.07:contrast=1.15",
    ),
    VisualFilter(
        id="muted",
        name="Muted",
        description="Subdued, elegant desaturated palette",
        ffmpeg_expr="eq=saturation=0.65:contrast=0.98",
    ),
    VisualFilter(
        id="sharp",
        name="Sharp",
        description="Enhanced edge crispness and micro-detail",
        ffmpeg_expr="unsharp=5:5:1.0:5:5:0.0",
    ),
    VisualFilter(
        id="soft",
        name="Soft",
        description="Dreamy soft diffusion glow",
        ffmpeg_expr="gblur=sigma=1.0:steps=1",
    ),
    VisualFilter(
        id="retro",
        name="Retro",
        description="Saturated 80s broadcast videotape style",
        ffmpeg_expr="curves=strong_contrast,hue=s=1.15:h=5",
    ),
    VisualFilter(
        id="film",
        name="Film",
        description="Authentic analog 35mm grain and rich emulation",
        ffmpeg_expr="eq=contrast=1.12:saturation=1.1,noise=c1s=5:c0f=u",
    ),
    VisualFilter(
        id="monochrome",
        name="Monochrome",
        description="Clean contemporary studio black and white",
        ffmpeg_expr="hue=s=0,eq=contrast=1.18:saturation=0.0",
    ),
]

_FILTER_INDEX: dict[str, VisualFilter] = {
    f.id.lower(): f for f in FILTERS
}
_FILTER_INDEX["none"] = _FILTER_INDEX["original"]
_FILTER_INDEX["default"] = _FILTER_INDEX["original"]
_FILTER_INDEX["bw"] = _FILTER_INDEX["black_and_white"]
_FILTER_INDEX["b&w"] = _FILTER_INDEX["black_and_white"]


def list_filters() -> list[VisualFilter]:
    """Return all available canonical visual filters."""
    return list(FILTERS)


def get_filter(filter_id: str | None) -> VisualFilter:
    """Retrieve visual filter by ID, defaulting safely to Original."""
    if not filter_id:
        return _FILTER_INDEX["original"]
    normalized = str(filter_id).strip().lower().replace(" ", "_").replace("-", "_")
    return _FILTER_INDEX.get(normalized, _FILTER_INDEX["original"])
