"""Campaign Discovery Agent Capability."""

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from clipping.agent.browser.driver import BrowserDriver, PlaywrightBrowserDriver
from clipping.agent.browser.engine import CloudBrowserEngine
from clipping.agent.browser.models import BrowserAction, BrowserActionType
from clipping.agent.campaign.models import (
    AccountRequirements,
    CampaignPlatform,
    CampaignRecord,
    CampaignStatus,
    PostingRequirements,
)
from clipping.agent.campaign.repository import CampaignRepository
from clipping.agent.capabilities.base import AgentCapability, CapabilityContext, CapabilityResult
from clipping.agent.escalation import EscalationContext, EscalationReason, EscalationSeverity
from clipping.logging.logger import get_logger

logger = get_logger("clipping.agent.campaign.discovery")


class CampaignDiscoveryCapability(AgentCapability):
    """
    Capability that discovers and normalizes campaigns from web platforms or direct specifications.
    Enforces deduplication, contract validation, and operator escalation for contradictory rules.
    """

    def __init__(
        self,
        repository: Optional[CampaignRepository] = None,
        browser_driver: Optional[BrowserDriver] = None,
    ):
        self._repo = repository
        self._driver = browser_driver

    @property
    def name(self) -> str:
        return "campaign_discovery"

    @property
    def description(self) -> str:
        return "Autonomously discovers, extracts, and validates campaigns from web sources or specifications"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def is_idempotent(self) -> bool:
        return True

    @property
    def is_reversible(self) -> bool:
        return True

    async def execute(self, context: CapabilityContext) -> CapabilityResult:
        repo = self._repo or CampaignRepository(storage_driver=context.storage_driver)
        inputs = context.inputs

        source = inputs.get("source", "direct_submission")
        raw_items: List[Dict[str, Any]] = inputs.get("campaigns", [])

        # If a direct single campaign spec is supplied in inputs:
        if "campaign" in inputs and isinstance(inputs["campaign"], dict):
            raw_items.append(inputs["campaign"])

        # If source is a web URL and no raw items are supplied, use browser to discover:
        if not raw_items and source.startswith("http"):
            browser_engine = CloudBrowserEngine(driver=self._driver)
            async with browser_engine:
                actions = [
                    BrowserAction(action_type=BrowserActionType.NAVIGATE, url=source),
                    BrowserAction(action_type=BrowserActionType.WAIT_FOR_SELECTOR, selector="body", timeout_ms=5000),
                ]
                res = await browser_engine.execute_workflow(actions)

                challenge = res.get("challenge")
                if challenge:
                    reason = EscalationReason.CAPTCHA_CHALLENGE if challenge == "captcha" else EscalationReason.MFA_REQUIRED
                    return CapabilityResult.escalate(
                        EscalationContext(
                            what_happened=f"Campaign source blocked by {challenge.upper()}",
                            why_it_happened=f"Automated navigation to {source} encountered a {challenge.upper()} challenge",
                            decision_required=f"Solve {challenge.upper()} challenge or supply alternative campaign discovery source",
                            available_options=["solve_challenge", "skip_source"],
                            metadata={"task_id": context.task_id, "reason": reason.value, "source_url": source},
                        )
                    )

                page_data = res.get("page_data")
                if page_data and page_data.text_content:
                    # In production web discovery, structured extraction or LLM parser parses campaigns.
                    # Here we support structured metadata if available in page_data:
                    extracted_campaign = self._extract_campaign_from_page(page_data, source)
                    if extracted_campaign:
                        raw_items.append(extracted_campaign)

        if not raw_items:
            logger.info("No campaigns found at source", source=source)
            return CapabilityResult.successful(
                outputs={"discovered_count": 0, "campaign_ids": [], "message": "No new campaigns found"}
            )

        discovered_ids: List[str] = []
        new_or_updated: List[CampaignRecord] = []

        for item in raw_items:
            # 1. Normalize
            cid = item.get("campaign_id") or self._generate_id(item.get("name", "campaign"), source)
            platforms = [
                CampaignPlatform(p) if isinstance(p, str) else p
                for p in item.get("required_platforms", ["youtube_shorts"])
            ]

            posting_req_data = item.get("posting_requirements", {})
            posting_reqs = (
                PostingRequirements(**posting_req_data)
                if isinstance(posting_req_data, dict)
                else posting_req_data
            )

            account_req_data = item.get("account_requirements", {})
            account_reqs = (
                AccountRequirements(**account_req_data)
                if isinstance(account_req_data, dict)
                else account_req_data
            )

            record = CampaignRecord(
                campaign_id=cid,
                name=item.get("name", f"Campaign {cid}"),
                source=source,
                description=item.get("description", ""),
                status=CampaignStatus(item.get("status", "active")),
                required_platforms=platforms,
                allowed_content_rules=item.get("allowed_content_rules", []),
                prohibited_content_rules=item.get("prohibited_content_rules", []),
                posting_requirements=posting_reqs,
                account_requirements=account_reqs,
                reuse_restrictions=item.get("reuse_restrictions"),
                seo_requirements=item.get("seo_requirements", {}),
                payment_info=item.get("payment_info"),
                source_video_requirements=item.get("source_video_requirements", {}),
                discovered_source_uris=item.get("discovered_source_uris", []),
            )

            # 2. Validate rules for contradictions
            contradiction = record.validate_rules()
            if contradiction:
                logger.warning("Contradictory campaign rules detected, escalating", campaign_id=cid, reason=contradiction)
                return CapabilityResult.escalate(
                    EscalationContext(
                        what_happened=f"Campaign '{record.name}' contains contradictory rules",
                        why_it_happened=contradiction,
                        decision_required="Review campaign requirements and resolve conflicting duration or content rules",
                        available_options=["resolve_contradiction", "reject_campaign"],
                        reason=EscalationReason.CONTRADICTORY_INSTRUCTIONS,
                        severity=EscalationSeverity.HIGH,
                        metadata={
                            "task_id": context.task_id,
                            "campaign_id": cid,
                            "contradiction": contradiction,
                        },
                    )
                )

            # 3. Check deduplication
            is_known = await repo.is_known_campaign(cid)
            if not is_known:
                logger.info("New campaign discovered", campaign_id=cid, name=record.name)
            else:
                logger.info("Updating existing campaign", campaign_id=cid, name=record.name)

            await repo.save_campaign(record)
            discovered_ids.append(cid)
            new_or_updated.append(record)

        return CapabilityResult.successful(
            outputs={
                "discovered_count": len(new_or_updated),
                "campaign_ids": discovered_ids,
            },
            checkpoint={"last_discovered_campaign_id": discovered_ids[-1] if discovered_ids else None},
        )

    @staticmethod
    def _generate_id(name: str, source: str) -> str:
        h = hashlib.sha256(f"{name}_{source}".encode("utf-8")).hexdigest()[:12]
        clean_name = "".join(c if c.isalnum() else "_" for c in name.lower()).strip("_")[:24]
        return f"camp_{clean_name}_{h}"

    @staticmethod
    def _extract_campaign_from_page(page_data: Any, source: str) -> Optional[Dict[str, Any]]:
        title = page_data.title or "Discovered Campaign"
        text = page_data.text_content or ""
        return {
            "name": title,
            "description": text[:300],
            "source": source,
            "status": "active",
            "required_platforms": ["youtube_shorts"],
            "discovered_source_uris": [],
        }
