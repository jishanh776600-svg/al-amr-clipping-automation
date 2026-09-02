"""FFmpeg Filtergraph Builder for Virtual Camera Cropping, Scaling & Subtitle Burning."""

import os
from typing import List, Optional
from clipping.contracts.director import ReframePlan, ReframeCropKeyframe
from clipping.rendering.base import FiltergraphBuilder
from clipping.rendering.exceptions import FiltergraphError


def escape_ffmpeg_filter_path(file_path: str) -> str:
    """Escapes file path for use inside FFmpeg filter strings."""
    # Convert Windows backslashes to forward slashes
    p = file_path.replace("\\", "/")
    # Escape colons (e.g. C:/ -> C\:/)
    p = p.replace(":", "\\:")
    # Escape single quotes and brackets
    p = p.replace("'", "'\\\\''").replace("[", "\\[").replace("]", "\\]")
    return p


class FFmpegFiltergraphBuilder(FiltergraphBuilder):
    """
    Constructs dynamic FFmpeg video filtergraph chains:
    crop -> scale -> ass subtitle overlay.
    """

    def build_filtergraph(
        self,
        reframe_plan: ReframePlan,
        subtitle_ass_path: Optional[str] = None,
        target_width: int = 1080,
        target_height: int = 1920,
    ) -> str:
        if not reframe_plan.keyframes:
            raise FiltergraphError("ReframePlan contains no keyframes")

        keyframes = reframe_plan.keyframes
        crop_w = keyframes[0].crop_w
        crop_h = keyframes[0].crop_h

        # 1. Build Crop Filter
        if len(keyframes) == 1:
            # Static crop
            crop_filter = f"crop={crop_w}:{crop_h}:{keyframes[0].crop_x}:{keyframes[0].crop_y}"
        else:
            # Dynamic time-varying crop expression
            x_expr = self._build_dynamic_x_expr(keyframes)
            crop_filter = f"crop=w={crop_w}:h={crop_h}:x='{x_expr}':y=0"

        # 2. Build Scale Filter (Target 1080x1920 portrait)
        scale_filter = f"scale={target_width}:{target_height}:flags=lanczos"

        filters = [crop_filter, scale_filter]

        # 3. Build Subtitle Overlay Filter
        if subtitle_ass_path:
            escaped_ass = escape_ffmpeg_filter_path(subtitle_ass_path)
            filters.append(f"ass=filename='{escaped_ass}'")

        return ",".join(filters)

    def _build_dynamic_x_expr(self, keyframes: List[ReframeCropKeyframe]) -> str:
        """Constructs nested ternary if(lt(t, T), X1, X2) expression for FFmpeg crop."""
        # Check if all keyframes have identical crop_x
        all_same = all(k.crop_x == keyframes[0].crop_x for k in keyframes)
        if all_same:
            return str(keyframes[0].crop_x)

        # Build nested if-expression from last to first
        expr = str(keyframes[-1].crop_x)
        for i in range(len(keyframes) - 2, -1, -1):
            t_next = keyframes[i + 1].timestamp
            x_cur = keyframes[i].crop_x
            expr = f"if(lt(t,{t_next:.3f}),{x_cur},{expr})"

        return expr
