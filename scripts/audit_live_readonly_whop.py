#!/usr/bin/env python3
"""Live Read-Only Whop Browsing Session for Production-Readiness Audit.

Strictly non-mutating (WHOP_DRY_RUN=true):
- Opens Whop Content Rewards
- Performs biological human scroll
- Identifies campaign cards from DOM
- Moves cursor to a real campaign card with Flash-Hogan kinematics
- Inspects visible information (title, payout/CPM, description)
- Safely visits the campaign detail page (read-only)
- Returns safely to feed
- Deterministically cleans up and terminates
"""

import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path

# Add project root to sys.path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from whop import (
    WhopBrowser,
    WhopConfig,
    parse_and_validate_session_state,
)
from whop.human_interaction import HumanActor, HumanPersona

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("audit_live_readonly")

def run_audit_session():
    run_id = f"audit_ro_{uuid.uuid4().hex[:8]}"
    log.info("Starting Live Read-Only Whop Audit Session: Run ID = %s", run_id)

    # 1. Load Whop configuration with dry_run strictly enforced
    cfg = WhopConfig(dry_run=True, headless=True)

    session_state = None
    raw_cookies = os.getenv("WHOP_COOKIES", "").strip()
    if raw_cookies:
        try:
            session_state = parse_and_validate_session_state(raw_cookies)
            log.info("Session state attached from WHOP_COOKIES environment.")
        except Exception as e:
            log.warning("Could not validate session state: %s", e)

    out_dir = Path("artifacts/audit_live_run")
    out_dir.mkdir(parents=True, exist_ok=True)

    with WhopBrowser(cfg) as browser:
        page = browser.launch(
            session_state=session_state,
            record_video_dir=str(out_dir),
        )
        actor = browser.get_human_actor(page)
        log.info("Browser launched with enterprise stealth and human actor.")

        # 2. Open Whop Content Rewards
        target_url = "https://whop.com/contentrewards/"
        log.info("Navigating safely to %s", target_url)
        page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
        time.sleep(3.0)
        actor.sync_page(page)

        # 3. Handle any initial overlay or cookie banner safely
        try:
            cookie_accept = page.locator("button:has-text('Accept'), button:has-text('Allow all')").first
            if cookie_accept.is_visible(timeout=2000):
                log.info("Dismissing cookie banner with human click...")
                actor.human_click(cookie_accept)
                time.sleep(1.0)
        except Exception:
            pass

        # 4. Scroll feed with natural trackpad momentum
        log.info("Scrolling Content Rewards feed with inertial trackpad simulation...")
        actor.human_scroll(delta_y=450, reading_pause=True)
        time.sleep(2.0)

        # 5. Identify real campaign cards from DOM
        log.info("Scanning DOM for candidate campaign cards...")
        card_locators = page.locator(
            "a[href*='/discover/'], [data-testid*='campaign'], a[href*='whop.com/']"
        ).all()

        found_cards = []
        for i, card in enumerate(card_locators[:15]):
            try:
                if card.is_visible(timeout=1000):
                    bbox = card.bounding_box()
                    text = card.inner_text().strip()
                    href = card.get_attribute("href") or ""
                    if bbox and bbox["width"] > 60 and bbox["height"] > 30 and len(text) > 5:
                        found_cards.append({
                            "index": i,
                            "locator": card,
                            "bbox": bbox,
                            "text": text.splitlines()[0] if text else "Untitled",
                            "href": href,
                        })
            except Exception:
                continue

        log.info("Identified %d visible campaign card elements in DOM.", len(found_cards))
        if not found_cards:
            log.warning("No campaign cards found via standard selectors. Capturing screenshot.")
            page.screenshot(path=str(out_dir / "audit_feed_fallback.png"))
            return {
                "run_id": run_id,
                "status": "NO_CARDS_VISIBLE",
                "campaign_inspected": None,
                "detail_page_visited": False,
            }

        # 6. Move to a real campaign card using DOM-derived geometry
        target_card = found_cards[0]
        log.info("Selecting primary campaign card: '%s'", target_card["text"])
        t_bbox = target_card["bbox"]

        # Move naturally to the campaign card with high-entropy non-round landing point
        card_center_x = round(t_bbox["x"] + t_bbox["width"] * random.uniform(0.392, 0.478) + random.uniform(-3.2, 3.8), 2)
        card_center_y = round(t_bbox["y"] + t_bbox["height"] * random.uniform(0.324, 0.418) + random.uniform(-2.8, 3.4), 2)
        log.info("Moving to campaign card geometry (X=%.2f, Y=%.2f)...", card_center_x, card_center_y)
        actor.move_to(card_center_x, card_center_y, allow_overshoot=True)
        time.sleep(1.0)

        # Inspect visible information with biological idle drift
        log.info("Inspecting visible campaign info with reading drift...")
        actor.idle_drift(duration_seconds=1.5, focus_region=(card_center_x, card_center_y))
        page.screenshot(path=str(out_dir / "audit_card_inspected.png"))

        # 7. Open the campaign detail page if link exists (read-only)
        detail_visited = False
        campaign_detail_url = target_card["href"]
        if campaign_detail_url:
            if not campaign_detail_url.startswith("http"):
                campaign_detail_url = f"https://whop.com{campaign_detail_url}"

            log.info("Safely navigating to campaign detail URL (read-only): %s", campaign_detail_url)
            page.goto(campaign_detail_url, wait_until="domcontentloaded", timeout=45000)
            time.sleep(3.0)
            actor.sync_page(page)
            detail_visited = True

            # Inspect detail page
            log.info("Detail page loaded. Inspecting visible rules and guidelines...")
            actor.human_scroll(delta_y=300, reading_pause=True)
            actor.idle_drift(duration_seconds=1.5)
            page.screenshot(path=str(out_dir / "audit_detail_page.png"))

            # 8. Return safely to feed
            log.info("Returning safely to Content Rewards feed...")
            page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
            time.sleep(2.0)
            actor.sync_page(page)

        log.info("Audit session completed safely with ZERO mutations.")
        return {
            "run_id": run_id,
            "status": "SUCCESS",
            "campaign_inspected": target_card["text"],
            "detail_page_visited": detail_visited,
            "detail_url": campaign_detail_url,
        }

if __name__ == "__main__":
    result = run_audit_session()
    print(f"AUDIT_RESULT: {json.dumps(result)}")
