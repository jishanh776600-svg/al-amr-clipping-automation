"""Autonomous Viral SEO & Copywriting Generator.

Synthesizes high-CTR, platform-specific metadata strictly conforming to
Campaign Brief guidelines and channel attribution rules.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from .brief_inspector import BriefComplianceData

log = logging.getLogger(__name__)


class ClipSEOPack:
    def __init__(
        self,
        yt_title: str,
        yt_description: str,
        yt_tags: list[str],
        insta_caption: str,
        insta_hashtags: list[str],
        hook_headline: str,
    ):
        self.yt_title = yt_title
        self.yt_description = yt_description
        self.yt_tags = yt_tags
        self.insta_caption = insta_caption
        self.insta_hashtags = insta_hashtags
        self.hook_headline = hook_headline

    @property
    def ig_caption(self) -> str:
        return self.insta_caption

    @property
    def ig_tags(self) -> list[str]:
        return self.insta_hashtags

    def to_dict(self) -> dict[str, Any]:
        return {
            "yt_title": self.yt_title,
            "yt_description": self.yt_description,
            "yt_tags": self.yt_tags,
            "insta_caption": self.insta_caption,
            "insta_hashtags": self.insta_hashtags,
            "ig_caption": self.insta_caption,
            "ig_tags": self.insta_hashtags,
            "hook_headline": self.hook_headline,
        }


class SEOGenerator:
    """Generates viral, platform-specific metadata matching top Content Rewards earners."""

    def generate_seo_pack(
        self,
        transcript_slice: str,
        compliance: BriefComplianceData,
        clip_index: int = 1,
    ) -> ClipSEOPack:
        """Create curiosity-driven titles, captions, and hashtag packs."""
        text = transcript_slice.strip()

        # 1. Derive Top Hook Headline
        # Look for numbers/money in transcript
        money_match = re.search(r"(\$[\d,]+|\d+(?:k|m|million|thousand))", text, re.I)
        if money_match:
            amount = money_match.group(1).upper()
            hook_headline = f"Trader Spends {amount} on Breakfast"
            yt_title = f"He Spent {amount} on THIS?! 💀"
        elif "trading" in text.lower() or "market" in text.lower():
            hook_headline = "How trading Changed My Life! 🌊 🌴"
            yt_title = "How Trading Changed EVERYTHING 📈"
        elif "mistake" in text.lower() or "lost" in text.lower():
            hook_headline = "His $100,000 Mistake Explained! 🚨"
            yt_title = "The $100K Mistake Nobody Talks About 💀"
        else:
            first_words = " ".join(text.split()[:5])
            hook_headline = f"{first_words.capitalize()}... 🔥"
            yt_title = f"Wait For The End... 🤯 #{clip_index}"

        # 2. YouTube Shorts Metadata
        yt_tags = ["#shorts", "#trading", "#wealth", "#mindset", "#crypto"]
        if compliance.mandatory_tags:
            yt_tags = list(set(compliance.mandatory_tags + yt_tags))[:5]

        yt_desc = (
            f"{yt_title}\n\n"
            f"Follow {compliance.handle} for daily high-value breakdowns!\n\n"
            f"{' '.join(yt_tags)}"
        )

        # 3. Instagram Reels Metadata
        insta_base_tags = [
            "#tradingtips", "#forex", "#crypto", "#entrepreneur", "#businessmotivation",
            "#financialfreedom", "#wealthmindset", "#daytrader", "#moneygoals", "#reelsviral"
        ]
        if compliance.mandatory_tags:
            insta_base_tags = compliance.mandatory_tags + insta_base_tags
        insta_tags = list(dict.fromkeys(insta_base_tags))[:15]

        insta_caption = (
            f"{hook_headline}\n\n"
            f"Save this reel before it gets deleted 📌\n"
            f"Drop your thoughts below 👇\n\n"
            f"Follow {compliance.handle} for more exclusive insights.\n\n"
            f"{' '.join(insta_tags)}"
        )

        return ClipSEOPack(
            yt_title=yt_title[:50],
            yt_description=yt_desc,
            yt_tags=yt_tags,
            insta_caption=insta_caption,
            insta_hashtags=insta_tags,
            hook_headline=hook_headline,
        )
