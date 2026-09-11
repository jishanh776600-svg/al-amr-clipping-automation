"""Speech Density and Dead-Air Analysis for AL AMR Campaign Engine.

Computes pacing, meaningful word density, and maximum dead-air pauses
using exact word timestamps and audio silence intervals.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..pipeline.prepare import Silence
from ..pipeline.transcript import Word


@dataclass
class DensityAnalysis:
    """Outcome of speech density and dead-air evaluation."""

    words_per_second: float
    total_words: int
    duration_s: float
    max_silence_gap_s: float
    total_silence_s: float
    dead_air_ratio: float
    density_score: float  # 0.0 to 10.0
    details: str = ""


def analyze_speech_density_and_silence(
    words: Sequence[Word],
    start_s: float,
    end_s: float,
    *,
    silences: Sequence[Silence] | None = None,
    minimum_density: float = 1.2,
    maximum_silence_s: float = 1.5,
) -> DensityAnalysis:
    """Calculate words-per-second, silence intervals, and dead air."""
    duration_s = max(0.1, end_s - start_s)
    total_words = len(words)
    words_per_second = total_words / duration_s

    # Intersect provided silences with [start_s, end_s]
    silence_gaps: list[float] = []
    if silences:
        for s in silences:
            overlap_start = max(start_s, s.start)
            overlap_end = min(end_s, s.end)
            if overlap_end > overlap_start:
                silence_gaps.append(overlap_end - overlap_start)

    max_silence = max(silence_gaps, default=0.0)
    total_silence = sum(silence_gaps)
    dead_air_ratio = total_silence / duration_s

    # Score calculation (0.0 to 10.0)
    # Healthy speech density for shorts/reels is between 2.0 and 3.5 words/sec.
    # Below 1.2 words/sec is sluggish; above 4.5 is too fast to follow.
    if words_per_second >= 2.0:
        base_density_score = 9.0
    elif words_per_second >= minimum_density:
        base_density_score = 7.5
    elif words_per_second >= 0.8:
        base_density_score = 5.0
    else:
        base_density_score = 2.0

    # Penalize long dead-air pauses
    penalty = 0.0
    if max_silence > maximum_silence_s:
        excess = max_silence - maximum_silence_s
        penalty = min(5.0, excess * 2.5)

    final_score = max(0.0, min(10.0, base_density_score - penalty))

    details = (
        f"Density: {words_per_second:.2f} w/s ({total_words} words over {duration_s:.1f}s). "
        f"Max silence pause: {max_silence:.2f}s (total dead air: {total_silence:.2f}s, {dead_air_ratio*100:.1f}%)."
    )

    return DensityAnalysis(
        words_per_second=round(words_per_second, 2),
        total_words=total_words,
        duration_s=round(duration_s, 2),
        max_silence_gap_s=round(max_silence, 2),
        total_silence_s=round(total_silence, 2),
        dead_air_ratio=round(dead_air_ratio, 3),
        density_score=round(final_score, 2),
        details=details,
    )
