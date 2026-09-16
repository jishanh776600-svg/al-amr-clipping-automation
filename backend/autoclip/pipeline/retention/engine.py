"""Retention Editing & Optimization Engine for Step 18.

Orchestrates dynamic pacing, retention analysis, visual enhancement,
pre-render quality gating, and multi-factor composite clip ranking.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Callable

from autoclip.campaign.models_intelligence import CampaignSpecification
from autoclip.db.models import Clip, RetentionOptimizationRecord, VisualCompositionRecord, new_id, utcnow
from autoclip.pipeline.prepare import Silence
from autoclip.pipeline.reframe.croppath import CropPath
from autoclip.pipeline.transcript import Transcript, Word

from .analyzer import RetentionAnalyzer
from .pacing import DynamicPacingEngine
from .quality_gate import FinalPreRenderQualityGate
from .visual_enhancer import VisualRetentionEnhancer

log = logging.getLogger(__name__)


class RetentionEditingEngine:
    """End-to-end engine for retention optimization, pacing tightening, and final gating."""

    def __init__(
        self,
        campaign_spec: CampaignSpecification | None = None,
        job_settings: dict[str, Any] | None = None,
        pacing_engine: DynamicPacingEngine | None = None,
        analyzer: RetentionAnalyzer | None = None,
        visual_enhancer: VisualRetentionEnhancer | None = None,
        quality_gate: FinalPreRenderQualityGate | None = None,
    ) -> None:
        self.campaign_spec = campaign_spec
        self.job_settings = job_settings or {}
        self.pacing_engine = pacing_engine or DynamicPacingEngine()
        self.analyzer = analyzer or RetentionAnalyzer()
        self.visual_enhancer = visual_enhancer or VisualRetentionEnhancer()
        self.quality_gate = quality_gate or FinalPreRenderQualityGate()

    def optimize_and_rank(
        self,
        clips: list[Clip],
        transcript: Transcript,
        crop_paths: dict[str, CropPath],
        source_path: Path,
        job_id: str,
        silences: list[Silence] | None = None,
        on_progress: Callable[[str, float, dict[str, Any]], None] | None = None,
        target_output_count: int | None = None,
    ) -> tuple[list[Clip], dict[str, CropPath], list[RetentionOptimizationRecord], dict[str, Any]]:
        """Processes all clips, applies pacing/visual edits, evaluates final gate, and ranks."""
        start_time = time.time()
        total_clips = len(clips)

        if on_progress:
            on_progress("INITIALIZING_RETENTION", 0.05, {"total": total_clips})

        all_records: list[RetentionOptimizationRecord] = []
        approved_clips: list[Clip] = []
        optimized_crop_paths: dict[str, CropPath] = {}

        from autoclip.campaign.duration import resolve_duration_limits

        min_dur, max_dur = resolve_duration_limits(
            job_settings=self.job_settings,
            campaign_spec=self.campaign_spec,
            default_min=20.0,
            default_max=60.0,
        )

        for index, clip in enumerate(clips):
            clip_t0 = time.time()
            progress_frac = (index + 0.1) / max(1, total_clips)

            if on_progress:
                on_progress(
                    "ANALYZING_RETENTION",
                    progress_frac,
                    {"clip_id": clip.id, "current": index + 1, "total": total_clips},
                )

            words = transcript.slice(clip.start_word, clip.end_word)
            base_crop_path = crop_paths.get(clip.id)

            # 1. Pacing & Silence Compression
            pacing_res = self.pacing_engine.analyze_and_tighten(
                words=words,
                clip_start_s=clip.start_s,
                clip_end_s=clip.end_s,
                silences=silences,
                min_duration_s=min_dur,
                max_duration_s=max_dur,
            )

            # Update clip with tightened boundaries
            clip.start_s = pacing_res.tightened_start_s
            clip.end_s = pacing_res.tightened_end_s
            duration_s = max(0.1, clip.end_s - clip.start_s)

            # 2. Retention Analysis
            retention_res = self.analyzer.analyze(
                words=words,
                duration_s=duration_s,
                pacing_score=pacing_res.pacing_score,
                dead_air_percentage=pacing_res.dead_air_percentage,
                campaign_spec=self.campaign_spec,
                hook_text=clip.hook,
            )

            # 3. Visual Retention Enhancement (Punch-ins)
            visual_moments = []
            enhanced_path = base_crop_path
            if base_crop_path:
                enhanced_path, visual_moments = self.visual_enhancer.enhance_composition(
                    crop_path=base_crop_path,
                    hook_start_s=clip.start_s,
                    hook_end_s=clip.start_s + 2.5,
                    climax_start_s=clip.start_s + duration_s * 0.6,
                    climax_end_s=clip.start_s + duration_s * 0.75,
                    duration_s=duration_s,
                )

            # 4. Final Pre-Render Quality Gate
            gate_res = self.quality_gate.evaluate(
                video_path=source_path,
                clip_start_s=clip.start_s,
                clip_end_s=clip.end_s,
                words=words,
                crop_path=enhanced_path or CropPath(source_width=1080, source_height=1920),
                campaign_spec=self.campaign_spec,
                retention_score=retention_res.retention_score,
                pacing_dead_air_pct=pacing_res.dead_air_percentage,
                visual_quality_score=90.0,
                min_duration_s=min_dur,
                max_duration_s=max_dur,
            )

            # 5. Composite Score Calculation (Steps 15 + 16 + 17 + 18)
            # Step 15 Viral score: clip.score (0 - 100)
            s15_viral = float(clip.score)
            s16_assembly = gate_res.quality_score
            s17_visual = 88.0
            s18_retention = retention_res.retention_score

            composite_score = round(
                0.25 * s15_viral +
                0.25 * s16_assembly +
                0.25 * s17_visual +
                0.25 * s18_retention,
                1
            )

            # Deduct gate warnings or rejections
            if gate_res.rejection_reasons:
                composite_score = min(30.0, composite_score)

            scoring_breakdown = {
                "step15_viral_score": s15_viral,
                "step16_assembly_score": s16_assembly,
                "step17_visual_score": s17_visual,
                "step18_retention_score": s18_retention,
                "composite_score": composite_score,
            }

            editing_decisions = {
                "tightened_start_s": clip.start_s,
                "tightened_end_s": clip.end_s,
                "silence_tightenings": pacing_res.silence_tightenings,
                "visual_punch_ins": visual_moments,
            }

            clip_elapsed = time.time() - clip_t0

            record = RetentionOptimizationRecord(
                id=new_id(),
                clip_id=clip.id,
                job_id=job_id,
                retention_score=retention_res.retention_score,
                final_score=composite_score,
                quality_status=gate_res.status,
                hook_strength=retention_res.hook_strength,
                speech_density_wps=retention_res.speech_density_wps,
                dead_air_percentage=pacing_res.dead_air_percentage,
                pacing_score=pacing_res.pacing_score,
                narrative_score=retention_res.narrative_score,
                editing_decisions=editing_decisions,
                visual_emphasis=visual_moments,
                scoring_breakdown=scoring_breakdown,
                rejection_reasons=gate_res.rejection_reasons,
                warnings=list(set(pacing_res.warnings + retention_res.warnings + gate_res.warnings)),
                processing_time_s=round(clip_elapsed, 2),
                version=1,
                telemetry={"pacing": pacing_res.dead_air_s, "metrics": retention_res.metrics},
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            all_records.append(record)

            if record.is_approved:
                approved_clips.append(clip)
                if enhanced_path:
                    optimized_crop_paths[clip.id] = enhanced_path
                log.info(
                    "Clip %s approved by Final Quality Gate (Status=%s, Final Score=%.1f, Retention=%.1f)",
                    clip.id, record.quality_status, record.final_score, record.retention_score
                )
            else:
                log.warning(
                    "Clip %s rejected by Final Quality Gate: %s",
                    clip.id, "; ".join(record.rejection_reasons)
                )

        # 6. Rank approved clips by final_score descending
        record_by_clip = {r.clip_id: r for r in all_records}
        approved_clips.sort(key=lambda c: record_by_clip.get(c.id, RetentionOptimizationRecord(id="", clip_id="", job_id="")).final_score, reverse=True)

        # Slice to configured target count
        limit = (
            target_output_count
            or (int(self.campaign_spec.output_count.value) if (self.campaign_spec and self.campaign_spec.output_count) else len(approved_clips))
        )
        final_clips = approved_clips[:limit]

        # Update ranks
        for r_idx, c in enumerate(final_clips, 1):
            c.rank = r_idx

        elapsed_total = round(time.time() - start_time, 2)
        avg_retention = (
            round(sum(r.retention_score for r in all_records) / len(all_records), 1)
            if all_records else 0.0
        )

        telemetry = {
            "clips_analyzed": total_clips,
            "clips_optimized": len(approved_clips),
            "clips_passed": sum(1 for r in all_records if r.quality_status == "FINAL_PASS"),
            "clips_warned": sum(1 for r in all_records if r.quality_status == "FINAL_WARN"),
            "clips_rejected": sum(1 for r in all_records if r.quality_status == "FINAL_REJECT"),
            "final_selected": len(final_clips),
            "avg_retention_score": avg_retention,
            "warned_count": sum(1 for r in all_records if r.quality_status == "FINAL_WARN"),
            "rejected_count": sum(1 for r in all_records if r.quality_status == "FINAL_REJECT"),
            "elapsed_s": elapsed_total,
            "optimizations": [r.to_dict() for r in all_records],
        }

        if on_progress:
            on_progress("RETENTION_COMPLETE", 1.0, telemetry)

        return final_clips, optimized_crop_paths, all_records, telemetry
