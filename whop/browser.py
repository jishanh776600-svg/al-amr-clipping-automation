"""Whop Browser Engine (Step 1 Foundation).

Launches Playwright Chromium with secure context configuration, safe timeouts,
read-only enforcement, and deterministic lifecycle cleanup.
"""

import logging
from typing import Any, Optional
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from .config import WhopConfig, WhopDryRunViolationError, sanitize_text
from .session import ValidatedSessionState, apply_session_to_context

log = logging.getLogger(__name__)

# Mutation actions blocked by Step 1 safety guard
FORBIDDEN_MUTATION_ACTIONS = frozenset([
    "join",
    "apply",
    "accept",
    "claim",
    "submit",
    "send",
    "message",
    "upload",
    "download_asset",
    "modify",
    "delete",
])


class WhopBrowser:
    """Read-only browser automation wrapper for Step 1 verification."""

    def __init__(self, config: Optional[WhopConfig] = None):
        self.config = config or WhopConfig.from_env()
        self._playwright: Any = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    def assert_action_permitted(self, action_name: str) -> None:
        """Enforces that mutation actions cannot be executed in Step 1."""
        clean_action = str(action_name).strip().lower()
        if self.config.dry_run or clean_action in FORBIDDEN_MUTATION_ACTIONS:
            raise WhopDryRunViolationError(
                f"Action '{clean_action}' is strictly forbidden by Step 1 read-only guard."
            )

    def launch(self, session_state: Optional[ValidatedSessionState] = None) -> Page:
        """Launches Chromium, configures context/timeouts, attaches session, and creates page."""
        log.info("Launching Chromium browser in headless=%s mode.", self.config.headless)
        self._playwright = sync_playwright().start()

        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-infobars",
            "--window-size=1920,1080",
        ]

        self._browser = self._playwright.chromium.launch(
            headless=self.config.headless,
            args=launch_args,
        )

        context_kwargs = {
            "viewport": {"width": 1920, "height": 1080},
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/130.0.0.0 Safari/537.36"
            ),
            "locale": "en-US",
            "timezone_id": "America/New_York",
        }

        # If a full Playwright storage state is provided, use it directly in context creation
        if session_state and session_state.raw_storage_dict:
            context_kwargs["storage_state"] = session_state.raw_storage_dict

        self._context = self._browser.new_context(**context_kwargs)
        self._context.set_default_navigation_timeout(self.config.navigation_timeout_ms)
        self._context.set_default_timeout(self.config.navigation_timeout_ms)

        # Fallback/auxiliary cookie application
        if session_state:
            apply_session_to_context(self._context, session_state)

        self._page = self._context.new_page()
        self._page.set_default_navigation_timeout(self.config.navigation_timeout_ms)
        self._page.set_default_timeout(self.config.navigation_timeout_ms)

        return self._page

    def navigate_safely(self, target_url: str) -> None:
        """Navigates to URL and waits for page load network state."""
        if not self._page:
            raise RuntimeError("Browser not launched. Call launch() first.")
        
        safe_url = sanitize_text(target_url)
        log.info("Navigating to %s (timeout=%dms)", safe_url, self.config.navigation_timeout_ms)

        # Attempt navigation with retry on transient network errors
        attempts = 2
        for i in range(attempts):
            try:
                self._page.goto(target_url, wait_until="domcontentloaded", timeout=self.config.navigation_timeout_ms)
                # Wait briefly for client-side stabilization
                self._page.wait_for_timeout(self.config.stabilize_wait_ms)
                return
            except Exception as exc:
                if i == attempts - 1:
                    log.error("Navigation failed after %d attempts: %s", attempts, sanitize_text(str(exc)))
                    raise
                log.warning("Navigation attempt %d failed, retrying once...", i + 1)
                self._page.wait_for_timeout(1000)

    @property
    def page(self) -> Optional[Page]:
        return self._page

    @property
    def context(self) -> Optional[BrowserContext]:
        return self._context

    def close(self) -> None:
        """Deterministically closes page, context, browser, and playwright instances."""
        if self._page:
            try:
                self._page.close()
            except Exception:
                pass
            self._page = None

        if self._context:
            try:
                self._context.close()
            except Exception:
                pass
            self._context = None

        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None

        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    def get_human_actor(self, page: Optional[Page] = None) -> Any:
        """Returns a HumanActor wrapping the given page or the active browser page."""
        from .human_interaction import HumanActor
        active_page = page or self._page
        if not active_page:
            raise RuntimeError("Cannot get human actor: browser page is not launched.")
        return HumanActor(active_page)

    def __enter__(self) -> "WhopBrowser":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
