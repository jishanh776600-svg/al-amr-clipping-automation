"""Production-Grade 9:16 Smart Reframing & Visual Composition.

Step 17: Converts approved ClipSpecifications into stable, visually compelling
vertical compositions suitable for Shorts/Reels before captioning and export.
Includes MediaPipe subject tracking, multi-person framing, virtual camera smoothing,
safe fallbacks (blurred-background fit), and a Pre-Render Visual Quality Gate.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from autoclip.campaign.models_intelligence import CampaignSpecification
from autoclip.db.models import Clip, VisualCompositionRecord, new_id, utcnow
from autoclip.pipeline import ffmpeg
from autoclip.pipeline.reframe.croppath import (
    CropPath,
    CropSegment,
    Strategy,
    centre_crop,
    target_crop_size,
)
from autoclip.pipeline.reframe.faces import (
    DEFAULT_SAMPLE_FPS,
    FaceDetectionUnavailable,
    FaceObservation,
    sample_faces,
)
from autoclip.pipeline.reframe.scenes import detect_shots
from autoclip.pipeline.reframe.tracker import FaceTrack, build_tracks
from autoclip.pipeline.transcript import Transcript

log = logging.getLogger(__name__)


@dataclass
class VisualGateResult:
    status: str  # VISUAL_PASS, VISUAL_WARN, VISUAL_REJECT
    quality_score: float
    warnings: list[str] = field(default_factory=list)
    rejection_reasons: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    rule_checks: list[dict[str, Any]] = field(default_factory=list)

    @property
    def is_approved(self) -> bool:
        return self.status in ("VISUAL_PASS", "VISUAL_WARN")


class VisualQualityGate:
    """Deterministic Pre-Render Visual Quality Gate validating subject safety,

    framing stability, output dimensions, and crop aesthetics.
    """

    def evaluate(
        self,
        crop_path: CropPath,
        source_w: int,
        source_h: int,
        output_w: int,
        output_h: int,
        observations: list[FaceObservation] | None = None,
        tracks: list[FaceTrack] | None = None,
        fallback_used: bool = False,
    ) -> VisualGateResult:
        warnings: list[str] = []
        rejections: list[str] = []
        rule_checks: list[dict[str, Any]] = []

        # ------------------------------------------------------------------
        # 1. Output Dimensions & Even-Pixel Safety
        # ------------------------------------------------------------------
        if output_w <= 0 or output_h <= 0:
            rejections.append(f"invalid_output_dimensions({output_w}x{output_h})")
        elif output_w % 2 != 0 or output_h % 2 != 0:
            rejections.append(f"non_even_output_dimensions({output_w}x{output_h})")
        else:
            rule_checks.append({"rule": "output_dimensions", "passed": True})

        # ------------------------------------------------------------------
        # 2. Black-Bar / Out-Of-Bounds Crop Inspection
        # ------------------------------------------------------------------
        has_out_of_bounds = False
        for seg in crop_path.segments:
            if seg.fit:
                continue  # Fit segments intentionally letterbox with blurred background
            for kf in seg.keyframes:
                if kf.x < -1.0 or kf.y < -1.0 or (kf.x + seg.width) > (source_w + 1.0) or (kf.y + seg.height) > (source_h + 1.0):
                    has_out_of_bounds = True
                    break
            if has_out_of_bounds:
                break

        if has_out_of_bounds:
            rejections.append("crop_coordinates_out_of_bounds_black_border_risk")
            rule_checks.append({"rule": "black_bar_safety", "passed": False})
        else:
            rule_checks.append({"rule": "black_bar_safety", "passed": True})

        # ------------------------------------------------------------------
        # 3. Tracking Confidence & Subject Visibility
        # ------------------------------------------------------------------
        total_obs = len(observations) if observations else 0
        tracking_confidence = 0.0
        if total_obs > 0 and crop_path.duration_s > 0:
            expected_obs = crop_path.duration_s * DEFAULT_SAMPLE_FPS
            tracking_confidence = min(1.0, total_obs / max(1.0, expected_obs))

        if total_obs == 0:
            warnings.append("no_faces_detected_using_general_or_centre_framing")
            rule_checks.append({"rule": "subject_visibility", "passed": True, "confidence": 0.0})
        elif tracking_confidence < 0.25:
            warnings.append(f"low_tracking_confidence({tracking_confidence:.2f})")
            rule_checks.append({"rule": "subject_visibility", "passed": True, "confidence": round(tracking_confidence, 2)})
        else:
            rule_checks.append({"rule": "subject_visibility", "passed": True, "confidence": round(tracking_confidence, 2)})

        # ------------------------------------------------------------------
        # 4. Face / Head Safety Margins (Headroom & Chin Protection)
        # ------------------------------------------------------------------
        headroom_violation = False
        if observations and crop_path.segments and not crop_path.segments[0].fit:
            # Check a sample of observations against crop keyframes
            for obs in observations[::2]:
                seg = None
                for s in crop_path.segments:
                    if s.start_s <= obs.t <= s.end_s:
                        seg = s
                        break
                if not seg and crop_path.segments:
                    seg = crop_path.segments[0]

                if seg and not seg.fit:
                    # Find x, y of crop at time obs.t
                    crop_x = seg.keyframes[0].x if seg.keyframes else 0.0
                    crop_y = seg.keyframes[0].y if seg.keyframes else 0.0
                    for k in seg.keyframes:
                        if k.t <= obs.t:
                            crop_x = k.x
                            crop_y = k.y
                        else:
                            break

                    rel_top = (obs.top - crop_y) / max(1.0, seg.height)
                    rel_bottom = (obs.bottom - crop_y) / max(1.0, seg.height)

                    if rel_top < -0.05 or rel_bottom > 1.05:
                        headroom_violation = True
                        break

        if headroom_violation:
            warnings.append("potential_headroom_or_chin_clipping_risk")
            rule_checks.append({"rule": "face_safety_margin", "passed": False})
        else:
            rule_checks.append({"rule": "face_safety_margin", "passed": True})

        # ------------------------------------------------------------------
        # 5. Framing Stability & Camera Movement
        # ------------------------------------------------------------------
        movement_score = 0.0
        total_panning_distance = 0.0
        kf_count = 0
        for seg in crop_path.segments:
            for i in range(len(seg.keyframes) - 1):
                k1 = seg.keyframes[i]
                k2 = seg.keyframes[i + 1]
                dt = max(0.01, k2.t - k1.t)
                dist = math.hypot(k2.x - k1.x, k2.y - k1.y)
                total_panning_distance += dist
                speed = dist / dt
                if speed > (source_w * 0.9):  # Over 90% frame width per second
                    warnings.append(f"rapid_camera_pan_detected({speed:.0f}px/s)")
                kf_count += 1

        avg_panning = (total_panning_distance / max(1, kf_count)) if kf_count > 0 else 0.0
        movement_score = min(100.0, avg_panning / 5.0)

        # ------------------------------------------------------------------
        # 6. Quality Score & Status Assignment
        # ------------------------------------------------------------------
        base_score = 88.0
        if tracking_confidence >= 0.6:
            base_score += 7.0
        if any(seg.strategy == Strategy.TRACK for seg in crop_path.segments):
            base_score += 5.0
        if fallback_used:
            base_score -= 8.0

        base_score -= len(warnings) * 6.0
        if rejections:
            base_score = min(35.0, base_score - len(rejections) * 30.0)

        quality_score = max(0.0, min(100.0, round(base_score, 1)))

        if rejections:
            status = "VISUAL_REJECT"
        elif quality_score >= 68.0 and not rejections:
            status = "VISUAL_PASS"
        elif quality_score >= 50.0:
            status = "VISUAL_WARN"
        else:
            status = "VISUAL_REJECT"

        metrics = {
            "source_w": source_w,
            "source_h": source_h,
            "output_w": output_w,
            "output_h": output_h,
            "segments_count": len(crop_path.segments),
            "tracking_confidence": round(tracking_confidence, 2),
            "movement_score": round(movement_score, 1),
            "fallback_used": fallback_used,
        }

        return VisualGateResult(
            status=status,
            quality_score=quality_score,
            warnings=warnings,
            rejection_reasons=rejections,
            metrics=metrics,
            rule_checks=rule_checks,
        )


class VisualCompositionEngine:
    """Production-grade visual composition engine for 9:16 Shorts/Reels framing.

    Detects speaker tracks with MediaPipe, applies virtual camera smoothing,
    enforces multi-person framing and blurred-background fallbacks, and gates quality.
    """

    def __init__(
        self,
        campaign_spec: CampaignSpecification | None = None,
        sample_fps: float = DEFAULT_SAMPLE_FPS,
        target_ratio: str = "9:16",
        per_clip_timeout_s: float = 15.0,
    ) -> None:
        self.campaign_spec = campaign_spec
        self.sample_fps = sample_fps
        self.target_ratio = target_ratio
        self.per_clip_timeout_s = per_clip_timeout_s
        self.quality_gate = VisualQualityGate()

    def compose(
        self,
        video_path: Path,
        clip: Clip,
        transcript: Transcript | None = None,
        on_progress: Callable[[str, float, dict[str, Any]], None] | None = None,
    ) -> tuple[CropPath, VisualCompositionRecord]:
        """Calculates dynamic 9:16 visual composition and evaluates visual quality."""
        start_t = time.time()
        meta = {"clip_id": clip.id, "rank": clip.rank}

        if on_progress:
            on_progress("ANALYZING_FRAME", 0.1, meta)

        info = ffmpeg.probe(video_path)
        if not info.has_video or not info.width or not info.height:
            raise ValueError(f"Video {video_path.name} has no valid video stream.")

        source_w, source_h = info.width, info.height
        duration_s = max(0.1, clip.end_s - clip.start_s)

        # ------------------------------------------------------------------
        # Aspect Ratio & Output Dimensions
        # ------------------------------------------------------------------
        aspect_w, aspect_h = (9, 16)
        if self.target_ratio == "1:1":
            aspect_w, aspect_h = (1, 1)
        elif self.target_ratio == "16:9":
            aspect_w, aspect_h = (16, 9)

        crop_w, crop_h = target_crop_size(source_w, source_h, aspect_w, aspect_h)
        # Ensure even pixel dimensions
        output_w = crop_w if crop_w % 2 == 0 else crop_w - 1
        output_h = crop_h if crop_h % 2 == 0 else crop_h - 1

        fallback_used = False
        fallback_reason = ""
        crop_path: CropPath | None = None
        observations: list[FaceObservation] = []
        tracks: list[FaceTrack] = []

        # ------------------------------------------------------------------
        # Native Portrait Source Handling (Passthrough)
        # ------------------------------------------------------------------
        source_aspect = source_w / source_h
        target_aspect = aspect_w / aspect_h

        # If source is already within 5% of target 9:16 aspect ratio
        if abs(source_aspect - target_aspect) < 0.05:
            log.info("Clip %s: Source is already native 9:16 portrait; using passthrough composition.", clip.id)
            crop_path = centre_crop(source_w, source_h, duration_s, aspect_w=aspect_w, aspect_h=aspect_h)
            crop_strategy = "passthrough"
            tracking_strategy = "native_portrait"
            tracking_confidence = 1.0
        else:
            if on_progress:
                on_progress("TRACKING_SUBJECT", 0.3, meta)

            from autoclip.pipeline.reframe import ReframeConfig, build_crop_path

            config = ReframeConfig(
                aspect_w=aspect_w,
                aspect_h=aspect_h,
                sample_fps=self.sample_fps,
            )

            try:
                observations = sample_faces(
                    video_path,
                    start_s=clip.start_s,
                    end_s=clip.end_s,
                    sample_fps=self.sample_fps,
                )
            except Exception as e:
                log.warning("MediaPipe face detection failed for clip %s: %s; falling back to centre crop.", clip.id, e)
                fallback_used = True
                fallback_reason = f"face_detection_error: {str(e)}"
                observations = []

            if on_progress:
                on_progress("COMPUTING_COMPOSITION", 0.55, meta)

            if observations:
                tracks = build_tracks(observations)
                try:
                    crop_path = build_crop_path(
                        video_path,
                        start_s=clip.start_s,
                        end_s=clip.end_s,
                        transcript=transcript,
                        config=config,
                    )
                except Exception as e:
                    log.warning("build_crop_path failed for clip %s: %s; falling back to centre crop.", clip.id, e)
                    fallback_used = True
                    fallback_reason = f"crop_path_build_error: {str(e)}"
                    crop_path = centre_crop(source_w, source_h, duration_s, aspect_w=aspect_w, aspect_h=aspect_h)
            else:
                fallback_used = True
                if not fallback_reason:
                    fallback_reason = "no_faces_detected_in_clip_window"
                crop_path = centre_crop(source_w, source_h, duration_s, aspect_w=aspect_w, aspect_h=aspect_h)

            if on_progress:
                on_progress("SMOOTHING_CAMERA", 0.75, meta)

            primary_strategy = crop_path.segments[0].strategy if crop_path.segments else Strategy.GENERAL
            crop_strategy = primary_strategy.value
            tracking_strategy = "mediapipe" if observations else "centre_fallback"
            tracking_confidence = min(1.0, len(observations) / max(1.0, duration_s * self.sample_fps))

        # ------------------------------------------------------------------
        # Pre-Render Visual Quality Gate
        # ------------------------------------------------------------------
        if on_progress:
            on_progress("VISUAL_QUALITY_GATE", 0.9, meta)

        gate_result = self.quality_gate.evaluate(
            crop_path=crop_path,
            source_w=source_w,
            source_h=source_h,
            output_w=output_w,
            output_h=output_h,
            observations=observations,
            tracks=tracks,
            fallback_used=fallback_used,
        )

        record_id = new_id()
        record = VisualCompositionRecord(
            id=record_id,
            clip_id=clip.id,
            job_id=clip.job_id,
            source_width=source_w,
            source_height=source_h,
            output_width=output_w,
            output_height=output_h,
            crop_strategy=crop_strategy,
            tracking_strategy=tracking_strategy,
            tracking_confidence=round(tracking_confidence, 2),
            camera_movement_score=round(gate_result.metrics.get("movement_score", 0.0), 1),
            smoothing_parameters={
                "min_cutoff": 0.6,
                "beta": 0.02,
                "sample_fps": self.sample_fps,
            },
            fallback_used=fallback_used,
            fallback_reason=fallback_reason,
            quality_score=gate_result.quality_score,
            quality_status=gate_result.status,
            warnings=gate_result.warnings,
            rejection_reasons=gate_result.rejection_reasons,
            version=1,
            telemetry={
                "processing_time_s": round(time.time() - start_t, 3),
                "metrics": gate_result.metrics,
                "rule_checks": gate_result.rule_checks,
            },
            created_at=utcnow(),
            updated_at=utcnow(),
        )

        if on_progress:
            on_progress("COMPOSITION_READY", 1.0, meta)

        return (crop_path, record)
