"""YouTube Publishing and Scheduling package exports."""

from clipping.publishing.models import (
    PublishStatus,
    PrivacyStatus,
    FailureClassification,
    YouTubeVideoMetadata,
    YouTubeVideoReference,
    PublishRequest,
    PublishAuditRecord,
    PublishSummary,
)
from clipping.publishing.oauth import OAuthCredentials, OAuthTokenManager
from clipping.publishing.client import (
    YouTubeClient,
    YouTubeClientError,
    HttpYouTubeClient,
    MockYouTubeClient,
)
from clipping.publishing.repository import PublishingRepository
from clipping.publishing.gates import PublishingGateEnforcer
from clipping.publishing.metadata import YouTubeMetadataBuilder
from clipping.publishing.service import PublishingService
from clipping.publishing.scheduler import PublishingScheduler

__all__ = [
    "PublishStatus",
    "PrivacyStatus",
    "FailureClassification",
    "YouTubeVideoMetadata",
    "YouTubeVideoReference",
    "PublishRequest",
    "PublishAuditRecord",
    "PublishSummary",
    "OAuthCredentials",
    "OAuthTokenManager",
    "YouTubeClient",
    "YouTubeClientError",
    "HttpYouTubeClient",
    "MockYouTubeClient",
    "PublishingRepository",
    "PublishingGateEnforcer",
    "YouTubeMetadataBuilder",
    "PublishingService",
    "PublishingScheduler",
]
