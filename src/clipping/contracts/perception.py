"""Perception Data Contracts (ASR, Diarization, Video Understanding)."""

from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, ConfigDict
from clipping.core.constants import SCHEMA_VERSION


class WordTimestamp(BaseModel):
    """Word-level speech timestamp with confidence and speaker attribution."""
    model_config = ConfigDict(frozen=True)

    word: str = Field(..., min_length=1)
    start: float = Field(..., ge=0.0, description="Start timestamp in seconds")
    end: float = Field(..., ge=0.0, description="End timestamp in seconds")
    probability: float = Field(..., ge=0.0, le=1.0, description="Confidence probability")
    speaker_id: Optional[str] = Field(default=None, max_length=64)

    def validate_bounds(self) -> None:
        if self.end <= self.start:
            raise ValueError(f"Word end time ({self.end}) must be > start time ({self.start})")


class SpeakerSegment(BaseModel):
    """Speaker turn segment from acoustic diarization."""
    model_config = ConfigDict(frozen=True)

    speaker_id: str = Field(..., min_length=1, max_length=64)
    start: float = Field(..., ge=0.0, description="Start time in seconds")
    end: float = Field(..., ge=0.0, description="End time in seconds")

    def validate_bounds(self) -> None:
        if self.end <= self.start:
            raise ValueError(f"SpeakerSegment end time ({self.end}) must be > start time ({self.start})")


class RawTranscript(BaseModel):
    """Raw ASR transcription output before speaker attribution."""
    model_config = ConfigDict(frozen=True)

    schema_version: str = Field(default=SCHEMA_VERSION)
    source_video_id: str = Field(..., min_length=1)
    language: Optional[str] = None
    language_probability: Optional[float] = None
    text: str
    words: List[WordTimestamp] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DiarizationResult(BaseModel):
    """Speaker diarization result containing turn intervals."""
    model_config = ConfigDict(frozen=True)

    schema_version: str = Field(default=SCHEMA_VERSION)
    source_video_id: str = Field(..., min_length=1)
    backend: str
    model_name: str
    num_speakers: int = Field(ge=0)
    segments: List[SpeakerSegment] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SpeakerAttributedTranscript(BaseModel):
    """Complete transcript with word-level speaker attribution."""
    model_config = ConfigDict(frozen=True)

    schema_version: str = Field(default=SCHEMA_VERSION)
    source_video_id: str = Field(..., min_length=1)
    text: str
    words: List[WordTimestamp] = Field(default_factory=list)
    speaker_segments: List[SpeakerSegment] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PerceptionMetadata(BaseModel):
    """Technical metadata describing the audio perception execution run."""
    model_config = ConfigDict(frozen=True)

    schema_version: str = Field(default=SCHEMA_VERSION)
    source_video_id: str = Field(..., min_length=1)
    asr_backend: str
    asr_model: str
    asr_device: str
    asr_compute_type: str
    asr_vad_enabled: bool
    diarization_backend: str
    diarization_model: str
    detected_language: Optional[str] = None
    num_speakers: int = Field(ge=0)
    total_words: int = Field(ge=0)
    audio_duration_seconds: float = Field(ge=0.0)
    source_checksum: str
    warnings: List[str] = Field(default_factory=list)
    completed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SceneCut(BaseModel):
    """Frame-accurate physical shot boundary from PySceneDetect."""
    model_config = ConfigDict(frozen=True)

    scene_id: int = Field(..., ge=0)
    start_frame: int = Field(..., ge=0)
    end_frame: int = Field(..., ge=0)
    start_time: float = Field(..., ge=0.0)
    end_time: float = Field(..., ge=0.0)


class FaceBoundingBox(BaseModel):
    """Face detection bounding box in normalized coordinates [0.0, 1.0]."""
    model_config = ConfigDict(frozen=True)

    frame_idx: int = Field(..., ge=0)
    timestamp: float = Field(..., ge=0.0)
    x: float = Field(..., ge=0.0, le=1.0, description="Left coordinate normalized")
    y: float = Field(..., ge=0.0, le=1.0, description="Top coordinate normalized")
    w: float = Field(..., gt=0.0, le=1.0, description="Width normalized")
    h: float = Field(..., gt=0.0, le=1.0, description="Height normalized")
    detection_confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class FaceTrack(BaseModel):
    """Continuous face trajectory identified across frames."""
    model_config = ConfigDict(frozen=True)

    track_id: int = Field(..., ge=0)
    speaker_id: Optional[str] = Field(default=None, max_length=64)
    boxes: List[FaceBoundingBox] = Field(default_factory=list)


class ActiveSpeakerSegment(BaseModel):
    """Resolved active speaker ↔ visual face association interval."""
    model_config = ConfigDict(frozen=True)

    speaker_id: str = Field(..., min_length=1)
    start_time: float = Field(..., ge=0.0)
    end_time: float = Field(..., ge=0.0)
    track_id: Optional[int] = None
    speaking_confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class SourceVideoMetadata(BaseModel):
    """Metadata describing an ingested long-form video."""
    model_config = ConfigDict(frozen=True)

    schema_version: str = Field(default=SCHEMA_VERSION)
    video_id: str = Field(..., min_length=1, max_length=128)
    title: str = Field(..., min_length=1, max_length=512)
    duration_seconds: float = Field(..., gt=0.0)
    width: int = Field(..., gt=0)
    height: int = Field(..., gt=0)
    fps: float = Field(..., gt=0.0)
    source_url: Optional[str] = None
    master_video_storage_key: str = Field(..., description="Logical storage key for raw video")
    audio_storage_key: str = Field(..., description="Logical storage key for extracted audio")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
