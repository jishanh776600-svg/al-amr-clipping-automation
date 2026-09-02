"""End-to-End Video Understanding and Visual Perception Engine."""

import json
import os
import tempfile
from typing import List, Optional, Tuple
from pydantic import TypeAdapter
from clipping.contracts.perception import (
    ActiveSpeakerSegment,
    FaceTrack,
    SceneCut,
    SpeakerAttributedTranscript,
)
from clipping.vision.base import (
    SceneDetector,
    PersonTracker,
    ActiveSpeakerResolver,
    VirtualCameraDirector,
)
from clipping.vision.scenes import PySceneDetectEngine
from clipping.vision.tracking import ByteTrackCpuTracker
from clipping.vision.active_speaker import DeterministicActiveSpeakerResolver
from clipping.vision.director import KalmanVirtualCameraDirector
from clipping.storage.base import StorageDriver
from clipping.storage.keys import StorageKeyBuilder
from clipping.logging.logger import get_logger

logger = get_logger("clipping.vision.engine")

_scene_list_adapter = TypeAdapter(List[SceneCut])
_face_track_list_adapter = TypeAdapter(List[FaceTrack])
_active_speaker_list_adapter = TypeAdapter(List[ActiveSpeakerSegment])


class VideoUnderstandingEngine:
    """
    Orchestrates Scene Detection, Face Tracking, and Active Speaker Resolution.
    Persists canonical artifacts into StorageDriver with complete idempotency and resumability.
    """

    def __init__(
        self,
        scene_detector: Optional[SceneDetector] = None,
        person_tracker: Optional[PersonTracker] = None,
        active_speaker_resolver: Optional[ActiveSpeakerResolver] = None,
        camera_director: Optional[VirtualCameraDirector] = None,
    ):
        self.scene_detector = scene_detector or PySceneDetectEngine()
        self.person_tracker = person_tracker or ByteTrackCpuTracker()
        self.active_speaker_resolver = active_speaker_resolver or DeterministicActiveSpeakerResolver()
        self.camera_director = camera_director or KalmanVirtualCameraDirector()

    async def process(
        self,
        source_video_id: str,
        storage_driver: StorageDriver,
        speaker_transcript: Optional[SpeakerAttributedTranscript] = None,
        force_recompute: bool = False,
    ) -> Tuple[List[SceneCut], List[FaceTrack], List[ActiveSpeakerSegment]]:
        scenes_key = f"sources/{source_video_id}/scenes.json"
        tracks_key = f"sources/{source_video_id}/face_tracks.json"
        active_key = f"sources/{source_video_id}/active_speaker.json"

        # 1. Full Idempotency Check
        if not force_recompute and (
            await storage_driver.exists(scenes_key)
            and await storage_driver.exists(tracks_key)
            and await storage_driver.exists(active_key)
        ):
            logger.info("Vision artifacts already exist in vault, skipping inference", source_video_id=source_video_id)
            scenes_bytes = await storage_driver.download_bytes(scenes_key)
            tracks_bytes = await storage_driver.download_bytes(tracks_key)
            active_bytes = await storage_driver.download_bytes(active_key)

            return (
                _scene_list_adapter.validate_json(scenes_bytes.decode("utf-8")),
                _face_track_list_adapter.validate_json(tracks_bytes.decode("utf-8")),
                _active_speaker_list_adapter.validate_json(active_bytes.decode("utf-8")),
            )

        # 2. Ephemeral Master Video Retrieval
        master_key = StorageKeyBuilder.source_master_video(source_video_id)
        if not await storage_driver.exists(master_key):
            raise FileNotFoundError(f"Master video not found in storage for {source_video_id}")

        # If speaker transcript wasn't passed directly, try loading from storage
        if speaker_transcript is None:
            transcript_key = f"sources/{source_video_id}/speaker_transcript.json"
            if await storage_driver.exists(transcript_key):
                transcript_bytes = await storage_driver.download_bytes(transcript_key)
                speaker_transcript = SpeakerAttributedTranscript.model_validate_json(
                    transcript_bytes.decode("utf-8")
                )
            else:
                speaker_transcript = SpeakerAttributedTranscript(
                    source_video_id=source_video_id,
                    text="",
                    words=[],
                    speaker_segments=[],
                )

        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_video_path = os.path.join(tmp_dir, "master_video.mp4")
            logger.info("Downloading master video for vision analysis", master_key=master_key)
            await storage_driver.download(master_key, temp_video_path)

            # 3. Stage A: Scene Cut Detection (Resumable)
            if not force_recompute and await storage_driver.exists(scenes_key):
                scenes_bytes = await storage_driver.download_bytes(scenes_key)
                scene_cuts = _scene_list_adapter.validate_json(scenes_bytes.decode("utf-8"))
            else:
                scene_cuts = await self.scene_detector.detect_scenes(
                    video_path=temp_video_path,
                    source_video_id=source_video_id,
                )
                await storage_driver.upload_bytes(
                    data=_scene_list_adapter.dump_json(scene_cuts, indent=2),
                    storage_key=scenes_key,
                    content_type="application/json",
                )

            # 4. Stage B: Face & Person Tracking (Resumable)
            if not force_recompute and await storage_driver.exists(tracks_key):
                tracks_bytes = await storage_driver.download_bytes(tracks_key)
                face_tracks = _face_track_list_adapter.validate_json(tracks_bytes.decode("utf-8"))
            else:
                face_tracks = await self.person_tracker.track_video(
                    video_path=temp_video_path,
                    source_video_id=source_video_id,
                )
                await storage_driver.upload_bytes(
                    data=_face_track_list_adapter.dump_json(face_tracks, indent=2),
                    storage_key=tracks_key,
                    content_type="application/json",
                )

            # 5. Stage C: Active Speaker Resolution (Resumable)
            if not force_recompute and await storage_driver.exists(active_key):
                active_bytes = await storage_driver.download_bytes(active_key)
                active_speakers = _active_speaker_list_adapter.validate_json(active_bytes.decode("utf-8"))
            else:
                active_speakers = await self.active_speaker_resolver.resolve_active_speakers(
                    source_video_id=source_video_id,
                    face_tracks=face_tracks,
                    speaker_transcript=speaker_transcript,
                    scene_cuts=scene_cuts,
                )
                await storage_driver.upload_bytes(
                    data=_active_speaker_list_adapter.dump_json(active_speakers, indent=2),
                    storage_key=active_key,
                    content_type="application/json",
                )

            return scene_cuts, face_tracks, active_speakers
