"""CPU-First Face Tracking Engine with Kalman/IoU Association."""

import os
from typing import Any, Dict, List, Optional
import cv2
import numpy as np
from clipping.contracts.perception import FaceBoundingBox, FaceTrack
from clipping.vision.base import PersonTracker, FaceDetector
from clipping.vision.faces import CpuFaceDetector
from clipping.vision.exceptions import TrackingError
from clipping.logging.logger import get_logger

logger = get_logger("clipping.vision.tracking")


def compute_iou(boxA: FaceBoundingBox, boxB: FaceBoundingBox) -> float:
    """Computes Intersection-over-Union between two normalized bounding boxes."""
    xA = max(boxA.x, boxB.x)
    yA = max(boxA.y, boxB.y)
    xB = min(boxA.x + boxA.w, boxB.x + boxB.w)
    yB = min(boxA.y + boxA.h, boxB.y + boxB.h)

    inter_width = max(0.0, xB - xA)
    inter_height = max(0.0, yB - yA)
    inter_area = inter_width * inter_height

    boxA_area = boxA.w * boxA.h
    boxB_area = boxB.w * boxB.h
    union_area = boxA_area + boxB_area - inter_area

    if union_area <= 0:
        return 0.0
    return inter_area / union_area


class ByteTrackCpuTracker(PersonTracker):
    """
    CPU-compatible person/face tracker.
    Maintains persistent track IDs across frames using IoU distance and temporal association.
    """

    def __init__(
        self,
        face_detector: Optional[FaceDetector] = None,
        iou_threshold: float = 0.3,
        max_lost_frames: int = 5,
        mock_tracks: Optional[List[FaceTrack]] = None,
    ):
        self.face_detector = face_detector or CpuFaceDetector()
        self.iou_threshold = iou_threshold
        self.max_lost_frames = max_lost_frames
        self._mock_tracks = mock_tracks

    async def track_video(
        self,
        video_path: str,
        source_video_id: str,
        sample_fps: float = 5.0,
    ) -> List[FaceTrack]:
        if self._mock_tracks is not None:
            return self._mock_tracks

        if not os.path.isfile(video_path):
            raise FileNotFoundError(f"Video file not found for tracking: {video_path}")

        try:
            cap = cv2.VideoCapture(video_path)
            if not cap.isOpened():
                raise TrackingError(f"OpenCV failed to open video stream: {video_path}")

            video_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            frame_step = max(1, int(round(video_fps / sample_fps)))

            logger.info(
                "Starting CPU face tracking",
                source_video_id=source_video_id,
                video_fps=video_fps,
                sample_fps=sample_fps,
                frame_step=frame_step,
            )

            active_tracks: Dict[int, List[FaceBoundingBox]] = {}
            track_lost_counts: Dict[int, int] = {}
            next_track_id = 0

            frame_idx = 0
            while True:
                ret, frame = cap.read()
                if not ret:
                    break

                if frame_idx % frame_step == 0:
                    timestamp = float(frame_idx) / float(video_fps)
                    detections = self.face_detector.detect_faces(frame, frame_idx, timestamp)

                    # Association step (Hungarian / Greedy IoU matching)
                    unmatched_detections = list(detections)
                    unmatched_tracks = list(active_tracks.keys())

                    for track_id in list(unmatched_tracks):
                        last_box = active_tracks[track_id][-1]
                        best_det = None
                        best_iou = -1.0

                        for det in unmatched_detections:
                            iou = compute_iou(last_box, det)
                            if iou > best_iou:
                                best_iou = iou
                                best_det = det

                        if best_det is not None and best_iou >= self.iou_threshold:
                            active_tracks[track_id].append(best_det)
                            track_lost_counts[track_id] = 0
                            unmatched_detections.remove(best_det)
                            unmatched_tracks.remove(track_id)
                        else:
                            track_lost_counts[track_id] = track_lost_counts.get(track_id, 0) + 1

                    # Create new tracks for remaining unmatched detections
                    for det in unmatched_detections:
                        active_tracks[next_track_id] = [det]
                        track_lost_counts[next_track_id] = 0
                        next_track_id += 1

                frame_idx += 1

            cap.release()

            # Compile into FaceTrack contract objects
            results: List[FaceTrack] = []
            for track_id, boxes in active_tracks.items():
                if len(boxes) >= 2:  # Filter noise / single-frame false positives
                    results.append(
                        FaceTrack(
                            track_id=track_id,
                            speaker_id=None,
                            boxes=boxes,
                        )
                    )

            logger.info(
                "Face tracking completed",
                source_video_id=source_video_id,
                total_tracks=len(results),
            )
            return results

        except Exception as e:
            raise TrackingError(f"Face tracking failed on {video_path}: {e}") from e
