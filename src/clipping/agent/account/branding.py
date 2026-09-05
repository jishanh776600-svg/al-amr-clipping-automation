"""Campaign-aware Professional Branding Generator.

Autonomously synthesizes platform-compliant, professional channel identities
(channel title, handle, bio, SEO keywords, hashtags, and avatar/banner visual specs)
tailored directly to campaign topics and target niches without operator intervention.
"""

import re
import hashlib
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field

from clipping.agent.campaign.models import CampaignRecord
from clipping.agent.vault.models import AccountPlatform


class ChannelBrandingProfile(BaseModel):
    """Normalized branding identity specification for a creator account or channel."""
    model_config = ConfigDict(frozen=True)

    channel_title: str = Field(..., min_length=1, max_length=100)
    handle: str = Field(..., min_length=1, max_length=30)
    bio: str = Field(..., max_length=500)
    seo_keywords: List[str] = Field(default_factory=list)
    hashtags: List[str] = Field(default_factory=list)
    avatar_spec: Dict[str, Any] = Field(default_factory=dict)
    banner_spec: Dict[str, Any] = Field(default_factory=dict)
    platform: AccountPlatform
    campaign_id: str
    target_niche: str


class CampaignBrandingGenerator:
    """
    Generates campaign-aligned, professional channel identities.
    Platform-specific formatting for YouTube and Instagram:
    - Clean, modern titles and handles
    - High-conversion, spam-free bios
    - Niche-relevant SEO metadata and hashtags
    - Curated visual design specifications (color palettes, banner dimensions, prompt guides)
    """

    NICHE_PALETTES = {
        "finance": {
            "primary": "#064E3B",      # Deep emerald
            "accent": "#10B981",       # Mint
            "bg": "#022C22",
            "taglines": ["Market Insights & Wealth Strategies", "Data-Driven Financial Perspectives", "Smart Investing & Alpha"],
            "base_tags": ["finance", "investing", "wealth", "markets", "economy", "money"],
        },
        "technology": {
            "primary": "#0F172A",      # Slate dark
            "accent": "#38BDF8",       # Sky cyan
            "bg": "#020617",
            "taglines": ["Next-Gen Tech & AI Insights", "The Frontier of Innovation", "Cutting-Edge Code & Cloud"],
            "base_tags": ["technology", "ai", "software", "futuretech", "cloud", "innovation"],
        },
        "fitness": {
            "primary": "#18181B",      # Zinc dark
            "accent": "#F97316",       # Vibrant orange
            "bg": "#09090B",
            "taglines": ["Peak Performance & Daily Discipline", "Science-Based Health & Strength", "Transformative Wellness"],
            "base_tags": ["fitness", "workout", "wellness", "discipline", "health", "strength"],
        },
        "business": {
            "primary": "#1E1B4B",      # Indigo dark
            "accent": "#6366F1",       # Indigo light
            "bg": "#0F0F23",
            "taglines": ["Founder Stories & Scaling Strategies", "Executive Highlights & Growth", "Business Mastery"],
            "base_tags": ["business", "entrepreneurship", "scaling", "startups", "leadership", "growth"],
        },
        "general": {
            "primary": "#111827",      # Gray dark
            "accent": "#8B5CF6",       # Violet
            "bg": "#030712",
            "taglines": ["Curated Highlights & Powerful Moments", "Perspective & Daily Insights", "Key Takeaways Daily"],
            "base_tags": ["highlights", "clips", "insights", "knowledge", "trending", "daily"],
        },
    }

    def detect_niche(self, campaign: CampaignRecord) -> str:
        """Determines the primary thematic niche of the campaign."""
        if campaign.account_requirements.required_niche:
            req = campaign.account_requirements.required_niche.lower()
            for key in self.NICHE_PALETTES:
                if key in req:
                    return key

        combined_text = f"{campaign.name} {campaign.description} {' '.join(campaign.posting_requirements.required_hashtags)}".lower()
        if any(w in combined_text for w in ["finance", "crypto", "trading", "invest", "cpm", "money", "alpha"]):
            return "finance"
        if any(w in combined_text for w in ["tech", "ai", "code", "cloud", "developer", "saas", "software"]):
            return "technology"
        if any(w in combined_text for w in ["fitness", "gym", "health", "workout", "nutrition"]):
            return "fitness"
        if any(w in combined_text for w in ["business", "startup", "founder", "marketing", "ecommerce", "scale"]):
            return "business"
        return "general"

    def _clean_campaign_name(self, raw_name: str) -> str:
        """Cleans noise like 'Campaign', 'Bounty', 'Clipping', or 'Whop' from title."""
        name = re.sub(r'(?i)\b(campaign|bounty|clipping|whop|cpm|program|test|v\d+)\b', '', raw_name)
        name = re.sub(r'[^a-zA-Z0-9\s]', ' ', name)
        name = " ".join(name.split())
        return name.strip() or "Creator"

    def generate_branding(
        self,
        campaign: CampaignRecord,
        platform: AccountPlatform = AccountPlatform.YOUTUBE,
    ) -> ChannelBrandingProfile:
        """
        Generates a complete, platform-specific ChannelBrandingProfile.
        Deterministic for the same campaign + platform combination.
        """
        niche = self.detect_niche(campaign)
        palette = self.NICHE_PALETTES[niche]
        clean_name = self._clean_campaign_name(campaign.name)
        words = clean_name.split()
        core_word = words[0] if words else "Alpha"
        cid_slug = campaign.campaign_id.replace("camp_", "")[:6]

        # Tagline selection (hash-based deterministic selection)
        hash_val = int(hashlib.md5(f"{campaign.campaign_id}_{platform.value}".encode()).hexdigest()[:4], 16)
        tagline = palette["taglines"][hash_val % len(palette["taglines"])]

        # Platform-specific formatting
        if platform == AccountPlatform.YOUTUBE:
            channel_title = f"{clean_name} Highlights" if len(f"{clean_name} Highlights") <= 50 else clean_name[:50]
            clean_handle_base = re.sub(r'[^a-zA-Z0-9]', '', f"{core_word.lower()}clips")[:20]
            handle = f"@{clean_handle_base}_{cid_slug}"[:30]
            bio = (
                f"{channel_title} | {tagline}.\n\n"
                f"Curated short-form insights and high-impact takeaways.\n"
                f"New daily shorts. Subscribe for top analysis."
            )
            banner_dims = {"width": 2560, "height": 1440}
        elif platform == AccountPlatform.INSTAGRAM:
            channel_title = f"{clean_name} | Clips" if len(f"{clean_name} | Clips") <= 30 else clean_name[:30]
            clean_handle_base = re.sub(r'[^a-zA-Z0-9_.]', '', f"{core_word.lower()}.clips")[:20]
            handle = f"@{clean_handle_base}.{cid_slug}"[:30]
            bio = (
                f"🎬 {clean_name} Highlights\n"
                f"💡 {tagline}\n"
                f"⚡ High-signal reels daily\n"
                f"👇 Follow for the latest drops"
            )
            banner_dims = {"width": 1080, "height": 1080}
        else:
            channel_title = f"{clean_name} Central"[:50]
            handle = f"@{core_word.lower()}_{cid_slug}"[:30]
            bio = f"{clean_name} official short-form updates and moments."
            banner_dims = {"width": 1200, "height": 675}

        # SEO Keywords & Hashtags synthesis
        raw_hashtags = list(campaign.posting_requirements.required_hashtags)
        for t in palette["base_tags"]:
            tag_formatted = f"#{t.lower().replace(' ', '')}"
            if tag_formatted not in raw_hashtags:
                raw_hashtags.append(tag_formatted)
        hashtags = raw_hashtags[:12]

        keywords = list(palette["base_tags"])
        if clean_name.lower() not in keywords:
            keywords.insert(0, clean_name.lower())
        for req_kw in campaign.posting_requirements.required_title_keywords:
            if req_kw.lower() not in keywords:
                keywords.append(req_kw.lower())

        initials = "".join([w[0].upper() for w in words[:2]]) or "AL"

        avatar_spec = {
            "style": "minimal_modern",
            "initials": initials,
            "background_color": palette["bg"],
            "accent_color": palette["accent"],
            "text_color": "#FFFFFF",
            "prompt": f"Minimalist professional app-style icon, lettermark '{initials}', {palette['accent']} neon accent on {palette['bg']} dark slate, clean vector style, high resolution",
        }

        banner_spec = {
            "headline": channel_title,
            "tagline": tagline,
            "background_color": palette["bg"],
            "accent_color": palette["accent"],
            "dimensions": banner_dims,
            "prompt": f"Modern YouTube channel banner, headline '{channel_title}', tagline '{tagline}', sleek dark theme {palette['bg']} with glowing {palette['accent']} geometry, 4K render",
        }

        return ChannelBrandingProfile(
            channel_title=channel_title,
            handle=handle,
            bio=bio,
            seo_keywords=keywords[:15],
            hashtags=hashtags,
            avatar_spec=avatar_spec,
            banner_spec=banner_spec,
            platform=platform,
            campaign_id=campaign.campaign_id,
            target_niche=niche,
        )
