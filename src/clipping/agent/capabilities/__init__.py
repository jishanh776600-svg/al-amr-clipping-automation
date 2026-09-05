"""Capabilities subpackage for Master Agent."""

from clipping.agent.capabilities.base import (
    AgentCapability,
    CapabilityContext,
    CapabilityResult,
)
from clipping.agent.capabilities.registry import CapabilityRegistry
from clipping.agent.capabilities.clipping_adapter import MediaClippingCapability
from clipping.agent.browser.capability import BrowserAutomationCapability
from clipping.agent.campaign.discovery import CampaignDiscoveryCapability
from clipping.agent.account.capability import AccountManagementCapability

__all__ = [
    "AgentCapability",
    "CapabilityContext",
    "CapabilityResult",
    "CapabilityRegistry",
    "MediaClippingCapability",
    "BrowserAutomationCapability",
    "CampaignDiscoveryCapability",
    "AccountManagementCapability",
]
