"""Layered Quality Assurance (L1-L5), Media Integrity & Readiness Contracts."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ConfigDict
from clipping.core.constants import (
    SCHEMA_VERSION,
    TARGET_WIDTH,
    TARGET_HEIGHT,
    TARGET_LOUDNORM_I,
    TARGET_LOUDNORM_TP,
)


class QASeverity(str, Enum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


class QACheckStatus(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    SKIPPED = "skipped"


class QAPassStatus(str, Enum):
    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"


class QACheck(BaseModel):
    """Granular check execution record."""
    model_config = ConfigDict(frozen=True)

    check_id: str = Field(..., min_length=1, max_length=128)
    name: str = Field(..., min_length=1, max_length=256)
    status: QACheckStatus
    severity: QASeverity
    message: str = Field(default="")
    measured_value: Optional[Any] = None
    expected_value: Optional[Any] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class MediaValidationResult(BaseModel):
    """Raw media and stream integrity probed from rendered MP4 container."""
    model_config = ConfigDict(frozen=True)

    is_valid: bool = True
    width: int = Field(default=0, ge=0)
    height: int = Field(default=0, ge=0)
    duration_seconds: float = Field(default=0.0, ge=0.0)
    fps: float = Field(default=0.0, ge=0.0)
    video_codec: str = Field(default="unknown")
    audio_codec: Optional[str] = None
    pixel_format: str = Field(default="unknown")
    audio_sample_rate: Optional[int] = None
    file_size_bytes: int = Field(default=0, ge=0)


class StructuralQAResult(BaseModel):
    """Level 1: Container, codec, resolution, and stream integrity."""
    model_config = ConfigDict(frozen=True)

    container_valid: bool = True
    width: int = Field(..., ge=0)
    height: int = Field(..., ge=0)
    fps: float = Field(..., gt=0.0)
    has_audio_stream: bool = True
    duration_seconds: float = Field(..., gt=0.0)

    @property
    def is_valid(self) -> bool:
        return (
            self.container_valid
            and self.width == TARGET_WIDTH
            and self.height == TARGET_HEIGHT
            and self.has_audio_stream
        )


class VisualQAResult(BaseModel):
    """Level 2: Visual artifact and UI safe-zone checks."""
    model_config = ConfigDict(frozen=True)

    black_segments_count: int = Field(default=0, ge=0)
    freeze_segments_count: int = Field(default=0, ge=0)
    safezone_violations_count: int = Field(default=0, ge=0)

    @property
    def is_valid(self) -> bool:
        return (
            self.black_segments_count == 0
            and self.freeze_segments_count == 0
            and self.safezone_violations_count == 0
        )


class AudioQAResult(BaseModel):
    """Level 3: EBU R128 audio loudness and dynamics standards."""
    model_config = ConfigDict(frozen=True)

    integrated_loudness_lufs: float = Field(..., description="Target -14 LUFS")
    true_peak_dbfs: float = Field(..., description="Target <= -1.0 dBFS")
    loudness_range_lra: float = Field(default=7.0, ge=0.0)

    @property
    def is_valid(self) -> bool:
        return (
            abs(self.integrated_loudness_lufs - TARGET_LOUDNORM_I) <= 1.5
            and self.true_peak_dbfs <= TARGET_LOUDNORM_TP
        )


class DuplicateFingerprint(BaseModel):
    """Level 5: Perceptual content hash for duplicate detection."""
    model_config = ConfigDict(frozen=True)

    clip_id: str = Field(..., min_length=1, max_length=128)
    perceptual_hash: str = Field(..., min_length=8, description="64-bit hexadecimal hash")
    audio_fingerprint: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class QAReport(BaseModel):
    """Comprehensive production QA report with check breakdown and gating verdict."""
    model_config = ConfigDict(frozen=True)

    schema_version: str = Field(default=SCHEMA_VERSION)
    clip_id: str = Field(..., min_length=1, max_length=128)
    source_video_id: str = Field(..., min_length=1, max_length=128)
    overall_status: QACheckStatus
    can_publish: bool
    checks: List[QACheck] = Field(default_factory=list)
    media_validation: Optional[MediaValidationResult] = None
    summary: str = Field(default="")
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class QAResult(BaseModel):
    """Aggregated L1-L5 QA evaluation report."""
    model_config = ConfigDict(frozen=True)

    schema_version: str = Field(default=SCHEMA_VERSION)
    clip_id: str = Field(..., min_length=1, max_length=128)
    overall_status: QAPassStatus
    structural: StructuralQAResult
    visual: VisualQAResult
    audio: AudioQAResult
    compliance_passed: bool
    duplicate_hash: str
    details: List[str] = Field(default_factory=list)
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
