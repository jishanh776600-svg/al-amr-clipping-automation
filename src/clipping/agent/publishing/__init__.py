"""Autonomous Campaign Publishing and Submission Layer."""

from clipping.agent.publishing.adapters.base import (
    PlatformPublishingAdapter,
    PlatformPublishResult,
    PlatformStatusResult,
)
from clipping.agent.publishing.adapters.instagram import InstagramPublishingAdapter
from clipping.agent.publishing.adapters.youtube import YouTubePublishingAdapter
from clipping.agent.publishing.capability import PublishingCapability
from clipping.agent.publishing.media_safety import MediaSafetyResult, MediaSafetyVerifier
from clipping.agent.publishing.models import (
    CampaignSubmissionRecord,
    PublishingContentMetadata,
    PublishingMode,
    SubmissionStateTransition,
    SubmissionStatus,
)
from clipping.agent.publishing.reconciliation import (
    PublishingReconciliationService,
    ReconciliationResult,
)
from clipping.agent.publishing.repository import CampaignSubmissionRepository
from clipping.agent.publishing.rule_engine import (
    SubmissionRuleEngine,
    SubmissionValidationResult,
)
from clipping.agent.publishing.safety_gate import PublishingSafetyGate, SafetyGateResult

__all__ = [
    "CampaignSubmissionRecord",
    "PublishingContentMetadata",
    "PublishingMode",
    "SubmissionStateTransition",
    "SubmissionStatus",
    "CampaignSubmissionRepository",
    "SubmissionRuleEngine",
    "SubmissionValidationResult",
    "PublishingSafetyGate",
    "SafetyGateResult",
    "MediaSafetyVerifier",
    "MediaSafetyResult",
    "PlatformPublishingAdapter",
    "PlatformPublishResult",
    "PlatformStatusResult",
    "YouTubePublishingAdapter",
    "InstagramPublishingAdapter",
    "PublishingReconciliationService",
    "ReconciliationResult",
    "PublishingCapability",
]
