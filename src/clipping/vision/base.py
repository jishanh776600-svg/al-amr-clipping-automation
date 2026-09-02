"""Abstract interfaces for Video Understanding and Virtual Camera Director."""

from abc import ABC, abstractmethod
from typing import Any, List, Optional
from clipping.contracts.perception import (
    SceneCut,
    FaceBoundingBox,
    FaceTrack,
    ActiveSpeakerSegment,
    SpeakerAttributedTranscript,
)
from clipping.contracts.director import ReframePlan


class SceneDetector(ABC):
    """Abstract interface for video shot and scene cut detection."""

    @abstractmethod
    async def detect_scenes(
        self,
        video_path: str,
        source_video_id: str,
        threshold: float = 27.0,
    ) -> List[SceneCut]:
        """Detects physical shot boundaries and scene cuts in video."""
        pass


class FaceDetector(ABC):
    """Abstract interface for CPU-first face detection."""

    @abstractmethod
    def detect_faces(
        self,
        frame: Any,
        frame_idx: int,
        timestamp: float,
    ) -> List[FaceBoundingBox]:
        """Detects faces in a single video frame returning normalized coordinates."""
        pass


class PersonTracker(ABC):
    """Abstract interface for multi-person and face tracking across video frames."""

    @abstractmethod
    async def track_video(
        self,
        video_path: str,
        source_video_id: str,
        sample_fps: float = 5.0,
    ) -> List[FaceTrack]:
        """Tracks faces/people across video frames maintaining stable track IDs."""
        pass


class ActiveSpeakerResolver(ABC):
    """Abstract interface for multi-modal speaker ↔ face association."""

    @abstractmethod
    async def resolve_active_speakers(
        self,
        source_video_id: str,
        face_tracks: List[FaceTrack],
        speaker_transcript: SpeakerAttributedTranscript,
        scene_cuts: List[SceneCut],
    ) -> List[ActiveSpeakerSegment]:
        """Associates acoustic speaker turns with visual face tracks."""
        pass


class VirtualCameraDirector(ABC):
    """Abstract interface for 9:16 virtual camera planning and trajectory smoothing."""

    @abstractmethod
    def generate_reframe_plan(
        self,
        clip_id: str,
        source_width: int,
        source_height: int,
        clip_start: float,
        clip_end: float,
        scene_cuts: List[SceneCut],
        face_tracks: List[FaceTrack],
        active_speakers: List[ActiveSpeakerSegment],
        speaker_transcript: Optional[SpeakerAttributedTranscript] = None,
    ) -> ReframePlan:
        """Computes smooth 9:16 crop coordinates and layout transitions."""
        pass
