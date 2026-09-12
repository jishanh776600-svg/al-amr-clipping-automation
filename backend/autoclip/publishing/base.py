"""Base classes and interfaces for AL AMR publishing adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


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
