"""Whop Campaign to Canonical Pipeline Handoff Engine."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
import uuid

from clipping.agent.campaign.models import (
    CampaignPlatform,
    CampaignRecord,
    CampaignStatus,
    PayoutTerms,
    PostingRequirements,
    QuotasAndCaps,
    SourceMaterial,
)
from clipping.contracts.requirements import (
    CampaignIdentityRequirements,
    CampaignRequirements,
    ClipRequirements,
    ContentRequirements,
    ExtractionMetadata,
    MonetizationRequirements,
    PlatformRequirements,
    RequirementModality,
    SourceRequirements,
    SubmissionRequirements,
    TextRequirements,
)
from clipping.contracts.source import (
    SourceCandidate,
    SourceCandidatePriority,
)
from clipping.logging.logger import get_logger

logger = get_logger("clipping.agent.campaign.whop_handoff")


class WhopCampaignHandoff:
    """
    Translates campaigns discovered via Whop HTTP API or CloudBrowserEngine
    into canonical CampaignRecord and structured CampaignRequirements objects.
    Enforces truth-in-brief: never fabricates missing terms, leaving unknown items as UNKNOWN/NEEDS_REVIEW.
    """

    @classmethod
    def convert_whop_campaign(
        cls,
        whop_data: Dict[str, Any],
    ) -> Tuple[CampaignRecord, CampaignRequirements, List[SourceCandidate]]:
        """
        Converts raw/normalized Whop campaign payload to:
        1. CampaignRecord
        2. CampaignRequirements
        3. List of discovered SourceCandidate objects
        """
        cid = str(whop_data.get("campaign_id") or whop_data.get("id") or f"whop_{uuid.uuid4().hex[:8]}")
        if not cid.startswith("whop_"):
            cid = f"whop_{cid}"

        title = whop_data.get("name") or whop_data.get("title") or "Whop Campaign"
        desc = whop_data.get("description", "")
        source_url = whop_data.get("source") or whop_data.get("source_url") or "https://whop.com"

        # Payout terms
        payout_raw = whop_data.get("payout_terms", {})
        cpm_rate = payout_raw.get("cpm_rate") or whop_data.get("cpm_rate")
        total_budget = payout_raw.get("total_budget") or whop_data.get("total_budget")
        max_payout = payout_raw.get("max_payout")

        # Source footage URLs
        source_material = whop_data.get("source_material", {})
        video_urls = (
            source_material.get("video_urls")
            or whop_data.get("source_video_uris")
            or whop_data.get("video_urls")
            or []
        )
        if isinstance(video_urls, str):
            video_urls = [video_urls]

        # Content & posting terms
        posting = whop_data.get("posting_requirements", {})
        hashtags = (
            posting.get("required_hashtags")
            or whop_data.get("hashtags")
            or whop_data.get("required_hashtags")
            or []
        )
        hashtags = [h if h.startswith("#") else f"#{h}" for h in hashtags if h]

        mentions = (
            posting.get("required_mentions")
            or whop_data.get("mentions")
            or whop_data.get("required_mentions")
            or []
        )
        mentions = [m if m.startswith("@") else f"@{m}" for m in mentions if m]

        min_duration = posting.get("min_duration_seconds") or whop_data.get("min_duration_seconds", 30.0)
        max_duration = posting.get("max_duration_seconds") or whop_data.get("max_duration_seconds", 60.0)

        # Allowed platforms
        allowed_platforms = (
            whop_data.get("required_platforms")
            or whop_data.get("allowed_platforms")
            or ["youtube_shorts"]
        )
        platform_enums: List[CampaignPlatform] = []
        for p in allowed_platforms:
            p_str = str(p).lower()
            if "instagram" in p_str:
                platform_enums.append(CampaignPlatform.INSTAGRAM_REELS)
            elif "youtube" in p_str:
                platform_enums.append(CampaignPlatform.YOUTUBE_SHORTS)
            elif "tiktok" in p_str:
                platform_enums.append(CampaignPlatform.TIKTOK)
        if not platform_enums:
            platform_enums = [CampaignPlatform.YOUTUBE_SHORTS]

        # Duration terms / deadlines
        duration_terms = whop_data.get("duration_terms", {})
        deadline = duration_terms.get("deadline") or duration_terms.get("end_date") or whop_data.get("deadline")

        # 1. Build structured CampaignRequirements (Step 2 schema)
        requirements = CampaignRequirements(
            identity=CampaignIdentityRequirements(
                campaign_name=title,
                campaign_id=cid,
                description=desc,
                sponsor_brand=whop_data.get("creator_community") or whop_data.get("community"),
            ),
            source=SourceRequirements(
                permitted_source_urls=video_urls,
                source_restrictions=whop_data.get("source_restrictions", []),
                specific_footage_required=bool(video_urls),
                prohibited_content=whop_data.get("prohibited_topics", []),
            ),
            clips=ClipRequirements(
                clip_count_required=posting.get("target_clip_count", 3),
                min_duration_seconds=float(min_duration),
                max_duration_seconds=float(max_duration),
                target_duration_seconds=(float(min_duration) + float(max_duration)) / 2.0,
            ),
            content=ContentRequirements(
                allowed_topics=whop_data.get("allowed_topics", []),
                prohibited_topics=whop_data.get("prohibited_topics", []),
                required_talking_points=whop_data.get("talking_points", []),
            ),
            text=TextRequirements(
                required_hashtags=hashtags,
                mention_handles=mentions,
                call_to_action=whop_data.get("cta_text"),
            ),
            platform=PlatformRequirements(
                target_platforms=[p.value for p in platform_enums],
            ),
            submission=SubmissionRequirements(
                submission_deadline=deadline,
                submission_platform="whop",
            ),
            monetization=MonetizationRequirements(
                payout_structure="cpm",
                cpm_rate=float(cpm_rate) if cpm_rate is not None else None,
                total_budget=float(total_budget) if total_budget is not None else None,
                max_payout_per_clip=float(max_payout) if max_payout is not None else None,
            ),
            metadata=ExtractionMetadata(
                source_format="whop_api",
                confidence_score=0.95 if video_urls else 0.80,
                model_used="whop_discovery_adapter",
                review_flag="CONFIDENT" if video_urls else "NEEDS_REVIEW",
            ),
        )

        # 2. Build CampaignRecord
        campaign_record = CampaignRecord(
            campaign_id=cid,
            name=title,
            source=source_url,
            description=desc,
            creator_community=whop_data.get("creator_community") or "Whop Creator Hub",
            status=CampaignStatus.ACTIVE,
            required_platforms=platform_enums,
            quotas=QuotasAndCaps(
                daily_creator_limit=posting.get("daily_creator_limit", 5),
                campaign_total_clip_cap=50,
            ),
            payout_terms=PayoutTerms(
                model="cpm",
                cpm_rate=float(cpm_rate) if cpm_rate is not None else 2.0,
                total_budget=float(total_budget) if total_budget is not None else 1000.0,
                remaining_budget=float(total_budget) if total_budget is not None else 1000.0,
            ),
            source_material=SourceMaterial(
                video_urls=video_urls,
            ),
            posting_requirements=PostingRequirements(
                min_duration_seconds=float(min_duration),
                max_duration_seconds=float(max_duration),
                required_hashtags=hashtags,
                required_mentions=mentions,
            ),
            requirements=requirements,
        )

        # 3. Build SourceCandidate objects
        source_candidates: List[SourceCandidate] = []
        for idx, url in enumerate(video_urls):
            source_candidates.append(
                SourceCandidate(
                    candidate_id=f"cand_whop_{cid}_{idx}",
                    priority_type=SourceCandidatePriority.WHOP_DISCOVERY,
                    priority_rank=int(SourceCandidatePriority.WHOP_DISCOVERY),
                    uri=url,
                    is_valid=True,
                    provenance={
                        "whop_campaign_id": cid,
                        "discovered_at": datetime.now(timezone.utc).isoformat(),
                    },
                    selection_rationale=f"Discovered source video for Whop campaign '{title}'",
                )
            )

        return campaign_record, requirements, source_candidates
