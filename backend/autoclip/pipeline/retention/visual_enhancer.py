"""Visual Retention Enhancement for Step 18.

Applies controlled visual punch-in / punch-out transitions at key moments
(e.g., hooks and climaxes) on top of Step 17 CropPath, strictly bound by source
dimensions to prevent black borders.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from autoclip.pipeline.reframe.croppath import (
    CropKeyframe,
    CropPath,
    CropSegment,
    Strategy,
    target_crop_size,
)

log = logging.getLogger(__name__)


@dataclass
class VisualEmphasisMoment:
    start_s: float
    end_s: float
    zoom_factor: float
    reason: str


class VisualRetentionEnhancer:
    """Enhances 9:16 vertical compositions with subtle, rate-limited visual emphasis."""

    def __init__(
        self,
        max_punch_ins_per_clip: int = 2,
        min_dwell_s: float = 3.0,
        punch_in_zoom: float = 1.22,
    ) -> None:
        self.max_punch_ins_per_clip = max_punch_ins_per_clip
        self.min_dwell_s = min_dwell_s
        self.punch_in_zoom = punch_in_zoom

    def enhance_composition(
        self,
        crop_path: CropPath,
        hook_start_s: float | None = None,
        hook_end_s: float | None = None,
        climax_start_s: float | None = None,
        climax_end_s: float | None = None,
        duration_s: float = 30.0,
    ) -> tuple[CropPath, list[dict[str, Any]]]:
        """Adds controlled punch-in keyframes at key narrative moments."""
        moments: list[VisualEmphasisMoment] = []

        # 1. Candidate visual emphasis points
        # Hook delivery (0.5s - 3.5s)
        if hook_start_s is not None and hook_end_s is not None and (hook_end_s - hook_start_s) >= 1.0:
            h_start = max(0.2, hook_start_s)
            h_end = min(duration_s - 1.0, max(h_start + self.min_dwell_s, hook_end_s))
            moments.append(
                VisualEmphasisMoment(
                    start_s=round(h_start, 2),
                    end_s=round(h_end, 2),
                    zoom_factor=self.punch_in_zoom,
                    reason="hook_delivery_punch_in",
                )
            )

        # Climax / realization
        if (
            climax_start_s is not None and climax_end_s is not None
            and len(moments) < self.max_punch_ins_per_clip
        ):
            c_start = climax_start_s
            c_end = min(duration_s - 0.5, max(c_start + self.min_dwell_s, climax_end_s))
            # Ensure spacing of at least 4s from hook
            if not moments or (c_start - moments[0].end_s >= 4.0):
                moments.append(
                    VisualEmphasisMoment(
                        start_s=round(c_start, 2),
                        end_s=round(c_end, 2),
                        zoom_factor=self.punch_in_zoom,
                        reason="climax_punch_in",
                    )
                )

        if not moments or not crop_path.segments:
            return crop_path, []

        # 2. Clone segments and apply subtle punch-in to keyframes without breaking boundaries
        enhanced_segments: list[CropSegment] = []
        source_w = crop_path.source_width
        source_h = crop_path.source_height

        for seg in crop_path.segments:
            new_seg = CropSegment(
                start_s=seg.start_s,
                end_s=seg.end_s,
                width=seg.width,
                height=seg.height,
                keyframes=list(seg.keyframes),
                strategy=seg.strategy,
                zoom=seg.zoom,
                fit=seg.fit,
                secondary_keyframes=list(seg.secondary_keyframes),
            )

            # If segment is not fit/blurred letterbox, apply punch-in zoom
            if not seg.fit and new_seg.keyframes:
                # Find matching moment
                for m in moments:
                    if seg.start_s <= m.start_s < seg.end_s:
                        # Slight zoom reduction to crop_w / crop_h
                        zoom = m.zoom_factor
                        zoomed_w = int(seg.width / zoom)
                        zoomed_h = int(seg.height / zoom)
                        # Keep even dimensions
                        if zoomed_w % 2 != 0:
                            zoomed_w -= 1
                        if zoomed_h % 2 != 0:
                            zoomed_h -= 1

                        dw = (seg.width - zoomed_w) / 2.0
                        dh = (seg.height - zoomed_h) / 2.0

                        # Adjust keyframes within the punch-in window
                        updated_kfs = []
                        for kf in new_seg.keyframes:
                            if m.start_s <= kf.t <= m.end_s:
                                nx = min(max(0.0, kf.x + dw), source_w - zoomed_w)
                                ny = min(max(0.0, kf.y + dh), source_h - zoomed_h)
                                updated_kfs.append(CropKeyframe(t=kf.t, x=round(nx, 1), y=round(ny, 1)))
                            else:
                                updated_kfs.append(kf)
                        new_seg.keyframes = updated_kfs
                        break

            enhanced_segments.append(new_seg)

        enhanced_path = CropPath(
            source_width=source_w,
            source_height=source_h,
            segments=enhanced_segments,
        )

        serialized_moments = [
            {
                "start_s": m.start_s,
                "end_s": m.end_s,
                "zoom_factor": m.zoom_factor,
                "reason": m.reason,
            }
            for m in moments
        ]

        return enhanced_path, serialized_moments
