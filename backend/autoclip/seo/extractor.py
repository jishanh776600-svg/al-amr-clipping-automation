"""Extracts and normalizes SEO requirements from CampaignSpecification or Brief."""

from __future__ import annotations

import re
from typing import Any

from autoclip.campaign.models_intelligence import CampaignSpecification
from .models import CampaignSEORequirements


def _clean_term(term: str) -> str:
    return term.strip().lower()


def _normalize_mention(mention: str) -> str:
    m = mention.strip()
    if m and not m.startswith("@"):
        return f"@{m}"
    return m


def _normalize_hashtag(hashtag: str) -> str:
    h = hashtag.strip()
    if h and not h.startswith("#"):
        return f"#{h}"
    return h


def extract_campaign_seo_requirements(
    campaign_spec: CampaignSpecification | None,
) -> CampaignSEORequirements:
    """Authoritatively extracts SEO requirements without duplicating campaign intelligence."""
    if campaign_spec is None:
        return CampaignSEORequirements()

    # 1. Required phrases / keywords
    required_phrases = []
    for item in campaign_spec.keywords:
        val = getattr(item, "value", str(item)).strip()
        if val and val not in required_phrases:
            required_phrases.append(val)

    # 2. Required mentions
    required_mentions = []
    for item in campaign_spec.required_mentions:
        val = getattr(item, "value", str(item)).strip()
        if val:
            norm = _normalize_mention(val)
            if norm not in required_mentions:
                required_mentions.append(norm)

    # 3. Required hashtags
    required_hashtags = []
    for item in campaign_spec.hashtags:
        val = getattr(item, "value", str(item)).strip()
        if val:
            norm = _normalize_hashtag(val)
            if norm not in required_hashtags:
                required_hashtags.append(norm)

    # 4. Prohibited terms (banned words + banned topics)
    prohibited_terms = []
    for item in (campaign_spec.banned_words + campaign_spec.banned_topics):
        val = getattr(item, "value", str(item)).strip()
        if val and val.lower() not in [p.lower() for p in prohibited_terms]:
            prohibited_terms.append(val)

    # 5. CTA requirements
    cta_req = bool(getattr(campaign_spec.cta_required, "value", False))
    cta_instructions = []
    for item in campaign_spec.cta_instructions:
        val = getattr(item, "value", str(item)).strip()
        if val and val not in cta_instructions:
            cta_instructions.append(val)

    # 6. Title patterns & description guidelines
    title_patterns = [
        getattr(item, "value", str(item)).strip()
        for item in campaign_spec.title_patterns
        if getattr(item, "value", str(item)).strip()
    ]
    description_guidelines = [
        getattr(item, "value", str(item)).strip()
        for item in campaign_spec.description_guidelines
        if getattr(item, "value", str(item)).strip()
    ]

    # 7. Brand name from title or branding rules
    brand_name = campaign_spec.title if campaign_spec.title != "Normalized Campaign" else ""
    if not brand_name and campaign_spec.branding_rules:
        brand_name = getattr(campaign_spec.branding_rules[0], "value", "")

    return CampaignSEORequirements(
        campaign_id=campaign_spec.campaign_id,
        campaign_title=campaign_spec.title,
        brand_name=brand_name,
        required_phrases=required_phrases,
        required_mentions=required_mentions,
        required_hashtags=required_hashtags,
        prohibited_terms=prohibited_terms,
        cta_required=cta_req,
        cta_instructions=cta_instructions,
        campaign_url=campaign_spec.campaign_url,
        title_patterns=title_patterns,
        description_guidelines=description_guidelines,
        platforms=list(campaign_spec.platforms) if campaign_spec.platforms else ["youtube", "instagram", "telegram"],
    )
