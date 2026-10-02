"""Whop Safe Diagnostics and Artifact Capture.

Captures safe, non-sensitive diagnostic artifacts (screenshot + JSON report)
for Step 1 verification without leaking any credentials, tokens, or cookies.
"""

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from playwright.sync_api import Page

from .config import sanitize_text

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class WhopDiagnosticReport:
    """Safe diagnostic report payload for workflow artifacts."""
    success: bool
    timestamp: str
    final_url: str
    page_title: str
    authenticated: bool
    reason: str
    dry_run: bool
    cookies_detected: int
    screenshot_file: Optional[str] = None


def detect_whop_authentication(page: Page) -> Tuple[bool, str]:
    """Inspects page URL and visible DOM elements to safely verify session authentication."""
    try:
        current_url = page.url.lower()
        title = page.title().lower()

        # 1. Check for Cloudflare interstitial challenge
        if "just a moment" in title or "cloudflare" in title or "attention required" in title:
            return False, "cloudflare_challenge_detected"

        # 2. Check for redirect to login or authentication endpoints
        login_indicators = ("/login", "/sign-in", "/signin", "/auth/login", "/join/login")
        if any(ind in current_url for ind in login_indicators):
            return False, "login_required"

        if "log in" in title or "sign in" in title or "welcome back" in title:
            # Verify if it's an unauthenticated landing screen
            return False, "login_required"

        # 3. Check for positive authenticated elements or indicators
        # Whop dashboard, user navigation, or creator navigation
        try:
            # Check for typical avatar / user profile menu or dashboard indicator
            # These selectors are safe, read-only checks
            has_user_nav = (
                page.locator("[data-testid*='user'], [data-testid*='avatar'], [aria-label*='Profile'], [aria-label*='Account']").count() > 0
                or page.locator("a[href*='/dashboards'], a[href*='/creator'], a[href*='/settings']").count() > 0
                or "/dashboards" in current_url
                or "/creator" in current_url
                or "/hub" in current_url
            )
            if has_user_nav:
                return True, "session_valid"
        except Exception:
            pass

        # If on main whop.com domain without redirect to /login and not a challenge
        if "whop.com" in current_url and "/login" not in current_url:
            return True, "session_active"

        return False, "session_unverified"
    except Exception as exc:
        log.warning("Authentication detection encountered an error: %s", sanitize_text(str(exc)))
        return False, "detection_error"


def capture_diagnostics(
    page: Page,
    output_dir: Path,
    is_authenticated: bool,
    status_reason: str,
    cookie_count: int = 0,
    screenshot_filename: str = "01_whop_authenticated.png",
) -> WhopDiagnosticReport:
    """Captures screenshot and safe JSON diagnostic report in the output directory."""
    output_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = output_dir / screenshot_filename

    # Capture clean full-page screenshot
    try:
        page.screenshot(path=str(screenshot_path), full_page=False)
        log.info("Diagnostic screenshot saved to %s", screenshot_path)
    except Exception as exc:
        log.error("Failed to capture diagnostic screenshot: %s", sanitize_text(str(exc)))
        screenshot_filename = None

    now_iso = datetime.now(timezone.utc).isoformat()
    clean_url = sanitize_text(page.url)
    clean_title = sanitize_text(page.title())

    report = WhopDiagnosticReport(
        success=bool(is_authenticated),
        timestamp=now_iso,
        final_url=clean_url,
        page_title=clean_title,
        authenticated=bool(is_authenticated),
        reason=status_reason,
        dry_run=True,
        cookies_detected=cookie_count,
        screenshot_file=screenshot_filename,
    )

    # Save JSON report
    report_path = output_dir / "diagnostic_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(asdict(report), f, indent=2)

    log.info("Diagnostic report saved to %s (authenticated=%s, reason=%s)", report_path, is_authenticated, status_reason)
    return report
