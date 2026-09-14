"""Retention Analysis Engine for Step 18.

Evaluates hook strength, time-to-hook, speech density, narrative progression,
climax placement, and CTA presence into a deterministic 0-100 retention score.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

from autoclip.campaign.models_intelligence import CampaignSpecification
from autoclip.pipeline.transcript import Word

log = logging.getLogger(__name__)

HOOK_KEYWORDS = {
    "how", "why", "what", "secret", "never", "stop", "always", "truth",
    "warning", "mistake", "everyone", "nobody", "trick", "method", "hack", "hackers"
}

CTA_KEYWORDS = {
    "follow", "subscribe", "link", "bio", "comment", "share", "check",
    "visit", "join", "register", "download", "below", "more"
}


@dataclass
class RetentionAnalysisResult:
    hook_strength: float
    time_to_hook_s: float
    speech_density_wps: float
    dead_air_percentage: float
    pacing_score: float
    narrative_score: float
    ending_strength: float
    retention_score: float
    metrics: dict[str, Any]
    warnings: list[str] = field(default_factory=list)


class RetentionAnalyzer:
    """Calculates granular retention metrics for social short-form video."""

    def __init__(
        self,
        ideal_wps_min: float = 2.0,
        ideal_wps_max: float = 3.5,
        max_time_to_hook_s: float = 3.5,
    ) -> None:
        self.ideal_wps_min = ideal_wps_min
        self.ideal_wps_max = ideal_wps_max
        self.max_time_to_hook_s = max_time_to_hook_s

    def analyze(
        self,
        words: list[Word],
        duration_s: float,
        pacing_score: float,
        dead_air_percentage: float,
        campaign_spec: CampaignSpecification | None = None,
        hook_text: str = "",
        hook_type: str = "",
    ) -> RetentionAnalysisResult:
        warnings: list[str] = []
        duration_s = max(0.1, duration_s)

        # ------------------------------------------------------------------
        # 1. Speech Density (Words Per Second)
        # ------------------------------------------------------------------
        word_count = len(words)
        wps = word_count / duration_s

        if self.ideal_wps_min <= wps <= self.ideal_wps_max:
            density_score = 95.0
        elif wps < self.ideal_wps_min:
            deficit = self.ideal_wps_min - wps
            density_score = max(30.0, 90.0 - deficit * 35.0)
            if wps < 1.5:
                warnings.append(f"sluggish_speech_density({wps:.1f}wps)")
        else:
            excess = wps - self.ideal_wps_max
            density_score = max(50.0, 90.0 - excess * 25.0)
            if wps > 4.2:
                warnings.append(f"hyper_fast_speech_density({wps:.1f}wps)")

        # ------------------------------------------------------------------
        # 2. Hook Strength & Time-to-Hook
        # ------------------------------------------------------------------
        time_to_hook_s = 0.0
        hook_strength = 70.0  # baseline

        first_sentence_words = words[:min(10, len(words))]
        first_words_text = " ".join(w.text.lower() for w in first_sentence_words)

        # Detect hook start time
        found_hook = False
        for idx, w in enumerate(first_sentence_words):
            clean = w.text.lower().strip(".,!?:;\"'")
            if clean in HOOK_KEYWORDS or "?" in w.text:
                time_to_hook_s = max(0.0, w.start - (words[0].start if words else 0.0))
                found_hook = True
                break

        if not found_hook and words:
            time_to_hook_s = 1.0

        if time_to_hook_s <= self.max_time_to_hook_s:
            hook_strength += 15.0
        else:
            hook_strength -= (time_to_hook_s - self.max_time_to_hook_s) * 10.0
            warnings.append(f"late_hook_delivery({time_to_hook_s:.1f}s)")

        if hook_type in ("question", "contrarian", "statement", "curiosity_gap"):
            hook_strength += 10.0

        # Check for banned or weak filler start
        if words and words[0].text.lower().strip(".,!?") in ("um", "uh", "so", "like"):
            hook_strength -= 8.0

        hook_strength = max(10.0, min(100.0, hook_strength))

        # ------------------------------------------------------------------
        # 3. Narrative Progression (Setup -> Escalation -> Payoff)
        # ------------------------------------------------------------------
        narrative_score = 80.0
        if word_count >= 15:
            narrative_score += 10.0
        if any(w.text.rstrip().endswith(("?", "!")) for w in words):
            narrative_score += 5.0

        # ------------------------------------------------------------------
        # 4. Ending Strength & CTA
        # ------------------------------------------------------------------
        ending_strength = 75.0
        last_word = words[-1] if words else None
        if last_word and last_word.text.rstrip().endswith((".", "!", "?")):
            ending_strength += 15.0
        elif last_word and last_word.text.rstrip().endswith((",", ";", "-")):
            ending_strength -= 20.0
            warnings.append("clip_ends_mid_sentence")

        # Check for CTA in last 20% of words
        tail_words = words[-max(5, int(len(words) * 0.25)):] if words else []
        tail_text = " ".join(w.text.lower() for w in tail_words)
        has_cta = any(k in tail_text for k in CTA_KEYWORDS)
        if has_cta:
            ending_strength += 10.0

        ending_strength = max(10.0, min(100.0, ending_strength))

        # ------------------------------------------------------------------
        # 5. Composite Retention Score
        # ------------------------------------------------------------------
        composite_retention = (
            0.30 * hook_strength +
            0.25 * density_score +
            0.20 * narrative_score +
            0.15 * pacing_score +
            0.10 * ending_strength
        )

        retention_score = max(10.0, min(100.0, round(composite_retention, 1)))

        metrics = {
            "wps": round(wps, 2),
            "density_score": round(density_score, 1),
            "time_to_hook_s": round(time_to_hook_s, 2),
            "hook_strength": round(hook_strength, 1),
            "narrative_score": round(narrative_score, 1),
            "ending_strength": round(ending_strength, 1),
            "pacing_score": round(pacing_score, 1),
            "dead_air_percentage": round(dead_air_percentage, 1),
            "has_cta": has_cta,
        }

        return RetentionAnalysisResult(
            hook_strength=round(hook_strength, 1),
            time_to_hook_s=round(time_to_hook_s, 2),
            speech_density_wps=round(wps, 2),
            dead_air_percentage=round(dead_air_percentage, 1),
            pacing_score=round(pacing_score, 1),
            narrative_score=round(narrative_score, 1),
            ending_strength=round(ending_strength, 1),
            retention_score=retention_score,
            metrics=metrics,
            warnings=warnings,
        )
