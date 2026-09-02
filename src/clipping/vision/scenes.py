"""Scene Cut Detection using PySceneDetect."""

import os
from typing import Any, List, Optional
from clipping.contracts.perception import SceneCut
from clipping.vision.base import SceneDetector
from clipping.vision.exceptions import SceneDetectionError
from clipping.logging.logger import get_logger

logger = get_logger("clipping.vision.scenes")


class PySceneDetectEngine(SceneDetector):
    """
    CPU-compatible scene detection engine using PySceneDetect ContentDetector.
    Detects physical shot cuts and transitions with exact frame mapping.
    """

    def __init__(self, mock_scenes: Optional[List[SceneCut]] = None):
        self._mock_scenes = mock_scenes

    async def detect_scenes(
        self,
        video_path: str,
        source_video_id: str,
        threshold: float = 27.0,
    ) -> List[SceneCut]:
        if self._mock_scenes is not None:
            return self._mock_scenes

        if not os.path.isfile(video_path):
            raise FileNotFoundError(f"Video file not found for scene detection: {video_path}")

        try:
            from scenedetect import SceneManager, open_video, ContentDetector

            logger.info("Opening video for PySceneDetect", video_path=video_path, threshold=threshold)
            video = open_video(video_path)
            scene_manager = SceneManager()
            scene_manager.add_detector(ContentDetector(threshold=threshold))

            scene_manager.detect_scenes(video)
            scene_list = scene_manager.get_scene_list()

            results: List[SceneCut] = []

            if not scene_list:
                # If no cuts detected, treat whole video as single scene
                fps = video.frame_rate if hasattr(video, "frame_rate") else 30.0
                duration_sec = video.duration.get_seconds() if hasattr(video, "duration") else 60.0
                total_frames = int(duration_sec * fps)
                results.append(
                    SceneCut(
                        scene_id=0,
                        start_frame=0,
                        end_frame=total_frames,
                        start_time=0.0,
                        end_time=duration_sec,
                    )
                )
            else:
                for idx, (start_timecode, end_timecode) in enumerate(scene_list):
                    start_sec = max(0.0, float(start_timecode.get_seconds()))
                    end_sec = max(start_sec + 0.01, float(end_timecode.get_seconds()))
                    start_frame = int(start_timecode.get_frames())
                    end_frame = int(end_timecode.get_frames())

                    results.append(
                        SceneCut(
                            scene_id=idx,
                            start_frame=start_frame,
                            end_frame=end_frame,
                            start_time=start_sec,
                            end_time=end_sec,
                        )
                    )

            logger.info(
                "Scene detection completed",
                source_video_id=source_video_id,
                total_scenes=len(results),
            )
            return results

        except Exception as e:
            raise SceneDetectionError(f"PySceneDetect failed on {video_path}: {e}") from e
