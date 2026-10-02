#!/usr/bin/env python3
"""Whop Cloud-Browser Access Test Runner (Step 1 Verification).

Executes a secure, read-only browser verification test against Whop:
1. Loads environment configuration.
2. Validates that WHOP_COOKIES secret is present and valid.
3. Launches Playwright Chromium with stealth context.
4. Restores authenticated session state.
5. Navigates to Whop entry point and waits for stabilization.
6. Assesses authentication state.
7. Generates safe diagnostic artifacts (screenshot + JSON report).
8. Closes all browser resources cleanly.
9. Returns exit code 0 on verified authentication, 1 on failure.

Strictly read-only: does not join, claim, apply, or mutate any campaign.
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
    WhopSessionError,
    capture_diagnostics,
    detect_whop_authentication,
    parse_and_validate_session_state,
    sanitize_text,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("whop_access_test")


def run_access_test(output_dir: Path) -> int:
    """Executes the Step 1 read-only Whop access test."""
    output_dir.mkdir(parents=True, exist_ok=True)
    config = WhopConfig.from_env()

    log.info("Starting Whop Access Test (DRY_RUN=%s, Headless=%s)", config.dry_run, config.headless)

    # 1. Validate WHOP_COOKIES secret presence
    try:
        raw_cookies = config.get_cookies_secret()
    except WhopConfigError as exc:
        log.error("Configuration Error: %s", sanitize_text(str(exc)))
        print("::error::WHOP_COOKIES secret is missing or empty. Please set WHOP_COOKIES in GitHub Secrets.")
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

    # 3. Launch browser and test authenticated navigation
    browser = WhopBrowser(config=config)
    is_authenticated = False
    status_reason = "uninitialized"

    try:
        page = browser.launch(session_state=session_state)
        target_url = config.base_url

        log.info("Navigating to entry point: %s", sanitize_text(target_url))
        browser.navigate_safely(target_url)

        # 4. Assess authentication state
        is_authenticated, status_reason = detect_whop_authentication(page)
        log.info("Authentication check result: authenticated=%s, reason=%s", is_authenticated, status_reason)

        # 5. Capture diagnostic artifacts
        screenshot_filename = "01_whop_authenticated.png" if is_authenticated else "01_whop_unauthenticated.png"
        report = capture_diagnostics(
            page=page,
            output_dir=output_dir,
            is_authenticated=is_authenticated,
            status_reason=status_reason,
            cookie_count=session_state.cookie_count,
            screenshot_filename=screenshot_filename,
        )

        print("\n================ WHOP ACCESS TEST REPORT ================")
        print(f"Success:          {report.success}")
        print(f"Authenticated:    {report.authenticated}")
        print(f"Status Reason:    {report.reason}")
        print(f"Page Title:       {report.page_title}")
        print(f"Final URL:        {report.final_url}")
        print(f"Screenshot:       {report.screenshot_file}")
        print(f"Timestamp:        {report.timestamp}")
        print("=========================================================\n")

        if not is_authenticated:
            log.warning("Whop session did not appear authenticated (reason: %s).", status_reason)
            return 1

        log.info("Whop authenticated session verified successfully!")
        return 0

    except Exception as exc:
        log.error("Unexpected error during access test: %s", sanitize_text(str(exc)))
        return 1
    finally:
        browser.close()
        log.info("Browser resources closed cleanly.")


def main():
    parser = argparse.ArgumentParser(description="Run Whop Access Test (Step 1)")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output/whop_diagnostics"),
        help="Directory to save diagnostic screenshot and report",
    )
    args = parser.parse_args()
    exit_code = run_access_test(args.output_dir)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
