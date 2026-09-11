"""Hook Analysis for AL AMR Campaign Engine.

Evaluates the opening seconds of a candidate highlight for curiosity,
tension, bold claims, question openings, and immediate retention potential.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from ..pipeline.transcript import Word

QUESTION_HOOK_REGEX = re.compile(
    r"\b(why|how|what|did you know|have you ever|is it possible|who|when|where|which|can you|are you)\b|\?",
    re.IGNORECASE,
)

BOLD_HOOK_REGEX = re.compile(
    r"\b(secret|never|mistake|truth|stop|nobody|everyone|always|warning|reason|shocking|"
    r"insane|crazy|game changer|unbelievable|proof|hack|trick|destroy|change your life|"
    r"biggest|worst|best|most powerful|hidden)\b",
    re.IGNORECASE,
)

STAT_HOOK_REGEX = re.compile(
    r"\b(\d+|percent|%|millions|billions|thousands|dollars|first time|number one)\b",
    re.IGNORECASE,
)

FILLER_START_REGEX = re.compile(
    r"^(um|uh|like|you know|so yeah|anyway|well|i mean)\b",
    re.IGNORECASE,
)


@dataclass
class HookAnalysis:
    """Outcome of hook evaluation."""

    score: float  # 0.0 to 10.0
    opening_text: str
    words_in_window: int
    has_question: bool
    has_bold_claim: bool
    has_statistics: bool
    has_filler_opening: bool
    hook_type: str
    stronger_opening_offset_s: float | None = None
    reason: str = ""


def analyze_hook(
    words: Sequence[Word],
    start_s: float,
    *,
    hook_window_s: float = 2.5,
) -> HookAnalysis:
    """Analyze the opening window of speech for hook strength."""
    if not words:
        return HookAnalysis(
            score=0.0,
            opening_text="",
            words_in_window=0,
            has_question=False,
            has_bold_claim=False,
            has_statistics=False,
            has_filler_opening=False,
            hook_type="none",
            reason="No spoken words in candidate.",
        )

    # Words that begin within hook_window_s from the start
    cutoff_time = start_s + hook_window_s
    window_words = [w for w in words if w.start <= cutoff_time]
    if not window_words:
        # First word starts after the hook window
        delay = words[0].start - start_s
        return HookAnalysis(
            score=max(0.0, 3.0 - delay),
            opening_text="",
            words_in_window=0,
            has_question=False,
            has_bold_claim=False,
            has_statistics=False,
            has_filler_opening=False,
            hook_type="delayed_speech",
            reason=f"Opening silence: speech does not begin until {delay:.2f}s into candidate.",
        )

    opening_text = " ".join(w.text for w in window_words).strip()
    full_text_sample = " ".join(w.text for w in words[:25]).strip()

    has_question = bool(QUESTION_HOOK_REGEX.search(opening_text) or "?" in full_text_sample[:80])
    has_bold = bool(BOLD_HOOK_REGEX.search(opening_text))
    has_stat = bool(STAT_HOOK_REGEX.search(opening_text))
    has_filler = bool(FILLER_START_REGEX.search(opening_text))

    # Base score determined by speech density in opening
    # 3-6 words in 2.5s is optimal pacing for a short opening statement
    word_count = len(window_words)
    if word_count >= 3:
        base_score = 6.0
    elif word_count in (1, 2):
        base_score = 4.5
    else:
        base_score = 2.0

    score = base_score
    hook_types: list[str] = []

    if has_question:
        score += 2.5
        hook_types.append("question")
    if has_bold:
        score += 2.0
        hook_types.append("bold_claim")
    if has_stat:
        score += 1.5
        hook_types.append("statistic")
    if has_filler:
        score -= 2.0
        hook_types.append("filler_start")

    # Clamped to 0.0 - 10.0
    final_score = max(0.0, min(10.0, score))
    primary_hook_type = "+".join(hook_types) if hook_types else "statement"

    # Search for a stronger opening statement within the first 8 seconds
    stronger_offset: float | None = None
    extended_words = [w for w in words if w.start <= start_s + 8.0]
    for i, w in enumerate(extended_words[len(window_words):], start=len(window_words)):
        sub_sample = " ".join(x.text for x in extended_words[i : i + 5])
        if QUESTION_HOOK_REGEX.search(sub_sample) or BOLD_HOOK_REGEX.search(sub_sample):
            stronger_offset = round(w.start - start_s, 2)
            break

    reason_parts = []
    if has_question:
        reason_parts.append("engaging question opening")
    if has_bold:
        reason_parts.append("high-impact bold claim")
    if has_stat:
        reason_parts.append("specific statistic/number")
    if has_filler:
        reason_parts.append("slow/filler opening")
    if not reason_parts:
        reason_parts.append(f"standard delivery ({word_count} words in {hook_window_s:.1f}s)")

    reason = f"Hook evaluated: {', '.join(reason_parts)}."
    if stronger_offset is not None:
        reason += f" (Stronger hook potential identified at +{stronger_offset}s)."

    return HookAnalysis(
        score=round(final_score, 2),
        opening_text=opening_text,
        words_in_window=word_count,
        has_question=has_question,
        has_bold_claim=has_bold,
        has_statistics=has_stat,
        has_filler_opening=has_filler,
        hook_type=primary_hook_type,
        stronger_opening_offset_s=stronger_offset,
        reason=reason,
    )
