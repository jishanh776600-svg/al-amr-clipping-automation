"""Whop Campaign Autonomous Joiner and Membership Engine (Step 10).

Executes genuine, idempotent campaign discovery and join mutations:
1. Inspects the authenticated Whop account to extract existing joined/claimed campaigns.
2. Filters marketplace candidates to guarantee NOT_ALREADY_JOINED = TRUE.
3. Preflights campaign eligibility and arms JOIN_MUTATION_ARMED.
4. Executes human-like click interaction via HumanActor with mutation guards.
5. Verifies post-join membership independently and persists durable ledger state.
6. Implements crash/timeout reconciliation so join actions are never duplicated.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from playwright.sync_api import Locator, Page

from .catalog import DiscoveredCampaign
from .config import FORBIDDEN_MUTATION_ACTIONS, WhopConfig, sanitize_text
from .human_interaction import HumanActor
from .ledger import CampaignLedger
from .models import CampaignRecord, CampaignState, WhopJoinRecord

log = logging.getLogger(__name__)

# Join and claim semantic UI selectors
JOIN_BUTTON_SELECTORS = [
    "button:has-text('Join campaign')",
    "button:has-text('Join Campaign')",
    "a:has-text('Join Campaign')",
    "a:has-text('Join campaign')",
    "a[href*='/join']",
    "button:has-text('Claim campaign')",
    "button:has-text('Claim Campaign')",
    "button:has-text('Join Reward')",
    "button:has-text('Join reward')",
    "button:has-text('Join')",
    "a:has-text('Join')",
    "button:has-text('Claim')",
    "a:has-text('Claim')",
    "[data-testid*='join-campaign']",
    "[data-testid*='claim-reward']",
    "[data-testid*='join-btn']",
    "button:has-text('Start Clipping')",
    "a:has-text('Start Clipping')",
    "button:has-text('Participate')",
]

# Post-join confirmed membership indicators
MEMBERSHIP_CONFIRMED_SELECTORS = [
    "button:has-text('Joined')",
    "a:has-text('Joined')",
    "button:has-text('Claimed')",
    "a:has-text('Claimed')",
    "button:has-text('Submit clip')",
    "button:has-text('Submit Clip')",
    "a:has-text('Submit clip')",
    "a:has-text('Submit Clip')",
    "button:has-text('Submit content')",
    "[data-testid*='submit-clip']",
    "[data-testid*='joined-badge']",
    ":text-matches('^Joined$', 'i')",
    ":text-matches('^Claimed$', 'i')",
    "text='You already have access to this free product'",
    "text='Visit your membership to access the content'",
]


class WhopJoinError(Exception):
    """Raised when campaign join mutation fails or encounters an unrecoverable error."""
    pass


class WhopAlreadyJoinedError(WhopJoinError):
    """Raised when an attempt is made to join a campaign that is already joined/claimed."""
    pass


class WhopJoinTimeoutError(WhopJoinError):
    """Raised when a join click interaction times out."""
    pass


class WhopCandidateNotEligibleError(WhopJoinError):
    """Raised when a candidate campaign does not meet required eligibility criteria."""
    pass


class WhopCampaignJoiner:
    """Production autonomous joiner for Whop creator campaigns."""

    def __init__(
        self,
        ledger: Optional[CampaignLedger] = None,
        config: Optional[WhopConfig] = None,
    ):
        self.ledger = ledger or CampaignLedger()
        self.config = config or WhopConfig.from_env()

    def get_account_joined_campaigns(self, page: Page) -> Set[str]:
        """Inspects the authenticated Whop account to identify all already-joined campaigns.
        
        Navigates to the user's hub/experiences dashboard, extracts campaign identifiers
        and URLs, and builds an authoritative set of joined campaign IDs to prevent reuse.
        """
        joined_ids: Set[str] = set()
        log.info("Inspecting authenticated Whop account for existing joined campaigns...")

        routes_to_inspect = ["/hub", "/experiences", "/creator/dashboard"]
        for route in routes_to_inspect:
            url = f"{self.config.base_url.rstrip('/')}{route}"
            try:
                log.info("Navigating to account route: %s", sanitize_text(url))
                page.goto(url, timeout=15000, wait_until="domcontentloaded")
                page.wait_for_timeout(2000)

                links = page.locator("a[href*='/discover/'], a[href*='/rewards/'], a[href*='/experiences/']")
                count = links.count()
                for i in range(min(count, 50)):
                    href = links.nth(i).get_attribute("href") or ""
                    clean_id = self._extract_campaign_id_from_url(href)
                    if clean_id:
                        joined_ids.add(clean_id)
            except Exception as e:
                log.debug("Account route check '%s' soft warning: %s", route, sanitize_text(str(e)))

        try:
            records = self.ledger.list_campaigns()
            for r in records:
                if r.current_state in (
                    CampaignState.CLAIMED,
                    CampaignState.INGESTED,
                    CampaignState.RENDERING,
                    CampaignState.RENDER_READY,
                    CampaignState.RENDER_WARN,
                    CampaignState.AWAITING_APPROVAL,
                    CampaignState.APPROVED,
                    CampaignState.SUBMITTING,
                    CampaignState.SUBMITTED,
                ):
                    joined_ids.add(r.campaign_id)
        except Exception as l_err:
            log.debug("Ledger inspection error: %s", l_err)

        log.info("Identified %d existing joined campaigns: %s", len(joined_ids), sorted(list(joined_ids)))
        return joined_ids

    def is_candidate_eligible_and_unjoined(
        self,
        candidate: DiscoveredCampaign,
        joined_ids: Set[str],
    ) -> Tuple[bool, str]:
        """Evaluates whether a candidate campaign is marketplace-eligible AND not already joined."""
        cid = candidate.campaign_id.strip()
        if cid in joined_ids:
            return False, f"REJECT: Campaign '{cid}' is already in the joined/claimed set."

        if any(jid in candidate.campaign_url for jid in joined_ids):
            return False, f"REJECT: Campaign URL contains an already-joined campaign ID."

        if candidate.cpm is not None and candidate.cpm < 1.0:
            return False, f"REJECT: CPM ${candidate.cpm:.2f} is below the minimum threshold of $1.00."

        if not candidate.source_urls:
            return False, f"REJECT: Campaign has no source media URLs."

        if candidate.eligible is False and candidate.eligibility_reasons:
            return False, f"REJECT: Marked as ineligible during discovery: {'; '.join(candidate.eligibility_reasons)}"

        return True, "ELIGIBLE_AND_UNJOINED"

    def arm_join_mutation(
        self,
        campaign: DiscoveredCampaign,
        account_identity: str,
    ) -> Dict[str, Any]:
        """Arms and records the explicit JOIN_MUTATION_ARMED metadata block."""
        now = datetime.now(timezone.utc).isoformat()
        armed_block = {
            "event": "JOIN_MUTATION_ARMED",
            "campaign_id": campaign.campaign_id,
            "campaign_name": campaign.title,
            "campaign_url": sanitize_text(campaign.campaign_url),
            "account_identity": sanitize_text(account_identity),
            "timestamp": now,
            "cpm": getattr(campaign, "cpm", 1.0),
        }
        log.info(
            "ARMED MUTATION: campaign_id=%s, name='%s', account='%s', timestamp=%s",
            armed_block["campaign_id"],
            armed_block["campaign_name"],
            armed_block["account_identity"],
            armed_block["timestamp"],
        )

        if not self.ledger.get_campaign(campaign.campaign_id):
            try:
                self.ledger.ingest_discovered_campaign(campaign, source="WhopCampaignJoiner")
            except Exception as ing_err:
                log.warning("Could not ingest campaign '%s' into ledger: %s", campaign.campaign_id, ing_err)

        try:
            self.ledger.record_event(
                campaign_id=campaign.campaign_id,
                target_state=CampaignState.CLAIMING,
                reason="JOIN_MUTATION_ARMED: Ready to execute atomic join click.",
                source="WhopCampaignJoiner",
                metadata=armed_block,
            )
        except Exception as e:
            log.debug("Event recording notice: %s", e)

        return armed_block

    def verify_campaign_membership(
        self,
        page: Page,
        campaign_id: str,
        campaign_url: str,
    ) -> bool:
        """Independently verifies that the authenticated account is an active member/clipper."""
        log.info("Verifying membership for campaign '%s'...", campaign_id)
        try:
            # 1. Check current page first before navigating away
            try:
                body_txt = page.locator("body").inner_text()
                if (
                    "you already have access to this free product" in body_txt.lower()
                    or "visit your membership" in body_txt.lower()
                    or "payment complete" in body_txt.lower()
                    or "/app/campaigns/" in page.url.lower()
                    or "/experiences/" in page.url.lower()
                ):
                    log.info("Membership confirmed via current page access (URL: %s).", sanitize_text(page.url))
                    return True
            except Exception:
                pass

            # Dismiss any welcome overlays
            try:
                got_it = page.locator("button:has-text('Got it'), button:has-text('Dismiss'), button:has-text('Close')")
                for i in range(got_it.count()):
                    if got_it.nth(i).is_visible():
                        got_it.nth(i).click()
                        page.wait_for_timeout(1000)
                        break
            except Exception:
                pass

            for sel in MEMBERSHIP_CONFIRMED_SELECTORS:
                loc = page.locator(sel)
                if loc.count() > 0:
                    for i in range(loc.count()):
                        if loc.nth(i).is_visible():
                            log.info("Membership confirmed via selector: %s (index %d)", sel, i)
                            return True

            # If already on the campaign page, check if join button is absent and submission controls exist
            has_join_btn = False
            for j_sel in JOIN_BUTTON_SELECTORS:
                j_loc = page.locator(j_sel)
                if j_loc.count() > 0:
                    for i in range(j_loc.count()):
                        if j_loc.nth(i).is_visible():
                            has_join_btn = True
                            break
                if has_join_btn:
                    break

            if not has_join_btn:
                sub_loc = page.locator("input[type='url'], button:has-text('Submit'), a:has-text('Guidelines')")
                for i in range(sub_loc.count()):
                    if sub_loc.nth(i).is_visible():
                        log.info("Membership verified: Join button absent and submission controls present.")
                        return True

            # Check campaign URL if not currently loaded
            if campaign_url and campaign_url not in page.url:
                page.goto(campaign_url, timeout=20000, wait_until="domcontentloaded")
                page.wait_for_timeout(2000)

                for sel in MEMBERSHIP_CONFIRMED_SELECTORS:
                    loc = page.locator(sel)
                    if loc.count() > 0:
                        for i in range(loc.count()):
                            if loc.nth(i).is_visible():
                                log.info("Membership confirmed on reload via selector: %s", sel)
                                return True

        except Exception as e:
            log.warning("Membership verification encountered an error: %s", sanitize_text(str(e)))

        return False

    def execute_join_mutation(
        self,
        page: Page,
        campaign: DiscoveredCampaign,
        account_identity: str,
        dry_run_override: Optional[bool] = None,
    ) -> WhopJoinRecord:
        """Executes the genuine join/claim mutation with biological human kinematics."""
        dry_run = self.config.dry_run if dry_run_override is None else dry_run_override
        now = datetime.now(timezone.utc).isoformat()
        cid = campaign.campaign_id

        existing_join = self.ledger.get_join_record(cid)
        if existing_join and existing_join.join_state == "JOINED" and existing_join.membership_verified:
            log.info("Campaign '%s' is already joined according to durable ledger; skipping mutation.", cid)
            return existing_join

        if not self.ledger.get_campaign(cid):
            try:
                self.ledger.ingest_discovered_campaign(campaign, source="WhopCampaignJoiner")
            except Exception as ing_err:
                log.warning("Could not ingest campaign '%s' into ledger: %s", cid, ing_err)

        join_record = WhopJoinRecord(
            campaign_id=cid,
            campaign_name=campaign.title,
            campaign_url=campaign.campaign_url,
            account_identity=account_identity,
            joined_at=None,
            join_attempt_count=(existing_join.join_attempt_count + 1) if existing_join else 1,
            join_state="ARMED",
            membership_verified=False,
            safe_evidence_references=[],
            created_at=now,
            updated_at=now,
        )
        self.ledger.save_join_record(join_record)

        armed_data = self.arm_join_mutation(campaign, account_identity)

        if campaign.campaign_url not in page.url:
            log.info("Navigating to campaign page: %s", sanitize_text(campaign.campaign_url))
            page.goto(campaign.campaign_url, timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)

        # Dismiss any welcome overlays
        try:
            got_it = page.locator("button:has-text('Got it'), button:has-text('Dismiss'), button:has-text('Close')")
            for i in range(got_it.count()):
                if got_it.nth(i).is_visible():
                    got_it.nth(i).click()
                    page.wait_for_timeout(1000)
                    break
        except Exception:
            pass

        if self.verify_campaign_membership(page, cid, campaign.campaign_url):
            log.info("Account is already a confirmed member of '%s'; no join click needed.", cid)
            join_record.join_state = "JOINED"
            join_record.membership_verified = True
            join_record.joined_at = datetime.now(timezone.utc).isoformat()
            self.ledger.update_join_state(cid, "JOINED", membership_verified=True)
            self._transition_campaign_to_claimed(cid)
            return join_record

        target_locator: Optional[Locator] = None
        target_selector_used = ""
        try:
            page.wait_for_selector("a[href*='/join'], a:has-text('Join'), button:has-text('Join')", timeout=8000)
        except Exception:
            pass

        for sel in JOIN_BUTTON_SELECTORS:
            loc = page.locator(sel)
            if loc.count() > 0:
                for idx in range(loc.count()):
                    if loc.nth(idx).is_visible():
                        target_locator = loc.nth(idx)
                        target_selector_used = sel
                        break
                if target_locator:
                    break

        if not target_locator:
            err = f"No visible Join/Claim button found on campaign page '{sanitize_text(campaign.campaign_url)}'."
            join_record.join_state = "JOIN_FAILED"
            join_record.last_error = err
            self.ledger.update_join_state(cid, "JOIN_FAILED", error=err)
            raise WhopJoinError(err)

        if dry_run:
            log.info("WHOP_DRY_RUN=true: Simulated join mutation for campaign '%s'. Button found: %s", cid, target_selector_used)
            join_record.join_state = "DRY_RUN_ARMED"
            self.ledger.update_join_state(cid, "DRY_RUN_ARMED")
            return join_record

        log.info("MUTATION EXECUTING: Performing genuine human-like click on '%s'...", target_selector_used)
        actor = HumanActor(page=page, dry_run=False)

        try:
            self.ledger.record_event(
                campaign_id=cid,
                target_state=CampaignState.CLAIMING,
                reason=f"CAMPAIGN_JOIN_ATTEMPT: Clicking Join button '{target_selector_used}'",
                source="WhopCampaignJoiner",
            )
        except Exception as evt_err:
            log.warning("Could not record join attempt event: %s", evt_err)

        try:
            try:
                target_locator.evaluate("el => el.removeAttribute('target')")
            except Exception:
                pass
            actor.human_click(target_locator, hesitation_scale=1.2, action_name="join")
            page.wait_for_timeout(4000)
            if "checkout" in page.url.lower():
                log.info("Redirected to checkout plan page: %s; clicking checkout Join button...", sanitize_text(page.url))
                try:
                    page.wait_for_selector("button:has-text('Join'), button:has-text('Claim')", timeout=15000)
                except Exception:
                    pass
                checkout_btn = page.locator("button:has-text('Join'), button:has-text('Claim')")
                if checkout_btn.count() > 0 and checkout_btn.first.is_visible():
                    actor.human_click(checkout_btn.first, hesitation_scale=1.2, action_name="checkout_join")
                    page.wait_for_timeout(5000)
        except Exception as click_err:
            err_msg = f"Join click failed: {click_err}"
            log.error(err_msg)
            join_record.join_state = "JOIN_FAILED"
            join_record.last_error = err_msg
            self.ledger.update_join_state(cid, "JOIN_FAILED", error=err_msg)
            raise WhopJoinError(err_msg) from click_err

        try:
            self.ledger.record_event(
                campaign_id=cid,
                target_state=CampaignState.CLAIMING,
                reason="CAMPAIGN_JOIN_RESPONSE: Join button clicked; verifying membership.",
                source="WhopCampaignJoiner",
            )
        except Exception as evt_err:
            log.warning("Could not record join response event: %s", evt_err)

        is_verified = self.verify_campaign_membership(page, cid, campaign.campaign_url)
        if not is_verified:
            log.warning("Initial membership check after join click did not confirm. Reconciling...")
            time.sleep(3)
            is_verified = self.verify_campaign_membership(page, cid, campaign.campaign_url)

        if is_verified:
            join_record.join_state = "JOINED"
            join_record.membership_verified = True
            join_record.joined_at = datetime.now(timezone.utc).isoformat()
            self.ledger.update_join_state(cid, "JOINED", membership_verified=True)
            self._transition_campaign_to_claimed(cid)

            try:
                self.ledger.record_event(
                    campaign_id=cid,
                    target_state=CampaignState.CLAIMED,
                    reason="CAMPAIGN_JOIN_VERIFIED: Membership confirmed independently.",
                    source="WhopCampaignJoiner",
                )
            except Exception as evt_err:
                log.warning("Could not record join verified event: %s", evt_err)
            log.info("SUCCESS: Campaign '%s' joined and membership verified!", cid)
            return join_record
        else:
            err = f"Join action completed but membership could not be confirmed for campaign '{cid}'."
            join_record.join_state = "JOIN_REQUIRES_RECONCILIATION"
            join_record.last_error = err
            self.ledger.update_join_state(cid, "JOIN_REQUIRES_RECONCILIATION", error=err)
            raise WhopJoinError(err)

    def _transition_campaign_to_claimed(self, campaign_id: str) -> None:
        """Transitions campaign state safely in ledger to CLAIMED."""
        try:
            camp = self.ledger.get_campaign(campaign_id)
            if camp:
                if camp.current_state == CampaignState.ELIGIBLE:
                    self.ledger.transition_state(
                        campaign_id,
                        CampaignState.CLAIMING,
                        reason="Initiating autonomous campaign join.",
                        source="WhopCampaignJoiner",
                    )
                if camp.current_state in (CampaignState.ELIGIBLE, CampaignState.CLAIMING):
                    self.ledger.transition_state(
                        campaign_id,
                        CampaignState.CLAIMED,
                        reason="Campaign successfully joined and membership verified.",
                        source="WhopCampaignJoiner",
                    )
        except Exception as e:
            log.debug("Campaign state transition notice: %s", e)

    @staticmethod
    def _extract_campaign_id_from_url(url: str) -> Optional[str]:
        """Extracts campaign identifier from discovery/reward URLs."""
        if not url:
            return None
        m = re.search(r'/(?:discover|rewards|experiences)/([A-Za-z0-9_\-]+)', url)
        if m:
            return m.group(1)
        return None
