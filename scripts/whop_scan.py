#!/usr/bin/env python3
"""Whop Campaign Discovery Runner (Step 2 Verification).

Executes a secure, read-only discovery run across the Whop creator catalog:
1. Loads configuration (WHOP_DRY_RUN=true strictly enforced).
2. Validates WHOP_COOKIES secret.
3. Launches Playwright Chromium with validated session state.
4. Navigates to Whop creator marketplace/catalog.
5. Discovers campaigns, extracts metadata, normalizes records.
6. Evaluates eligibility (CPM >= $1.00, platform, source media).
7. Assigns non-mutating multi-account niche routing recommendations.
8. Generates artifacts/whop_campaign_discovery.json & whop_campaign_discovery.md.
9. Captures non-sensitive diagnostic screenshot.
10. Closes all browser resources cleanly.

Strictly read-only: does not join, apply, claim, or mutate any campaign.
"""

import argparse
import logging
import os
import sys
from pathlib import Path

# Add project root to sys.path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from whop import (
    WhopBrowser,
    WhopConfig,
    WhopConfigError,
    WhopScraper,
    WhopSessionError,
    capture_diagnostics,
    detect_whop_authentication,
    parse_and_validate_session_state,
    sanitize_text,
    save_discovery_artifacts,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("whop_scan")


def run_discovery_scan(
    output_dir: Path,
    target_url: str = "https://whop.com/discover",
    max_campaigns: int = 50,
    inspect_details: bool = True,
) -> int:
    """Executes the Step 2 read-only Whop campaign discovery scan."""
    output_dir.mkdir(parents=True, exist_ok=True)
    config = WhopConfig.from_env()

    log.info(
        "Starting Whop Campaign Discovery Scan (DRY_RUN=%s, Headless=%s, URL=%s)",
        config.dry_run,
        config.headless,
        sanitize_text(target_url),
    )

    # 1. Validate WHOP_COOKIES secret
    try:
        raw_cookies = config.get_cookies_secret()
    except WhopConfigError as exc:
        log.error("Configuration Error: %s", sanitize_text(str(exc)))
        print("::error::WHOP_COOKIES secret is missing. Set WHOP_COOKIES in GitHub Secrets.")
        return 1

    # 2. Parse and validate storage state
    try:
        session_state = parse_and_validate_session_state(raw_cookies)
        log.info(
            "Session validation passed: %d cookies parsed across domains: %s",
            session_state.cookie_count,
            ", ".join(session_state.domains),
        )
    except WhopSessionError as exc:
        log.error("Session Validation Error: %s", sanitize_text(str(exc)))
        print(f"::error::Session state validation failed: {sanitize_text(str(exc))}")
        return 1

    # 3. Launch browser and execute discovery
    browser = WhopBrowser(config=config)
    try:
        page = browser.launch(session_state=session_state)
        scraper = WhopScraper(browser=browser, config=config)

        # Run discovery scan
        report = scraper.run_discovery(
            target_url=target_url,
            max_campaigns=max_campaigns,
            inspect_details=inspect_details,
        )

        # Save artifacts
        json_path, md_path = save_discovery_artifacts(report, output_dir=output_dir)

        # Also capture final diagnostic screenshot
        screenshot_path = output_dir / "02_whop_discovery_page.png"
        try:
            page.screenshot(path=str(screenshot_path), full_page=False)
            log.info("Discovery page screenshot saved to %s", screenshot_path)
        except Exception as exc:
            log.warning("Could not capture discovery screenshot: %s", sanitize_text(str(exc)))

        # Summary output
        print("\n================ WHOP DISCOVERY SCAN RESULTS ================")
        print(f"Run ID:            {report.run_id}")
        print(f"Authenticated:     {report.authenticated}")
        print(f"Final URL:         {report.final_url}")
        print(f"Campaigns Found:   {report.campaign_count}")
        print(f"Eligible:          {report.eligible_count}")
        print(f"Rejected:          {report.rejected_count}")
        print(f"JSON Artifact:     {json_path}")
        print(f"Markdown Summary:  {md_path}")
        print("=============================================================\n")

        if not report.authenticated:
            log.warning("Scan completed but session did not verify as authenticated.")
            return 1

        log.info("Whop discovery completed successfully!")
        return 0

    except Exception as exc:
        log.error("Unexpected error during discovery scan: %s", sanitize_text(str(exc)))
        return 1
    finally:
        browser.close()
        log.info("Browser closed cleanly.")


def main():
    parser = argparse.ArgumentParser(description="Run Whop Campaign Discovery Scan (Step 2)")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/whop_discovery"),
        help="Directory to save discovery artifacts",
    )
    parser.add_argument(
        "--target-url",
        type=str,
        default="https://whop.com/discover",
        help="Whop entry point or marketplace catalog URL",
    )
    parser.add_argument(
        "--max-campaigns",
        type=int,
        default=50,
        help="Maximum campaign cards to inspect",
    )
    parser.add_argument(
        "--no-details",
        action="store_true",
        help="Skip navigating into individual detail cards",
    )
    args = parser.parse_args()

    exit_code = run_discovery_scan(
        output_dir=args.output_dir,
        target_url=args.target_url,
        max_campaigns=args.max_campaigns,
        inspect_details=not args.no_details,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()