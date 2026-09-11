"""Call-to-Action (CTA) Detection for AL AMR Campaign Engine.

Detects conversion, engagement, subscription, and outbound action phrases
near the conclusion of candidate clips.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from ..pipeline.transcript import Word

CTA_PATTERNS: dict[str, re.Pattern] = {
    "subscribe_follow": re.compile(
        r"\b(subscribe|follow|hit subscribe|smash that subscribe|follow for more|sub to the channel)\b",
        re.IGNORECASE,
    ),
    "engagement": re.compile(
        r"\b(like and subscribe|leave a comment|comment below|drop a comment|share this|"
        r"let me know in the comments|tell me what you think)\b",
        re.IGNORECASE,
    ),
    "link_outbound": re.compile(
        r"\b(link in bio|link below|check the link|check out the link|link in description|"
        r"visit|head over to|click the link)\b",
        re.IGNORECASE,
    ),
    "direct_action": re.compile(
        r"\b(sign up|download|get started|join now|grab yours|try it out|register today|"
        r"learn more|read more|check it out)\b",
        re.IGNORECASE,
    ),
}


@dataclass
class CtaAnalysis:
    """Outcome of Call-To-Action detection."""

    has_cta: bool
    score: float  # 0.0 to 10.0
    cta_type: str
    matched_phrase: str
    cta_time_offset_s: float | None = None
    is_within_window: bool = False
    ending_text: str = ""
    details: str = ""


def analyze_cta(
    words: Sequence[Word],
    end_s: float,
    *,
    cta_window_s: float = 6.0,
    required_types: Sequence[str] | None = None,
) -> CtaAnalysis:
    """Analyze the final seconds of a candidate clip for CTA presence."""
    if not words:
        return CtaAnalysis(
            has_cta=False,
            score=0.0,
            cta_type="none",
            matched_phrase="",
            details="No words in candidate.",
        )

    cutoff_time = max(0.0, end_s - cta_window_s)
    closing_words = [w for w in words if w.end >= cutoff_time]
    closing_text = " ".join(w.text for w in closing_words).strip()
    full_text = " ".join(w.text for w in words[-40:]).strip()

    detected_type = "none"
    matched_phrase = ""
    detected_offset: float | None = None
    within_window = False

    # Check closing window first
    for cta_type, pattern in CTA_PATTERNS.items():
        match = pattern.search(closing_text)
        if match:
            detected_type = cta_type
            matched_phrase = match.group(0)
            within_window = True
            # Find word timestamp for match
            first_word_in_match = matched_phrase.split()[0].lower()
            for w in closing_words:
                if first_word_in_match in w.text.lower():
                    detected_offset = round(end_s - w.start, 2)
                    break
            break

    # If not found in closing window, scan the wider tail (last 40 words)
    if not within_window:
        for cta_type, pattern in CTA_PATTERNS.items():
            match = pattern.search(full_text)
            if match:
                detected_type = cta_type
                matched_phrase = match.group(0)
                within_window = False
                first_word_in_match = matched_phrase.split()[0].lower()
                for w in reversed(words):
                    if first_word_in_match in w.text.lower():
                        detected_offset = round(end_s - w.start, 2)
                        break
                break

    has_cta = bool(matched_phrase)

    # Score calculation
    if not has_cta:
        score = 0.0
        details = "No Call-to-Action detected in candidate."
    elif within_window:
        base_score = 8.5
        # Check if matched type satisfies required types (if specified)
        if required_types and detected_type not in required_types:
            score = 6.0
            details = f"CTA '{matched_phrase}' detected ({detected_type}), but required type was {required_types}."
        else:
            score = base_score + (1.5 if detected_type in ("direct_action", "link_outbound") else 1.0)
            score = min(10.0, score)
            details = f"Strong CTA '{matched_phrase}' detected {detected_offset}s before clip end."
    else:
        # CTA exists but occurred too early before the final window
        score = 4.0
        details = f"CTA '{matched_phrase}' detected too early ({detected_offset}s before clip end; window is {cta_window_s}s)."

    return CtaAnalysis(
        has_cta=has_cta,
        score=round(score, 2),
        cta_type=detected_type,
        matched_phrase=matched_phrase,
        cta_time_offset_s=detected_offset,
        is_within_window=within_window,
        ending_text=closing_text,
        details=details,
    )
