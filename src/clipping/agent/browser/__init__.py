"""Browser Automation Module."""

from clipping.agent.browser.capability import BrowserAutomationCapability
from clipping.agent.browser.driver import BrowserDriver, PlaywrightBrowserDriver, MockBrowserDriver
from clipping.agent.browser.engine import CloudBrowserEngine
from clipping.agent.browser.models import (
    BrowserAction,
    BrowserActionResult,
    BrowserActionType,
    BrowserSessionConfig,
    PageExtractionResult,
)

__all__ = [
    "BrowserAutomationCapability",
    "BrowserDriver",
    "PlaywrightBrowserDriver",
    "MockBrowserDriver",
    "CloudBrowserEngine",
    "BrowserAction",
    "BrowserActionResult",
    "BrowserActionType",
    "BrowserSessionConfig",
    "PageExtractionResult",
]
