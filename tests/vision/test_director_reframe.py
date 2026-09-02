"""Unit tests for Virtual Camera Director and 9:16 Reframe Planning."""

import pytest
from clipping.contracts.perception import (
    ActiveSpeakerSegment,
    FaceBoundingBox,
    FaceTrack,
    SceneCut,
)
from clipping.contracts.director import ReframePlan, SpeakerLayout
from clipping.vision.director import KalmanVirtualCameraDirector
from clipping.vision.exceptions import ReframeError


def test_reframe_geometry_and_bounds_1080p():
    # 1920x1080 standard landscape source
    source_w = 1920
    source_h = 1080

    director = KalmanVirtualCameraDirector()

    face_tracks = [
        # Face on the far right (x=0.85)
        FaceTrack(
            track_id=0,
            boxes=[
                FaceBoundingBox(frame_idx=0, timestamp=0.0, x=0.85, y=0.2, w=0.1, h=0.2),
                FaceBoundingBox(frame_idx=30, timestamp=3.0, x=0.85, y=0.2, w=0.1, h=0.2),
            ],
        )
    ]
    active_speakers = [
        ActiveSpeakerSegment(speaker_id="SPEAKER_00", start_time=0.0, end_time=3.0, track_id=0, speaking_confidence=0.95),
    ]
    scenes = [SceneCut(scene_id=0, start_frame=0, end_frame=90, start_time=0.0, end_time=3.0)]

    plan = director.generate_reframe_plan(
        clip_id="CLIP_001",
        source_width=source_w,
        source_height=source_h,
        clip_start=0.0,
        clip_end=3.0,
        scene_cuts=scenes,
        face_tracks=face_tracks,
        active_speakers=active_speakers,
    )

    assert isinstance(plan, ReframePlan)
    assert plan.target_width == 1080
    assert plan.target_height == 1920
    assert plan.source_width == 1920
    assert plan.source_height == 1080
    assert len(plan.keyframes) > 0

    # Verify every keyframe satisfies boundary constraints
    for kf in plan.keyframes:
        assert kf.crop_x >= 0
        assert kf.crop_x + kf.crop_w <= source_w
        assert kf.crop_y >= 0
        assert kf.crop_y + kf.crop_h <= source_h
        assert kf.crop_w == 608  # round(1080 * 9 / 16)
        assert kf.crop_h == 1080


def test_scene_cut_hard_reset():
    # Two scenes with speakers at opposite sides of the screen
    source_w = 1920
    source_h = 1080

    scenes = [
        SceneCut(scene_id=0, start_frame=0, end_frame=60, start_time=0.0, end_time=2.0),
        SceneCut(scene_id=1, start_frame=61, end_frame=120, start_time=2.033, end_time=4.0),
    ]

    face_tracks = [
        # Track 0 in Scene 0 on left (x=0.1)
        FaceTrack(
            track_id=0,
            boxes=[FaceBoundingBox(frame_idx=0, timestamp=0.0, x=0.1, y=0.2, w=0.1, h=0.2)],
        ),
        # Track 1 in Scene 1 on right (x=0.8)
        FaceTrack(
            track_id=1,
            boxes=[FaceBoundingBox(frame_idx=61, timestamp=2.033, x=0.8, y=0.2, w=0.1, h=0.2)],
        ),
    ]

    active_speakers = [
        ActiveSpeakerSegment(speaker_id="SPEAKER_00", start_time=0.0, end_time=2.0, track_id=0, speaking_confidence=0.95),
        ActiveSpeakerSegment(speaker_id="SPEAKER_01", start_time=2.033, end_time=4.0, track_id=1, speaking_confidence=0.95),
    ]

    director = KalmanVirtualCameraDirector(sample_step_seconds=0.1)
    plan = director.generate_reframe_plan(
        clip_id="CLIP_SCENE_CUT",
        source_width=source_w,
        source_height=source_h,
        clip_start=0.0,
        clip_end=4.0,
        scene_cuts=scenes,
        face_tracks=face_tracks,
        active_speakers=active_speakers,
    )

    # Find keyframe just before and right after scene cut at t=2.0s
    kf_before = next(k for k in plan.keyframes if 1.8 <= k.timestamp <= 1.9)
    kf_after = next(k for k in plan.keyframes if 2.1 <= k.timestamp <= 2.2)

    # In Scene 0 (left speaker), crop_x is near 0
    assert kf_before.crop_x < 300
    # In Scene 1 (right speaker), crop_x jumps immediately to the right without dragging over multiple seconds
    assert kf_after.crop_x > 1000


def test_invalid_clip_boundaries():
    director = KalmanVirtualCameraDirector()
    with pytest.raises(ReframeError):
        director.generate_reframe_plan(
            clip_id="CLIP_INVALID",
            source_width=1920,
            source_height=1080,
            clip_start=10.0,
            clip_end=5.0,  # Invalid: end < start
            scene_cuts=[],
            face_tracks=[],
            active_speakers=[],
        )
