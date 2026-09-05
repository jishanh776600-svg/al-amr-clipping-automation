"""Platform-Agnostic Publishing Adapters."""

from clipping.agent.publishing.adapters.base import (
    PlatformPublishingAdapter,
    PlatformPublishResult,
    PlatformStatusResult,
)
from clipping.agent.publishing.adapters.instagram import InstagramPublishingAdapter
from clipping.agent.publishing.adapters.youtube import YouTubePublishingAdapter

__all__ = [
    "PlatformPublishingAdapter",
    "PlatformPublishResult",
    "PlatformStatusResult",
    "YouTubePublishingAdapter",
    "InstagramPublishingAdapter",
]
