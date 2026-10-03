"""Whop Automated Authentication and Session Renewal Engine.

Provides autonomous login and session regeneration using credentials:
- Automatically activates when cookies expire or are missing.
- Uses HumanActor biological kinematics and typing cadence.
- Extracts session state and stores fresh cookies in local environment.
- Strictly redacts passwords and secrets from all logs and diagnostics.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from playwright.sync_api import BrowserContext, Page, sync_playwright

from .browser import WhopBrowser
from .config import WhopConfig, sanitize_text
from .diagnostics import detect_whop_authentication
from .human_interaction import HumanActor
from .session import ValidatedSessionState, parse_and_validate_session_state
from .stealth import apply_stealth_to_context

log = logging.getLogger(__name__)

# UI Selectors for Whop login
EMAIL_INPUT_SELECTORS = [
    "input[type='email']",
    "input[name='email']",
    "input[placeholder*='email' i]",
    "input[aria-label*='email' i]",
    "#email",
]

PASSWORD_INPUT_SELECTORS = [
    "input[type='password']",
    "input[name='password']",
    "input[placeholder*='password' i]",
    "input[aria-label*='password' i]",
    "#password",
]

LOGIN_SUBMIT_SELECTORS = [
    "button[type='submit']",
    "button:has-text('Continue')",
    "button:has-text('Sign In')",
    "button:has-text('Log In')",
    "button:has-text('Sign in')",
    "button:has-text('Log in')",
    "[data-testid*='login-button']",
    "[data-testid*='submit-btn']",
]


class WhopAuthenticationError(Exception):
    """Raised when authentication fails or credentials are invalid."""
    pass


class WhopAuthenticator:
    """Handles automated login and self-healing session refresh."""

    def __init__(self, config: Optional[WhopConfig] = None):
        self.config = config or WhopConfig.from_env()

    def login_with_credentials(
        self,
        email: Optional[str] = None,
        password: Optional[str] = None,
        headless: Optional[bool] = None,
        save_to_env: bool = True,
    ) -> ValidatedSessionState:
        """Executes autonomous login into Whop using credentials."""
        user_email = (email or os.getenv("WHOP_EMAIL", "")).strip()
        user_pass = (password or os.getenv("WHOP_PASSWORD", "")).strip()

        if not user_email or not user_pass:
            raise WhopAuthenticationError(
                "Cannot perform auto-login: WHOP_EMAIL or WHOP_PASSWORD is not provided. "
                "Configure them in .env or pass them directly."
            )

        is_headless = self.config.headless if headless is None else headless
        login_url = f"{self.config.base_url.rstrip('/')}/login"
        log.info("Starting autonomous Whop login for email: %s (headless=%s)...", sanitize_text(user_email), is_headless)

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=is_headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--window-size=1920,1080",
                ],
            )
            ctx = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/130.0.0.0 Safari/537.36"
                ),
            )
            apply_stealth_to_context(ctx)
            page = ctx.new_page()

            try:
                log.info("Navigating to login page: %s", sanitize_text(login_url))
                page.goto(login_url, timeout=35000, wait_until="domcontentloaded")
                page.wait_for_timeout(3000)

                actor = HumanActor(page=page, dry_run=False)

                # 1. Fill email
                email_loc = None
                for sel in EMAIL_INPUT_SELECTORS:
                    loc = page.locator(sel)
                    if loc.count() > 0 and loc.first.is_visible():
                        email_loc = loc.first
                        break

                if not email_loc:
                    raise WhopAuthenticationError("Email input field not found on Whop login page.")

                log.info("Entering email via human-like typing cadence...")
                actor.human_type(email_loc, user_email, simulate_mistakes=False)
                page.wait_for_timeout(1000)

                # Check if there is an intermediate "Continue" button
                pwd_loc = None
                for p_sel in PASSWORD_INPUT_SELECTORS:
                    loc = page.locator(p_sel)
                    if loc.count() > 0 and loc.first.is_visible():
                        pwd_loc = loc.first
                        break

                if not pwd_loc:
                    # Look for Continue button
                    continue_btn = None
                    for b_sel in LOGIN_SUBMIT_SELECTORS:
                        loc = page.locator(b_sel)
                        if loc.count() > 0 and loc.first.is_visible():
                            continue_btn = loc.first
                            break
                    if continue_btn:
                        actor.human_click(continue_btn, action_name="submit")
                        page.wait_for_timeout(2500)

                    # Now re-check for password field
                    for p_sel in PASSWORD_INPUT_SELECTORS:
                        loc = page.locator(p_sel)
                        if loc.count() > 0 and loc.first.is_visible():
                            pwd_loc = loc.first
                            break

                # 2. Check for OTP verification screen or Password field
                otp_loc = None
                for otp_sel in ("input[name='otp']", "input[inputmode='numeric']", "input[placeholder*='code' i]"):
                    loc = page.locator(otp_sel)
                    if loc.count() > 0 and loc.first.is_visible():
                        otp_loc = loc.first
                        break

                if otp_loc or page.locator(":text-matches('Verify it\\'s you|entering the code', 'i')").count() > 0:
                    log.info("Whop requested 6-digit email OTP verification code.")
                    from .gmail_otp import GmailOTPReader
                    reader = GmailOTPReader()
                    if reader.is_configured():
                        log.info("Fetching Whop OTP autonomously from Gmail inbox via IMAP...")
                        otp_code = reader.fetch_latest_whop_otp(timeout_seconds=60.0)
                        log.info("Entering 6-digit OTP code into Whop login screen...")
                        if otp_loc:
                            actor.human_click(otp_loc)
                            page.keyboard.type(otp_code, delay=150)
                        else:
                            page.keyboard.type(otp_code, delay=150)
                        page.wait_for_timeout(2000)

                        sign_in_btn = page.locator("button:has-text('Sign in'), button:has-text('Submit')")
                        if sign_in_btn.count() > 0 and sign_in_btn.first.is_visible():
                            actor.human_click(sign_in_btn.first, action_name="submit")
                            page.wait_for_timeout(4000)
                    else:
                        raise WhopAuthenticationError(
                            "Whop requested OTP verification code, but GMAIL_APP_PASSWORD is not configured in .env. "
                            "Please set GMAIL_APP_PASSWORD to enable autonomous OTP fetching."
                        )
                elif pwd_loc:
                    log.info("Entering password via secure human typing cadence...")
                    actor.human_type(pwd_loc, user_pass, simulate_mistakes=False)
                    page.wait_for_timeout(1000)

                    submit_btn = None
                    for b_sel in LOGIN_SUBMIT_SELECTORS:
                        loc = page.locator(b_sel)
                        if loc.count() > 0 and loc.first.is_visible():
                            submit_btn = loc.first
                            break

                    if submit_btn:
                        actor.human_click(submit_btn, action_name="submit")
                        page.wait_for_timeout(4000)
                    else:
                        page.keyboard.press("Enter")
                        page.wait_for_timeout(4000)

                # 4. Wait for redirect or check authentication
                is_auth, reason = detect_whop_authentication(page)
                if not is_auth:
                    # Give it up to 10 more seconds to settle
                    for _ in range(5):
                        page.wait_for_timeout(2000)
                        is_auth, reason = detect_whop_authentication(page)
                        if is_auth:
                            break

                # 5. Extract fresh session cookies
                cookies = ctx.cookies()
                whop_cookies = [c for c in cookies if "whop.com" in c.get("domain", "")]
                if not whop_cookies:
                    raise WhopAuthenticationError(
                        f"Login completed but zero Whop cookies captured. Current URL: {sanitize_text(page.url)}"
                    )

                cookies_json = json.dumps(whop_cookies)
                validated_state = parse_and_validate_session_state(cookies_json)

                log.info(
                    "Autonomous login SUCCESS: Captured %d valid session cookies across %s!",
                    validated_state.cookie_count,
                    validated_state.domains,
                )

                if save_to_env:
                    self.save_session(cookies_json)

                return validated_state

            finally:
                page.close()
                ctx.close()
                browser.close()

    def save_session(self, cookies_json: str) -> None:
        """Saves refreshed cookies to data/whop_storage_state.json and .env."""
        # 1. Save to data/whop_storage_state.json
        data_dir = Path("data")
        data_dir.mkdir(parents=True, exist_ok=True)
        storage_path = data_dir / "whop_storage_state.json"
        storage_path.write_text(cookies_json, encoding="utf-8")
        log.info("Saved fresh session state to %s", storage_path)

        # 2. Update .env
        env_file = Path(".env")
        if env_file.exists():
            content = env_file.read_text(encoding="utf-8")
            if "WHOP_COOKIES=" in content:
                # Replace existing
                new_content = re.sub(
                    r'WHOP_COOKIES=.*',
                    f'WHOP_COOKIES={json.dumps(cookies_json)}',
                    content,
                )
            else:
                new_content = content.rstrip() + f'\nWHOP_COOKIES={json.dumps(cookies_json)}\n'
            env_file.write_text(new_content, encoding="utf-8")
            log.info("Updated WHOP_COOKIES in .env file.")
        
        # Also set in current process environment
        os.environ["WHOP_COOKIES"] = cookies_json

    def ensure_valid_session(self) -> ValidatedSessionState:
        """Self-healing session retriever: validates current cookies or regenerates via login."""
        current_cookies = os.getenv("WHOP_COOKIES", "").strip()
        if current_cookies:
            try:
                state = parse_and_validate_session_state(current_cookies)
                return state
            except Exception as e:
                log.warning("Existing WHOP_COOKIES invalid (%s); attempting credential renewal...", e)

        # Attempt auto-login if credentials exist
        email = os.getenv("WHOP_EMAIL", "").strip()
        password = os.getenv("WHOP_PASSWORD", "").strip()
        if email and password:
            return self.login_with_credentials(email, password)

        raise WhopAuthenticationError(
            "No valid WHOP_COOKIES and no WHOP_EMAIL/WHOP_PASSWORD configured in environment."
        )
