"""Data models for BGM Vault package."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from autoclip.db.models import BGMAssetRecord


@dataclass
class BGMUploadMetadata:
    """Metadata supplied by operator when uploading a background music track."""

    name: str = ""
    genre: str = ""
    mood: str = ""
    tags: list[str] = field(default_factory=list)


@dataclass
class BGMFilterCriteria:
    """Criteria for filtering vault assets."""

    enabled_only: bool = False
    genre: str | None = None
