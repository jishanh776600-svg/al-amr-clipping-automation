"""Canonical Source Contracts and Resolution Models."""

from datetime import datetime, timezone
from enum import Enum, IntEnum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ConfigDict


class SourceCandidatePriority(IntEnum):
    """Deterministic source priority hierarchy."""
    OPERATOR_UPLOAD = 1       # Explicit operator-uploaded source
    OPERATOR_URL = 2          # Explicit operator-provided source URL
    CAMPAIGN_BRIEF = 3        # Valid source URL specified by campaign brief
    WHOP_DISCOVERY = 4        # Valid source discovered by Whop campaign discovery
    CAMPAIGN_REPOSITORY = 5   # Existing legitimate campaign repository source


class SourceAccessStatus(str, Enum):
    """Accessibility and authorization state of a candidate source."""
    ACCESSIBLE = "accessible"
    RESTRICTED = "restricted"
    NEEDS_AUTH = "needs_auth"
    INACCESSIBLE = "inaccessible"
    PENDING = "pending"


class SourceCandidate(BaseModel):
    """Candidate source identified during campaign ingestion or operator input."""
    model_config = ConfigDict(frozen=True)

    candidate_id: str = Field(..., description="Unique ID for candidate")
    priority_type: SourceCandidatePriority = Field(..., description="Priority hierarchy level")
    priority_rank: int = Field(..., description="Lower number = higher precedence (1-5)")
    uri: str = Field(..., description="Original raw URI or filesystem path")
    is_valid: bool = Field(default=True, description="Whether this source is currently valid")
    rejection_reason: Optional[str] = Field(default=None, description="Why candidate was disqualified if invalid")
    provenance: Dict[str, Any] = Field(default_factory=dict, description="Diagnostic and source lineage")
    selection_rationale: Optional[str] = Field(default=None, description="Why this candidate was chosen or ranked")


class SourceResolutionResult(BaseModel):
    """
    Canonical result emitted by the Source Resolution Engine.
    Contains complete media diagnostics, resolved paths, and full audit provenance.
    Never silently substitutes another source.
    """
    model_config = ConfigDict(frozen=True)

    source_type: str = Field(..., description="Detected type: youtube, direct_url, gdrive, local_file, custom")
    original_uri: str = Field(..., description="Original URI as entered or extracted")
    resolved_uri: str = Field(..., description="Sanitized, canonical URI or storage reference")
    local_storage_path: Optional[str] = Field(default=None, description="Local cached or working file path")
    title: Optional[str] = Field(default=None, description="Video title if retrievable")
    duration: Optional[float] = Field(default=None, description="Duration in seconds")
    width: Optional[int] = Field(default=None, description="Frame width in pixels")
    height: Optional[int] = Field(default=None, description="Frame height in pixels")
    fps: Optional[float] = Field(default=None, description="Frames per second")
    file_size: Optional[int] = Field(default=None, description="Size in bytes")
    mime_type: Optional[str] = Field(default=None, description="MIME content type (e.g. video/mp4)")
    checksum: Optional[str] = Field(default=None, description="SHA-256 hex digest of the media file")
    extraction_method: str = Field(default="direct", description="Method used: operator_upload, direct_download, yt_dlp, local_probe")
    source_access_status: SourceAccessStatus = Field(default=SourceAccessStatus.ACCESSIBLE)
    failure_reason: Optional[str] = Field(default=None, description="Detailed explanation if source resolution failed")
    provenance: Dict[str, Any] = Field(default_factory=dict, description="Complete provenance and verification audit")
    ranked_candidates: List[SourceCandidate] = Field(default_factory=list, description="All candidates considered in priority order")
    selection_rationale: Optional[str] = Field(default=None, description="Human/machine explanation of why this source won")
    verified_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_valid(self) -> bool:
        return self.source_access_status == SourceAccessStatus.ACCESSIBLE and self.failure_reason is None
