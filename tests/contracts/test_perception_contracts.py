"""Unit tests for Perception contracts."""

import pytest
from pydantic import ValidationError
from clipping.contracts.perception import (
    WordTimestamp,
    SpeakerSegment,
    SceneCut,
    FaceBoundingBox,
    FaceTrack,
    ActiveSpeakerSegment,
    SourceVideoMetadata,
)


def test_valid_word_timestamp():
    word = WordTimestamp(word="automation", start=1.25, end=1.85, probability=0.98, speaker_id="SPEAKER_00")
    assert word.word == "automation"
    assert word.probability == 0.98

    # Probability bounds validation
    with pytest.raises(ValidationError):
        WordTimestamp(word="fail", start=0.0, end=1.0, probability=1.5)


def test_valid_scene_cut_and_face_box():
    cut = SceneCut(scene_id=1, start_frame=0, end_frame=90, start_time=0.0, end_time=3.0)
    assert cut.scene_id == 1

    face = FaceBoundingBox(frame_idx=15, timestamp=0.5, x=0.2, y=0.3, w=0.4, h=0.5, detection_confidence=0.95)
    assert face.x == 0.2
    assert face.w == 0.4

    # Normalized coordinate bounds validation
    with pytest.raises(ValidationError):
        FaceBoundingBox(frame_idx=0, timestamp=0.0, x=1.5, y=0.0, w=0.5, h=0.5)


def test_source_video_metadata():
    meta = SourceVideoMetadata(
        video_id="VID_12345",
        title="Long Form Podcast Episode 1",
        duration_seconds=3600.0,
        width=1920,
        height=1080,
        fps=30.0,
        master_video_storage_key="sources/VID_12345/master.mp4",
        audio_storage_key="sources/VID_12345/audio.wav",
    )
    assert meta.duration_seconds == 3600.0
    assert meta.created_at.tzinfo is not None
