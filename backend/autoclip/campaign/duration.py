"""Canonical duration constraint resolution and validation across AutoClip stages."""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_MIN_DURATION_S: float = 20.0
DEFAULT_MAX_DURATION_S: float = 60.0


def resolve_duration_limits(
    job_settings: dict[str, Any] | None = None,
    campaign_spec: Any | None = None,
    campaign_brief: Any | None = None,
    default_min: float = DEFAULT_MIN_DURATION_S,
    default_max: float = DEFAULT_MAX_DURATION_S,
) -> tuple[float, float]:
    """Deterministically resolve canonical (min_duration_s, max_duration_s) limits.

    Priority:
    1. Explicit job overrides in job_settings: 'min_duration_s', 'max_duration_s'.
    2. Campaign Specification requirements (if campaign active).
    3. Campaign Brief requirements (if campaign active).
    4. Nested clips settings in job_settings: 'clips.min_duration_s', 'clips.max_duration_s'.
    5. Configured defaults (default_min, default_max).
    """
    settings = job_settings or {}
    clips_settings = settings.get("clips") if isinstance(settings.get("clips"), dict) else {}

    min_val: float | None = None
    max_val: float | None = None

    # 1. Top-level settings overrides (e.g. from ingest form or CLI)
    if settings.get("min_duration_s") is not None:
        try:
            min_val = float(settings["min_duration_s"])
        except (ValueError, TypeError):
            pass

    if settings.get("max_duration_s") is not None:
        try:
            max_val = float(settings["max_duration_s"])
        except (ValueError, TypeError):
            pass

    # 2. Campaign specification
    if campaign_spec:
        if min_val is None:
            dur_min = getattr(campaign_spec, "duration_min_s", None)
            if dur_min and getattr(dur_min, "value", None) is not None:
                min_val = float(dur_min.value)
            elif getattr(campaign_spec, "duration_range", None) and campaign_spec.duration_range.value:
                min_val = float(campaign_spec.duration_range.value[0])

        if max_val is None:
            dur_max = getattr(campaign_spec, "duration_max_s", None)
            if dur_max and getattr(dur_max, "value", None) is not None:
                max_val = float(dur_max.value)
            elif getattr(campaign_spec, "duration_range", None) and campaign_spec.duration_range.value:
                max_val = float(campaign_spec.duration_range.value[1])

    # 3. Campaign brief
    if campaign_brief:
        if min_val is None and getattr(campaign_brief, "minimum_duration", None) is not None:
            min_val = float(campaign_brief.minimum_duration)
        if max_val is None and getattr(campaign_brief, "maximum_duration", None) is not None:
            max_val = float(campaign_brief.maximum_duration)

    # 4. Nested clips settings (from base settings.json)
    if min_val is None and clips_settings.get("min_duration_s") is not None:
        try:
            min_val = float(clips_settings["min_duration_s"])
        except (ValueError, TypeError):
            pass

    if max_val is None and clips_settings.get("max_duration_s") is not None:
        try:
            max_val = float(clips_settings["max_duration_s"])
        except (ValueError, TypeError):
            pass

    # 5. Defaults
    final_min = float(min_val if min_val is not None and min_val > 0 else default_min)
    final_max = float(max_val if max_val is not None and max_val > 0 else default_max)

    if final_min >= final_max:
        final_max = final_min + 5.0

    return (final_min, final_max)


def validate_duration_bounds(
    duration_s: float,
    min_duration_s: float,
    max_duration_s: float,
    label: str = "Clip",
) -> tuple[bool, str | None]:
    """Strictly validates that duration_s is within [min_duration_s, max_duration_s].

    Tolerates NO deviations below min_duration_s or above max_duration_s.
    """
    if duration_s < min_duration_s:
        return False, f"{label} duration {duration_s:.2f}s is strictly below minimum {min_duration_s:.2f}s"
    if duration_s > max_duration_s:
        return False, f"{label} duration {duration_s:.2f}s is strictly above maximum {max_duration_s:.2f}s"
    return True, None


DEFAULT_MAX_CLIPS: int = 5


def resolve_max_clips(
    job_settings: dict[str, Any] | None = None,
    campaign_spec: Any | None = None,
    campaign_brief: Any | None = None,
    default_max_clips: int = DEFAULT_MAX_CLIPS,
) -> int:
    """Deterministically resolve canonical target max_clips count.

    Priority:
    1. Explicit job overrides in job_settings: 'max_clips'.
    2. Campaign Specification requirements (output_count).
    3. Campaign Brief requirements (output_count or maximum_candidates).
    4. Nested clips settings in job_settings: 'clips.max_clips'.
    5. Configured default (default_max_clips, defaults to 5).
    """
    settings = job_settings or {}
    clips_settings = settings.get("clips") if isinstance(settings.get("clips"), dict) else {}

    count: int | None = None

    # 1. Top-level settings overrides
    if settings.get("max_clips") is not None:
        try:
            count = int(settings["max_clips"])
        except (ValueError, TypeError):
            pass

    # 2. Campaign specification
    if count is None and campaign_spec:
        out_cnt = getattr(campaign_spec, "output_count", None)
        if out_cnt is not None:
            val = getattr(out_cnt, "value", out_cnt)
            if val is not None:
                try:
                    count = int(val)
                except (ValueError, TypeError):
                    pass

    # 3. Campaign brief
    if count is None and campaign_brief:
        out_cnt = getattr(campaign_brief, "output_count", None)
        if out_cnt is not None:
            try:
                count = int(out_cnt)
            except (ValueError, TypeError):
                pass
        if count is None:
            max_cands = getattr(campaign_brief, "maximum_candidates", None)
            if max_cands is not None:
                try:
                    count = int(max_cands)
                except (ValueError, TypeError):
                    pass

    # 4. Nested clips settings
    if count is None and clips_settings.get("max_clips") is not None:
        try:
            count = int(clips_settings["max_clips"])
        except (ValueError, TypeError):
            pass

    # 5. Default
    if count is None or count <= 0:
        count = default_max_clips

    return max(1, count)
