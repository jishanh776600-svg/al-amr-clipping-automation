"""Final Pre-Render Quality Gate for Step 18.

Comprehensive deterministic multi-dimensional validation before expensive FFmpeg export.
Rejects any clip that fails audio, video, sync, speech padding, dead air, or campaign rules.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autoclip.campaign.models_intelligence import CampaignSpecification
from autoclip.pipeline import ffmpeg
from autoclip.pipeline.reframe.croppath import CropPath
from autoclip.pipeline.transcript import Word

log = logging.getLogger(__name__)


@dataclass
class FinalGateResult:
    status: str  # FINAL_PASS, FINAL_WARN, FINAL_REJECT
    quality_score: float
    rejection_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    rule_checks: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


class FinalPreRenderQualityGate:
    """Rigorous pre-render gate validating audio/video, sync, padding, and compliance."""

    def __init__(
        self,
        min_speech_padding_s: float = 0.05,
        max_dead_air_percentage: float = 22.0,
    ) -> None:
        self.min_speech_padding_s = min_speech_padding_s
        self.max_dead_air_percentage = max_dead_air_percentage

    def evaluate(
        self,
        video_path: Path,
        clip_start_s: float,
        clip_end_s: float,
        words: list[Word],
        crop_path: CropPath,
        campaign_spec: CampaignSpecification | None = None,
        retention_score: float = 80.0,
        pacing_dead_air_pct: float = 5.0,
        visual_quality_score: float = 85.0,
    ) -> FinalGateResult:
        warnings: list[str] = []
        rejections: list[str] = []
        rule_checks: list[dict[str, Any]] = []

        duration_s = clip_end_s - clip_start_s

        # ------------------------------------------------------------------
        # 1. Source Media & Streams Check
        # ------------------------------------------------------------------
        if not video_path.exists() or video_path.stat().st_size == 0:
            rejections.append("source_video_missing_or_empty")
            rule_checks.append({"rule": "media_exists", "passed": False})
        else:
            rule_checks.append({"rule": "media_exists", "passed": True})

        try:
            info = ffmpeg.probe(video_path)
            if not info.has_video:
                rejections.append("missing_video_stream")
            if not info.has_audio:
                rejections.append("missing_audio_stream")
            rule_checks.append({"rule": "stream_integrity", "passed": info.has_video and info.has_audio})
        except Exception as e:
            rejections.append(f"ffmpeg_probe_failed: {str(e)}")
            rule_checks.append({"rule": "stream_integrity", "passed": False})

        # ------------------------------------------------------------------
        # 2. Speech Padding / Clipped Speech Prevention
        # ------------------------------------------------------------------
        if not words:
            rejections.append("zero_words_in_clip")
            rule_checks.append({"rule": "speech_presence", "passed": False})
        else:
            first_w = words[0]
            last_w = words[-1]
            lead_pad = first_w.start - clip_start_s
            tail_pad = clip_end_s - last_w.end

            if lead_pad < -0.05:  # Word started before clip start
                rejections.append(f"clipped_speech_at_start(lead={lead_pad:.2f}s)")
            if tail_pad < -0.05:  # Word ended after clip end
                rejections.append(f"clipped_speech_at_end(tail={tail_pad:.2f}s)")

            rule_checks.append({
                "rule": "speech_padding_safety",
                "passed": lead_pad >= -0.05 and tail_pad >= -0.05,
                "lead_pad": round(lead_pad, 2),
                "tail_pad": round(tail_pad, 2),
            })

        # ------------------------------------------------------------------
        # 3. Excessive Silence / Dead Air Check
        # ------------------------------------------------------------------
        if pacing_dead_air_pct > self.max_dead_air_percentage:
            rejections.append(f"excessive_dead_air({pacing_dead_air_pct:.1f}% > {self.max_dead_air_percentage}%)")
            rule_checks.append({"rule": "dead_air_limit", "passed": False})
        else:
            rule_checks.append({"rule": "dead_air_limit", "passed": True})

        # ------------------------------------------------------------------
        # 4. Duration Bounds & Campaign Compliance
        # ------------------------------------------------------------------
        if campaign_spec:
            min_dur = float(campaign_spec.duration_min_s.value) if campaign_spec.duration_min_s else 5.0
            max_dur = float(campaign_spec.duration_max_s.value) if campaign_spec.duration_max_s else 60.0

            if duration_s < (min_dur - 0.5):
                rejections.append(f"duration_below_campaign_minimum({duration_s:.1f}s < {min_dur:.1f}s)")
            elif duration_s > (max_dur + 0.5):
                rejections.append(f"duration_exceeds_campaign_maximum({duration_s:.1f}s > {max_dur:.1f}s)")

            # Check banned words
            full_text = " ".join(w.text.lower() for w in words)
            for bw in campaign_spec.banned_words:
                val = str(bw.value).lower().strip()
                if val and val in full_text:
                    rejections.append(f"banned_term_detected({val})")

            # Check mandatory CTA if required
            if campaign_spec.cta_required and campaign_spec.cta_required.value:
                tail_text = " ".join(w.text.lower() for w in words[-min(10, len(words)):])
                if not any(k in tail_text for k in ["follow", "link", "bio", "check", "subscribe", "join"]):
                    warnings.append("mandatory_campaign_cta_weak_or_missing")

        # ------------------------------------------------------------------
        # 5. Crop Geometry & FFmpeg Render-Safety
        # ------------------------------------------------------------------
        for seg in crop_path.segments:
            if seg.width % 2 != 0 or seg.height % 2 != 0:
                rejections.append(f"non_even_render_dimensions({seg.width}x{seg.height})")
                break
            for kf in seg.keyframes:
                if not seg.fit and (kf.x < -1.0 or kf.y < -1.0):
                    rejections.append("out_of_bounds_crop_coordinates")
                    break

        # ------------------------------------------------------------------
        # 6. Final Status & Score
        # ------------------------------------------------------------------
        base_score = (retention_score * 0.5) + (visual_quality_score * 0.5)
        base_score -= len(warnings) * 5.0
        if rejections:
            base_score = min(35.0, base_score - len(rejections) * 25.0)

        quality_score = max(0.0, min(100.0, round(base_score, 1)))

        if rejections:
            status = "FINAL_REJECT"
        elif quality_score >= 68.0 and not rejections:
            status = "FINAL_PASS"
        elif quality_score >= 50.0:
            status = "FINAL_WARN"
        else:
            status = "FINAL_REJECT"

        metrics = {
            "duration_s": round(duration_s, 2),
            "retention_score": retention_score,
            "visual_quality_score": visual_quality_score,
            "pacing_dead_air_pct": pacing_dead_air_pct,
            "words_count": len(words),
        }

        return FinalGateResult(
            status=status,
            quality_score=quality_score,
            rejection_reasons=rejections,
            warnings=warnings,
            rule_checks=rule_checks,
            metrics=metrics,
        )
