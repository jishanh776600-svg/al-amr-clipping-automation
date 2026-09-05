"""Pluggable Browser Driver Abstraction for Cloud Worker Automation."""

import asyncio
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from clipping.agent.browser.models import (
    BrowserAction,
    BrowserActionResult,
    BrowserActionType,
    BrowserSessionConfig,
    PageExtractionResult,
)
from clipping.logging.logger import get_logger

logger = get_logger("clipping.agent.browser.driver")

# Known Challenge / Bot-wall keywords in title or DOM
CAPTCHA_SIGNATURES = ["cf-turnstile", "g-recaptcha", "h-captcha", "arkose", "challenge-running", "captcha", "security check"]
MFA_SIGNATURES = ["two-factor", "2-step", "authenticator code", "mfa", "sms code", "verify your identity"]


class BrowserDriver(ABC):
    """Abstract Browser Driver interface."""

    @abstractmethod
    async def start(self, config: Optional[BrowserSessionConfig] = None) -> None:
        """Launch or initialize browser session."""
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Close page, context, and browser cleanly."""
        pass

    @abstractmethod
    async def execute_action(self, action: BrowserAction) -> BrowserActionResult:
        """Execute a single declarative interaction instruction."""
        pass

    @abstractmethod
    async def extract_page(self) -> PageExtractionResult:
        """Extract structured textual and structural metadata from the current page."""
        pass

    @abstractmethod
    async def take_screenshot(self) -> bytes:
        """Capture screenshot of the active viewport."""
        pass


class PlaywrightBrowserDriver(BrowserDriver):
    """
    Production-grade Playwright driver executing headless Chromium in cloud workers.
    Ensures isolated contexts, strict timeouts, and clean resource deallocation.
    """

    def __init__(self, config: Optional[BrowserSessionConfig] = None):
        self.config = config or BrowserSessionConfig()
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._lock = asyncio.Lock()

    async def start(self, config: Optional[BrowserSessionConfig] = None) -> None:
        if config:
            self.config = config
        async with self._lock:
            if self._page is not None:
                return

            try:
                from playwright.async_api import async_playwright
            except ImportError as e:
                raise RuntimeError(
                    "Playwright is not installed. Install via pip install playwright && playwright install chromium"
                ) from e

            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=self.config.headless,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )

            context_kwargs: Dict[str, Any] = {
                "viewport": {
                    "width": self.config.viewport_width,
                    "height": self.config.viewport_height,
                },
                "ignore_https_errors": self.config.ignore_https_errors,
            }
            if self.config.user_agent:
                context_kwargs["user_agent"] = self.config.user_agent
            if self.config.storage_state_path:
                context_kwargs["storage_state"] = self.config.storage_state_path

            self._context = await self._browser.new_context(**context_kwargs)
            self._context.set_default_timeout(float(self.config.timeout_ms))
            self._page = await self._context.new_page()
            logger.info("Playwright headless browser session initialized", headless=self.config.headless)

    async def stop(self) -> None:
        async with self._lock:
            if self._page:
                try:
                    await self._page.close()
                except Exception as e:
                    logger.warning("Error closing browser page", error=str(e))
                self._page = None

            if self._context:
                try:
                    await self._context.close()
                except Exception as e:
                    logger.warning("Error closing browser context", error=str(e))
                self._context = None

            if self._browser:
                try:
                    await self._browser.close()
                except Exception as e:
                    logger.warning("Error closing browser process", error=str(e))
                self._browser = None

            if self._playwright:
                try:
                    await self._playwright.stop()
                except Exception as e:
                    logger.warning("Error stopping playwright instance", error=str(e))
                self._playwright = None

            logger.info("Playwright browser session cleanly shutdown")

    async def execute_action(self, action: BrowserAction) -> BrowserActionResult:
        if not self._page:
            await self.start()

        assert self._page is not None
        timeout = float(action.timeout_ms or self.config.timeout_ms)

        try:
            if action.action_type == BrowserActionType.NAVIGATE:
                if not action.url:
                    raise ValueError("NAVIGATE action requires 'url'")
                wait_until = action.options.get("wait_until", "networkidle")
                response = await self._page.goto(action.url, timeout=timeout, wait_until=wait_until)
                status_code = response.status if response else None

                # Check challenges
                challenge = await self._detect_challenges()
                return BrowserActionResult(
                    success=True,
                    action_type=action.action_type,
                    data={"status_code": status_code, "current_url": self._page.url},
                    detected_challenge=challenge,
                )

            elif action.action_type == BrowserActionType.CLICK:
                if not action.selector:
                    raise ValueError("CLICK action requires 'selector'")
                await self._page.click(action.selector, timeout=timeout)
                return BrowserActionResult(success=True, action_type=action.action_type)

            elif action.action_type == BrowserActionType.FILL:
                if not action.selector or action.text is None:
                    raise ValueError("FILL action requires 'selector' and 'text'")
                await self._page.fill(action.selector, action.text, timeout=timeout)
                return BrowserActionResult(success=True, action_type=action.action_type)

            elif action.action_type == BrowserActionType.TYPE:
                if not action.selector or action.text is None:
                    raise ValueError("TYPE action requires 'selector' and 'text'")
                delay = action.options.get("delay_ms", 50)
                await self._page.type(action.selector, action.text, delay=delay, timeout=timeout)
                return BrowserActionResult(success=True, action_type=action.action_type)

            elif action.action_type == BrowserActionType.SCROLL:
                pixels = action.scroll_pixels or 500
                await self._page.mouse.wheel(0, pixels)
                await asyncio.sleep(0.2)
                return BrowserActionResult(success=True, action_type=action.action_type, data={"scrolled": pixels})

            elif action.action_type == BrowserActionType.EXTRACT_TEXT:
                if not action.selector:
                    raise ValueError("EXTRACT_TEXT action requires 'selector'")
                element = await self._page.wait_for_selector(action.selector, timeout=timeout)
                text = await element.inner_text() if element else ""
                return BrowserActionResult(success=True, action_type=action.action_type, data=text)

            elif action.action_type == BrowserActionType.EXTRACT_ATTRIBUTES:
                if not action.selector or not action.attribute_name:
                    raise ValueError("EXTRACT_ATTRIBUTES action requires 'selector' and 'attribute_name'")
                element = await self._page.wait_for_selector(action.selector, timeout=timeout)
                val = await element.get_attribute(action.attribute_name) if element else None
                return BrowserActionResult(success=True, action_type=action.action_type, data=val)

            elif action.action_type == BrowserActionType.SCREENSHOT:
                shot = await self._page.screenshot(type="jpeg", quality=80)
                return BrowserActionResult(
                    success=True,
                    action_type=action.action_type,
                    screenshot_bytes=shot,
                )

            elif action.action_type == BrowserActionType.EVALUATE:
                if not action.expression:
                    raise ValueError("EVALUATE action requires 'expression'")
                eval_res = await self._page.evaluate(action.expression)
                return BrowserActionResult(success=True, action_type=action.action_type, data=eval_res)

            elif action.action_type == BrowserActionType.WAIT_FOR_SELECTOR:
                if not action.selector:
                    raise ValueError("WAIT_FOR_SELECTOR action requires 'selector'")
                await self._page.wait_for_selector(action.selector, timeout=timeout)
                return BrowserActionResult(success=True, action_type=action.action_type)

            else:
                raise NotImplementedError(f"Unsupported action type: {action.action_type}")

        except Exception as e:
            logger.error("Browser action failed", action_type=action.action_type, error=str(e))
            shot = None
            try:
                if self._page:
                    shot = await self._page.screenshot(type="jpeg", quality=60)
            except Exception:
                pass
            return BrowserActionResult(
                success=False,
                action_type=action.action_type,
                error_message=str(e),
                screenshot_bytes=shot,
            )

    async def extract_page(self) -> PageExtractionResult:
        if not self._page:
            await self.start()
        assert self._page is not None

        url = self._page.url
        title = await self._page.title()
        text_content = await self._page.evaluate("() => document.body ? document.body.innerText : ''")
        has_captcha = any(sig in (text_content.lower() + title.lower()) for sig in CAPTCHA_SIGNATURES)
        has_mfa = any(sig in (text_content.lower() + title.lower()) for sig in MFA_SIGNATURES)

        return PageExtractionResult(
            url=url,
            title=title,
            text_content=text_content,
            has_captcha=has_captcha,
            has_mfa=has_mfa,
        )

    async def take_screenshot(self) -> bytes:
        if not self._page:
            await self.start()
        assert self._page is not None
        return await self._page.screenshot(type="jpeg", quality=75)

    async def _detect_challenges(self) -> Optional[str]:
        if not self._page:
            return None
        title = (await self._page.title()).lower()
        content = (await self._page.content()).lower()
        for sig in CAPTCHA_SIGNATURES:
            if sig in title or sig in content:
                return "captcha"
        for sig in MFA_SIGNATURES:
            if sig in title or sig in content:
                return "mfa"
        return None


class MockBrowserDriver(BrowserDriver):
    """Deterministic, lightweight browser driver for tests and environments without Chromium."""

    def __init__(self, config: Optional[BrowserSessionConfig] = None):
        self.config = config or BrowserSessionConfig()
        self.is_running = False
        self.current_url = "about:blank"
        self.current_title = "Empty Page"
        self.current_text = ""
        self.executed_actions: List[BrowserAction] = []
        self.mock_pages: Dict[str, Dict[str, Any]] = {}
        self.simulate_captcha = False
        self.simulate_mfa = False

    def register_mock_page(self, url: str, title: str, content: str, elements: Optional[List[Dict[str, Any]]] = None) -> None:
        self.mock_pages[url] = {
            "title": title,
            "content": content,
            "elements": elements or [],
        }

    async def start(self, config: Optional[BrowserSessionConfig] = None) -> None:
        self.is_running = True

    async def stop(self) -> None:
        self.is_running = False

    async def execute_action(self, action: BrowserAction) -> BrowserActionResult:
        self.executed_actions.append(action)

        if action.action_type == BrowserActionType.NAVIGATE:
            self.current_url = action.url or "about:blank"
            if self.simulate_captcha:
                self.current_title = "Security Check - Cloudflare Turnstile Challenge"
                self.current_text = "Please verify you are human: captcha challenge"
                return BrowserActionResult(
                    success=True,
                    action_type=action.action_type,
                    data={"current_url": self.current_url},
                    detected_challenge="captcha",
                )
            if self.simulate_mfa:
                self.current_title = "Two-Factor Authentication Required"
                self.current_text = "Enter the 6-digit MFA authenticator code"
                return BrowserActionResult(
                    success=True,
                    action_type=action.action_type,
                    data={"current_url": self.current_url},
                    detected_challenge="mfa",
                )

            page = self.mock_pages.get(self.current_url, {
                "title": f"Page at {self.current_url}",
                "content": f"Mock page content for {self.current_url}",
            })
            self.current_title = page["title"]
            self.current_text = page["content"]
            return BrowserActionResult(
                success=True,
                action_type=action.action_type,
                data={"current_url": self.current_url},
            )

        elif action.action_type == BrowserActionType.EXTRACT_TEXT:
            return BrowserActionResult(
                success=True,
                action_type=action.action_type,
                data=self.current_text,
            )

        elif action.action_type == BrowserActionType.SCREENSHOT:
            return BrowserActionResult(
                success=True,
                action_type=action.action_type,
                screenshot_bytes=b"MOCK_SCREENSHOT_JPEG",
            )

        return BrowserActionResult(success=True, action_type=action.action_type, data="ok")

    async def extract_page(self) -> PageExtractionResult:
        has_captcha = self.simulate_captcha or any(sig in self.current_text.lower() for sig in CAPTCHA_SIGNATURES)
        has_mfa = self.simulate_mfa or any(sig in self.current_text.lower() for sig in MFA_SIGNATURES)
        return PageExtractionResult(
            url=self.current_url,
            title=self.current_title,
            text_content=self.current_text,
            has_captcha=has_captcha,
            has_mfa=has_mfa,
        )

    async def take_screenshot(self) -> bytes:
        return b"MOCK_SCREENSHOT_BYTES"
