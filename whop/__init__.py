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

from .catalog import (
    ACCOUNT_1_FINANCE_BUSINESS,
    ACCOUNT_2_ENTERTAINMENT_PODCASTS,
    ACCOUNT_3_ALL_IN_ONE_VIRAL,
    SUPPORTED_PLATFORMS,
    DiscoveredCampaign,
    build_discovered_campaign,
    evaluate_eligibility,
    normalize_platforms,
    parse_cpm,
    route_niche,
)
from .scraper import (
    DiscoveryRunReport,
    WhopScraper,
    generate_markdown_summary,
    save_discovery_artifacts,
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
    "ACCOUNT_1_FINANCE_BUSINESS",
    "ACCOUNT_2_ENTERTAINMENT_PODCASTS",
    "ACCOUNT_3_ALL_IN_ONE_VIRAL",
    "SUPPORTED_PLATFORMS",
    "DiscoveredCampaign",
    "build_discovered_campaign",
    "evaluate_eligibility",
    "normalize_platforms",
    "parse_cpm",
    "route_niche",
    "DiscoveryRunReport",
    "WhopScraper",
    "generate_markdown_summary",
    "save_discovery_artifacts",
]

