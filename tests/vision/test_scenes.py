"""Unit tests for Scene Detection Engine."""

import pytest
from clipping.contracts.perception import SceneCut
from clipping.vision.scenes import PySceneDetectEngine
from clipping.vision.exceptions import SceneDetectionError


@pytest.mark.asyncio
async def test_scene_detection_mock():
    mock_cuts = [
        SceneCut(scene_id=0, start_frame=0, end_frame=300, start_time=0.0, end_time=10.0),
        SceneCut(scene_id=1, start_frame=301, end_frame=900, start_time=10.033, end_time=30.0),
    ]
    engine = PySceneDetectEngine(mock_scenes=mock_cuts)
    scenes = await engine.detect_scenes("dummy_video.mp4", source_video_id="VID_SCENE_01")

    assert len(scenes) == 2
    assert scenes[0].scene_id == 0
    assert scenes[0].start_time == 0.0
    assert scenes[0].end_time == 10.0
    assert scenes[1].scene_id == 1
    assert scenes[1].start_frame == 301


@pytest.mark.asyncio
async def test_scene_detection_missing_file():
    engine = PySceneDetectEngine()
    with pytest.raises(FileNotFoundError):
        await engine.detect_scenes("/non/existent/video.mp4", source_video_id="VID_MISSING")
