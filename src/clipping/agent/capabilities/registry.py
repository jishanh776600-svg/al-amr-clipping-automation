"""Registry managing dynamic registration, lookup, and introspection of Agent Capabilities."""

from typing import Any, Dict, List, Optional
from clipping.agent.capabilities.base import AgentCapability
from clipping.agent.exceptions import CapabilityNotFoundError
from clipping.logging.logger import get_logger

logger = get_logger("clipping.agent.capabilities.registry")


class CapabilityRegistry:
    """
    Central repository of capabilities accessible by the Master Agent.
    Allows modular extension across future phases without modifying core orchestrator.
    """

    def __init__(self):
        self._capabilities: Dict[str, AgentCapability] = {}

    def register(self, capability: AgentCapability, override: bool = False) -> None:
        """Registers a new capability implementation."""
        name = capability.name.strip().lower()
        if not name:
            raise ValueError("Capability name cannot be empty")

        if name in self._capabilities and not override:
            raise ValueError(f"Capability '{name}' is already registered. Set override=True to replace.")

        self._capabilities[name] = capability
        logger.info("Registered agent capability", name=name, version=capability.version)

    def get(self, name: str) -> AgentCapability:
        """Resolves a capability by unique name. Raises CapabilityNotFoundError if absent."""
        clean_name = name.strip().lower()
        cap = self._capabilities.get(clean_name)
        if not cap:
            raise CapabilityNotFoundError(
                f"Capability '{clean_name}' not found. Registered capabilities: {list(self._capabilities.keys())}"
            )
        return cap

    def has(self, name: str) -> bool:
        """Checks whether a capability name is registered."""
        return name.strip().lower() in self._capabilities

    def list_capabilities(self) -> List[Dict[str, Any]]:
        """Returns metadata descriptors of all registered capabilities."""
        return [
            {
                "name": cap.name,
                "description": cap.description,
                "version": cap.version,
                "is_idempotent": cap.is_idempotent,
                "is_reversible": cap.is_reversible,
            }
            for cap in self._capabilities.values()
        ]
