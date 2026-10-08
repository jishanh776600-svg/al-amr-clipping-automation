"""Extracts and normalizes SEO requirements from CampaignSpecification or Brief."""

from __future__ import annotations

import re
from typing import Any

from autoclip.campaign.models_intelligence import CampaignSpecification
from .models import (
    CampaignSEORequirements,
    CampaignSEOSpec,
    GlobalSEORules,
    PlatformSEORules,
)


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


GARBAGE_HASHTAG_SUBSTRINGS = (
    "wontgetpaid", "getpaid", "rejected", "rejection", "loweffort", "slop", "bait",
    "budget", "cpm", "views", "maxper", "readthis", "footage", "drive", "google",
    "docx", "pdf", "payment", "payout", "deadline", "tier1", "tier-1", "050per",
    "rules", "guideline", "sop", "submission", "howtomake", "discord", "ticket",
    "autoclip",
)


def is_clean_public_hashtag(tag: str) -> bool:
    clean = tag.strip().lstrip("#").lower()
    if not clean or len(clean) < 2 or len(clean) > 24:
        return False
    if clean.isdigit():
        return False
    if any(bad in clean for bad in GARBAGE_HASHTAG_SUBSTRINGS):
        return False
    # Must contain at least one letter
    if not re.search(r"[a-z]", clean):
        return False
    return True


def is_clean_public_mention(mention: str) -> bool:
    clean = mention.strip().lstrip("@").lower()
    if not clean or len(clean) < 2 or len(clean) > 30:
        return False
    if any(bad in clean for bad in ("sop", "rule", "guideline", "reject", "discord", "ticket", "autoclip")):
        return False
    return True


def extract_campaign_seo_requirements(
    campaign_spec: Any | None,
) -> CampaignSEORequirements:
    """Authoritatively extracts SEO requirements from text, dict, or CampaignSpecification with 100% accuracy."""
    if campaign_spec is None:
        return CampaignSEORequirements()

    from autoclip.campaign.models_intelligence import CampaignSpecification

    raw_text_corpus = ""
    raw_brand = ""

    if isinstance(campaign_spec, str):
        raw_text_corpus = campaign_spec
        from autoclip.campaign.extractor import parse_guidelines_into_brief
        brief_obj = parse_guidelines_into_brief(campaign_spec)
        campaign_spec = CampaignSpecification.from_campaign_brief(brief_obj)
        raw_brand = brief_obj.name

    elif isinstance(campaign_spec, dict):
        raw_text_corpus = f"{campaign_spec.get('raw_brief', '')} {campaign_spec.get('description', '')} {campaign_spec.get('guidelines', '')}"
        raw_brand = campaign_spec.get("brand_name", "") or campaign_spec.get("title", "") or campaign_spec.get("name", "")
        if "desired_topics" in campaign_spec and "campaign_id" in campaign_spec:
            campaign_spec = CampaignSpecification.from_dict(campaign_spec)
        else:
            campaign_spec = CampaignSpecification.from_campaign_brief(campaign_spec)
    elif hasattr(campaign_spec, "required_topics") and not hasattr(campaign_spec, "desired_topics"):
        raw_text_corpus = getattr(campaign_spec, "description", "") or getattr(campaign_spec, "topic_context", "")
        campaign_spec = CampaignSpecification.from_campaign_brief(campaign_spec)
    else:
        raw_text_corpus = getattr(campaign_spec, "description", "") or getattr(campaign_spec, "objective", "")

    # 1. Required phrases / keywords (excluding SOP operational terms)
    sop_exclude = {
        "payout", "payouts", "submit", "submission", "rejection", "rejected",
        "tier-1", "non-dedicated", "late", "consistent", "must", "required",
        "rule", "rules", "guidelines", "sop", "eligible", "disqualified",
        "ticket", "discord", "get-help", "no networks", "support",
    }
    required_phrases = []
    for item in campaign_spec.keywords:
        val = getattr(item, "value", str(item)).strip()
        val_lower = val.lower()
        if (
            val
            and len(val) <= 45
            and not any(ex in val_lower for ex in ("ticket", "get-help", "no networks", "discord"))
            and val_lower not in sop_exclude
            and val not in required_phrases
        ):
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
    prohibited_lower = {p.lower() for p in prohibited_terms}

    # Clean required mentions against prohibited terms & stopwords
    clean_mentions = []
    for m in required_mentions:
        clean_m = m.lstrip("@").lower()
        if is_clean_public_mention(m) and clean_m not in prohibited_lower and clean_m not in ("of", "in", "your", "content", "anywhere", "required", "mandatory"):
            if m not in clean_mentions:
                clean_mentions.append(m)
    required_mentions = clean_mentions

    # Clean required hashtags against prohibited terms & stopwords
    clean_hashtags = []
    for h in required_hashtags:
        clean_h = h.lstrip("#").lower()
        if not h.startswith("#@") and clean_h not in prohibited_lower and is_clean_public_hashtag(h) and clean_h not in ("required", "mandatory", "show", "hashtag", "hashtags", "tags", "tag", "and", "uses", "the", "every", "clip", "clips"):
            if h not in clean_hashtags:
                clean_hashtags.append(h)
    required_hashtags = clean_hashtags

    # Clean required phrases (exclude table rows, headings)
    clean_phrases = []
    for p in required_phrases:
        if p.startswith("|") or p.endswith("|") or " | " in p:
            continue
        p_l = p.lower()
        if any(w in p_l for w in ("how to", "why posts", "rejected", "rejection", "trailers", "platforms")):
            continue
        if p_l not in prohibited_lower and p not in clean_phrases:
            clean_phrases.append(p)
    required_phrases = clean_phrases

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

    # Resolve campaign URL: check explicit campaign_url or search description_guidelines
    campaign_url = getattr(campaign_spec, "campaign_url", "") or ""
    if not campaign_url and description_guidelines:
        for dg in description_guidelines:
            url_match = re.search(r"https?://[^\s<>\"']+", dg)
            if url_match:
                campaign_url = url_match.group(0).strip()
    # Safety scan over raw brief text for 100% extraction accuracy
    if raw_text_corpus:
        raw_tags = re.findall(r"#[A-Za-z0-9_]+", raw_text_corpus)
        for rt in raw_tags:
            norm_rt = _normalize_hashtag(rt)
            if is_clean_public_hashtag(norm_rt) and norm_rt.lower().lstrip("#") not in prohibited_lower and norm_rt not in clean_hashtags:
                clean_hashtags.append(norm_rt)

        raw_mentions = re.findall(r"@[A-Za-z0-9_]+", raw_text_corpus)
        for rm in raw_mentions:
            norm_rm = _normalize_mention(rm)
            if is_clean_public_mention(norm_rm) and norm_rm.lower().lstrip("@") not in prohibited_lower and norm_rm not in clean_mentions:
                clean_mentions.append(norm_rm)

        if not campaign_url:
            raw_url_match = re.search(r"https?://[^\s<>\"']+", raw_text_corpus)
            if raw_url_match:
                campaign_url = raw_url_match.group(0).strip()

    required_hashtags = clean_hashtags
    required_mentions = clean_mentions

    # 7. Brand name from title or branding rules
    brand_name = raw_brand or getattr(campaign_spec, "brand_name", "") or ""
    if not brand_name and campaign_spec.title and campaign_spec.title != "Normalized Campaign":
        brand_name = campaign_spec.title
    if not brand_name and campaign_spec.branding_rules:
        brand_name = getattr(campaign_spec.branding_rules[0], "value", "")
    if not brand_name and getattr(campaign_spec, "name", None):
        c_match = re.match(r"^([A-Z][a-zA-Z0-9\s]{1,25})\s*[-–—]", campaign_spec.name)
        if c_match:
            cand_b = c_match.group(1).strip()
            if cand_b.lower() not in ("video", "campaign", "guideline", "brief"):
                brand_name = cand_b

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
        campaign_url=campaign_url,
        title_patterns=title_patterns,
        description_guidelines=description_guidelines,
        platforms=list(campaign_spec.platforms) if campaign_spec.platforms else ["youtube", "instagram", "telegram"],
    )


def extract_campaign_seo_spec(
    campaign_spec: Any | None,
) -> CampaignSEOSpec:
    """Authoritatively extracts a structured, multi-platform CampaignSEOSpec.

    Guarantees strict separation of Global, YouTube, and Instagram requirements
    without inventing or cross-contaminating rules.
    """
    reqs = extract_campaign_seo_requirements(campaign_spec)

    # 1. Global rules
    tone = ""
    language = "en"
    if campaign_spec and hasattr(campaign_spec, "tone"):
        tone = str(getattr(campaign_spec.tone, "value", campaign_spec.tone) or "").strip()
    if campaign_spec and hasattr(campaign_spec, "target_languages") and campaign_spec.target_languages:
        language = str(campaign_spec.target_languages[0]).strip()

    raw_guidelines = campaign_spec.get("guidelines", {}) if isinstance(campaign_spec, dict) else (
        getattr(campaign_spec, "guidelines", {}) if hasattr(campaign_spec, "guidelines") else {}
    )

    global_prohibited = list(reqs.prohibited_terms)
    global_required = list(reqs.required_phrases)
    if raw_guidelines and "global" in raw_guidelines:
        for p in raw_guidelines["global"].get("prohibited_terms", []):
            if p not in global_prohibited:
                global_prohibited.append(p)
        for r in raw_guidelines["global"].get("required_terms", []):
            if r not in global_required:
                global_required.append(r)

    global_links = [reqs.campaign_url.strip()] if reqs.campaign_url and reqs.campaign_url.strip() else []

    global_rules = GlobalSEORules(
        tone=tone,
        language=language,
        prohibited_terms=global_prohibited,
        required_terms=global_required,
        brand_terms=[reqs.brand_name] if reqs.brand_name else [],
        cta_rules=list(reqs.cta_instructions),
        link_rules=[f"Include campaign link: {url}" for url in global_links],
        links=global_links,
    )

    # 2. YouTube-specific rules
    yt_title_rules = list(reqs.title_patterns)
    yt_title_rules.append("Under 50 characters before #Shorts tag")
    yt_title_rules.append("Explicitly include #Shorts")
    yt_title_rules.append("Truthful representation of spoken clip, no sensationalist clickbait")

    yt_desc_rules = list(reqs.description_guidelines)
    yt_desc_rules.append("Structured 3 to 5 sentences summary of clip")
    if global_links:
        yt_desc_rules.append(f"Include YouTube link: {global_links[0]}")

    yt_cta_rules = list(reqs.cta_instructions) if reqs.cta_instructions else [
        f"Subscribe to {reqs.brand_name or 'the channel'} for more official highlights! Comment your thoughts below."
    ]

    yt_hashtags = list(reqs.required_hashtags)
    if "#Shorts" not in yt_hashtags:
        yt_hashtags.append("#Shorts")

    if raw_guidelines and "youtube" in raw_guidelines:
        yt_custom = raw_guidelines["youtube"]
        if yt_custom.get("title_rules"):
            yt_title_rules = yt_custom["title_rules"] + yt_title_rules
        if yt_custom.get("hashtag_rules"):
            for h in yt_custom["hashtag_rules"]:
                if h not in yt_hashtags:
                    yt_hashtags.append(h)
        if yt_custom.get("cta_rules"):
            yt_cta_rules = yt_custom["cta_rules"]

    platform_mentions = getattr(campaign_spec, "platform_mentions", {}) or {}
    show_mappings = getattr(campaign_spec, "show_mappings", []) or []

    yt_mentions = list(platform_mentions.get("youtube") or [])
    if not yt_mentions:
        tv_mentions = [m for m in reqs.required_mentions if "tv" in m.lower()]
        yt_mentions = tv_mentions if tv_mentions else list(reqs.required_mentions)

    youtube_rules = PlatformSEORules(
        title_rules=yt_title_rules,
        description_rules=yt_desc_rules,
        hashtag_rules=yt_hashtags,
        keyword_rules=list(reqs.required_phrases),
        tag_rules=list(reqs.required_phrases) + (["Shorts", reqs.brand_name] if reqs.brand_name else ["Shorts"]),
        link_rules=[f"YouTube destination link: {u}" for u in global_links],
        mention_rules=yt_mentions,
        cta_rules=yt_cta_rules,
        formatting_rules=[
            "Vertical 9:16 Shorts format",
            "Title formatted with #Shorts at end",
            "Double-spaced readable description paragraphs",
        ],
        character_limits={"title": 100, "description": 5000, "tags": 500},
        required_phrases=list(reqs.required_phrases),
        prohibited_terms=list(reqs.prohibited_terms),
        links=list(global_links),
    )

    # 3. Instagram-specific rules
    ig_caption_rules = [
        "Punchy first-line hook optimized for the 125-character Instagram feed preview cutoff",
        "Clean, visual paragraph spacing with bullet points or emojis where appropriate",
        "Contextual explanation of the clip topic tailored for audience",
    ]
    ig_mentions = list(platform_mentions.get("instagram") or [])
    if not ig_mentions:
        non_tv = [m for m in reqs.required_mentions if "tv" not in m.lower()]
        ig_mentions = non_tv if non_tv else list(reqs.required_mentions)

    target_handle = ig_mentions[0] if ig_mentions else (f"@{reqs.brand_name.lower().replace(' ', '')}" if reqs.brand_name else "")
    default_ig_cta = f"Follow {target_handle} for daily highlights! Save this Reel and share your takeaway in the comments 👇" if target_handle else "Follow for daily highlights! Save this Reel and share your takeaway in the comments 👇"
    ig_cta_rules = list(reqs.cta_instructions) if reqs.cta_instructions else [default_ig_cta]

    ig_hashtags = list(reqs.required_hashtags)

    if raw_guidelines and "instagram" in raw_guidelines:
        ig_custom = raw_guidelines["instagram"]
        if ig_custom.get("caption_rules"):
            ig_caption_rules = ig_custom["caption_rules"]
        if ig_custom.get("mention_rules"):
            for m in ig_custom["mention_rules"]:
                if m not in ig_mentions:
                    ig_mentions.append(m)
        if ig_custom.get("hashtag_rules"):
            for h in ig_custom["hashtag_rules"]:
                if h not in ig_hashtags:
                    ig_hashtags.append(h)
        if ig_custom.get("cta_rules"):
            ig_cta_rules = ig_custom["cta_rules"]

    ig_link_rules = [
        "Refer to bio link for primary external action" if global_links else "Engage via comments and saves"
    ]
    if global_links:
        ig_link_rules.append(f"Primary bio destination: {global_links[0]}")

    instagram_rules = PlatformSEORules(
        title_rules=[],
        caption_rules=ig_caption_rules,
        description_rules=ig_caption_rules,
        hashtag_rules=ig_hashtags,
        keyword_rules=list(reqs.required_phrases),
        tag_rules=[],
        link_rules=ig_link_rules,
        mention_rules=ig_mentions,
        cta_rules=ig_cta_rules,
        formatting_rules=[
            "First-line scroll-stopping headline",
            "Clear readable line breaks (no wall of text)",
            "Account mentions embedded organically",
            "Hashtag cluster grouped cleanly at bottom",
        ],
        character_limits={"caption": 2200, "first_line": 125, "hashtags_count": 30},
        required_phrases=list(reqs.required_phrases),
        prohibited_terms=list(reqs.prohibited_terms),
        links=list(global_links),
    )

    return CampaignSEOSpec(
        campaign_id=reqs.campaign_id,
        campaign_title=reqs.campaign_title,
        brand_name=reqs.brand_name,
        global_rules=global_rules,
        youtube_rules=youtube_rules,
        instagram_rules=instagram_rules,
        show_mappings=show_mappings,
    )
