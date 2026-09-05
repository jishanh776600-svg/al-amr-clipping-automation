"""Campaign Discovery Agent Capability.

Autonomously discovers, extracts, normalizes, and ranks campaigns from Whop and other legitimate sources.
Detects duplicates, tracks term changes over time, checks for contradictions, and computes opportunity scores.
"""

import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from clipping.agent.browser.driver import BrowserDriver
from clipping.agent.campaign.evaluator import CampaignEvaluator
from clipping.agent.campaign.intelligence import CampaignIntelligenceEngine
from clipping.agent.campaign.models import (
    AccountRequirements,
    CampaignDuration,
    CampaignPlatform,
    CampaignRecord,
    CampaignStatus,
    PayoutModel,
    PayoutTerms,
    PostCampaignRules,
    PostingRequirements,
    QuotasAndCaps,
    SourceMaterial,
)
from clipping.agent.campaign.repository import CampaignRepository
from clipping.agent.campaign.sources.registry import CampaignSourceRegistry
from clipping.agent.campaign.sources.whop import WhopCampaignSource
from clipping.agent.capabilities.base import AgentCapability, CapabilityContext, CapabilityResult
from clipping.agent.escalation import EscalationContext, EscalationReason, EscalationSeverity
from clipping.logging.logger import get_logger

logger = get_logger("clipping.agent.campaign.discovery")


class CampaignDiscoveryCapability(AgentCapability):
    """
    Production capability that discovers, extracts, normalizes, and ranks campaigns.
    Whop is the primary discovery engine, with extensible source integration,
    contradiction detection, duplicate auditing, and multi-factor economic evaluation.
    """

    def __init__(
        self,
        repository: Optional[CampaignRepository] = None,
        browser_driver: Optional[BrowserDriver] = None,
        source_registry: Optional[CampaignSourceRegistry] = None,
        evaluator: Optional[CampaignEvaluator] = None,
    ):
        self._repo = repository
        self._driver = browser_driver
        self._evaluator = evaluator or CampaignEvaluator()
        
        if source_registry:
            self._registry = source_registry
        else:
            self._registry = CampaignSourceRegistry()
            # If a browser driver was passed, wire it to the Whop source in registry
            if browser_driver:
                self._registry.register(WhopCampaignSource(browser_driver=browser_driver))

    @property
    def name(self) -> str:
        return "campaign_discovery"

    @property
    def description(self) -> str:
        return "Autonomously discovers, extracts, and validates clipping campaigns from Whop and creator marketplaces"

    @property
    def version(self) -> str:
        return "2.0.0"

    @property
    def is_idempotent(self) -> bool:
        return True

    @property
    def is_reversible(self) -> bool:
        return True

    async def execute(self, context: CapabilityContext) -> CapabilityResult:
        repo = self._repo or CampaignRepository(storage_driver=context.storage_driver)
        inputs = context.inputs

        source_name = inputs.get("source", "whop")
        raw_items: List[Dict[str, Any]] = list(inputs.get("campaigns", []))

        # If a single direct campaign is supplied in inputs:
        if "campaign" in inputs and isinstance(inputs["campaign"], dict):
            raw_items.append(inputs["campaign"])

        # If no direct campaigns supplied, use registered campaign discovery sources:
        if not raw_items:
            source_impl = self._registry.get_source(source_name) or self._registry.get_primary_source()
            metadata = {
                "source_url": source_name if source_name.startswith("http") else None,
                "custom_endpoint": inputs.get("custom_endpoint"),
                "raw_campaigns": inputs.get("raw_campaigns"),
            }
            try:
                discovered_raw = await source_impl.discover(
                    query=inputs.get("query"),
                    platform=inputs.get("platform"),
                    limit=inputs.get("limit", 50),
                    metadata=metadata,
                )
                
                # Check for security challenge escalation from browser/source
                for item in discovered_raw:
                    if isinstance(item, dict) and "challenge" in item:
                        challenge = item["challenge"]
                        reason = EscalationReason.CAPTCHA_CHALLENGE if challenge == "captcha" else EscalationReason.MFA_REQUIRED
                        return CapabilityResult.escalate(
                            EscalationContext(
                                what_happened=f"Campaign discovery blocked by {challenge.upper()} challenge on {source_impl.name}",
                                why_it_happened=f"Automated discovery encountered a {challenge.upper()} security gate",
                                decision_required=f"Solve {challenge.upper()} challenge or supply alternative campaign discovery source",
                                available_options=["solve_challenge", "skip_source"],
                                metadata={"task_id": context.task_id, "reason": reason.value, "source": source_impl.source_id},
                            )
                        )

                raw_items.extend(discovered_raw)
            except Exception as e:
                logger.error("Campaign discovery error from source", source=source_impl.source_id, error=str(e))
                return CapabilityResult.failed(
                    error_type="DiscoveryError",
                    error_message=f"Failed to discover campaigns from {source_impl.name}: {str(e)}",
                    is_transient=True,
                )

        if not raw_items:
            logger.info("No campaigns found during discovery", source=source_name)
            return CapabilityResult.successful(
                outputs={"discovered_count": 0, "campaign_ids": [], "message": "No new campaigns found"}
            )

        # Load existing campaigns for duplicate detection
        existing_campaigns = await repo.list_campaigns()

        discovered_ids: List[str] = []
        ranked_summaries: List[Dict[str, Any]] = []

        for item in raw_items:
            # 1. Normalize into CampaignRecord
            candidate = self._normalize_item(item, source_name)

            # 2. Check for contradictory rules & impossible constraints
            contradiction = candidate.validate_rules()
            if contradiction:
                logger.warning("Contradictory campaign rules detected, escalating", campaign_id=candidate.campaign_id, reason=contradiction)
                return CapabilityResult.escalate(
                    EscalationContext(
                        what_happened=f"Campaign '{candidate.name}' contains contradictory rules",
                        why_it_happened=contradiction,
                        decision_required="Review campaign requirements and resolve conflicting duration, payout, or content rules",
                        available_options=["resolve_contradiction", "reject_campaign"],
                        reason=EscalationReason.CONTRADICTORY_INSTRUCTIONS,
                        severity=EscalationSeverity.HIGH,
                        metadata={
                            "task_id": context.task_id,
                            "campaign_id": candidate.campaign_id,
                            "contradiction": contradiction,
                        },
                    )
                )

            # 3. Intelligent Evaluation & Opportunity Scoring
            score = self._evaluator.evaluate(candidate)
            candidate = candidate.model_copy(
                update={
                    "opportunity_score": score.overall_score,
                    "opportunity_tier": score.tier.value,
                }
            )

            # 4. Duplicate Detection & Term Drift Auditing
            duplicate = CampaignIntelligenceEngine.detect_duplicate(candidate, existing_campaigns)
            if duplicate:
                logger.info("Existing campaign updated with latest crawl terms", campaign_id=duplicate.campaign_id)
                final_record = CampaignIntelligenceEngine.audit_and_merge(duplicate, candidate)
            else:
                logger.info("New high-intelligence campaign registered", campaign_id=candidate.campaign_id, score=score.overall_score)
                final_record = candidate
                existing_campaigns.append(final_record)

            await repo.save_campaign(final_record)
            discovered_ids.append(final_record.campaign_id)
            ranked_summaries.append({
                "campaign_id": final_record.campaign_id,
                "name": final_record.name,
                "score": score.overall_score,
                "tier": score.tier.value,
                "cpm_rate": final_record.payout_terms.cpm_rate,
                "earning_potential": score.estimated_earning_potential,
                "recommendation": score.recommendation_notes[0] if score.recommendation_notes else "Viable opportunity",
            })

        # Sort ranked summaries by score descending
        ranked_summaries.sort(key=lambda x: x["score"], reverse=True)

        return CapabilityResult.successful(
            outputs={
                "discovered_count": len(discovered_ids),
                "campaign_ids": discovered_ids,
                "ranked_campaigns": ranked_summaries,
                "top_opportunity": ranked_summaries[0] if ranked_summaries else None,
            },
            checkpoint={"last_discovered_campaign_id": discovered_ids[-1] if discovered_ids else None},
        )

    def _normalize_item(self, item: Dict[str, Any], source: str) -> CampaignRecord:
        """Normalizes raw dictionary into full CampaignRecord."""
        cid = item.get("campaign_id") or item.get("id") or self._generate_id(item.get("name") or item.get("title", "campaign"), source)
        title = item.get("name") or item.get("title") or f"Campaign {cid}"

        # Normalize platforms
        raw_platforms = item.get("required_platforms") or item.get("allowed_platforms") or ["youtube_shorts"]
        platforms = [
            CampaignPlatform(p) if isinstance(p, str) else p
            for p in raw_platforms
        ]

        # Normalize Posting Requirements
        p_req_data = item.get("posting_requirements", {})
        if isinstance(p_req_data, dict):
            raw_tags = p_req_data.get("required_hashtags", item.get("hashtags", []))
            formatted_tags = [t if t.startswith("#") else f"#{t}" for t in raw_tags]
            posting_reqs = PostingRequirements(
                min_duration_seconds=p_req_data.get("min_duration_seconds", item.get("min_duration_seconds", 30.0)),
                max_duration_seconds=p_req_data.get("max_duration_seconds", item.get("max_duration_seconds", 60.0)),
                required_hashtags=formatted_tags,
                required_mentions=p_req_data.get("required_mentions", item.get("mentions", [])),
                daily_post_limit=p_req_data.get("daily_post_limit", item.get("daily_post_limit", 3)),
            )
        else:
            posting_reqs = p_req_data

        # Normalize Account Requirements
        a_req_data = item.get("account_requirements", {})
        if isinstance(a_req_data, dict):
            account_reqs = AccountRequirements(
                allowed_platforms=platforms,
                allow_account_reuse=a_req_data.get("allow_account_reuse", item.get("allow_account_reuse", True)),
                min_subscribers=a_req_data.get("min_subscribers", item.get("min_subscribers", 0)),
            )
        else:
            account_reqs = a_req_data

        # Normalize Payout Terms
        payout_raw = item.get("payout_terms") or item.get("payment_info") or {}
        cpm_rate = payout_raw.get("cpm_rate") or item.get("cpm_rate") or item.get("cpm") or (item.get("min_payout", 2.0) if "cpm" in str(item).lower() else None)
        if cpm_rate is not None:
            try:
                cpm_rate = float(cpm_rate)
            except (ValueError, TypeError):
                cpm_rate = 2.0

        payout_terms = PayoutTerms(
            model=PayoutModel(payout_raw.get("model", "cpm")),
            cpm_rate=cpm_rate,
            fixed_amount=payout_raw.get("fixed_amount") or item.get("fixed_amount"),
            min_payout=payout_raw.get("min_payout") or item.get("min_payout"),
            max_payout=payout_raw.get("max_payout") or item.get("max_payout"),
            currency=payout_raw.get("currency", "USD"),
            total_budget=payout_raw.get("total_budget") or item.get("total_budget"),
            remaining_budget=payout_raw.get("remaining_budget") or item.get("remaining_budget") or item.get("total_budget"),
            budget_exhausted=bool(payout_raw.get("budget_exhausted") or item.get("budget_exhausted", False)),
        )

        # Source Material & Video URIs
        source_uris = (
            item.get("discovered_source_uris")
            or item.get("source_video_uris")
            or item.get("video_urls")
            or item.get("source_urls")
            or (item.get("source_material", {}).get("video_urls") if isinstance(item.get("source_material"), dict) else [])
            or []
        )
        if isinstance(source_uris, str):
            source_uris = [source_uris]

        source_material = SourceMaterial(
            video_urls=source_uris,
            google_drive_folder=item.get("google_drive_folder") or (item.get("source_material", {}).get("google_drive_folder") if isinstance(item.get("source_material"), dict) else None),
            preferred_segments=item.get("preferred_segments", []),
        )

        # Duration Terms
        duration_raw = item.get("duration_terms", {})
        duration_terms = CampaignDuration(
            start_date=duration_raw.get("start_date") or item.get("start_date"),
            end_date=duration_raw.get("end_date") or item.get("end_date"),
            deadline=duration_raw.get("deadline") or item.get("deadline"),
            is_expired=bool(duration_raw.get("is_expired") or item.get("is_expired", False)),
        )

        # Quotas
        quotas_raw = item.get("quotas", {})
        quotas = QuotasAndCaps(
            daily_creator_limit=quotas_raw.get("daily_creator_limit", item.get("daily_creator_limit", 3)),
            max_submissions_per_creator=quotas_raw.get("max_submissions_per_creator", item.get("max_submissions_per_creator")),
        )

        # Post Campaign Rules
        post_rules_raw = item.get("post_campaign_rules", {})
        if isinstance(post_rules_raw, dict):
            post_campaign_rules = PostCampaignRules(
                allow_account_reuse_after_campaign=post_rules_raw.get("allow_account_reuse_after_campaign", True),
                privatize_videos_on_completion=post_rules_raw.get("privatize_videos_on_completion", False),
                delete_videos_on_completion=post_rules_raw.get("delete_videos_on_completion", False),
                cooldown_days_before_reuse=post_rules_raw.get("cooldown_days_before_reuse", 0),
                retain_branding=post_rules_raw.get("retain_branding", True),
            )
        elif isinstance(post_rules_raw, PostCampaignRules):
            post_campaign_rules = post_rules_raw
        else:
            post_campaign_rules = PostCampaignRules()

        return CampaignRecord(
            campaign_id=cid,
            name=title,
            source=item.get("source", source),
            description=item.get("description", ""),
            status=CampaignStatus(item.get("status", "active")),
            required_platforms=platforms,
            allowed_content_rules=item.get("allowed_content_rules", []),
            prohibited_content_rules=item.get("prohibited_content_rules", []),
            posting_requirements=posting_reqs,
            account_requirements=account_reqs,
            post_campaign_rules=post_campaign_rules,
            reuse_restrictions=item.get("reuse_restrictions"),
            seo_requirements=item.get("seo_requirements", {}),
            payment_info=payout_raw,
            source_video_requirements=item.get("source_video_requirements", {}),
            discovered_source_uris=source_uris,
            payout_terms=payout_terms,
            duration_terms=duration_terms,
            source_material=source_material,
            quotas=quotas,
            creator_community=item.get("creator_community") or item.get("community"),
            canonical_url=item.get("canonical_url") or item.get("source"),
        )


    @staticmethod
    def _generate_id(name: str, source: str) -> str:
        h = hashlib.sha256(f"{name}_{source}".encode("utf-8")).hexdigest()[:12]
        clean_name = "".join(c if c.isalnum() else "_" for c in name.lower()).strip("_")[:24]
        return f"camp_{clean_name}_{h}"
