"""Structured exception hierarchy for Master Agent architecture."""

from typing import Any, Dict, Optional


class AgentError(Exception):
    """Base exception for all Master Agent errors."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class InvalidStateTransitionError(AgentError):
    """Raised when an illegal task state transition is attempted."""
    pass


class TaskDependencyError(AgentError):
    """Raised when task dependencies are unfulfilled or form a cycle."""
    pass


class CapabilityNotFoundError(AgentError):
    """Raised when a requested capability is not registered in the CapabilityRegistry."""
    pass


class CapabilityExecutionError(AgentError):
    """Raised when a capability encounters an unhandled execution error."""
    pass


class TransientTaskError(AgentError):
    """
    Error caused by temporary, recoverable issues (e.g. network blips, 429 rate limit).
    Safely eligible for bounded retry according to policy.
    """
    pass


class PermanentTaskError(AgentError):
    """
    Error caused by deterministic, unrecoverable failures (e.g. invalid arguments, malformed data).
    Must NOT be retried.
    """
    pass


class PolicyViolationError(AgentError):
    """Raised when an action is denied by the Policy Engine."""
    pass


class ExternalPlatformBlockedError(AgentError):
    """Raised when an external platform explicitly blocks execution (e.g. IP block, CAPTCHA)."""
    pass


class AuthenticationRequiredError(AgentError):
    """Raised when credential lookup fails or re-authentication/2FA is mandatory."""
    pass


class ResourceLimitExceededError(AgentError):
    """Raised when task execution exceeds configured resource or quota thresholds."""
    pass


class HumanInterventionRequiredError(AgentError):
    """
    Raised when autonomous execution cannot safely proceed without human input
    (e.g., CAPTCHA, 2FA, ambiguous campaign instructions, irreversible action).
    Triggers an Escalation.
    """
    pass
