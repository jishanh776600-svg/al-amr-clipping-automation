"""Dynamic Pacing Engine for Step 18.

Detects dead-air regions, preserves intentional dramatic pauses,
tightens excessive silence, and guarantees word-boundary safety with zero clipping.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from autoclip.pipeline.prepare import Silence
from autoclip.pipeline.transcript import Transcript, Word

log = logging.getLogger(__name__)

DRAMATIC_CUE_WORDS = {
    "why", "how", "what", "listen", "imagine", "suddenly", "because",
    "the truth is", "here's why", "secret", "never", "always", "shocking"
}


@dataclass
class PauseRegion:
    start_s: float
    end_s: float
    duration_s: float
    is_intentional: bool
    reason: str


@dataclass
class PacingResult:
    pause_regions: list[PauseRegion]
    dead_air_s: float
    dead_air_percentage: float
    pacing_score: float
    tightened_start_s: float
    tightened_end_s: float
    silence_tightenings: list[dict[str, Any]]
    warnings: list[str] = field(default_factory=list)


class DynamicPacingEngine:
    """Detects and tightens pauses while preserving natural dramatic speech rhythm."""

    def __init__(
        self,
        pause_threshold_s: float = 0.75,
        target_pause_s: float = 0.35,
        word_padding_s: float = 0.08,
        max_allowed_dead_air_pct: float = 18.0,
    ) -> None:
        self.pause_threshold_s = pause_threshold_s
        self.target_pause_s = target_pause_s
        self.word_padding_s = word_padding_s
        self.max_allowed_dead_air_pct = max_allowed_dead_air_pct

    def analyze_and_tighten(
        self,
        words: list[Word],
        clip_start_s: float,
        clip_end_s: float,
        silences: list[Silence] | None = None,
        min_duration_s: float = 20.0,
        max_duration_s: float = 60.0,
    ) -> PacingResult:
        warnings: list[str] = []
        raw_duration = max(0.1, clip_end_s - clip_start_s)

        if not words:
            return PacingResult(
                pause_regions=[],
                dead_air_s=raw_duration,
                dead_air_percentage=100.0,
                pacing_score=0.0,
                tightened_start_s=clip_start_s,
                tightened_end_s=clip_end_s,
                silence_tightenings=[],
                warnings=["no_words_in_clip_window"],
            )

        # 1. Start and End Boundary Tightening
        first_word = words[0]
        last_word = words[-1]

        # Tighten leading dead air (leave safe padding of 0.08s)
        lead_in_gap = max(0.0, first_word.start - clip_start_s)
        if lead_in_gap > 0.3:
            tightened_start_s = max(clip_start_s, first_word.start - self.word_padding_s)
        else:
            tightened_start_s = clip_start_s

        # Tighten trailing dead air (leave safe padding of 0.12s)
        tail_out_gap = max(0.0, clip_end_s - last_word.end)
        if tail_out_gap > 0.4:
            tightened_end_s = min(clip_end_s, last_word.end + self.word_padding_s * 1.5)
        else:
            tightened_end_s = clip_end_s

        tightened_duration = max(0.1, tightened_end_s - tightened_start_s)

        # Ensure boundary tightening does not shorten clip below configured minimum
        if tightened_duration < min_duration_s and raw_duration >= min_duration_s:
            tightened_start_s = clip_start_s
            tightened_end_s = clip_end_s
            tightened_duration = raw_duration
            warnings.append(f"pacing_compression_prevented_to_preserve_min_duration({min_duration_s:.1f}s)")

        # 2. Inter-word pause analysis
        pauses: list[PauseRegion] = []
        silence_tightenings: list[dict[str, Any]] = []
        total_dead_air = 0.0

        for i in range(len(words) - 1):
            w_curr = words[i]
            w_next = words[i + 1]
            gap = w_next.start - w_curr.end

            if gap >= self.pause_threshold_s:
                # Check for intentional dramatic pause
                prev_text_clean = w_curr.text.lower().strip(".,!?:;\"'")
                is_dramatic = False
                reason = "excessive_dead_air"

                # If word ended in question mark or exclamation mark
                if w_curr.text.rstrip().endswith(("?", "!", "...")):
                    is_dramatic = True
                    reason = "rhetorical_question_or_punchline_pause"
                elif prev_text_clean in DRAMATIC_CUE_WORDS:
                    is_dramatic = True
                    reason = f"dramatic_cue_pause({prev_text_clean})"

                # If intentional and under 1.4s, preserve it
                if is_dramatic and gap <= 1.4:
                    pauses.append(
                        PauseRegion(
                            start_s=w_curr.end,
                            end_s=w_next.start,
                            duration_s=round(gap, 2),
                            is_intentional=True,
                            reason=reason,
                        )
                    )
                else:
                    # Accidental dead air
                    total_dead_air += gap
                    pauses.append(
                        PauseRegion(
                            start_s=w_curr.end,
                            end_s=w_next.start,
                            duration_s=round(gap, 2),
                            is_intentional=False,
                            reason=reason,
                        )
                    )
                    # Propose silence compression interval
                    tightened_gap = min(gap, self.target_pause_s)
                    reduction = gap - tightened_gap
                    silence_tightenings.append(
                        {
                            "start_s": round(w_curr.end + self.word_padding_s, 2),
                            "end_s": round(w_next.start - self.word_padding_s, 2),
                            "original_gap": round(gap, 2),
                            "tightened_gap": round(tightened_gap, 2),
                            "reduction_s": round(reduction, 2),
                        }
                    )

        dead_air_pct = min(100.0, (total_dead_air / tightened_duration) * 100.0)

        # 3. Pacing score calculation (0 - 100)
        # Optimal dead air is 2% - 8% (natural breathing). Excess > 15% penalized.
        base_pacing = 92.0
        if dead_air_pct > self.max_allowed_dead_air_pct:
            overage = dead_air_pct - self.max_allowed_dead_air_pct
            base_pacing -= overage * 2.5
            warnings.append(f"high_dead_air_percentage({dead_air_pct:.1f}%)")
        elif dead_air_pct > 10.0:
            base_pacing -= (dead_air_pct - 10.0) * 1.0

        # Reward presence of intentional pauses
        intentional_count = sum(1 for p in pauses if p.is_intentional)
        base_pacing += min(8.0, intentional_count * 2.0)

        pacing_score = max(10.0, min(100.0, round(base_pacing, 1)))

        return PacingResult(
            pause_regions=pauses,
            dead_air_s=round(total_dead_air, 2),
            dead_air_percentage=round(dead_air_pct, 1),
            pacing_score=pacing_score,
            tightened_start_s=round(tightened_start_s, 2),
            tightened_end_s=round(tightened_end_s, 2),
            silence_tightenings=silence_tightenings,
            warnings=warnings,
        )
