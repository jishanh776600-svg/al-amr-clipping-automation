"""Brief Inspector & Compliance Guardian.

Extracts mandatory attribution handles, hashtags, duration limits, and compliance
rules from Campaign Brief documents or operator input.
"""

from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger(__name__)


class BriefComplianceData:
    """Parsed compliance rules and attribution metadata."""

    def __init__(
        self,
        handle: str = "@ALPHA_FUTURES",
        mandatory_tags: list[str] | None = None,
        banned_words: list[str] | None = None,
        min_duration_s: float = 30.0,
        max_duration_s: float = 60.0,
        topic: str = "",
        raw_brief: str = "",
    ):
        self.handle = handle
        self.mandatory_tags = mandatory_tags or []
        self.banned_words = banned_words or []
        self.min_duration_s = min_duration_s
        self.max_duration_s = max_duration_s
        self.topic = topic
        self.raw_brief = raw_brief

    def to_dict(self) -> dict[str, Any]:
        return {
            "handle": self.handle,
            "mandatory_tags": self.mandatory_tags,
            "banned_words": self.banned_words,
            "min_duration_s": self.min_duration_s,
            "max_duration_s": self.max_duration_s,
            "topic": self.topic,
        }


def parse_brief_compliance(
    brief_text: str | None = None,
    job_settings: dict[str, Any] | None = None,
) -> BriefComplianceData:
    """Extract mandatory attribution handle and rules from brief text or settings."""
    text = (brief_text or "").strip()
    settings = job_settings or {}

    # 1. Extract Handle (@channel or @handle)
    handle = None
    if text:
        # Search for @handle in text
        handles = re.findall(r"@([A-Za-z0-9_]+)", text)
        if handles:
            handle = f"@{handles[0].upper()}"

    if not handle:
        # Check settings
        cand = (
            settings.get("channel_handle")
            or settings.get("attribution_handle")
            or (settings.get("campaign") or {}).get("target_handle")
        )
        if cand:
            clean = str(cand).strip()
            handle = clean if clean.startswith("@") else f"@{clean.upper()}"

    if not handle:
        handle = "@ALPHA_FUTURES"  # Signature default matching winning campaign

    # 2. Extract Mandatory Hashtags
    hashtags = []
    if text:
        hashtags = [f"#{tag.lower()}" for tag in re.findall(r"#([A-Za-z0-9_]+)", text)]

    # 3. Extract Duration Bounds
    min_dur = 30.0
    max_dur = 59.0
    if text:
        m_min = re.search(r"(?:min|minimum)\s*[:=]?\s*(\d+)", text, re.I)
        if m_min:
            min_dur = float(m_min.group(1))
        m_max = re.search(r"(?:max|maximum)\s*[:=]?\s*(\d+)", text, re.I)
        if m_max:
            max_dur = float(m_max.group(1))

    # 4. Extract Banned Words
    banned = []
    if text and ("banned" in text.lower() or "prohibited" in text.lower() or "do not say" in text.lower()):
        lines = text.splitlines()
        for line in lines:
            if any(k in line.lower() for k in ("banned", "prohibited", "avoid", "no mention")):
                words = re.findall(r"\b[A-Za-z0-9_-]+\b", line)
                banned.extend([w.lower() for w in words if len(w) > 3 and w.lower() not in ("banned", "words", "prohibited")])

    return BriefComplianceData(
        handle=handle,
        mandatory_tags=hashtags,
        banned_words=list(set(banned)),
        min_duration_s=min_dur,
        max_duration_s=max_dur,
        topic=text[:100],
        raw_brief=text,
    )
