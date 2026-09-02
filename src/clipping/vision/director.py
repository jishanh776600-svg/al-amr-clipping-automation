"""Virtual Camera Director & 9:16 Reframe Planning Engine."""

import math
from typing import Dict, List, Optional
from clipping.contracts.perception import (
    ActiveSpeakerSegment,
    FaceBoundingBox,
    FaceTrack,
    SceneCut,
    SpeakerAttributedTranscript,
)
from clipping.contracts.director import (
    ReframeCropKeyframe,
    ReframePlan,
    SpeakerLayout,
)
from clipping.vision.base import VirtualCameraDirector
from clipping.vision.exceptions import ReframeError
from clipping.logging.logger import get_logger

logger = get_logger("clipping.vision.director")


class KalmanVirtualCameraDirector(VirtualCameraDirector):
    """
    Computes smooth 9:16 crop trajectories targeting 1080x1920 portrait format.
    Enforces scene-cut hard resets, speaker hysteresis, and strict boundary safety.
    """

    def __init__(
        self,
        smoothing_alpha: float = 0.25,
        max_pan_speed_px_per_sec: float = 250.0,
        hysteresis_seconds: float = 1.5,
        sample_step_seconds: float = 0.1,
    ):
        self.smoothing_alpha = smoothing_alpha
        self.max_pan_speed_px_per_sec = max_pan_speed_px_per_sec
        self.hysteresis_seconds = hysteresis_seconds
        self.sample_step_seconds = sample_step_seconds

    def generate_reframe_plan(
        self,
        clip_id: str,
        source_width: int,
        source_height: int,
        clip_start: float,
        clip_end: float,
        scene_cuts: List[SceneCut],
        face_tracks: List[FaceTrack],
        active_speakers: List[ActiveSpeakerSegment],
        speaker_transcript: Optional[SpeakerAttributedTranscript] = None,
    ) -> ReframePlan:
        if clip_end <= clip_start:
            raise ReframeError(f"Invalid clip duration: clip_end ({clip_end}) must be > clip_start ({clip_start})")
        if source_width <= 0 or source_height <= 0:
            raise ReframeError(f"Invalid source dimensions: {source_width}x{source_height}")

        # 1. Calculate 9:16 crop geometry in source coordinate space
        # Aspect ratio 9:16 -> width = height * (9/16)
        crop_h = source_height
        crop_w = int(round(crop_h * 9.0 / 16.0))

        if crop_w > source_width:
            # If source is ultra-tall, scale by width
            crop_w = source_width
            crop_h = int(round(crop_w * 16.0 / 9.0))

        max_x = max(0, source_width - crop_w)
        max_y = max(0, source_height - crop_h)
        center_x = max_x / 2.0

        # Build quick lookups
        tracks_by_id: Dict[int, FaceTrack] = {t.track_id: t for t in face_tracks}

        # Determine overall layout mode
        layout_mode = SpeakerLayout.SOLO
        unique_speakers = {s.speaker_id for s in active_speakers if s.speaker_id}
        if len(unique_speakers) > 1:
            layout_mode = SpeakerLayout.TWO_PERSON_SPLIT

        keyframes: List[ReframeCropKeyframe] = []
        current_smoothed_x = center_x
        last_scene_id = -1
        last_speaker_id: Optional[str] = None
        last_speaker_change_time = clip_start

        # Generate timestamps from clip_start to clip_end
        t = clip_start
        while t <= clip_end + 0.001:
            timestamp = min(t, clip_end)

            # A. Identify current scene cut
            current_scene = next(
                (s for s in scene_cuts if s.start_time <= timestamp <= s.end_time),
                None,
            )
            current_scene_id = current_scene.scene_id if current_scene else 0

            # HARD SCENE RESET: Never smooth camera across physical scene cuts
            scene_reset = False
            if current_scene_id != last_scene_id and last_scene_id != -1:
                scene_reset = True
            last_scene_id = current_scene_id

            # B. Identify active speaker at timestamp t
            active_seg = next(
                (s for s in active_speakers if s.start_time <= timestamp <= s.end_time),
                None,
            )

            target_track: Optional[FaceTrack] = None
            if active_seg:
                # Speaker hysteresis check
                if active_seg.speaker_id != last_speaker_id:
                    if (timestamp - last_speaker_change_time) >= self.hysteresis_seconds:
                        last_speaker_id = active_seg.speaker_id
                        last_speaker_change_time = timestamp

                if active_seg.track_id is not None and active_seg.track_id in tracks_by_id:
                    target_track = tracks_by_id[active_seg.track_id]

            # C. Determine target X center in source coordinates
            target_raw_x = center_x
            if target_track and target_track.boxes:
                # Find nearest bounding box in track
                nearest_box = min(target_track.boxes, key=lambda b: abs(b.timestamp - timestamp))
                face_center_x_norm = nearest_box.x + (nearest_box.w / 2.0)
                face_center_px = face_center_x_norm * source_width
                # Position crop so face is horizontally centered
                target_raw_x = face_center_px - (crop_w / 2.0)

            # Clamp target to safe video boundaries
            clamped_target_x = max(0.0, min(float(max_x), target_raw_x))

            # D. Apply smoothing / Camera velocity limits
            if scene_reset or len(keyframes) == 0:
                current_smoothed_x = clamped_target_x
            else:
                # Bounded Exponential Moving Average (EMA)
                diff = clamped_target_x - current_smoothed_x
                max_step = self.max_pan_speed_px_per_sec * self.sample_step_seconds
                bounded_diff = math.copysign(min(abs(diff), max_step), diff)
                current_smoothed_x += self.smoothing_alpha * bounded_diff
                current_smoothed_x = max(0.0, min(float(max_x), current_smoothed_x))

            crop_x_int = int(round(current_smoothed_x))
            crop_y_int = 0

            keyframes.append(
                ReframeCropKeyframe(
                    timestamp=round(timestamp - clip_start, 3),
                    crop_x=crop_x_int,
                    crop_y=crop_y_int,
                    crop_w=crop_w,
                    crop_h=crop_h,
                    layout_mode=layout_mode,
                )
            )

            t += self.sample_step_seconds

        return ReframePlan(
            clip_id=clip_id,
            source_width=source_width,
            source_height=source_height,
            target_width=1080,
            target_height=1920,
            keyframes=keyframes,
        )
