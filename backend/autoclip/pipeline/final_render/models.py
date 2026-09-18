"""Data models for Step 22 Final Render, Packaging, and Quality Gate."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FinalRenderConfig:
    """Output configuration for broadcast-quality short-form video."""

    width: int = 1080
    height: int = 1920
    ratio: str = "9:16"
    crf: int = 18
    preset: str = "medium"
    video_codec: str = "libx264"
    audio_codec: str = "aac"
    audio_bitrate: str = "192k"
    audio_sample_rate: int = 48000
    pixel_format: str = "yuv420p"
    max_duration_diff_s: float = 0.5
    max_av_sync_diff_s: float = 0.35

    def to_dict(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "ratio": self.ratio,
            "crf": self.crf,
            "preset": self.preset,
            "video_codec": self.video_codec,
            "audio_codec": self.audio_codec,
            "audio_bitrate": self.audio_bitrate,
            "audio_sample_rate": self.audio_sample_rate,
            "pixel_format": self.pixel_format,
            "max_duration_diff_s": self.max_duration_diff_s,
            "max_av_sync_diff_s": self.max_av_sync_diff_s,
        }


@dataclass
class FinalRenderGateResult:
    """Outcome of evaluating final rendered media against broadcast standards."""

    status: str = "RENDER_PASS"  # "RENDER_PASS", "RENDER_WARN", "RENDER_REJECT"
    quality_score: float = 100.0
    video_metrics: dict[str, Any] = field(default_factory=dict)
    audio_metrics: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    rejection_reasons: list[str] = field(default_factory=list)

    @property
    def is_approved(self) -> bool:
        return self.status in ("RENDER_PASS", "RENDER_WARN")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "quality_score": self.quality_score,
            "is_approved": self.is_approved,
            "video_metrics": self.video_metrics,
            "audio_metrics": self.audio_metrics,
            "provenance": self.provenance,
            "warnings": self.warnings,
            "rejection_reasons": self.rejection_reasons,
        }


@dataclass
class FinalRenderMetadata:
    """Full forensic provenance metadata bundled into output package."""

    job_id: str
    clip_id: str
    source_id: str
    final_rank: int
    title: str
    duration_s: float
    width: int
    height: int
    fps: float
    video_codec: str
    audio_codec: str
    caption_style: str
    visual_filter: str = "original"
    bgm_asset_id: str | None = None
    bgm_asset_name: str = ""
    quality_score: float = 100.0
    quality_status: str = "RENDER_PASS"
    render_timestamp: str = ""
    pipeline_version: str = "step_22"
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "clip_id": self.clip_id,
            "source_id": self.source_id,
            "final_rank": self.final_rank,
            "title": self.title,
            "duration_s": self.duration_s,
            "resolution": f"{self.width}x{self.height}",
            "fps": self.fps,
            "video_codec": self.video_codec,
            "audio_codec": self.audio_codec,
            "caption_style": self.caption_style,
            "visual_filter": self.visual_filter,
            "bgm_asset_id": self.bgm_asset_id,
            "bgm_asset_name": self.bgm_asset_name,
            "quality_score": self.quality_score,
            "quality_status": self.quality_status,
            "render_timestamp": self.render_timestamp,
            "pipeline_version": self.pipeline_version,
            "extra": self.extra,
        }
