"""Production-grade FinalRenderEngine combining reframed video, captions, mixed BGM, and quality gate."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Callable

from autoclip.db.models import Clip, FinalRenderRecord, new_id, utcnow
from autoclip.pipeline import export, ffmpeg
from .models import FinalRenderConfig, FinalRenderGateResult, FinalRenderMetadata
from .packager import OutputPackager
from .quality_gate import FinalRenderQualityGate

log = logging.getLogger(__name__)


class FinalRenderEngine:
    """Orchestrates final rendering, quality validation, and atomic packaging."""

    def __init__(
        self,
        config: FinalRenderConfig | None = None,
        quality_gate: FinalRenderQualityGate | None = None,
    ) -> None:
        self.config = config or FinalRenderConfig()
        self.quality_gate = quality_gate or FinalRenderQualityGate(config=self.config)

    def render_and_package(
        self,
        clip: Clip,
        source_media_path: Path,
        crop_path: export.CropPath,
        caption_style_key: str,
        ass_path: Path | None,
        audio_path: Path | None,
        bgm_asset_id: str | None,
        bgm_asset_name: str,
        exports_base_dir: Path,
        render_work_dir: Path,
        attempt: int = 1,
        on_progress: Callable[[float], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
        min_duration_s: float | None = None,
        max_duration_s: float | None = None,
        visual_filter: str = "original",
        words: list[Any] | None = None,
        edl: Any | None = None,
    ) -> tuple[Path, FinalRenderRecord]:
        """Renders, evaluates, and packages one final clip.

        Idempotency rule: If final.mp4 already exists in the package dir and passes
        FinalRenderQualityGate, reuses it safely without re-encoding!
        """
        start_time = time.time()
        duration_s = clip.end_s - clip.start_s

        # 1. Resolve package destination
        package_dir = OutputPackager.get_package_dir(
            exports_base_dir,
            job_id=clip.job_id,
            rank=clip.rank,
            clip_id=clip.id,
            title=clip.title,
        )
        package_dir.mkdir(parents=True, exist_ok=True)
        final_mp4_path = package_dir / "final.mp4"

        # 2. Idempotency check: Reuse existing valid final output
        if final_mp4_path.is_file():
            existing_gate = self.quality_gate.evaluate(
                final_mp4_path,
                expected_duration_s=duration_s,
                expected_caption_style=caption_style_key,
                expected_bgm_asset_id=bgm_asset_id,
                min_duration_s=min_duration_s,
                max_duration_s=max_duration_s,
                expected_visual_filter=visual_filter,
            )
            if existing_gate.is_approved:
                log.info("Clip %s output already exists and passes quality gate at %s; reusing.", clip.id, final_mp4_path)
                elapsed_s = round(time.time() - start_time, 3)

                metadata = FinalRenderMetadata(
                    job_id=clip.job_id,
                    clip_id=clip.id,
                    source_id=getattr(clip, "source_id", ""),
                    final_rank=clip.rank,
                    title=clip.title,
                    duration_s=existing_gate.video_metrics.get("duration_s", duration_s),
                    width=existing_gate.video_metrics.get("width", self.config.width),
                    height=existing_gate.video_metrics.get("height", self.config.height),
                    fps=existing_gate.video_metrics.get("fps", 30.0),
                    video_codec=existing_gate.video_metrics.get("video_codec", "h264"),
                    audio_codec=existing_gate.audio_metrics.get("audio_codec", "aac"),
                    caption_style=caption_style_key,
                    visual_filter=visual_filter,
                    bgm_asset_id=bgm_asset_id,
                    bgm_asset_name=bgm_asset_name,
                    quality_score=existing_gate.quality_score,
                    quality_status=existing_gate.status,
                    render_timestamp=utcnow(),
                )
                OutputPackager.package_clip(package_dir, final_mp4_path, metadata, existing_gate)

                record = FinalRenderRecord(
                    id=new_id(),
                    job_id=clip.job_id,
                    clip_id=clip.id,
                    output_path=str(final_mp4_path),
                    package_dir=str(package_dir),
                    duration=existing_gate.video_metrics.get("duration_s", duration_s),
                    width=existing_gate.video_metrics.get("width", self.config.width),
                    height=existing_gate.video_metrics.get("height", self.config.height),
                    fps=existing_gate.video_metrics.get("fps", 30.0),
                    video_codec=existing_gate.video_metrics.get("video_codec", "h264"),
                    audio_codec=existing_gate.audio_metrics.get("audio_codec", "aac"),
                    caption_style=caption_style_key,
                    bgm_asset_id=bgm_asset_id,
                    quality_score=existing_gate.quality_score,
                    quality_status=existing_gate.status,
                    render_status="completed",
                    render_attempt=attempt,
                    error_details=existing_gate.rejection_reasons,
                    telemetry={"idempotent_reuse": True, "gate": existing_gate.to_dict()},
                    created_at=utcnow(),
                    updated_at=utcnow(),
                )
                if on_progress:
                    on_progress(1.0)
                return final_mp4_path, record

            else:
                log.warning("Existing output for clip %s failed quality gate (%s); re-rendering.", clip.id, existing_gate.rejection_reasons)
                final_mp4_path.unlink(missing_ok=True)

        # 3. Render into atomic temporary target
        temp_dest = package_dir / "final_render_tmp.mp4"
        temp_dest.unlink(missing_ok=True)

        from autoclip.config import ExportSettings
        from autoclip.pipeline import captions as captions_module

        style = captions_module.resolve_style(caption_style_key)

        # Generate B-roll Edit Decision List (EDL) if words provided and EDL not precomputed
        if edl is None and words:
            try:
                from autoclip.pipeline.broll import SemanticBrollEngine, VisualAssetVault
                broll_engine = SemanticBrollEngine(vault=VisualAssetVault())
                edl = broll_engine.generate_edl(
                    words=words,
                    clip_id=clip.id,
                    clip_start_s=clip.start_s,
                    clip_end_s=clip.end_s,
                )
            except Exception as e:
                log.warning("Semantic B-roll generation failed for clip %s: %s; falling back to clean A-roll.", clip.id, e)
                edl = None

        if edl is not None:
            try:
                import json
                (package_dir / "edl.json").write_text(json.dumps(edl.to_dict(), indent=2), encoding="utf-8")
            except Exception as e:
                log.warning("Failed to save edl.json for clip %s: %s", clip.id, e)

        request = export.ExportRequest(
            source=source_media_path,
            destination=temp_dest,
            start_s=clip.start_s,
            end_s=clip.end_s,
            crop_path=crop_path,
            words=words or [],
            style=style,
            ratio=self.config.ratio,
            burn_captions=bool(ass_path and Path(ass_path).is_file()),
            ass_path=ass_path,
            audio_path=audio_path,
            visual_filter=visual_filter,
            edl=edl,
        )

        export_settings = ExportSettings(
            ratio=self.config.ratio,
            crf=self.config.crf,
        )

        try:
            export.export_clip(
                request,
                work_dir=render_work_dir,
                settings=export_settings,
                on_progress=on_progress,
                cancelled=cancelled,
            )
        except Exception as exc:
            temp_dest.unlink(missing_ok=True)
            elapsed_s = round(time.time() - start_time, 3)
            err_details = [f"FFmpeg export failed: {str(exc)}"]
            record = FinalRenderRecord(
                id=new_id(),
                job_id=clip.job_id,
                clip_id=clip.id,
                output_path="",
                package_dir=str(package_dir),
                duration=0.0,
                width=self.config.width,
                height=self.config.height,
                fps=0.0,
                video_codec=self.config.video_codec,
                audio_codec=self.config.audio_codec,
                caption_style=caption_style_key,
                bgm_asset_id=bgm_asset_id,
                quality_score=0.0,
                quality_status="RENDER_REJECT",
                render_status="failed",
                render_attempt=attempt,
                error_details=err_details,
                telemetry={"elapsed_s": elapsed_s, "error": str(exc)},
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            raise RuntimeError(f"Rendering failed for clip {clip.id}: {exc}") from exc

        # 4. Evaluate FinalRenderQualityGate on rendered temp output
        gate_res = self.quality_gate.evaluate(
            temp_dest,
            expected_duration_s=duration_s,
            expected_caption_style=caption_style_key,
            expected_bgm_asset_id=bgm_asset_id,
            min_duration_s=min_duration_s,
            max_duration_s=max_duration_s,
            expected_visual_filter=visual_filter,
        )

        elapsed_s = round(time.time() - start_time, 3)

        # 5. Handle Quality Gate outcome
        if not gate_res.is_approved:
            temp_dest.unlink(missing_ok=True)
            log.error("Clip %s rejected by FinalRenderQualityGate: %s", clip.id, gate_res.rejection_reasons)
            record = FinalRenderRecord(
                id=new_id(),
                job_id=clip.job_id,
                clip_id=clip.id,
                output_path="",
                package_dir=str(package_dir),
                duration=gate_res.video_metrics.get("duration_s", 0.0),
                width=gate_res.video_metrics.get("width", 0),
                height=gate_res.video_metrics.get("height", 0),
                fps=gate_res.video_metrics.get("fps", 0.0),
                video_codec=gate_res.video_metrics.get("video_codec", ""),
                audio_codec=gate_res.audio_metrics.get("audio_codec", ""),
                caption_style=caption_style_key,
                bgm_asset_id=bgm_asset_id,
                quality_score=gate_res.quality_score,
                quality_status="RENDER_REJECT",
                render_status="failed",
                render_attempt=attempt,
                error_details=gate_res.rejection_reasons,
                telemetry={"elapsed_s": elapsed_s, "gate": gate_res.to_dict()},
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            return temp_dest, record

        # 6. Atomic move to final.mp4 and package metadata
        sidecar_srt = (render_work_dir / clip.id / "captions.srt") if (render_work_dir / clip.id / "captions.srt").is_file() else None
        metadata = FinalRenderMetadata(
            job_id=clip.job_id,
            clip_id=clip.id,
            source_id=getattr(clip, "source_id", ""),
            final_rank=clip.rank,
            title=clip.title,
            duration_s=gate_res.video_metrics.get("duration_s", duration_s),
            width=gate_res.video_metrics.get("width", self.config.width),
            height=gate_res.video_metrics.get("height", self.config.height),
            fps=gate_res.video_metrics.get("fps", 30.0),
            video_codec=gate_res.video_metrics.get("video_codec", "h264"),
            audio_codec=gate_res.audio_metrics.get("audio_codec", "aac"),
            caption_style=caption_style_key,
            visual_filter=visual_filter,
            bgm_asset_id=bgm_asset_id,
            bgm_asset_name=bgm_asset_name,
            quality_score=gate_res.quality_score,
            quality_status=gate_res.status,
            render_timestamp=utcnow(),
        )

        final_path = OutputPackager.package_clip(
            package_dir=package_dir,
            temp_video_path=temp_dest,
            metadata=metadata,
            quality_result=gate_res,
            sidecar_srt_path=sidecar_srt,
        )

        record = FinalRenderRecord(
            id=new_id(),
            job_id=clip.job_id,
            clip_id=clip.id,
            output_path=str(final_path),
            package_dir=str(package_dir),
            duration=gate_res.video_metrics.get("duration_s", duration_s),
            width=gate_res.video_metrics.get("width", self.config.width),
            height=gate_res.video_metrics.get("height", self.config.height),
            fps=gate_res.video_metrics.get("fps", 30.0),
            video_codec=gate_res.video_metrics.get("video_codec", "h264"),
            audio_codec=gate_res.audio_metrics.get("audio_codec", "aac"),
            caption_style=caption_style_key,
            bgm_asset_id=bgm_asset_id,
            quality_score=gate_res.quality_score,
            quality_status=gate_res.status,
            render_status="completed",
            render_attempt=attempt,
            error_details=gate_res.rejection_reasons,
            telemetry={"elapsed_s": elapsed_s, "warnings": gate_res.warnings, "gate": gate_res.to_dict()},
            created_at=utcnow(),
            updated_at=utcnow(),
        )

        return final_path, record
