"""Vision and Virtual Camera Director package exports."""

from clipping.vision.base import (
    SceneDetector,
    FaceDetector,
    PersonTracker,
    ActiveSpeakerResolver,
    VirtualCameraDirector,
)
from clipping.vision.scenes import PySceneDetectEngine
from clipping.vision.faces import CpuFaceDetector
from clipping.vision.tracking import ByteTrackCpuTracker
from clipping.vision.active_speaker import DeterministicActiveSpeakerResolver
from clipping.vision.director import KalmanVirtualCameraDirector
from clipping.vision.engine import VideoUnderstandingEngine
from clipping.vision.exceptions import (
    VisionError,
    SceneDetectionError,
    TrackingError,
    ActiveSpeakerResolutionError,
    ReframeError,
)

__all__ = [
    "SceneDetector",
    "FaceDetector",
    "PersonTracker",
    "ActiveSpeakerResolver",
    "VirtualCameraDirector",
    "PySceneDetectEngine",
    "CpuFaceDetector",
    "ByteTrackCpuTracker",
    "DeterministicActiveSpeakerResolver",
    "KalmanVirtualCameraDirector",
    "VideoUnderstandingEngine",
    "VisionError",
    "SceneDetectionError",
    "TrackingError",
    "ActiveSpeakerResolutionError",
    "ReframeError",
]
