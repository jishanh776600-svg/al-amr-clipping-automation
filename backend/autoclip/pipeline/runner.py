"""Pipeline orchestration.

Runs a job stage by stage, writing each stage's artifacts into
``work/{job_id}/``. Because every stage's output is a file on disk, a retry
skips everything already done and resumes at the stage that failed — which
matters when stage two took eleven minutes and stage four hit a rate limit.

Progress is reported through a callback rather than written directly, so the
same runner serves the CLI (a progress bar) and the web API (an SSE stream).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .. import paths
from ..campaign import (
    CampaignBrief,
    CampaignEvaluator,
    CampaignSpecification,
    CandidateDiscoveryEngine,
    ClipAssemblyEngine,
    candidates_to_clips,
    rank_and_filter_candidates,
    specifications_to_clips,
)
from ..config import Settings
from ..config import load as load_settings
from ..db import store
from ..db.models import (
    CampaignEvaluationRow,
    Clip,
    Export,
    Job,
    Source,
    RetentionOptimizationRecord,
    VisualCompositionRecord,
    CaptionOptimizationRecord,
    BGMMixRecord,
    FinalRenderRecord,
    new_id,
    utcnow,
)
from ..db.models import Transcript as TranscriptRow
from ..providers import build_provider, detection_config
from . import Stage, captions, export, ffmpeg, highlights, prepare, transcribe
from .prepare import Silence
from .reframe import ReframeConfig, VisualCompositionEngine, build_crop_path
from .reframe.croppath import CropPath
from .retention import RetentionEditingEngine
from .transcript import Transcript
from .validator import validate_media_output

log = logging.getLogger(__name__)

#: Weight of each stage in the overall progress bar. Rough proportions of wall
#: time on a mid-range machine — transcription and export dominate.
STAGE_WEIGHTS: dict[Stage, float] = {
    Stage.PREPARE: 0.05,
    Stage.TRANSCRIBE: 0.30,
    Stage.HIGHLIGHTS: 0.15,
    Stage.REFRAME: 0.15,
    Stage.RETENTION: 0.10,
    Stage.CAPTIONS: 0.02,
    Stage.AUDIO_MIX: 0.03,
    Stage.EXPORT: 0.20,
}


class JobCancelled(RuntimeError):
    """The job was cancelled by the user."""


class PipelineError(RuntimeError):
    """A stage failed in a way the user should see."""

    def __init__(self, message: str, *, stage: Stage) -> None:
        super().__init__(message)
        self.stage = stage


@dataclass
class ProgressEvent:
    stage: Stage
    #: Progress within the stage, 0..1.
    stage_progress: float
    #: Progress across the whole job, 0..1.
    overall: float
    message: str = ""


ProgressHandler = Callable[[ProgressEvent], None]


class JobWorkspace:
    """Paths for one job's intermediate artifacts."""

    def __init__(self, job_id: str) -> None:
        self.root = paths.job_work_dir(job_id)
        self.root.mkdir(parents=True, exist_ok=True)

    @property
    def audio(self) -> Path:
        return self.root / "audio.wav"

    @property
    def transcript(self) -> Path:
        return self.root / "transcript.json"

    @property
    def silences(self) -> Path:
        return self.root / "silences.json"

    @property
    def thumbnails(self) -> Path:
        return self.root / "thumbnails"

    def crop_path(self, clip_id: str) -> Path:
        return self.root / "crops" / f"{clip_id}.json"

    @property
    def captions_dir(self) -> Path:
        return self.root / "captions"

    @property
    def audio_mix_dir(self) -> Path:
        return self.root / "audio_mix"


class PipelineRunner:
    """Executes one job."""

    def __init__(
        self,
        job: Job,
        source: Source,
        *,
        settings: Settings | None = None,
        on_progress: ProgressHandler | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> None:
        self.job = job
        self.source = source
        self.settings = settings or load_settings()
        self.on_progress = on_progress
        self._is_cancelled = is_cancelled or (lambda: False)
        self.workspace = JobWorkspace(job.id)
        self._completed_weight = 0.0

    # -- progress ----------------------------------------------------------

    def _check_cancelled(self) -> None:
        if self._is_cancelled():
            raise JobCancelled("Job cancelled.")

    def _emit(
        self,
        stage: Stage,
        stage_progress: float,
        message: str = "",
        *,
        overall: float | None = None,
    ) -> None:
        if overall is None:
            overall = self._completed_weight + STAGE_WEIGHTS[stage] * stage_progress
        overall = min(1.0, overall)
        store.update_job(self.job.id, current_stage=stage.value, progress=round(overall, 4))
        if self.on_progress:
            self.on_progress(
                ProgressEvent(
                    stage=stage,
                    stage_progress=stage_progress,
                    overall=overall,
                    message=message or stage.label,
                )
            )

    def _finish_stage(self, stage: Stage) -> None:
        """Mark a stage complete.

        ``overall`` is passed explicitly rather than derived. Deriving it after
        incrementing the accumulated weight counts the stage twice, so the bar
        jumped past the true total and then snapped backwards when the next
        stage reported 0 — visibly, at every stage boundary.
        """
        self._completed_weight += STAGE_WEIGHTS[stage]
        self._emit(stage, 1.0, overall=self._completed_weight)

    def _stage_progress(self, stage: Stage, default_message: str = "") -> Callable[[float], None]:
        def report(fraction: float) -> None:
            msg = default_message
            if default_message and fraction > 0:
                msg = f"{default_message} ({int(fraction * 100)}%)"
            self._emit(stage, max(0.0, min(1.0, fraction)), message=msg)

        return report

    # -- entry point -------------------------------------------------------

    async def run(self) -> list[Clip]:
        """Run the full pipeline and return the exported clips."""
        store.update_job(self.job.id, status="running", started_at=utcnow(), error=None)

        # Apply campaign export preferences if configured
        campaign_data = self.job.settings.get("campaign")
        if campaign_data:
            campaign = CampaignBrief.model_validate(campaign_data)
            if campaign.aspect_ratio:
                self.settings.export.ratio = campaign.aspect_ratio
            if campaign.caption_preset:
                self.settings.export.caption_style = campaign.caption_preset

        try:
            audio = self._stage_prepare()
            transcript = self._stage_transcribe(audio)
            silences = self._load_or_detect_silences(audio)
            clips = await self._stage_highlights(transcript, silences)
            if not clips:
                log.info("Job %s completed with 0 production-ready clips.", self.job.id)
                store.update_job(self.job.id, status="done", progress=1.0, finished_at=utcnow())
                return []
            crop_paths = self._stage_reframe(clips, transcript)
            reframe_approved = [c for c in clips if c.id in crop_paths]
            if not reframe_approved:
                log.info("Job %s completed with 0 approved visual compositions.", self.job.id)
                store.update_job(self.job.id, status="done", progress=1.0, finished_at=utcnow())
                return []

            # Step 18: Retention Editing & Final Quality Gate
            final_clips, final_crop_paths = self._stage_retention(
                reframe_approved, transcript, crop_paths, silences
            )
            if not final_clips:
                log.info("Job %s completed with 0 clips passing Final Quality Gate.", self.job.id)
                store.update_job(self.job.id, status="done", progress=1.0, finished_at=utcnow())
                return []

            ass_paths = self._stage_captions(final_clips, transcript, final_crop_paths)
            source_path = Path(self.source.path)
            audio_paths = self._stage_audio_mix(final_clips, source_path)
            self._stage_export(final_clips, transcript, final_crop_paths, ass_paths=ass_paths, audio_paths=audio_paths)
        except JobCancelled:
            store.update_job(self.job.id, status="cancelled", finished_at=utcnow(), progress=0.0)
            raise
        except Exception as exc:
            log.exception("Job %s failed.", self.job.id)
            store.update_job(self.job.id, status="failed", error=str(exc), finished_at=utcnow())
            raise

        store.update_job(self.job.id, status="done", progress=1.0, finished_at=utcnow())
        return final_clips

    # -- stages ------------------------------------------------------------

    def _stage_acquire(self) -> Path:
        self._check_cancelled()
        if self.source.path and Path(self.source.path).is_file():
            return Path(self.source.path)

        if not self.source.url:
            raise PipelineError("Source media file does not exist and no URL was provided.", stage=Stage.PREPARE)

        from .source_acquisition import JobContext, get_default_registry

        registry = get_default_registry(self.settings.ingest)
        target_dir = paths.source_media_dir(self.source.id)
        target_dir.mkdir(parents=True, exist_ok=True)

        job_ctx = JobContext(
            job_id=self.job.id,
            source_id=self.source.id,
            settings=self.settings.ingest,
        )

        result = registry.acquire(
            source_url=self.source.url,
            target_dir=target_dir,
            job_context=job_ctx,
        )

        media_info = result.media_info
        duration = result.duration
        w = media_info.width if media_info else None
        h = media_info.height if media_info else None
        fps = media_info.fps if media_info else None
        has_audio = media_info.has_audio if media_info else True
        has_video = media_info.has_video if media_info else True

        updated_source = store.update_source(
            self.source.id,
            path=str(result.local_media_path),
            filename=result.local_media_path.name,
            duration_s=duration,
            width=w,
            height=h,
            fps=fps,
            has_audio=has_audio,
            has_video=has_video,
        )
        if updated_source:
            self.source = updated_source
        else:
            self.source.path = str(result.local_media_path)
            self.source.duration_s = duration

        if "provenance" in result.provider_metadata:
            self.job.settings["acquisition_telemetry"] = result.provider_metadata["provenance"]
            store.update_job(self.job.id, settings=self.job.settings)

        return result.local_media_path

    def _stage_prepare(self) -> Path:
        stage = Stage.PREPARE
        self._check_cancelled()
        source_path = self._stage_acquire()

        audio_valid = False
        if self.workspace.audio.exists() and self.workspace.audio.stat().st_size > 0:
            try:
                from .ffmpeg import probe

                info = probe(self.workspace.audio)
                if info.has_audio and info.duration_s > 0:
                    audio_valid = True
                    log.info("Reusing validated audio for job %s (%.1fs).", self.job.id, info.duration_s)
            except Exception as exc:
                log.warning("Existing audio file for job %s is invalid (%s), re-extracting.", self.job.id, exc)
                try:
                    self.workspace.audio.unlink(missing_ok=True)
                except Exception:
                    pass

        if not audio_valid:
            self._emit(stage, 0.0, "Extracting audio")
            prepare.extract_audio(
                source_path,
                self.workspace.audio,
                duration_s=self.source.duration_s,
                on_progress=self._stage_progress(stage, "Extracting audio"),
            )

        has_thumbnails = (
            self.workspace.thumbnails.is_dir()
            and any(self.workspace.thumbnails.glob("thumb_*.jpg"))
        )
        if self.source.has_video and not has_thumbnails:
            self._emit(stage, 0.85, "Generating thumbnails")
            prepare.generate_thumbnails(source_path, self.workspace.thumbnails)

        self._finish_stage(stage)
        return self.workspace.audio

    def _stage_transcribe(self, audio: Path) -> Transcript:
        stage = Stage.TRANSCRIBE
        self._check_cancelled()

        if self.workspace.transcript.exists():
            log.info("Reusing existing transcript for job %s.", self.job.id)
            transcript = Transcript.load(self.workspace.transcript)
            self._finish_stage(stage)
            return transcript

        def on_status(message: str, fraction: float = 0.0) -> None:
            self._emit(stage, max(0.0, min(1.0, fraction)), message=message)

        self._emit(stage, 0.0, "Loading speech model...")
        transcript = transcribe.transcribe(
            audio,
            self.settings.whisper,
            duration_s=self.source.duration_s,
            on_progress=self._stage_progress(stage, "Transcribing speech"),
            on_status=on_status,
            cancelled=self._is_cancelled,
        )

        if self.settings.whisper.diarization:
            from ..config import HF_TOKEN_KEY, get_secret

            self._emit(stage, 0.95, "Identifying speakers")
            transcribe.diarize(audio, transcript, hf_token=get_secret(HF_TOKEN_KEY, self.settings))

        transcript.save(self.workspace.transcript)
        store.upsert_transcript(
            TranscriptRow(
                job_id=self.job.id,
                json_path=str(self.workspace.transcript),
                language=transcript.language,
                model=transcript.model,
                has_diarization=transcript.has_diarization,
                word_count=len(transcript.words),
                source=transcript.source,
            )
        )

        self._finish_stage(stage)
        return transcript

    def _load_or_detect_silences(self, audio: Path) -> list[Silence]:
        if self.workspace.silences.exists():
            raw = json.loads(self.workspace.silences.read_text(encoding="utf-8"))
            return [Silence(**item) for item in raw]

        silences = prepare.detect_silences(audio)
        self.workspace.silences.write_text(
            json.dumps([{"start": s.start, "end": s.end} for s in silences]),
            encoding="utf-8",
        )
        return silences

    async def _stage_highlights(
        self, transcript: Transcript, silences: list[Silence]
    ) -> list[Clip]:
        stage = Stage.HIGHLIGHTS
        self._check_cancelled()

        existing = store.list_clips(self.job.id)
        if existing:
            log.info("Reusing %d existing clips for job %s.", len(existing), self.job.id)
            self._finish_stage(stage)
            return existing

        # Check for campaign specification or brief in job settings / database
        campaign_spec: CampaignSpecification | None = None
        spec_row = store.get_campaign_spec_for_job(self.job.id)
        if spec_row:
            try:
                campaign_spec = CampaignSpecification.from_dict(spec_row.spec)
            except Exception as e:
                log.warning("Failed parsing stored campaign spec for job %s: %s", self.job.id, e)

        if not campaign_spec and "campaign_spec" in self.job.settings:
            try:
                campaign_spec = CampaignSpecification.from_dict(self.job.settings["campaign_spec"])
            except Exception as e:
                log.warning("Failed parsing campaign_spec from settings for job %s: %s", self.job.id, e)

        campaign_data = self.job.settings.get("campaign")
        campaign = CampaignBrief.model_validate(campaign_data) if campaign_data else None
        if not campaign and campaign_spec:
            campaign = campaign_spec.to_campaign_brief()

        # Step 15: Autonomous Candidate Discovery & Contradiction-Aware Scoring
        discovery_engine = CandidateDiscoveryEngine(
            campaign_spec=campaign_spec,
            campaign_brief=campaign,
            job_settings=self.job.settings,
        )

        def on_discovery_progress(substage: str, frac: float, meta: dict[str, Any]) -> None:
            if meta:
                if substage == "DISCOVERING_CANDIDATES":
                    msg = f"Discovering candidates ({meta.get('discovered', 0)} found)"
                elif substage == "SCORING_CANDIDATES":
                    msg = f"Scoring candidates ({meta.get('scored', 0)}/{meta.get('discovered', 0)})"
                elif substage == "FILTERING_CANDIDATES":
                    msg = f"Filtering candidates ({meta.get('scored', 0)} evaluated)"
                elif substage == "SELECTING_TOP_CANDIDATES":
                    msg = f"Selecting top {meta.get('target', 3)} viral candidates"
                elif substage == "CANDIDATES_READY":
                    msg = f"Candidates ready: {meta.get('selected_count', 0)} selected ({meta.get('rejected_count', 0)} rejected)"
                else:
                    msg = substage
            else:
                msg = substage
            self._emit(stage, frac, msg)

        selected_candidates, all_candidates, telemetry = discovery_engine.run(
            transcript=transcript,
            job_id=self.job.id,
            silences=silences,
            on_progress=on_discovery_progress,
        )

        # Store discovery telemetry in job settings
        self.job.settings["candidate_telemetry"] = telemetry
        store.update_job(self.job.id, settings=self.job.settings)

        # Persist ALL candidates in SQLite
        if all_candidates:
            store.replace_clip_candidates(self.job.id, all_candidates)

        if selected_candidates:
            # Step 16: Campaign-Aware Clip Assembly, Smart Boundaries & Quality Gate
            assembly_engine = ClipAssemblyEngine(
                campaign_spec=campaign_spec,
                campaign_brief=campaign,
                job_settings=self.job.settings,
            )

            def on_assembly_progress(substage: str, frac: float, meta: dict[str, Any]) -> None:
                if meta:
                    curr = meta.get("current", 1)
                    total = meta.get("total", len(selected_candidates))
                    if substage == "OPTIMIZING_BOUNDARIES":
                        msg = f"Optimizing boundaries ({curr}/{total})"
                    elif substage == "ANALYZING_HOOK":
                        msg = f"Analyzing hook ({curr}/{total})"
                    elif substage == "VERIFYING_PAYOFF":
                        msg = f"Verifying climax & payoff ({curr}/{total})"
                    elif substage == "VERIFYING_CTA":
                        msg = f"Verifying CTA ({curr}/{total})"
                    elif substage == "RUNNING_QUALITY_GATE":
                        msg = f"Running quality gate ({curr}/{total})"
                    elif substage == "CLIPS_APPROVED":
                        msg = f"Clip {curr}/{total} approved"
                    elif substage == "CLIPS_REJECTED":
                        msg = f"Clip {curr}/{total} rejected"
                    else:
                        msg = substage
                else:
                    msg = substage
                self._emit(stage, frac, msg)

            approved_specs, all_specs, assembly_telemetry = assembly_engine.assemble(
                candidates=selected_candidates,
                transcript=transcript,
                job_id=self.job.id,
                source_id=self.source.id,
                silences=silences,
                on_progress=on_assembly_progress,
            )

            # Store assembly telemetry in job settings
            self.job.settings["assembly_telemetry"] = assembly_telemetry
            store.update_job(self.job.id, settings=self.job.settings)

            # Persist ALL clip specifications in SQLite
            if all_specs:
                store.replace_clip_specifications(self.job.id, all_specs)

            if approved_specs:
                clips = specifications_to_clips(approved_specs, selected_candidates)
                store.replace_clips(self.job.id, clips)

                # Persist evaluations for UI/database backward compatibility
                if campaign is not None:
                    evaluator = CampaignEvaluator(campaign)
                    for clip, spec in zip(clips, approved_specs):
                        words = transcript.slice(clip.start_word, clip.end_word)
                        cand = next((c for c in selected_candidates if c.id == spec.candidate_id), None)
                        ev = evaluator.evaluate_candidate(
                            candidate_id=clip.id,
                            start_s=clip.start_s,
                            end_s=clip.end_s,
                            words=words,
                            base_viral_score=float(cand.score if cand else spec.quality_score),
                            silences=silences,
                            clip_title=clip.title,
                            clip_hook=clip.hook,
                            clip_reason=clip.reason,
                        )
                        store.create_campaign_evaluation(
                            CampaignEvaluationRow(
                                clip_id=ev.clip_id,
                                campaign_id=ev.campaign_id,
                                approved=spec.is_approved,
                                final_score=round(spec.quality_score / 10.0, 2),
                                hook_score=round(float(spec.boundary_adjustments.get("hook_score", 5.0)), 2),
                                cta_score=round(float(spec.boundary_adjustments.get("cta_score", 0.0) if "cta_score" in spec.boundary_adjustments else (10.0 if spec.cta_start else 0.0)), 2),
                                viral_score=round(spec.quality_score / 10.0, 2),
                                density_score=round(float(spec.telemetry.get("metrics", {}).get("words_per_sec", 2.0)) * 3.5, 2),
                                hard_failures=spec.rejection_reasons,
                                soft_warnings=spec.warnings,
                                rule_results=ev.rule_results,
                            )
                        )
                self._finish_stage(stage)
                return clips
            else:
                log.warning("All candidate clips were rejected by PreRenderQualityGate.")
                store.replace_clips(self.job.id, [])
                self._finish_stage(stage)
                return []

        # Fallback to highlights.detect if discovery yielded no candidates
        log.warning("Autonomous discovery yielded no candidates, falling back to highlight detection.")
        provider_name = self.job.provider or self.settings.active_provider
        provider = build_provider(provider_name, self.settings)
        config = detection_config(self.settings)

        if campaign is not None:
            config.min_duration_s = campaign.minimum_duration
            config.max_duration_s = campaign.maximum_duration
            config.max_clips = max(config.max_clips, campaign.maximum_candidates * 2)

        total_duration = transcript.words[-1].end if transcript.words else 0.0
        if total_duration > 0 and config.min_duration_s >= total_duration:
            config.min_duration_s = max(3.0, total_duration * 0.4)
            config.max_duration_s = max(config.min_duration_s + 1.0, total_duration)

        self._emit(stage, 0.0, f"Finding highlights with {provider.name}")
        clips = await highlights.detect(
            transcript,
            provider,
            config,
            job_id=self.job.id,
            silences=silences,
            campaign=campaign,
            on_progress=self._stage_progress(stage),
        )

        if campaign is not None:
            self._emit(stage, 0.9, f"Evaluating {len(clips)} candidate(s) against campaign '{campaign.name}'")
            evaluator = CampaignEvaluator(campaign)
            evaluations = []
            for clip in clips:
                words = transcript.slice(clip.start_word, clip.end_word)
                speakers = {w.speaker for w in words if w.speaker}
                ev = evaluator.evaluate_candidate(
                    candidate_id=clip.id,
                    start_s=clip.start_s,
                    end_s=clip.end_s,
                    words=words,
                    base_viral_score=float(clip.score),
                    silences=silences,
                    clip_title=clip.title,
                    clip_hook=clip.hook,
                    clip_reason=clip.reason,
                    speaker_count=len(speakers) if speakers else None,
                )
                evaluations.append(ev)

            clips, ranked_evals = rank_and_filter_candidates(clips, evaluations, campaign)
            if not clips:
                from .highlights import HighlightError
                rejection_reasons = "; ".join(f for ev in evaluations for f in ev.hard_failures)
                raise HighlightError(f"All candidates failed campaign rules: {rejection_reasons}")

            store.replace_clips(self.job.id, clips)
            for ev in ranked_evals:
                store.create_campaign_evaluation(
                    CampaignEvaluationRow(
                        clip_id=ev.clip_id,
                        campaign_id=ev.campaign_id,
                        approved=ev.approved,
                        final_score=ev.final_score,
                        hook_score=ev.hook_score,
                        cta_score=ev.cta_score,
                        viral_score=ev.base_viral_score,
                        density_score=ev.density_score,
                        hard_failures=ev.hard_failures,
                        soft_warnings=ev.soft_warnings,
                        rule_results=ev.rule_results,
                    )
                )
        else:
            store.replace_clips(self.job.id, clips)

        self._finish_stage(stage)
        return clips

    def _stage_reframe(self, clips: list[Clip], transcript: Transcript) -> dict[str, CropPath]:
        stage = Stage.REFRAME
        self._check_cancelled()
        source_path = Path(self.source.path)

        crop_paths: dict[str, CropPath] = {}

        if not self.source.has_video:
            # Audio-only sources render as captions on a solid background, so
            # there is nothing to reframe.
            self._finish_stage(stage)
            return crop_paths

        # Check for campaign specification
        campaign_spec: CampaignSpecification | None = None
        spec_row = store.get_campaign_spec_for_job(self.job.id)
        if spec_row:
            try:
                campaign_spec = CampaignSpecification.from_dict(spec_row.spec)
            except Exception as e:
                log.warning("Failed parsing campaign spec in reframe stage: %s", e)
        if not campaign_spec and "campaign_spec" in self.job.settings:
            try:
                campaign_spec = CampaignSpecification.from_dict(self.job.settings["campaign_spec"])
            except Exception as e:
                log.warning("Failed parsing campaign_spec setting: %s", e)

        target_ratio = self.settings.export.ratio or "9:16"
        engine = VisualCompositionEngine(
            campaign_spec=campaign_spec,
            target_ratio=target_ratio,
        )

        composition_records: list[VisualCompositionRecord] = []
        total_clips = len(clips)

        for index, clip in enumerate(clips):
            self._check_cancelled()
            cached = self.workspace.crop_path(clip.id)

            def on_comp_progress(substage: str, frac: float, meta: dict[str, Any]) -> None:
                step_frac = (index + frac) / max(1, total_clips)
                self._emit(stage, step_frac, f"Reframing clip {index + 1}/{total_clips} ({substage})")

            if cached.exists():
                crop_path = CropPath.load(cached)
                existing = store.get_visual_composition(clip.id)
                if existing:
                    comp_rec = existing
                else:
                    _, comp_rec = engine.compose(
                        video_path=source_path,
                        clip=clip,
                        transcript=transcript,
                        on_progress=on_comp_progress,
                    )
            else:
                crop_path, comp_rec = engine.compose(
                    video_path=source_path,
                    clip=clip,
                    transcript=transcript,
                    on_progress=on_comp_progress,
                )

            composition_records.append(comp_rec)

            if comp_rec.quality_status != "VISUAL_REJECT":
                crop_paths[clip.id] = crop_path
                if not cached.exists():
                    crop_path.save(cached)
                log.info(
                    "Clip %s visual composition approved (%s, score=%.1f, strategy=%s)",
                    clip.id, comp_rec.quality_status, comp_rec.quality_score, comp_rec.crop_strategy,
                )
            else:
                log.warning(
                    "Clip %s visual composition rejected: %s",
                    clip.id, "; ".join(comp_rec.rejection_reasons),
                )

            self._emit(stage, (index + 1) / max(1, total_clips), f"Reframing clip {index + 1}/{total_clips} complete")

        # Persist compositions in SQLite
        if composition_records:
            store.replace_visual_compositions(self.job.id, composition_records)

        # Update telemetry in job.settings
        approved_count = len(crop_paths)
        rejected_count = total_clips - approved_count
        reframe_telemetry = {
            "total_evaluated": total_clips,
            "approved": approved_count,
            "rejected": rejected_count,
            "compositions": [r.to_dict() for r in composition_records],
        }
        self.job.settings["visual_composition_telemetry"] = reframe_telemetry
        store.update_job(self.job.id, settings=self.job.settings)

        self._finish_stage(stage)
        return crop_paths

    def _stage_retention(
        self,
        clips: list[Clip],
        transcript: Transcript,
        crop_paths: dict[str, CropPath],
        silences: list[Silence] | None = None,
    ) -> tuple[list[Clip], dict[str, CropPath]]:
        stage = Stage.RETENTION
        self._check_cancelled()
        source_path = Path(self.source.path)

        # Check for campaign specification
        campaign_spec: CampaignSpecification | None = None
        spec_row = store.get_campaign_spec_for_job(self.job.id)
        if spec_row:
            try:
                campaign_spec = CampaignSpecification.from_dict(spec_row.spec)
            except Exception as e:
                log.warning("Failed parsing campaign spec in retention stage: %s", e)
        if not campaign_spec and "campaign_spec" in self.job.settings:
            try:
                campaign_spec = CampaignSpecification.from_dict(self.job.settings["campaign_spec"])
            except Exception as e:
                log.warning("Failed parsing campaign_spec setting: %s", e)

        engine = RetentionEditingEngine(campaign_spec=campaign_spec)

        def on_retention_progress(substage: str, frac: float, meta: dict[str, Any]) -> None:
            self._emit(stage, frac, f"Optimizing retention ({substage})")

        final_clips, final_crop_paths, records, telemetry = engine.optimize_and_rank(
            clips=clips,
            transcript=transcript,
            crop_paths=crop_paths,
            source_path=source_path,
            job_id=self.job.id,
            silences=silences,
            on_progress=on_retention_progress,
        )

        # Persist retention optimizations in SQLite
        if records:
            store.replace_retention_optimizations(self.job.id, records)

        # Update telemetry in job.settings
        self.job.settings["retention_telemetry"] = telemetry
        store.update_job(self.job.id, settings=self.job.settings)

        # Update clips in store to reflect tightened start/end timings and new ranks
        if final_clips:
            store.replace_clips(self.job.id, final_clips)

        self._finish_stage(stage)
        return final_clips, final_crop_paths

    def _stage_captions(
        self,
        clips: list[Clip],
        transcript: Transcript,
        crop_paths: dict[str, CropPath] | None = None,
    ) -> dict[str, Path]:
        stage = Stage.CAPTIONS
        self._check_cancelled()

        # Resolve operator-selected caption style (Priority: job settings -> export settings -> campaign spec -> default)
        selected_style = (
            self.job.settings.get("caption_style")
            or (self.job.settings.get("export") or {}).get("caption_style")
            or getattr(self.settings.export, "caption_style", None)
            or captions.DEFAULT_STYLE
        )
        style = captions.resolve_style(selected_style)

        campaign_spec: CampaignSpecification | None = None
        spec_row = store.get_campaign_spec_for_job(self.job.id)
        if spec_row:
            try:
                campaign_spec = CampaignSpecification.from_dict(spec_row.spec)
            except Exception as e:
                log.warning("Could not parse campaign spec for captions: %s", e)

        # Retrieve composition records for visual safety / face avoidance
        comp_records = store.list_visual_compositions(self.job.id)
        comp_by_clip = {r.clip_id: r for r in comp_records}

        engine = captions.CaptionEngine(campaign_spec=campaign_spec)
        ratio = self.settings.export.ratio
        out_w, out_h = export.ratio_dimensions(ratio)

        caption_records: list[CaptionOptimizationRecord] = []
        ass_paths: dict[str, Path] = {}
        total = len(clips)

        for index, clip in enumerate(clips):
            self._check_cancelled()
            words = transcript.slice(clip.start_word, clip.end_word)
            crop_path = (crop_paths or {}).get(clip.id)
            comp_rec = comp_by_clip.get(clip.id)

            def on_progress(p: float) -> None:
                self._emit(stage, (index + p) / max(1, total), f"Generating captions for clip {index + 1}/{total}")

            ssa_file, opt_record = engine.generate_captions(
                clip=clip,
                words=words,
                style_key=style.key,
                crop_path=crop_path,
                composition_record=comp_rec,
                width=out_w,
                height=out_h,
            )
            caption_records.append(opt_record)

            # Pre-render ASS file to clip-specific workspace
            clip_work_dir = self.workspace.captions_dir / clip.id
            clip_work_dir.mkdir(parents=True, exist_ok=True)
            ass_path = clip_work_dir / "captions.ass"
            ssa_file.save(str(ass_path))
            ass_paths[clip.id] = ass_path

            # Also generate .srt sidecar
            srt_path = clip_work_dir / "captions.srt"
            captions.write_srt(srt_path, words, time_offset_s=clip.start_s)

            log.info(
                "Clip %s caption optimization complete: status=%s, segments=%d, style=%s",
                clip.id,
                opt_record.quality_status,
                len(opt_record.caption_segments),
                opt_record.style_label,
            )
            self._emit(stage, (index + 1) / max(1, total), f"Captions ready for clip {index + 1}/{total}")

        # Persist caption records in SQLite
        if caption_records:
            store.replace_caption_optimizations(self.job.id, caption_records)

        # Update telemetry in job settings
        approved_count = sum(1 for r in caption_records if r.quality_status != "CAPTION_REJECT")
        telemetry = {
            "selected_style": style.key,
            "style_label": style.label,
            "total_evaluated": total,
            "approved": approved_count,
            "rejected": total - approved_count,
            "records": [r.to_dict() for r in caption_records],
        }
        self.job.settings["caption_telemetry"] = telemetry
        store.update_job(self.job.id, settings=self.job.settings)

        self._finish_stage(stage)
        return ass_paths

    def _stage_audio_mix(
        self,
        clips: list[Clip],
        source_path: Path,
    ) -> dict[str, Path]:
        stage = Stage.AUDIO_MIX
        self._check_cancelled()

        from .audio_mix import BGMMixingEngine

        bgm_enabled = bool(self.job.settings.get("bgm_enabled", False))
        bgm_asset_id = self.job.settings.get("bgm_asset_id")
        bgm_asset = None

        if bgm_enabled:
            if bgm_asset_id:
                bgm_asset = store.get_bgm_asset(bgm_asset_id)
            if not bgm_asset or not Path(bgm_asset.file_path).is_file():
                asset_name = self.job.settings.get("bgm_asset_name", bgm_asset_id or "Unknown")
                raise RuntimeError(
                    f"BGM audio dependency failed: asset '{asset_name}' ({bgm_asset_id}) was not found on disk."
                )

        engine = BGMMixingEngine()
        audio_paths: dict[str, Path] = {}
        mix_records: list[BGMMixRecord] = []
        total = len(clips)

        self.workspace.audio_mix_dir.mkdir(parents=True, exist_ok=True)

        for index, clip in enumerate(clips):
            self._check_cancelled()
            clip_dur = clip.end_s - clip.start_s
            clip_dir = self.workspace.audio_mix_dir / clip.id
            clip_dir.mkdir(parents=True, exist_ok=True)
            clip_audio_out = clip_dir / "mixed_audio.m4a"

            self._emit(stage, index / max(1, total), f"Mixing background audio for clip {index + 1}/{total}")

            record = engine.mix_clip(
                clip=clip,
                speech_input_path=source_path,
                speech_start_offset_s=clip.start_s,
                duration_s=clip_dur,
                bgm_asset=bgm_asset,
                output_path=clip_audio_out,
            )
            mix_records.append(record)
            if clip_audio_out.is_file():
                audio_paths[clip.id] = clip_audio_out

            log.info(
                "Clip %s audio mix complete: status=%s, lufs=%.1f, peak=%.1f dB, applied=%s",
                clip.id, record.quality_status, record.integrated_lufs, record.true_peak_db, record.bgm_applied
            )
            self._emit(stage, (index + 1) / max(1, total), f"Audio ready for clip {index + 1}/{total}")

        if mix_records:
            store.replace_bgm_mixes(self.job.id, mix_records)

        approved_count = sum(1 for r in mix_records if r.is_approved)
        telemetry = {
            "bgm_enabled": bgm_enabled,
            "bgm_asset_id": bgm_asset_id,
            "bgm_asset_name": bgm_asset.name if bgm_asset else "",
            "total_clips": total,
            "approved_clips": approved_count,
            "records": [r.to_dict() for r in mix_records],
        }
        self.job.settings["bgm_mix_telemetry"] = telemetry
        store.update_job(self.job.id, settings=self.job.settings)

        self._finish_stage(stage)
        return audio_paths

    def _stage_export(
        self,
        clips: list[Clip],
        transcript: Transcript,
        crop_paths: dict[str, CropPath],
        ass_paths: dict[str, Path] | None = None,
        audio_paths: dict[str, Path] | None = None,
    ) -> None:
        stage = Stage.EXPORT
        self._check_cancelled()

        # Step 20: Validate BGM dependency if enabled and record telemetry
        bgm_enabled = bool(self.job.settings.get("bgm_enabled", False))
        bgm_asset_id = self.job.settings.get("bgm_asset_id")
        bgm_asset_name = self.job.settings.get("bgm_asset_name")
        bgm_asset_path = self.job.settings.get("bgm_asset_path")

        if bgm_enabled:
            if not bgm_asset_path or not Path(bgm_asset_path).exists():
                raise RuntimeError(
                    f"BGM audio dependency failed: asset '{bgm_asset_name}' ({bgm_asset_id}) was not found on disk at {bgm_asset_path}."
                )

        self.job.settings["bgm_telemetry"] = {
            "enabled": bgm_enabled,
            "asset_id": bgm_asset_id,
            "asset_name": bgm_asset_name,
        }
        store.update_job(self.job.id, settings=self.job.settings)

        selected_style = (
            self.job.settings.get("caption_style")
            or (self.job.settings.get("export") or {}).get("caption_style")
            or getattr(self.settings.export, "caption_style", None)
            or captions.DEFAULT_STYLE
        )
        style = captions.resolve_style(selected_style)
        ratio = self.settings.export.ratio
        source_path = Path(self.source.path)
        exports_base = paths.exports_dir()
        exports_base.mkdir(parents=True, exist_ok=True)

        from .final_render import FinalRenderConfig, FinalRenderEngine

        render_engine = FinalRenderEngine(config=FinalRenderConfig(ratio=ratio))
        final_render_records: list[FinalRenderRecord] = []

        for index, clip in enumerate(clips):
            self._check_cancelled()

            crop_path = crop_paths.get(clip.id) or self._fallback_crop_path(clip, ratio)
            clip_ass = (ass_paths or {}).get(clip.id)
            clip_audio = (audio_paths or {}).get(clip.id)

            def clip_progress(fraction: float, i: int = index) -> None:
                self._emit(stage, (i + fraction) / len(clips), f"Rendering & validating clip {i + 1}/{len(clips)}")

            final_path, render_rec = render_engine.render_and_package(
                clip=clip,
                source_media_path=source_path,
                crop_path=crop_path,
                caption_style_key=style.key,
                ass_path=clip_ass,
                audio_path=clip_audio,
                bgm_asset_id=bgm_asset_id if bgm_enabled else None,
                bgm_asset_name=bgm_asset_name if bgm_enabled else "",
                exports_base_dir=exports_base,
                render_work_dir=self.workspace.captions_dir,
                on_progress=clip_progress,
                cancelled=self._is_cancelled,
            )
            final_render_records.append(render_rec)

            if render_rec.is_approved:
                existing_exports = store.list_exports(clip.id)
                if not any(e.path == str(final_path) for e in existing_exports):
                    store.create_export(
                        Export(
                            id=new_id(),
                            clip_id=clip.id,
                            path=str(final_path),
                            ratio=ratio,
                            style=style.key,
                            size_bytes=final_path.stat().st_size if final_path.is_file() else 0,
                        )
                    )
                store.update_clip(clip.id, status="exported")
                log.info(
                    "Clip %s final render approved (status=%s, score=%.1f) -> %s",
                    clip.id, render_rec.quality_status, render_rec.quality_score, final_path
                )
            else:
                log.warning(
                    "Clip %s final render rejected by quality gate (%s); not marked as exported.",
                    clip.id, render_rec.error_details
                )
                store.update_clip(clip.id, status="candidate")

        if final_render_records:
            store.replace_final_renders(self.job.id, final_render_records)

        approved_renders = sum(1 for r in final_render_records if r.is_approved)
        render_telemetry = {
            "total_clips": len(clips),
            "rendered": len(final_render_records),
            "approved": approved_renders,
            "warned": sum(1 for r in final_render_records if r.quality_status == "RENDER_WARN"),
            "rejected": sum(1 for r in final_render_records if r.quality_status == "RENDER_REJECT"),
            "failed": sum(1 for r in final_render_records if r.render_status == "failed"),
            "avg_quality_score": round(
                sum(r.quality_score for r in final_render_records) / max(1, len(final_render_records)), 1
            ),
            "records": [r.to_dict() for r in final_render_records],
        }
        self.job.settings["render_telemetry"] = render_telemetry
        store.update_job(self.job.id, settings=self.job.settings)

        # Step 23: Generate Campaign-Aware SEO & Metadata for Approved Clips
        from autoclip.seo import SEOEngine
        from autoclip.campaign.models_intelligence import CampaignSpecification

        campaign_spec = None
        if getattr(self.job, "campaign_spec_id", None):
            spec_rec = store.get_campaign_spec(self.job.campaign_spec_id)
            if spec_rec:
                campaign_spec = CampaignSpecification.from_dict(spec_rec.spec)
        if campaign_spec is None:
            spec_rec = store.get_campaign_spec_for_job(self.job.id)
            if spec_rec:
                campaign_spec = CampaignSpecification.from_dict(spec_rec.spec)
        if campaign_spec is None and "campaign_spec" in self.job.settings and isinstance(self.job.settings["campaign_spec"], dict):
            campaign_spec = CampaignSpecification.from_dict(self.job.settings["campaign_spec"])
        if campaign_spec is None:
            guideline = store.get_guideline_for_job(self.job.id)
            if guideline and guideline.parsed_brief:
                campaign_spec = CampaignSpecification.from_campaign_brief(guideline.parsed_brief, filename=guideline.filename)

        seo_engine = SEOEngine.from_campaign_spec(campaign_spec)
        seo_records = []
        for clip in clips:
            matching_render = next((r for r in final_render_records if r.clip_id == clip.id and r.is_approved), None)
            if matching_render is not None:
                clip_words = transcript.slice(clip.start_word, clip.end_word)
                slice_text = " ".join(w.word for w in clip_words)
                meta_rec = seo_engine.generate_for_clip(clip, transcript_text=slice_text)
                seo_records.append(meta_rec)

        if seo_records:
            store.replace_clip_metadata(self.job.id, seo_records)
            self.job.settings["seo_telemetry"] = {
                "total_metadata": len(seo_records),
                "pass_count": sum(1 for m in seo_records if m.compliance_status == "SEO_PASS"),
                "warn_count": sum(1 for m in seo_records if m.compliance_status == "SEO_WARN"),
                "reject_count": sum(1 for m in seo_records if m.compliance_status == "SEO_REJECT"),
                "avg_compliance_score": round(sum(m.compliance_score for m in seo_records) / len(seo_records), 1),
            }
            store.update_job(self.job.id, settings=self.job.settings)

        self._finish_stage(stage)

    def _fallback_crop_path(self, clip: Clip, ratio: str) -> CropPath:
        """Centre crop for sources with no reframe data (audio-only, or a failure)."""
        from .reframe.croppath import centre_crop

        width, height = export.ratio_dimensions(ratio)
        info = ffmpeg.probe(Path(self.source.path))
        return centre_crop(
            info.width or width,
            info.height or height,
            clip.end_s - clip.start_s,
            aspect_w=9 if ratio == "9:16" else (1 if ratio == "1:1" else 16),
            aspect_h=16 if ratio == "9:16" else (1 if ratio == "1:1" else 9),
        )


async def run_job(
    job_id: str,
    *,
    settings: Settings | None = None,
    on_progress: ProgressHandler | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> list[Clip]:
    """Load a job and run it to completion."""
    job = store.get_job(job_id)
    if job is None:
        raise PipelineError(f"Job {job_id} not found.", stage=Stage.PREPARE)

    source = store.get_source(job.source_id)
    if source is None:
        raise PipelineError(f"Source {job.source_id} not found.", stage=Stage.PREPARE)

    runner = PipelineRunner(
        job, source, settings=settings, on_progress=on_progress, is_cancelled=is_cancelled
    )
    # The pipeline is mostly blocking work (ffmpeg, Whisper, MediaPipe) with one
    # async stage. Running it in a worker thread keeps the web server's event
    # loop responsive while a job is going.
    return await asyncio.get_running_loop().run_in_executor(None, _run_sync, runner)


def _run_sync(runner: PipelineRunner) -> list[Clip]:
    return asyncio.run(runner.run())
