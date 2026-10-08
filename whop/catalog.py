"""Whop Campaign Catalog and Normalization (Step 2).

Defines the normalized data structures, parser functions, read-only eligibility
evaluators, and multi-account niche routing logic for discovered Whop campaigns.
Strictly non-mutating and deterministic. Never fabricates missing data.
"""

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# Valid short-form platforms accepted by the clipping engine
SUPPORTED_PLATFORMS = frozenset(["youtube", "instagram", "tiktok"])

# Target account routing identifiers
ACCOUNT_1_FINANCE_BUSINESS = "ACCOUNT_1_FINANCE_BUSINESS"
ACCOUNT_2_ENTERTAINMENT_PODCASTS = "ACCOUNT_2_ENTERTAINMENT_PODCASTS"
ACCOUNT_3_ALL_IN_ONE_VIRAL = "ACCOUNT_3_ALL_IN_ONE_VIRAL"

# Keyword indicators for niche routing
FINANCE_BUSINESS_KEYWORDS = frozenset([
    "trading", "crypto", "forex", "saas", "wealth", "finance", "ecommerce",
    "business", "investing", "stocks", "money", "marketing", "b2b", "agency",
    "real estate", "drop shipping", "dropshipping", "sales", "entrepreneur",
    "ai tool", "software", "fintech"
])

ENTERTAINMENT_PODCASTS_KEYWORDS = frozenset([
    "podcast", "interview", "comedy", "lifestyle", "storytime", "entertainment",
    "talk show", "celebrity", "gaming", "vlog", "humor", "creator",
    "drama", "culture", "reaction", "stories", "host"
])

ALL_IN_ONE_VIRAL_KEYWORDS = frozenset([
    "viral", "challenge", "facts", "motivation", "curiosity", "experiment",
    "satisfying", "compilation", "life hack", "trending", "clips", "highlight"
])


@dataclass
class DiscoveredCampaign:
    """Normalized, validated record of a discovered Whop campaign."""
    campaign_id: str
    title: str
    campaign_url: str
    payout_raw: str = ""
    cpm: Optional[float] = None
    platforms: List[str] = field(default_factory=list)
    source_urls: List[str] = field(default_factory=list)
    guideline_urls: List[str] = field(default_factory=list)
    mentions: List[str] = field(default_factory=list)
    hashtags: List[str] = field(default_factory=list)
    raw_text: str = ""
    discovered_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    discovery_source: str = "whop"
    
    # Read-only evaluation and routing metadata
    eligible: bool = False
    eligibility_reasons: List[str] = field(default_factory=list)
    niche_candidates: List[str] = field(default_factory=list)
    recommended_account: Optional[str] = None
    routing_reason: str = "UNEVALUATED"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def parse_cpm(payout_raw: Optional[str]) -> Optional[float]:
    """Extracts and normalizes CPM rate as float or None.

    Supports patterns like:
    - '$1.00 CPM' -> 1.0
    - '$2.50 / 1k' -> 2.5
    - '$3 / 1,000 views' -> 3.0
    - 'CPM: $1.75' -> 1.75
    - '€2.00 CPM' -> 2.0
    - '$0.50' (if labeled as CPM in text) -> 0.5

    Returns None if ambiguous, flat-rate, or missing. Never guesses.
    """
    if not payout_raw:
        return None

    clean = payout_raw.strip().replace(",", "")

    # Check for negative / zero / meaningless
    if not any(char.isdigit() for char in clean):
        return None

    # Pattern 1: $X.XX CPM or CPM: $X.XX or $X CPM
    m1 = re.search(r"[\$€£]?\s*(\d+(?:\.\d+)?)\s*(?:cpm|\/\s*(?:1k|1000|1,000)\s*(?:views)?)", clean, re.IGNORECASE)
    if m1:
        try:
            return float(m1.group(1))
        except ValueError:
            return None

    # Pattern 2: CPM [:]? [$€£]? X.XX
    m2 = re.search(r"cpm\s*[:\-]?\s*[\$€£]?\s*(\d+(?:\.\d+)?)", clean, re.IGNORECASE)
    if m2:
        try:
            return float(m2.group(1))
        except ValueError:
            return None

    # Pattern 3: $X.XX per 1k views / per 1,000
    m3 = re.search(r"[\$€£]?\s*(\d+(?:\.\d+)?)\s*per\s*(?:1k|1000|1,000|\bk\b)\b", clean, re.IGNORECASE)
    if m3:
        try:
            return float(m3.group(1))
        except ValueError:
            return None

    return None


def normalize_platforms(platforms_input: Any) -> List[str]:
    """Normalizes raw platform strings or lists into lowercase canonical platform IDs.

    Supported output: 'youtube', 'instagram', 'tiktok'
    Never invents a platform.
    """
    if not platforms_input:
        return []

    tokens: List[str] = []
    if isinstance(platforms_input, str):
        tokens = re.split(r"[,\|\/\+&]", platforms_input)
    elif isinstance(platforms_input, (list, tuple, set)):
        tokens = [str(x) for x in platforms_input]

    found: set[str] = set()
    for tok in tokens:
        t = tok.strip().lower()
        if "youtube" in t or "shorts" in t or "yt" == t:
            found.add("youtube")
        if "instagram" in t or "reels" in t or "insta" in t or "ig" == t:
            found.add("instagram")
        if "tiktok" in t or "tt" == t:
            found.add("tiktok")

    return sorted(list(found))


def evaluate_eligibility(
    cpm: Optional[float],
    platforms: List[str],
    source_urls: List[str],
    title: str,
    raw_text: str,
    require_drive_or_direct: bool = False,
) -> Tuple[bool, List[str]]:
    """Evaluates campaign eligibility based strictly on read-only acceptance rules.

    A. CPM >= $1.00
    B. Supports at least one short-form platform (youtube, instagram, tiktok)
    C. Usable source/media reference exists
    D. Campaign title and data are sufficiently complete
    E. If require_drive_or_direct is True: must have Google Drive or direct media (S3/MP4) source
    """
    reasons: List[str] = []

    # 1. Payout Check
    if cpm is None:
        reasons.append("UNKNOWN_PAYOUT")
    elif cpm < 1.00:
        reasons.append("REJECTED_PAYOUT")

    # 2. Platform Check
    valid_platforms = [p for p in platforms if p in SUPPORTED_PLATFORMS]
    if not platforms:
        reasons.append("UNKNOWN_PLATFORM")
    elif not valid_platforms:
        reasons.append("REJECTED_PLATFORM")

    # 3. Source Reference Check: Must have a valid Google Drive or direct media source
    has_drive_or_direct = bool(source_urls and any(
        ("drive.google.com" in str(u).lower())
        or any(str(u).lower().split("?")[0].endswith(ext) for ext in (".mp4", ".mov", ".mkv", ".webm"))
        or ("amazonaws.com" in str(u).lower() and not str(u).lower().endswith(".pdf"))
        for u in source_urls
    ))
    if not source_urls:
        reasons.append("REJECTED_SOURCE")
    elif not has_drive_or_direct:
        reasons.append("REJECTED_NON_DRIVE_SOURCE")
        reasons.append("REJECTED_NO_GOOGLE_DRIVE_SOURCE")

    # 4. Completeness Check
    if not title or len(title.strip()) < 3:
        reasons.append("INCOMPLETE_CAMPAIGN_DATA")

    # 5. Dedicated Channel / Account Rejection:
    # Reject campaigns requiring a dedicated/new account or channel
    DEDICATED_ACCOUNT_KEYWORDS = [
        "dedicated account", "dedicated channel", "new account only",
        "separate account", "new channel only", "must create a new",
        "fresh handle", "dedicated page", "dedicated handle",
        "no personal account", "only dedicated", "new accounts only",
        "fresh account only", "create a dedicated", "must be a dedicated",
    ]
    raw_lower = raw_text.lower()
    title_lower = title.lower()
    if any(k in raw_lower or k in title_lower for k in DEDICATED_ACCOUNT_KEYWORDS):
        reasons.append("REJECTED_REQUIRES_DEDICATED_ACCOUNT")

    # 6. Strict Clipping vs UGC Gate:
    # Reject UGC campaigns (User Generated Content, filming yourself, SaaS app reviews, etc.)
    UGC_KEYWORDS = [
        "ugc", "product review", "saas demo", "testimonial",
        "promote our", "video of yourself", "talking head", "record a video of",
        "explain our", "create ugc", "create a video explaining"
    ]
    if any(k in title_lower or k in raw_lower for k in UGC_KEYWORDS):
        reasons.append("REJECTED_UGC_CAMPAIGN")

    # Strictly require clipping indicators (streamer, twitch, kick, podcast, vod, gaming, clipping, clips)
    CLIPPING_KEYWORDS = [
        "clip", "clipping", "clips", "twitch", "streamer", "stream",
        "vod", "podcast", "gaming", "highlights", "interview", "talk show"
    ]
    if not any(k in title_lower or k in raw_lower for k in CLIPPING_KEYWORDS):
        reasons.append("REJECTED_NON_CLIPPING_CAMPAIGN")

    # Decision
    if not reasons:
        return True, ["ELIGIBLE"]

    return False, reasons


def route_niche(
    title: str,
    raw_text: str,
    hashtags: Optional[List[str]] = None,
) -> Tuple[List[str], Optional[str], str]:
    """Calculates non-mutating multi-account niche routing recommendations."""
    blob = f"{title} {raw_text} {' '.join(hashtags or [])}".lower()
    words = set(re.findall(r"\b[a-z0-9\-]+\b", blob))

    score_1 = len(words.intersection(FINANCE_BUSINESS_KEYWORDS))
    score_2 = len(words.intersection(ENTERTAINMENT_PODCASTS_KEYWORDS))
    score_3 = len(words.intersection(ALL_IN_ONE_VIRAL_KEYWORDS))

    candidates: List[str] = []
    if score_1 > 0:
        candidates.append(ACCOUNT_1_FINANCE_BUSINESS)
    if score_2 > 0:
        candidates.append(ACCOUNT_2_ENTERTAINMENT_PODCASTS)
    if score_3 > 0:
        candidates.append(ACCOUNT_3_ALL_IN_ONE_VIRAL)

    scores = [
        (score_1, ACCOUNT_1_FINANCE_BUSINESS, "Strong finance/business keyword indicators"),
        (score_2, ACCOUNT_2_ENTERTAINMENT_PODCASTS, "Strong entertainment/podcast keyword indicators"),
        (score_3, ACCOUNT_3_ALL_IN_ONE_VIRAL, "Strong viral/challenge keyword indicators"),
    ]

    scores.sort(key=lambda x: x[0], reverse=True)
    top_score, top_account, top_reason = scores[0]
    second_score = scores[1][0]

    if top_score == 0:
        return [], None, "NO_CONFIDENT_NICHE_MATCH"

    if top_score == second_score and top_score > 0:
        return candidates, None, f"AMBIGUOUS_NICHE_MATCH_TIE (score {top_score})"

    return candidates, top_account, f"{top_reason} (confidence_score={top_score})"


def build_discovered_campaign(
    campaign_id: str,
    title: str,
    campaign_url: str,
    payout_raw: str = "",
    platforms_raw: Any = None,
    source_urls: Optional[List[str]] = None,
    guideline_urls: Optional[List[str]] = None,
    mentions: Optional[List[str]] = None,
    hashtags: Optional[List[str]] = None,
    raw_text: str = "",
    discovered_at: Optional[str] = None,
    require_drive_or_direct: bool = False,
) -> DiscoveredCampaign:
    """Builder that normalizes all fields, evaluates eligibility, and assigns routing."""
    cpm = parse_cpm(payout_raw)
    platforms = normalize_platforms(platforms_raw)
    sources = [u.strip() for u in (source_urls or []) if u and u.strip()]
    guidelines = [u.strip() for u in (guideline_urls or []) if u and u.strip()]
    clean_mentions = [m.strip() for m in (mentions or []) if m and m.strip()]
    clean_hashtags = [h.strip() for h in (hashtags or []) if h and h.strip()]

    is_eligible, reasons = evaluate_eligibility(
        cpm=cpm,
        platforms=platforms,
        source_urls=sources,
        title=title,
        raw_text=raw_text,
        require_drive_or_direct=require_drive_or_direct,
    )

    niche_candidates, recommended_account, routing_reason = route_niche(
        title=title,
        raw_text=raw_text,
        hashtags=clean_hashtags,
    )

    return DiscoveredCampaign(
        campaign_id=str(campaign_id).strip(),
        title=str(title).strip(),
        campaign_url=str(campaign_url).strip(),
        payout_raw=str(payout_raw).strip(),
        cpm=cpm,
        platforms=platforms,
        source_urls=sources,
        guideline_urls=guidelines,
        mentions=clean_mentions,
        hashtags=clean_hashtags,
        raw_text=str(raw_text).strip(),
        discovered_at=discovered_at or datetime.now(timezone.utc).isoformat(),
        discovery_source="whop",
        eligible=is_eligible,
        eligibility_reasons=reasons,
        niche_candidates=niche_candidates,
        recommended_account=recommended_account,
        routing_reason=routing_reason,
    )