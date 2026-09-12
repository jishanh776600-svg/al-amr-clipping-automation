"""Publishing and distribution adapters."""

from .base import BasePublisher, PublishingMetadata, PublishingResult
from .instagram import InstagramPublisher
from .publisher import publish_clip, publish_to_telegram
from .service import PublishingService
from .telegram import TelegramPublisher
from .youtube import YouTubePublisher

__all__ = [
    "BasePublisher",
    "PublishingMetadata",
    "PublishingResult",
    "TelegramPublisher",
    "YouTubePublisher",
    "InstagramPublisher",
    "PublishingService",
    "publish_clip",
    "publish_to_telegram",
]
