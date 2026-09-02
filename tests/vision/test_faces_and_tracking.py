"""Unit tests for Face Detection and Tracking."""

import numpy as np
import pytest
from clipping.contracts.perception import FaceBoundingBox, FaceTrack
from clipping.vision.faces import CpuFaceDetector
from clipping.vision.tracking import ByteTrackCpuTracker, compute_iou


def test_compute_iou():
    box1 = FaceBoundingBox(frame_idx=0, timestamp=0.0, x=0.2, y=0.2, w=0.2, h=0.2)
    box2 = FaceBoundingBox(frame_idx=0, timestamp=0.0, x=0.2, y=0.2, w=0.2, h=0.2)
    assert compute_iou(box1, box2) == pytest.approx(1.0)

    box3 = FaceBoundingBox(frame_idx=0, timestamp=0.0, x=0.5, y=0.5, w=0.2, h=0.2)
    assert compute_iou(box1, box3) == 0.0


def test_face_detector_empty_frame():
    detector = CpuFaceDetector()
    assert detector.detect_faces(None, 0, 0.0) == []
    assert detector.detect_faces(np.zeros((0, 0, 3), dtype=np.uint8), 0, 0.0) == []


@pytest.mark.asyncio
async def test_tracking_with_mock_tracks():
    mock_tracks = [
        FaceTrack(
            track_id=0,
            speaker_id=None,
            boxes=[
                FaceBoundingBox(frame_idx=0, timestamp=0.0, x=0.2, y=0.2, w=0.15, h=0.2),
                FaceBoundingBox(frame_idx=1, timestamp=0.2, x=0.21, y=0.2, w=0.15, h=0.2),
                FaceBoundingBox(frame_idx=2, timestamp=0.4, x=0.22, y=0.2, w=0.15, h=0.2),
            ],
        ),
        FaceTrack(
            track_id=1,
            speaker_id=None,
            boxes=[
                FaceBoundingBox(frame_idx=0, timestamp=0.0, x=0.7, y=0.2, w=0.15, h=0.2),
                FaceBoundingBox(frame_idx=1, timestamp=0.2, x=0.71, y=0.2, w=0.15, h=0.2),
            ],
        ),
    ]

    tracker = ByteTrackCpuTracker(mock_tracks=mock_tracks)
    tracks = await tracker.track_video("dummy.mp4", source_video_id="VID_TRACK_01")

    assert len(tracks) == 2
    assert tracks[0].track_id == 0
    assert len(tracks[0].boxes) == 3
    assert tracks[1].track_id == 1
    assert len(tracks[1].boxes) == 2
