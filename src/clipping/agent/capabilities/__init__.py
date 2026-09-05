"""Capabilities subpackage for Master Agent."""

from clipping.agent.capabilities.base import (
    AgentCapability,
    CapabilityContext,
    CapabilityResult,
)
from clipping.agent.capabilities.registry import CapabilityRegistry
from clipping.agent.capabilities.clipping_adapter import MediaClippingCapability

__all__ = [
    "AgentCapability",
    "CapabilityContext",
    "CapabilityResult",
    "CapabilityRegistry",
    "MediaClippingCapability",
]
