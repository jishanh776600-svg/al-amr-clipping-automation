"""Cloud Browser Engine with resilient lifecycle management and anti-bot escalation."""

import asyncio
from typing import Any, Dict, List, Optional
from clipping.agent.browser.driver import BrowserDriver, PlaywrightBrowserDriver
from clipping.agent.browser.models import (
    BrowserAction,
    BrowserActionResult,
    BrowserActionType,
    BrowserSessionConfig,
    PageExtractionResult,
)
from clipping.agent.escalation import EscalationContext, EscalationReason, EscalationSeverity
from clipping.agent.exceptions import CapabilityExecutionError
from clipping.logging.logger import get_logger

logger = get_logger("clipping.agent.browser.engine")


class CloudBrowserEngine:
    """
    High-level browser automation engine designed for headless cloud workers.
    Provides session context manager, automated recovery, and security challenge escalation.
    """

    def __init__(self, driver: Optional[BrowserDriver] = None, config: Optional[BrowserSessionConfig] = None):
        self.config = config or BrowserSessionConfig()
        self.driver = driver or PlaywrightBrowserDriver(config=self.config)

    async def __aenter__(self):
        await self.driver.start(self.config)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.driver.stop()

    async def execute_workflow(
        self,
        actions: List[BrowserAction],
        capture_evidence: bool = True,
    ) -> Dict[str, Any]:
        """
        Executes an ordered sequence of browser actions.
        Stops immediately and raises appropriate escalation if a CAPTCHA or MFA challenge is encountered.
        """
        results: List[BrowserActionResult] = []
        evidence_screenshot: Optional[bytes] = None

        for idx, action in enumerate(actions):
            logger.debug("Executing browser action", step=idx + 1, action_type=action.action_type)
            res = await self.driver.execute_action(action)
            results.append(res)

            if res.detected_challenge:
                logger.warning("Bot challenge detected on page", challenge=res.detected_challenge)
                if capture_evidence and not res.screenshot_bytes:
                    res = res.model_copy(update={"screenshot_bytes": await self.driver.take_screenshot()})
                return {
                    "success": False,
                    "challenge": res.detected_challenge,
                    "step_failed": idx,
                    "results": results,
                    "evidence_screenshot": res.screenshot_bytes,
                }

            if not res.success:
                logger.error("Browser action failed", step=idx + 1, error=res.error_message)
                return {
                    "success": False,
                    "error": res.error_message,
                    "step_failed": idx,
                    "results": results,
                    "evidence_screenshot": res.screenshot_bytes,
                }

        if capture_evidence:
            try:
                evidence_screenshot = await self.driver.take_screenshot()
            except Exception:
                evidence_screenshot = None

        page_data = await self.driver.extract_page()

        if page_data.has_captcha:
            return {
                "success": False,
                "challenge": "captcha",
                "results": results,
                "page_data": page_data,
                "evidence_screenshot": evidence_screenshot,
            }
        if page_data.has_mfa:
            return {
                "success": False,
                "challenge": "mfa",
                "results": results,
                "page_data": page_data,
                "evidence_screenshot": evidence_screenshot,
            }

        return {
            "success": True,
            "results": results,
            "page_data": page_data,
            "evidence_screenshot": evidence_screenshot,
        }
