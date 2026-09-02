"""Integration tests for VideoUnderstandingEngine and Storage Persistence."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from clipping.contracts.perception import (
    ActiveSpeakerSegment,
    FaceBoundingBox,
    FaceTrack,
    SceneCut,
    SpeakerAttributedTranscript,
    SpeakerSegment,
)
from clipping.vision.engine import VideoUnderstandingEngine
from clipping.storage.local import LocalStorageDriver


@pytest.mark.asyncio
async def test_video_understanding_lifecycle(temp_vault_dir):
    storage = LocalStorageDriver(root_dir=temp_vault_dir)

    # 1. Seed master video in storage vault
    master_key = "sources/VID_VISION_INT/master.mp4"
    await storage.upload_bytes(b"MOCK_MP4_BINARY_DATA", master_key, content_type="video/mp4")

    # 2. Mock Scene Detector
    mock_scenes = [SceneCut(scene_id=0, start_frame=0, end_frame=150, start_time=0.0, end_time=5.0)]
    mock_scene_detector = MagicMock()
    mock_scene_detector.detect_scenes = AsyncMock(return_value=mock_scenes)

    # 3. Mock Person Tracker
    mock_tracks = [
        FaceTrack(
            track_id=0,
            boxes=[FaceBoundingBox(frame_idx=0, timestamp=0.0, x=0.5, y=0.2, w=0.2, h=0.25)],
        )
    ]
    mock_tracker = MagicMock()
    mock_tracker.track_video = AsyncMock(return_value=mock_tracks)

    # 4. Mock Active Speaker Resolver
    mock_active = [
        ActiveSpeakerSegment(speaker_id="SPEAKER_00", start_time=0.0, end_time=5.0, track_id=0, speaking_confidence=0.95)
    ]
    mock_resolver = MagicMock()
    mock_resolver.resolve_active_speakers = AsyncMock(return_value=mock_active)

    engine = VideoUnderstandingEngine(
        scene_detector=mock_scene_detector,
        person_tracker=mock_tracker,
        active_speaker_resolver=mock_resolver,
    )

    # 5. Execute processing
    scenes, tracks, active = await engine.process(
        source_video_id="VID_VISION_INT",
        storage_driver=storage,
    )

    assert len(scenes) == 1
    assert len(tracks) == 1
    assert len(active) == 1

    # 6. Verify artifacts persisted to StorageDriver
    assert await storage.exists("sources/VID_VISION_INT/scenes.json") is True
    assert await storage.exists("sources/VID_VISION_INT/face_tracks.json") is True
    assert await storage.exists("sources/VID_VISION_INT/active_speaker.json") is True

    # 7. Idempotency Check: Re-run should skip inference
    mock_scene_detector.detect_scenes.side_effect = RuntimeError("Should not be called on cached run")
    cached_scenes, cached_tracks, cached_active = await engine.process(
        source_video_id="VID_VISION_INT",
        storage_driver=storage,
        force_recompute=False,
    )
    assert len(cached_scenes) == 1
    assert len(cached_tracks) == 1
    assert len(cached_active) == 1
