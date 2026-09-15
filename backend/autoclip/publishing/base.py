"""Base classes and interfaces for AL AMR publishing adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


ErrorCode = Literal[
    "authentication_error",
    "permission_error",
    "rate_limit",
    "invalid_metadata",
    "invalid_media",
    "network_error",
    "platform_error",
    "duplicate",
    "unknown",
]


def is_error_retryable(error_code: str | None) -> bool:
    """Classify whether an error code represents a retryable condition."""
    if not error_code:
        return False
    return error_code in ("rate_limit", "network_error", "platform_error")


@dataclass
class PublishingMetadata:
    title: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    privacy: Literal["public", "unlisted", "private"] = "unlisted"
    destination: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class PublishingResult:
    platform: str
    success: bool
    status: Literal["published", "failed", "skipped", "ready_for_upload"]
    external_id: str | None = None
    url: str | None = None
    error: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    # Step 25 normalized fields
    destination_id: str = ""
    remote_media_id: str | None = None
    remote_post_id: str | None = None
    permalink: str | None = None
    published_at: str | None = None
    retryable: bool = False
    error_code: str | None = None
    error_message: str | None = None
    telemetry: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        # Sync url and permalink if one is missing
        if self.url and not self.permalink:
            self.permalink = self.url
        elif self.permalink and not self.url:
            self.url = self.permalink

        # Sync external_id and remote_post_id/remote_media_id
        if self.external_id and not self.remote_post_id:
            self.remote_post_id = self.external_id
        if self.external_id and not self.remote_media_id:
            self.remote_media_id = self.external_id

        # Sync error and error_message
        if self.error and not self.error_message:
            self.error_message = self.error
        elif self.error_message and not self.error:
            self.error = self.error_message

        # Ensure retryable flag matches error classification if set
        if self.error_code and not self.retryable:
            self.retryable = is_error_retryable(self.error_code)


# Alias PublicationResult to PublishingResult for consistent naming
PublicationResult = PublishingResult


class BasePublisher(ABC):
    """Abstract base class for all platform publishing adapters."""

    platform_name: str

    @abstractmethod
    def is_configured(self) -> bool:
        """Returns True if required platform credentials are present."""
        ...

    @abstractmethod
    async def publish(
        self,
        media_path: Path,
        metadata: PublishingMetadata,
        drive_link: str | None = None,
        dry_run: bool = False,
    ) -> PublishingResult:
        """Publishes the media file to the destination platform."""
        ...

