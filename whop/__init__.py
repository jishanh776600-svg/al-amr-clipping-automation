"""Whop Cloud-Browser Foundation Module (Step 1).

Read-only, secure foundation for future Whop automation.
"""

from .config import (
    WhopConfig,
    WhopConfigError,
    WhopDryRunViolationError,
    sanitize_text,
)
from .session import (
    ValidatedSessionState,
    WhopSessionError,
    parse_and_validate_session_state,
    apply_session_to_context,
)
from .browser import (
    WhopBrowser,
    FORBIDDEN_MUTATION_ACTIONS,
)
from .diagnostics import (
    WhopDiagnosticReport,
    capture_diagnostics,
    detect_whop_authentication,
)

__all__ = [
    "WhopConfig",
    "WhopConfigError",
    "WhopDryRunViolationError",
    "sanitize_text",
    "ValidatedSessionState",
    "WhopSessionError",
    "parse_and_validate_session_state",
    "apply_session_to_context",
    "WhopBrowser",
    "FORBIDDEN_MUTATION_ACTIONS",
    "WhopDiagnosticReport",
    "capture_diagnostics",
    "detect_whop_authentication",
]
