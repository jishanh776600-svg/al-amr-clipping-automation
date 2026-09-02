"""CPU-First Face Detection Engine using OpenCV."""

from typing import Any, List, Optional
import cv2
import numpy as np
from clipping.contracts.perception import FaceBoundingBox
from clipping.vision.base import FaceDetector
from clipping.logging.logger import get_logger

logger = get_logger("clipping.vision.faces")


class CpuFaceDetector(FaceDetector):
    """
    CPU-compatible face detector utilizing OpenCV DNN / Cascades.
    Produces normalized bounding boxes [x, y, w, h] in [0.0, 1.0].
    """

    def __init__(self, min_face_size: int = 30):
        self.min_face_size = min_face_size
        self._cascade = None
        if hasattr(cv2, "CascadeClassifier") and hasattr(cv2, "data") and hasattr(cv2.data, "haarcascades"):
            try:
                cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
                self._cascade = cv2.CascadeClassifier(cascade_path)
            except Exception:
                pass

    def detect_faces(
        self,
        frame: Any,
        frame_idx: int,
        timestamp: float,
    ) -> List[FaceBoundingBox]:
        if frame is None or not isinstance(frame, np.ndarray) or frame.size == 0:
            return []

        height, width = frame.shape[:2]
        if height == 0 or width == 0:
            return []

        results: List[FaceBoundingBox] = []

        if self._cascade is not None:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if len(frame.shape) == 3 else frame
            faces = self._cascade.detectMultiScale(
                gray,
                scaleFactor=1.1,
                minNeighbors=4,
                minSize=(self.min_face_size, self.min_face_size),
            )

            for (x_px, y_px, w_px, h_px) in faces:
                norm_x = max(0.0, min(1.0, float(x_px) / float(width)))
                norm_y = max(0.0, min(1.0, float(y_px) / float(height)))
                norm_w = max(0.01, min(1.0 - norm_x, float(w_px) / float(width)))
                norm_h = max(0.01, min(1.0 - norm_y, float(h_px) / float(height)))

                results.append(
                    FaceBoundingBox(
                        frame_idx=frame_idx,
                        timestamp=timestamp,
                        x=norm_x,
                        y=norm_y,
                        w=norm_w,
                        h=norm_h,
                        detection_confidence=0.90,
                    )
                )

        return results
