"""Master Agent Browser Automation Capability."""

import json
from typing import Any, Dict, List, Optional
from clipping.agent.browser.driver import BrowserDriver, PlaywrightBrowserDriver, MockBrowserDriver
from clipping.agent.browser.engine import CloudBrowserEngine
from clipping.agent.browser.models import (
    BrowserAction,
    BrowserActionType,
    BrowserSessionConfig,
)
from clipping.agent.capabilities.base import AgentCapability, CapabilityContext, CapabilityResult
from clipping.agent.escalation import EscalationContext, EscalationReason, EscalationSeverity
from clipping.logging.logger import get_logger

logger = get_logger("clipping.agent.browser.capability")


class BrowserAutomationCapability(AgentCapability):
    """
    Capability allowing the Master Agent to perform controlled, headless web interactions.
    Handles navigation, DOM scraping, form entry, and challenge escalations.
    """

    def __init__(self, driver: Optional[BrowserDriver] = None):
        self._driver = driver

    @property
    def name(self) -> str:
        return "browser_operation"

    @property
    def description(self) -> str:
        return "Executes isolated, headless browser interactions with security escalation safeguards"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def is_idempotent(self) -> bool:
        return False  # Web mutations may change remote state

    @property
    def is_reversible(self) -> bool:
        return False

    async def execute(self, context: CapabilityContext) -> CapabilityResult:
        inputs = context.inputs
        raw_actions = inputs.get("actions", [])
        initial_url = inputs.get("url")

        actions: List[BrowserAction] = []
        if initial_url and not any(a.get("action_type") == BrowserActionType.NAVIGATE.value for a in raw_actions if isinstance(a, dict)):
            actions.append(BrowserAction(action_type=BrowserActionType.NAVIGATE, url=initial_url))

        for item in raw_actions:
            if isinstance(item, BrowserAction):
                actions.append(item)
            elif isinstance(item, dict):
                actions.append(BrowserAction(**item))

        if not actions:
            return CapabilityResult.failed(
                error_type="EmptyWorkflowError",
                message="No browser actions or target URL provided in inputs",
                is_transient=False,
            )

        timeout_ms = inputs.get("timeout_ms", 30000)
        session_config = BrowserSessionConfig(
            headless=inputs.get("headless", True),
            timeout_ms=timeout_ms,
        )

        engine = CloudBrowserEngine(driver=self._driver, config=session_config)

        try:
            async with engine:
                outcome = await engine.execute_workflow(actions, capture_evidence=True)

                evidence_screenshot = outcome.get("evidence_screenshot")
                screenshot_key = None
                if evidence_screenshot and context.storage_driver:
                    screenshot_key = f"browser_evidence/{context.task_id}/screenshot.jpg"
                    await context.storage_driver.upload_bytes(
                        evidence_screenshot,
                        screenshot_key,
                        content_type="image/jpeg",
                    )

                challenge = outcome.get("challenge")
                if challenge:
                    reason = EscalationReason.CAPTCHA_CHALLENGE if challenge == "captcha" else EscalationReason.MFA_REQUIRED
                    logger.warning(
                        "Browser operation encountered challenge, escalating to operator",
                        task_id=context.task_id,
                        challenge=challenge,
                    )
                    return CapabilityResult.escalate(
                        escalation_context=EscalationContext(
                            what_happened=f"Automated browser stopped: {challenge.upper()} challenge encountered",
                            why_it_happened=f"Anti-bot protection or verification challenge ({challenge}) triggered on page",
                            decision_required=f"Manually resolve {challenge.upper()} or provide fresh session credentials",
                            available_options=["resolve_manually", "provide_session_cookie", "abort_operation"],
                            reason=reason,
                            severity=EscalationSeverity.CRITICAL,
                            metadata={
                                "task_id": context.task_id,
                                "url": initial_url or (actions[0].url if actions else "unknown"),
                                "challenge_type": challenge,
                                "screenshot_key": screenshot_key,
                            },
                        )
                    )

                if not outcome["success"]:
                    return CapabilityResult.failed(
                        error_type="BrowserActionFailure",
                        message=outcome.get("error", "Unknown browser interaction error"),
                        is_transient=True,
                        details={
                            "step_failed": outcome.get("step_failed"),
                            "screenshot_key": screenshot_key,
                        },
                        should_retry=True,
                    )

                page_data = outcome.get("page_data")
                outputs = {
                    "current_url": page_data.url if page_data else None,
                    "title": page_data.title if page_data else None,
                    "text_content": page_data.text_content if page_data else None,
                    "screenshot_key": screenshot_key,
                    "completed_actions": len(actions),
                }
                return CapabilityResult.successful(outputs=outputs)

        except Exception as e:
            logger.error("Browser capability execution error", task_id=context.task_id, error=str(e))
            return CapabilityResult.failed(
                error_type=type(e).__name__,
                message=str(e),
                is_transient=True,
                should_retry=True,
            )
