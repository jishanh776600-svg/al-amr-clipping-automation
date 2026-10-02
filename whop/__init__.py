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

from .models import (
    CampaignEvent,
    CampaignRecord,
    CampaignRule,
    CampaignState,
    InvalidStateTransitionError,
    ParsingStatus,
    RuleCategory,
    WhopCampaignBrief,
    validate_campaign_brief,
    validate_transition,
)
from .ledger import (
    CampaignLedger,
    DEFAULT_LEDGER_PATH,
)
from .guidelines import (
    compute_guideline_hash,
    fetch_guideline_document,
    normalize_guideline_content,
    parse_campaign_guidelines,
)
from .human_interaction import (
    HumanActor,
    HumanPersona,
    Point,
    QWERTY_NEIGHBORS,
    generate_bezier_curve,
    sample_gaussian_target,
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
    "CampaignEvent",
    "CampaignRecord",
    "CampaignState",
    "InvalidStateTransitionError",
    "validate_transition",
    "CampaignLedger",
    "DEFAULT_LEDGER_PATH",
    "CampaignRule",
    "RuleCategory",
    "ParsingStatus",
    "WhopCampaignBrief",
    "validate_campaign_brief",
    "compute_guideline_hash",
    "fetch_guideline_document",
    "normalize_guideline_content",
    "parse_campaign_guidelines",
    "HumanActor",
    "HumanPersona",
    "Point",
    "QWERTY_NEIGHBORS",
    "generate_bezier_curve",
    "sample_gaussian_target",
]


