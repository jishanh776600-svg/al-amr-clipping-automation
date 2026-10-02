"""Whop Campaign Scraper and Catalog Discovery (Step 2).

Safely navigates the live Whop creator marketplace/catalog, discovers campaigns,
extracts raw metadata, normalizes records, and compiles discovery reports.
Strictly non-mutating (WHOP_DRY_RUN=true). Never joins, claims, or applies.
"""

import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from playwright.sync_api import Locator, Page

from .browser import WhopBrowser
from .catalog import DiscoveredCampaign, build_discovered_campaign
from .config import WhopConfig, sanitize_text
from .diagnostics import detect_whop_authentication

log = logging.getLogger(__name__)

# Centralized Whop Navigation Targets
MARKETPLACE_ROUTES = [
    "/discover",
    "/affiliates",
    "/creators",
    "/hub",
]

# Centralized UI Selectors
SELECTORS = {
    # Modal dismissals
    "welcome_modal_dismiss": "button:has-text('Got it'), button:has-text('Dismiss'), button:has-text('Close'), [aria-label='Close']",
    "cookie_banner_dismiss": "button:has-text('Accept'), button:has-text('Allow all')",

    # Search & filters
    "search_input": "input[type='search'], input[placeholder*='Search'], input[aria-label*='Search']",

    # Campaign cards and containers
    "campaign_card": "[data-testid*='campaign'], [data-testid*='reward'], article, [role='article'], .campaign-card, a[href*='/discover/'], a[href*='/rewards/']",
    "card_title": "h1, h2, h3, h4, [data-testid*='title'], .title, font[style*='bold']",
    "card_link": "a[href]",
    
    # Detail page selectors
    "detail_title": "h1, [data-testid*='title']",
    "detail_payout": "[data-testid*='payout'], [data-testid*='cpm'], [data-testid*='reward'], :text-matches('(\\$[0-9]+(\\.[0-9]+)?\\s*(CPM|/\\s*1k|per\\s*1k))', 'i')",
    "detail_description": "[data-testid*='description'], [data-testid*='guideline'], [data-testid*='rules'], main p, article p",
}


@dataclass
class DiscoveryRunReport:
    """Complete summary of a campaign discovery execution."""
    run_id: str
    timestamp: str
    authenticated: bool
    dry_run: bool
    final_url: str
    page_title: str
    campaign_count: int
    eligible_count: int
    rejected_count: int
    campaigns: List[Dict[str, Any]] = field(default_factory=list)
    extraction_warnings: List[str] = field(default_factory=list)
    parser_warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class WhopScraper:
    """Read-only Playwright scraper for Whop campaign discovery."""

    def __init__(self, browser: WhopBrowser, config: Optional[WhopConfig] = None):
        self.browser = browser
        self.config = config or browser.config
        self.warnings: List[str] = []
        self.parser_warnings: List[str] = []

    def dismiss_overlays_safely(self, page: Page) -> None:
        """Safely dismisses welcome modals or cookie banners without mutating account state."""
        try:
            dismiss_buttons = page.locator(SELECTORS["welcome_modal_dismiss"])
            if dismiss_buttons.count() > 0:
                first_btn = dismiss_buttons.first
                if first_btn.is_visible():
                    log.info("Dismissing transient overlay/welcome modal.")
                    first_btn.click(timeout=3000)
                    page.wait_for_timeout(1000)
        except Exception as exc:
            log.debug("No overlay dismissed or dismissal timed out: %s", sanitize_text(str(exc)))

    def discover_marketplace(self, target_url: Optional[str] = None) -> Tuple[Page, str]:
        """Navigates to the Whop discovery/campaign marketplace and stabilizes page.
        
        If no explicit target_url is provided, first loads home page to dismiss any
        welcome dialogs, inspects the sidebar navigation links, and navigates to the
        actual marketplace/discover/affiliates target.
        """
        page = self.browser.page
        if not page:
            raise RuntimeError("Browser page not initialized. Launch browser first.")

        base = self.config.base_url.rstrip("/")

        if target_url and target_url != f"{base}/discover":
            start_url = target_url
            log.info("Navigating to specified target endpoint: %s", sanitize_text(start_url))
            self.browser.navigate_safely(start_url)
            self.dismiss_overlays_safely(page)
        else:
            # 1. Load home page first where session is verified
            home_url = f"{base}/home/"
            log.info("Loading home dashboard first to resolve navigation: %s", sanitize_text(home_url))
            self.browser.navigate_safely(home_url)
            self.dismiss_overlays_safely(page)

            # 2. Inspect visible sidebar navigation links (Discover, Affiliates, etc.)
            sidebar_links = page.locator("a[href]")
            found_target: Optional[str] = None
            log.info("Scanning sidebar for discovery / affiliates / marketplace links...")

            candidates: List[Tuple[str, str]] = []
            for k in range(min(sidebar_links.count(), 60)):
                try:
                    href = sidebar_links.nth(k).get_attribute("href") or ""
                    link_text = sidebar_links.nth(k).inner_text().strip().lower()
                    if href:
                        candidates.append((link_text, href))
                        if "discover" in link_text or "affiliates" in link_text or "creators" in link_text:
                            found_target = href if href.startswith("http") else f"{base}{href}"
                            log.info("Found navigation item '%s' -> %s", link_text, found_target)
                            break
                except Exception:
                    pass

            if found_target:
                log.info("Navigating to detected marketplace endpoint: %s", sanitize_text(found_target))
                self.browser.navigate_safely(found_target)
                self.dismiss_overlays_safely(page)
            else:
                log.info("Sidebar candidates found: %s", candidates[:10])
                # Fallback to /affiliates or /hub
                fallback_url = f"{base}/affiliates"
                log.info("Navigating to fallback endpoint: %s", sanitize_text(fallback_url))
                self.browser.navigate_safely(fallback_url)
                self.dismiss_overlays_safely(page)

        # Confirm authentication
        is_auth, reason = detect_whop_authentication(page)
        if not is_auth:
            warn = f"Discovery page authentication warning: reason={reason}"
            log.warning(warn)
            self.warnings.append(warn)

        return page, page.url


    def extract_campaign_cards_from_dom(self, page: Page) -> List[Dict[str, Any]]:
        """Extracts visible campaign cards from the current DOM."""
        extracted_raw_cards: List[Dict[str, Any]] = []
        seen_identifiers: Set[str] = set()

        # Check candidate card selectors
        card_locators = page.locator("a[href*='/discover/'], a[href*='/rewards/'], a[href*='/campaigns/'], a[href*='/hub/']")
        count = card_locators.count()
        log.info("Scanning DOM for campaign card links: found %d candidate links", count)

        for i in range(min(count, 100)):
            try:
                card = card_locators.nth(i)
                href = card.get_attribute("href") or ""
                if not href or href in seen_identifiers:
                    continue

                full_url = href if href.startswith("http") else f"{self.config.base_url.rstrip('/')}{href}"
                card_text = card.inner_text().strip()

                # Basic heuristic: if the card contains title or payout indications
                if len(card_text) < 4:
                    continue

                lines = [line.strip() for line in card_text.splitlines() if line.strip()]
                title = lines[0] if lines else "Untitled Whop Campaign"
                payout_raw = ""
                for line in lines:
                    if any(term in line.lower() for term in ("cpm", "/ 1k", "per 1k", "$", "reward")):
                        payout_raw = line
                        break

                # Extract ID from URL slug
                slug = href.strip("/").split("/")[-1]
                campaign_id = slug if slug else f"whop_card_{i+1}"

                seen_identifiers.add(href)
                extracted_raw_cards.append({
                    "campaign_id": campaign_id,
                    "title": title,
                    "campaign_url": full_url,
                    "payout_raw": payout_raw,
                    "raw_text": card_text,
                })
            except Exception as exc:
                self.parser_warnings.append(f"Error reading card {i}: {sanitize_text(str(exc))}")

        return extracted_raw_cards

    def inspect_campaign_details(self, page: Page, card: Dict[str, Any]) -> DiscoveredCampaign:
        """Navigates to detail page (read-only) and extracts rich metadata."""
        url = card["campaign_url"]
        log.info("Inspecting campaign details: %s", sanitize_text(url))


        try:
            self.browser.navigate_safely(url)
            self.dismiss_overlays_safely(page)
            page_text = page.locator("body").inner_text()
        except Exception as exc:
            err = f"Failed to navigate to {sanitize_text(url)}: {sanitize_text(str(exc))}"
            log.warning(err)
            self.warnings.append(err)
            return build_discovered_campaign(
                campaign_id=card["campaign_id"],
                title=card["title"],
                campaign_url=url,
                payout_raw=card.get("payout_raw", ""),
                raw_text=card.get("raw_text", ""),
            )

        # 1. Title refinement
        title = card["title"]
        try:
            h1_el = page.locator("h1").first
            if h1_el.is_visible():
                h1_text = h1_el.inner_text().strip()
                if h1_text and len(h1_text) >= 3:
                    title = h1_text
        except Exception:
            pass

        # 2. Payout Extraction
        payout_raw = card.get("payout_raw", "")
        # Search for CPM pattern in page text
        cpm_matches = re.findall(
            r"([\$€£]?\s*\d+(?:\.\d+)?\s*(?:cpm|\/\s*(?:1k|1000|1,000)\s*(?:views)?|per\s*(?:1k|1000|1,000)\s*views))",
            page_text,
            re.IGNORECASE
        )
        if cpm_matches:
            payout_raw = cpm_matches[0]

        # 3. Platform Detection from text
        detected_platforms = []
        lower_blob = page_text.lower()
        if "youtube" in lower_blob or "shorts" in lower_blob:
            detected_platforms.append("youtube")
        if "instagram" in lower_blob or "reels" in lower_blob:
            detected_platforms.append("instagram")
        if "tiktok" in lower_blob:
            detected_platforms.append("tiktok")

        # 4. Source URL references
        source_urls: List[str] = []
        # Find Google Drive, YouTube, Dropbox, Mega, direct MP4 links
        link_elements = page.locator("a[href]")
        for j in range(min(link_elements.count(), 150)):
            try:
                href = link_elements.nth(j).get_attribute("href") or ""
                if any(domain in href.lower() for domain in (
                    "drive.google.com",
                    "youtube.com/watch",
                    "youtu.be",
                    "dropbox.com",
                    "mega.nz",
                    ".mp4"
                )):
                    if href not in source_urls:
                        source_urls.append(href)
            except Exception:
                pass

        # 5. Guideline references
        guideline_urls: List[str] = []
        for j in range(min(link_elements.count(), 150)):
            try:
                href = link_elements.nth(j).get_attribute("href") or ""
                link_text = link_elements.nth(j).inner_text().lower()
                if any(w in link_text for w in ("rule", "guideline", "brief", "instruction", "terms", "doc")):
                    if href not in guideline_urls and href not in source_urls:
                        guideline_urls.append(href)
            except Exception:
                pass

        # 6. Mentions and hashtags
        mentions = list(set(re.findall(r"@[\w\.\-]+", page_text)))
        hashtags = list(set(re.findall(r"#\w+", page_text)))

        return build_discovered_campaign(
            campaign_id=card["campaign_id"],
            title=title,
            campaign_url=url,
            payout_raw=payout_raw,
            platforms_raw=detected_platforms,
            source_urls=source_urls,
            guideline_urls=guideline_urls,
            mentions=mentions,
            hashtags=hashtags,
            raw_text=page_text[:2000],  # bounded raw text snippet
        )

    def run_discovery(
        self,
        target_url: Optional[str] = None,
        max_campaigns: int = 40,
        inspect_details: bool = True,
    ) -> DiscoveryRunReport:
        """Executes complete campaign discovery scan and returns DiscoveryRunReport."""
        run_id = f"whop_disc_{int(time.time())}"
        now_iso = datetime.now(timezone.utc).isoformat()

        page, final_url = self.discover_marketplace(target_url=target_url)
        page_title = page.title()
        is_auth, auth_reason = detect_whop_authentication(page)

        # 1. Discover raw cards
        raw_cards = self.extract_campaign_cards_from_dom(page)
        log.info("Discovered %d campaign cards from marketplace", len(raw_cards))

        campaign_records: List[DiscoveredCampaign] = []

        if raw_cards:
            for card in raw_cards[:max_campaigns]:
                if inspect_details:
                    rec = self.inspect_campaign_details(page, card)
                else:
                    rec = build_discovered_campaign(
                        campaign_id=card["campaign_id"],
                        title=card["title"],
                        campaign_url=card["campaign_url"],
                        payout_raw=card.get("payout_raw", ""),
                        raw_text=card.get("raw_text", ""),
                    )
                campaign_records.append(rec)
        else:
            self.warnings.append("No campaign cards discovered from current page DOM.")

        # Calculate counts
        eligible_count = sum(1 for c in campaign_records if c.eligible)
        rejected_count = sum(1 for c in campaign_records if not c.eligible)

        report = DiscoveryRunReport(
            run_id=run_id,
            timestamp=now_iso,
            authenticated=is_auth,
            dry_run=True,
            final_url=sanitize_text(final_url),
            page_title=sanitize_text(page_title),
            campaign_count=len(campaign_records),
            eligible_count=eligible_count,
            rejected_count=rejected_count,
            campaigns=[c.to_dict() for c in campaign_records],
            extraction_warnings=[sanitize_text(w) for w in self.warnings],
            parser_warnings=[sanitize_text(w) for w in self.parser_warnings],
        )

        return report


def generate_markdown_summary(report: DiscoveryRunReport) -> str:
    """Generates human-readable Markdown summary of the discovery run."""
    lines: List[str] = [
        "# Whop Campaign Discovery Summary",
        "",
        f"- **Run ID**: `{report.run_id}`",
        f"- **Timestamp**: `{report.timestamp}`",
        f"- **Authenticated**: `{'✅ Yes' if report.authenticated else '❌ No'}`",
        f"- **Dry Run Mode**: `{'🔒 Enabled (Read-Only)' if report.dry_run else '⚠️ Active'}`",
        f"- **Final URL**: `{report.final_url}`",
        f"- **Page Title**: `{report.page_title}`",
        "",
        "## Statistics",
        f"- **Total Campaigns Discovered**: `{report.campaign_count}`",
        f"- **Eligible (≥ $1.00 CPM + Platform + Source)**: `{report.eligible_count}`",
        f"- **Rejected**: `{report.rejected_count}`",
        "",
    ]

    eligible_camps = [c for c in report.campaigns if c.get("eligible")]
    rejected_camps = [c for c in report.campaigns if not c.get("eligible")]

    lines.append("## Eligible Campaigns")
    if eligible_camps:
        for idx, c in enumerate(eligible_camps, 1):
            cpm_str = f"${c['cpm']:.2f} CPM" if c.get("cpm") is not None else "N/A"
            plats = ", ".join(c.get("platforms", [])) or "None"
            route = c.get("recommended_account") or "Unassigned"
            reason = c.get("routing_reason", "")
            lines.extend([
                f"### {idx}. {c.get('title', 'Untitled')}",
                f"- **Campaign ID**: `{c.get('campaign_id')}`",
                f"- **URL**: {c.get('campaign_url')}",
                f"- **CPM**: `{cpm_str}` (Raw: `{c.get('payout_raw')}`)",
                f"- **Platforms**: `{plats}`",
                f"- **Sources Found**: `{len(c.get('source_urls', []))}`",
                f"- **Recommended Route**: `{route}` ({reason})",
                "",
            ])
    else:
        lines.append("*(No eligible campaigns met all strict criteria in this scan)*\n")

    lines.append("## Rejected Campaigns")
    if rejected_camps:
        for idx, c in enumerate(rejected_camps[:20], 1):
            reasons = ", ".join(c.get("eligibility_reasons", [])) or "UNKNOWN"
            cpm_val = f"${c['cpm']:.2f}" if c.get("cpm") is not None else "None"
            lines.append(
                f"{idx}. **{c.get('title', 'Untitled')}** (`{c.get('campaign_id')}`) "
                f"— Rejection: `{reasons}` | CPM: `{cpm_val}` (Raw: `{c.get('payout_raw')}`)"
            )
        if len(rejected_camps) > 20:
            lines.append(f"... and {len(rejected_camps) - 20} more rejected campaigns.")
    else:
        lines.append("*(No rejected campaigns)*\n")

    if report.extraction_warnings:
        lines.extend([
            "",
            "## Warnings / Diagnostics",
            *[f"- ⚠️ {w}" for w in report.extraction_warnings],
        ])

    return "\n".join(lines)


def save_discovery_artifacts(report: DiscoveryRunReport, output_dir: Path) -> Tuple[Path, Path]:
    """Saves both JSON and Markdown discovery artifacts safely."""
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "whop_campaign_discovery.json"
    md_path = output_dir / "whop_campaign_discovery.md"

    # Save JSON artifact
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report.to_dict(), f, indent=2)

    # Save Markdown artifact
    md_content = generate_markdown_summary(report)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    log.info("Saved discovery JSON to %s and Markdown to %s", json_path, md_path)
    return json_path, md_path