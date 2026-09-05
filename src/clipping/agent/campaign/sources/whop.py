"""Whop Campaign Discovery Source Integration.

Primary production campaign-discovery source for AL AMR CLIPPING.
Extracts creator clipping briefs, CPM rates, source video footage, and content terms
from Whop's creator rewards ecosystem using resilient HTTP APIs first, falling back to
cloud browser automation only when required.
"""

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import httpx

from clipping.agent.browser.driver import BrowserDriver
from clipping.agent.browser.engine import CloudBrowserEngine
from clipping.agent.browser.models import BrowserAction, BrowserActionType
from clipping.agent.campaign.sources.base import CampaignSource
from clipping.logging.logger import get_logger

logger = get_logger("clipping.agent.campaign.sources.whop")


class WhopCampaignSource(CampaignSource):
    """
    Primary campaign discovery source integrating Whop creator clipping rewards.
    Extracts structured campaign terms, source material URLs, CPM rates, and restrictions.
    """

    DEFAULT_BASE_URL = "https://api.whop.com/v5"
    DEFAULT_PORTAL_URL = "https://whop.com/creator-rewards"

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_token: Optional[str] = None,
        browser_driver: Optional[BrowserDriver] = None,
        timeout_seconds: float = 15.0,
    ):
        self._base_url = base_url or self.DEFAULT_BASE_URL
        self._api_token = api_token
        self._driver = browser_driver
        self._timeout = timeout_seconds

    @property
    def source_id(self) -> str:
        return "whop"

    @property
    def name(self) -> str:
        return "Whop Creator Rewards & Marketplace"

    @property
    def is_primary(self) -> bool:
        return True

    async def discover(
        self,
        query: Optional[str] = None,
        platform: Optional[str] = None,
        limit: int = 50,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Discovers active clipping campaigns from Whop.
        Uses structured HTTP requests when available, falling back to browser automation.
        """
        meta = metadata or {}
        custom_endpoint = meta.get("custom_endpoint")
        raw_campaigns = meta.get("raw_campaigns")

        # 1. If direct raw payload is provided in metadata (e.g. webhook or local test feed)
        if raw_campaigns and isinstance(raw_campaigns, list):
            return [self._normalize_whop_campaign(item) for item in raw_campaigns[:limit]]

        # 2. Attempt resilient HTTP request
        target_url = custom_endpoint or f"{self._base_url}/campaigns"
        try:
            headers = {"Accept": "application/json", "User-Agent": "AlAmrClippingBot/2.0"}
            if self._api_token:
                headers["Authorization"] = f"Bearer {self._api_token}"

            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(target_url, headers=headers, params={"limit": limit, "type": "clipping"})
                if resp.status_code == 200:
                    data = resp.json()
                    items = data.get("data", data.get("campaigns", []))
                    if isinstance(items, list) and items:
                        logger.info("Successfully fetched campaigns from Whop API", count=len(items))
                        return [self._normalize_whop_campaign(i) for i in items[:limit]]
                elif resp.status_code in (401, 403, 404):
                    logger.info(f"Whop HTTP endpoint returned {resp.status_code}, falling back to browser exploration")
        except Exception as e:
            logger.info("Direct Whop HTTP endpoint unavailable, falling back to browser exploration", error=str(e))

        # 3. Fallback to CloudBrowserEngine
        portal_url = meta.get("source_url") or self.DEFAULT_PORTAL_URL
        return await self._discover_via_browser(portal_url=portal_url, limit=limit)

    async def fetch_campaign_detail(
        self,
        campaign_ref: str,
    ) -> Optional[Dict[str, Any]]:
        """Fetches detailed brief terms for a single Whop campaign."""
        detail_url = f"{self._base_url}/campaigns/{campaign_ref}"
        try:
            headers = {"Accept": "application/json", "User-Agent": "AlAmrClippingBot/2.0"}
            if self._api_token:
                headers["Authorization"] = f"Bearer {self._api_token}"

            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(detail_url, headers=headers)
                if resp.status_code == 200:
                    return self._normalize_whop_campaign(resp.json())
        except Exception as e:
            logger.info("Failed to fetch Whop campaign detail via HTTP", campaign_ref=campaign_ref, error=str(e))

        # Fallback to browser navigation for detail
        campaign_page_url = f"https://whop.com/campaigns/{campaign_ref}"
        results = await self._discover_via_browser(portal_url=campaign_page_url, limit=1)
        return results[0] if results else None

    async def _discover_via_browser(
        self,
        portal_url: str,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Explores Whop campaign pages using headless CloudBrowserEngine."""
        engine = CloudBrowserEngine(driver=self._driver)
        async with engine:
            actions = [
                BrowserAction(action_type=BrowserActionType.NAVIGATE, url=portal_url),
                BrowserAction(action_type=BrowserActionType.WAIT_FOR_SELECTOR, selector="body", timeout_ms=5000),
            ]
            res = await engine.execute_workflow(actions)

            # Check for security challenges
            challenge = res.get("challenge")
            if challenge:
                logger.warning("Whop page returned security challenge", challenge=challenge)
                return [{"challenge": challenge, "source_url": portal_url}]

            page_data = res.get("page_data")
            if not page_data or not page_data.text_content:
                return []

            # Extract structured campaigns from page data or embedded JSON-LD / HTML
            extracted = self._extract_from_page_content(page_data, portal_url)
            return [self._normalize_whop_campaign(c) for c in extracted[:limit]]

    def _extract_from_page_content(self, page_data: Any, source_url: str) -> List[Dict[str, Any]]:
        """Parses campaign briefs from raw page text and metadata."""
        text = page_data.text_content or ""
        title = page_data.title or "Whop Creator Clipping Campaign"

        # Look for CPM patterns like "$2.50 CPM", "$2 CPM", "CPM: $3"
        cpm_match = re.search(r"\$(\d+(?:\.\d{1,2})?)\s*(?:CPM|per\s*1[,.]?000\s*views)", text, re.IGNORECASE)
        cpm_rate = float(cpm_match.group(1)) if cpm_match else 2.0  # default target sweet spot

        # Look for total pool / budget patterns like "$10,000 Pool", "Total Budget: $5,000"
        budget_match = re.search(r"(?:pool|budget)[\s:]*\$(\d[\d,]*)", text, re.IGNORECASE)
        total_budget = float(budget_match.group(1).replace(",", "")) if budget_match else None

        # Look for source video links or Google Drive links in page text
        video_urls = re.findall(r"https?://(?:www\.)?(?:youtube\.com/watch\?v=[a-zA-Z0-9_-]+|drive\.google\.com/[^\s\"'>]+|youtu\.be/[a-zA-Z0-9_-]+)", text)

        # Look for required hashtags
        hashtags = re.findall(r"#([a-zA-Z0-9_]+)", text)
        if not hashtags:
            hashtags = ["whop", "creator"]

        return [
            {
                "id": f"whop_{abs(hash(source_url)) % 1000000:06d}",
                "title": title,
                "community": "Whop Creator Hub",
                "description": text[:500],
                "source_url": source_url,
                "cpm_rate": cpm_rate,
                "total_budget": total_budget,
                "source_video_uris": list(set(video_urls)),
                "hashtags": [f"#{h}" for h in hashtags[:5]],
                "allowed_platforms": ["youtube_shorts", "tiktok"],
            }
        ]

    def _normalize_whop_campaign(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        """Converts raw Whop data into standardized campaign brief dictionary."""
        cid = str(raw.get("id") or raw.get("campaign_id") or "whop_campaign")
        if not cid.startswith("whop_"):
            cid = f"whop_{cid}"

        title = raw.get("title") or raw.get("name") or "Whop Clipping Campaign"
        cpm_rate = raw.get("cpm_rate") or raw.get("cpm") or (raw.get("payout_terms", {}).get("cpm_rate"))
        if cpm_rate is not None:
            try:
                cpm_rate = float(cpm_rate)
            except (ValueError, TypeError):
                cpm_rate = 2.0
        else:
            cpm_rate = 2.0  # default preferred CPM

        source_uris = raw.get("source_video_uris") or raw.get("video_urls") or raw.get("source_urls") or raw.get("discovered_source_uris") or []
        if isinstance(source_uris, str):
            source_uris = [source_uris]


        hashtags = raw.get("hashtags") or raw.get("required_hashtags") or ["#whop"]
        hashtags = [h if h.startswith("#") else f"#{h}" for h in hashtags]

        mentions = raw.get("mentions") or raw.get("required_mentions") or []
        mentions = [m if m.startswith("@") else f"@{m}" for m in mentions]

        return {
            "campaign_id": cid,
            "name": title,
            "source": raw.get("source_url") or raw.get("source") or "https://whop.com",
            "description": raw.get("description", ""),
            "creator_community": raw.get("community") or raw.get("company_name") or "Whop Creator Hub",
            "status": raw.get("status", "active"),
            "required_platforms": raw.get("allowed_platforms") or raw.get("required_platforms") or ["youtube_shorts"],
            "payout_terms": {
                "model": "cpm",
                "cpm_rate": cpm_rate,
                "min_payout": raw.get("min_payout", 5.0),
                "max_payout": raw.get("max_payout", 500.0),
                "currency": "USD",
                "total_budget": raw.get("total_budget"),
                "remaining_budget": raw.get("remaining_budget") or raw.get("total_budget"),
                "budget_exhausted": bool(raw.get("budget_exhausted", False)),
            },
            "duration_terms": {
                "start_date": raw.get("start_date"),
                "end_date": raw.get("end_date"),
                "deadline": raw.get("deadline"),
                "is_expired": bool(raw.get("is_expired", False)),
            },
            "source_material": {
                "video_urls": source_uris,
                "google_drive_folder": raw.get("google_drive_folder"),
                "preferred_segments": raw.get("preferred_segments", []),
            },
            "posting_requirements": {
                "min_duration_seconds": raw.get("min_duration_seconds", 30.0),
                "max_duration_seconds": raw.get("max_duration_seconds", 60.0),
                "required_hashtags": hashtags,
                "required_mentions": mentions,
                "daily_post_limit": raw.get("daily_post_limit", 3),
            },
            "account_requirements": {
                "allowed_platforms": raw.get("allowed_platforms") or ["youtube_shorts"],
                "allow_account_reuse": raw.get("allow_account_reuse", True),
                "min_subscribers": raw.get("min_subscribers", 0),
            },
            "quotas": {
                "daily_creator_limit": raw.get("daily_creator_limit", 3),
                "max_submissions_per_creator": raw.get("max_submissions_per_creator"),
            },
            "allowed_content_rules": raw.get("allowed_content_rules", ["high_energy", "clear_audio", "dynamic_speaker_framing"]),
            "prohibited_content_rules": raw.get("prohibited_content_rules", ["copyright_music", "watermarks", "nudity"]),
            "discovered_source_uris": source_uris,
        }
