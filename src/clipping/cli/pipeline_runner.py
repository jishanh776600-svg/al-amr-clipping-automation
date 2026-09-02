"""Command-line remote pipeline execution entrypoint with real media engine wiring,
checkpoint resumption, lease locking, and cooperative emergency stop."""

import argparse
import asyncio
import os
import sys
import uuid
from typing import Dict, List, Optional

from pydantic import TypeAdapter

from clipping.approval.gateway import TelegramApprovalGateway
from clipping.approval.repository import ApprovalRepository
from clipping.approval.security import SecurityValidator
from clipping.approval.service import ApprovalService
from clipping.approval.transport import HttpTelegramTransport, MockTelegramTransport
from clipping.config.settings import Settings
from clipping.contracts.clip import ClipSelectionResult, RankedCandidate
from clipping.contracts.director import ReframePlan
from clipping.contracts.perception import (
    ActiveSpeakerSegment,
    FaceTrack,
    SceneCut,
    SourceVideoMetadata,
    SpeakerAttributedTranscript,
    WordTimestamp,
)
from clipping.contracts.qa import QAReport
from clipping.contracts.rendering import RenderOutput
from clipping.control.repository import ControlRepository
from clipping.core.workspace import WorkerScratchWorkspace
from clipping.discovery.engine import ClipDiscoveryEngine
from clipping.ingestion.base import VideoIngestor
from clipping.ingestion.remote import RemoteVideoIngestor
from clipping.ingestion.source import SourceReference
from clipping.logging.logger import get_logger
from clipping.perception.engine import AudioPerceptionEngine
from clipping.qa.engine import QAEngine
from clipping.rendering.engine import RenderOrchestrationEngine
from clipping.state.lease import JobLeaseRepository
from clipping.state.models import JobState, PipelineStage
from clipping.state.remote import RemoteStorageStateRepository
from clipping.storage.base import StorageDriver
from clipping.storage.factory import create_storage_driver
from clipping.vision.base import VirtualCameraDirector
from clipping.vision.director import KalmanVirtualCameraDirector
from clipping.vision.engine import VideoUnderstandingEngine

logger = get_logger("clipping.cli.pipeline_runner")

_scene_list_adapter = TypeAdapter(List[SceneCut])
_face_track_list_adapter = TypeAdapter(List[FaceTrack])
_active_speaker_list_adapter = TypeAdapter(List[ActiveSpeakerSegment])


def get_storage_driver(settings: Settings) -> StorageDriver:
    return create_storage_driver(settings)


def get_ingestor(settings: Settings) -> VideoIngestor:
    return RemoteVideoIngestor()


def get_perception_engine(settings: Settings) -> AudioPerceptionEngine:
    return AudioPerceptionEngine()


def get_vision_engine(settings: Settings) -> VideoUnderstandingEngine:
    return VideoUnderstandingEngine()


def get_discovery_engine(settings: Settings) -> ClipDiscoveryEngine:
    return ClipDiscoveryEngine()


def get_camera_director(settings: Settings) -> VirtualCameraDirector:
    return KalmanVirtualCameraDirector()


def get_render_engine(settings: Settings) -> RenderOrchestrationEngine:
    return RenderOrchestrationEngine()


def get_qa_engine(settings: Settings) -> QAEngine:
    return QAEngine()


def get_approval_gateway(settings: Settings, storage: StorageDriver) -> TelegramApprovalGateway:
    repo = ApprovalRepository(storage_driver=storage)
    bot_token = settings.TELEGRAM_BOT_TOKEN.get_secret_value() if settings.TELEGRAM_BOT_TOKEN else ""
    if bot_token:
        transport = HttpTelegramTransport(bot_token=bot_token)
    else:
        transport = MockTelegramTransport()

    security = SecurityValidator(
        allowed_user_ids=settings.get_allowed_telegram_user_ids(),
        allowed_chat_ids=settings.get_allowed_telegram_chat_ids(),
    )
    service = ApprovalService(repository=repo, transport=transport, security_validator=security)
    return TelegramApprovalGateway(approval_service=service, approval_repository=repo)


async def run_pipeline(
    source_uri: str,
    campaign_id: str = "default_campaign",
    job_id: Optional[str] = None,
    worker_id: Optional[str] = None,
    ingestor: Optional[VideoIngestor] = None,
    perception_engine: Optional[AudioPerceptionEngine] = None,
    vision_engine: Optional[VideoUnderstandingEngine] = None,
    discovery_engine: Optional[ClipDiscoveryEngine] = None,
    camera_director: Optional[VirtualCameraDirector] = None,
    render_engine: Optional[RenderOrchestrationEngine] = None,
    qa_engine: Optional[QAEngine] = None,
    approval_gateway: Optional[TelegramApprovalGateway] = None,
    settings: Optional[Settings] = None,
    storage: Optional[StorageDriver] = None,
) -> int:
    active_settings = settings or Settings()
    active_job_id = job_id or f"job_{uuid.uuid4().hex[:12]}"
    active_worker_id = worker_id or os.getenv("GITHUB_RUN_ID", f"worker_{uuid.uuid4().hex[:8]}")

    logger.info("Initializing remote pipeline execution", job_id=active_job_id, worker_id=active_worker_id, source_uri=source_uri)

    active_storage = storage or get_storage_driver(active_settings)
    state_repo = RemoteStorageStateRepository(storage_driver=active_storage)
    control_repo = ControlRepository(storage_driver=active_storage)
    lease_repo = JobLeaseRepository(storage_driver=active_storage)

    # 0. Global Master Control Pre-flight Check
    if await control_repo.is_emergency_stopped():
        logger.error("Global EMERGENCY STOP is active. Pipeline worker aborting execution.", job_id=active_job_id)
        return 1

    if await control_repo.is_automation_paused():
        logger.warning("Global automation is PAUSED. Pipeline worker skipping execution.", job_id=active_job_id)
        return 0

    # 1. Acquire Distributed Job Lease (Duplicate Prevention)
    acquired, collision_reason = await lease_repo.acquire_lease(
        job_id=active_job_id,
        worker_id=active_worker_id,
        ttl_seconds=3600,
    )
    if not acquired:
        logger.warning("Could not acquire job lease. Another worker is active.", job_id=active_job_id, reason=collision_reason)
        return 0  # Gracefully skip duplicate execution

    try:
        # 2. Initialize / Resume Job State
        job = await state_repo.get_job(active_job_id)
        if not job:
            source_id = f"src_{uuid.uuid4().hex[:8]}"
            job = await state_repo.create_job(
                job_id=active_job_id,
                campaign_id=campaign_id,
                source_video_id=source_id,
                idempotency_key=f"idemp_{active_job_id}",
                metadata={"source_uri": source_uri, "worker_id": active_worker_id},
            )
        else:
            source_id = job.source_video_id

        # Instantiate engines
        active_ingestor = ingestor or get_ingestor(active_settings)
        active_perception_engine = perception_engine or get_perception_engine(active_settings)
        active_vision_engine = vision_engine or get_vision_engine(active_settings)
        active_discovery_engine = discovery_engine or get_discovery_engine(active_settings)
        active_camera_director = camera_director or get_camera_director(active_settings)
        active_render_engine = render_engine or get_render_engine(active_settings)
        active_qa_engine = qa_engine or get_qa_engine(active_settings)
        active_approval_gateway = approval_gateway or get_approval_gateway(active_settings, active_storage)

        with WorkerScratchWorkspace(job_id=active_job_id) as workspace:
            logger.info("Pipeline execution starting", current_stage=job.current_stage.value, source_video_id=source_id)

            # =========================================================================
            # STAGE 01: INGESTION
            # =========================================================================
            if await control_repo.is_emergency_stopped():
                logger.error("Emergency stop triggered before Ingestion; halting", job_id=active_job_id)
                await state_repo.update_job_state(
                    job_id=active_job_id,
                    new_state=JobState.FAILED,
                    new_stage=PipelineStage.INGESTION,
                    reason="Halted: Master Control Emergency Stop before Ingestion",
                )
                return 1

            master_video_key = f"sources/{source_id}/master.mp4"
            source_meta_key = f"sources/{source_id}/metadata.json"
            source_metadata: Optional[SourceVideoMetadata] = None

            if await active_storage.exists(master_video_key) and await active_storage.exists(source_meta_key):
                logger.info("Resuming: source video already ingested, skipping download", source_video_id=source_id)
                meta_bytes = await active_storage.download_bytes(source_meta_key)
                source_metadata = SourceVideoMetadata.model_validate_json(meta_bytes.decode("utf-8"))
            else:
                await state_repo.update_job_state(
                    job_id=active_job_id,
                    new_state=JobState.INGESTING_VIDEO,
                    new_stage=PipelineStage.INGESTION,
                    reason="Ingesting source video via RemoteVideoIngestor",
                )
                source_ref = SourceReference.from_uri(source_uri)
                source_metadata = await active_ingestor.ingest(
                    source_ref=source_ref,
                    storage_driver=active_storage,
                    source_video_id=source_id,
                    force_reingest=False,
                )
                logger.info(
                    "Source video ingested successfully",
                    source_video_id=source_id,
                    duration=source_metadata.duration_seconds,
                    resolution=f"{source_metadata.width}x{source_metadata.height}",
                )

            # =========================================================================
            # STAGE 02: TRANSCRIPTION & PERCEPTION
            # =========================================================================
            if await control_repo.is_emergency_stopped():
                logger.error("Emergency stop triggered before Transcription; halting", job_id=active_job_id)
                await state_repo.update_job_state(
                    job_id=active_job_id,
                    new_state=JobState.FAILED,
                    new_stage=PipelineStage.PERCEPTION,
                    reason="Halted: Master Control Emergency Stop before Perception",
                )
                return 1

            speaker_key = f"sources/{source_id}/speaker_transcript.json"
            p_meta_key = f"sources/{source_id}/perception_metadata.json"
            speaker_transcript: Optional[SpeakerAttributedTranscript] = None

            if await active_storage.exists(speaker_key) and await active_storage.exists(p_meta_key):
                logger.info("Resuming: perception artifacts already exist, skipping inference", source_video_id=source_id)
                s_bytes = await active_storage.download_bytes(speaker_key)
                speaker_transcript = SpeakerAttributedTranscript.model_validate_json(s_bytes.decode("utf-8"))
            else:
                await state_repo.update_job_state(
                    job_id=active_job_id,
                    new_state=JobState.TRANSCRIBING,
                    new_stage=PipelineStage.PERCEPTION,
                    reason="Transcribing speech with faster-whisper and attributing speakers",
                )
                speaker_transcript, _ = await active_perception_engine.process(
                    source_video_id=source_id,
                    storage_driver=active_storage,
                    force_recompute=False,
                )
                logger.info(
                    "Transcription completed successfully",
                    source_video_id=source_id,
                    words_count=len(speaker_transcript.words),
                )

            # =========================================================================
            # STAGE 03: UNDERSTANDING & VISION
            # =========================================================================
            if await control_repo.is_emergency_stopped():
                logger.error("Emergency stop triggered before Understanding; halting", job_id=active_job_id)
                await state_repo.update_job_state(
                    job_id=active_job_id,
                    new_state=JobState.FAILED,
                    new_stage=PipelineStage.DIRECTOR,
                    reason="Halted: Master Control Emergency Stop before Vision",
                )
                return 1

            scenes_key = f"sources/{source_id}/scenes.json"
            tracks_key = f"sources/{source_id}/face_tracks.json"
            active_key = f"sources/{source_id}/active_speaker.json"
            scene_cuts: List[SceneCut] = []
            face_tracks: List[FaceTrack] = []
            active_speakers: List[ActiveSpeakerSegment] = []

            if (
                await active_storage.exists(scenes_key)
                and await active_storage.exists(tracks_key)
                and await active_storage.exists(active_key)
            ):
                logger.info("Resuming: vision artifacts already exist, skipping inference", source_video_id=source_id)
                sc_bytes = await active_storage.download_bytes(scenes_key)
                scene_cuts = _scene_list_adapter.validate_json(sc_bytes.decode("utf-8"))
                ft_bytes = await active_storage.download_bytes(tracks_key)
                face_tracks = _face_track_list_adapter.validate_json(ft_bytes.decode("utf-8"))
                as_bytes = await active_storage.download_bytes(active_key)
                active_speakers = _active_speaker_list_adapter.validate_json(as_bytes.decode("utf-8"))
            else:
                await state_repo.update_job_state(
                    job_id=active_job_id,
                    new_state=JobState.RESOLVING_SPEAKERS,
                    new_stage=PipelineStage.DIRECTOR,
                    reason="Detecting scene cuts, tracking faces, and resolving active speakers",
                )
                scene_cuts, face_tracks, active_speakers = await active_vision_engine.process(
                    source_video_id=source_id,
                    storage_driver=active_storage,
                    speaker_transcript=speaker_transcript,
                    force_recompute=False,
                )
                logger.info(
                    "Vision understanding completed",
                    source_video_id=source_id,
                    scenes_count=len(scene_cuts),
                    tracks_count=len(face_tracks),
                )

            # =========================================================================
            # STAGE 04: DISCOVERY & INTELLIGENCE
            # =========================================================================
            if await control_repo.is_emergency_stopped():
                logger.error("Emergency stop triggered before Discovery; halting", job_id=active_job_id)
                await state_repo.update_job_state(
                    job_id=active_job_id,
                    new_state=JobState.FAILED,
                    new_stage=PipelineStage.INTELLIGENCE,
                    reason="Halted: Master Control Emergency Stop before Discovery",
                )
                return 1

            selected_key = f"sources/{source_id}/selected_clips.json"
            selection_result: Optional[ClipSelectionResult] = None

            if await active_storage.exists(selected_key):
                logger.info("Resuming: clip selection already exists, skipping discovery", source_video_id=source_id)
                sel_bytes = await active_storage.download_bytes(selected_key)
                selection_result = ClipSelectionResult.model_validate_json(sel_bytes.decode("utf-8"))
            else:
                await state_repo.update_job_state(
                    job_id=active_job_id,
                    new_state=JobState.DISCOVERING_CLIPS,
                    new_stage=PipelineStage.INTELLIGENCE,
                    reason="Scoring, deduplicating, and selecting viral clip candidates",
                )
                selection_result = await active_discovery_engine.process(
                    source_video_id=source_id,
                    storage_driver=active_storage,
                    transcript=speaker_transcript,
                    campaign_id=campaign_id,
                    force_recompute=False,
                )

            if not selection_result.selected_clips:
                logger.warning("Clip discovery produced 0 candidate clips.", source_video_id=source_id)
                await state_repo.update_job_state(
                    job_id=active_job_id,
                    new_state=JobState.AWAITING_APPROVAL,
                    new_stage=PipelineStage.APPROVAL,
                    reason="Discovery produced 0 candidates meeting quality threshold",
                    metadata={"selected_clips_count": 0},
                )
                logger.info("Pipeline completed with 0 discovery candidates", job_id=active_job_id)
                return 0

            logger.info(
                "Clip discovery completed",
                source_video_id=source_id,
                selected_count=len(selection_result.selected_clips),
            )

            # =========================================================================
            # STAGE 05 & 06: REFRAME & RENDER
            # =========================================================================
            if await control_repo.is_emergency_stopped():
                logger.error("Emergency stop triggered before Rendering; halting", job_id=active_job_id)
                await state_repo.update_job_state(
                    job_id=active_job_id,
                    new_state=JobState.FAILED,
                    new_stage=PipelineStage.RENDERING,
                    reason="Halted: Master Control Emergency Stop before Rendering",
                )
                return 1

            await state_repo.update_job_state(
                job_id=active_job_id,
                new_state=JobState.REFRAMING_AND_RENDERING,
                new_stage=PipelineStage.RENDERING,
                reason=f"Reframing to 9:16 portrait and rendering {len(selection_result.selected_clips)} clips via FFmpeg",
            )

            source_w = source_metadata.width if source_metadata else 1920
            source_h = source_metadata.height if source_metadata else 1080
            render_outputs: Dict[str, RenderOutput] = {}
            reframe_plans: Dict[str, ReframePlan] = {}

            for ranked_cand in selection_result.selected_clips:
                cand = ranked_cand.candidate
                clip_id = cand.candidate_id

                if await control_repo.is_emergency_stopped():
                    logger.error("Emergency stop triggered during clip rendering", job_id=active_job_id, clip_id=clip_id)
                    await state_repo.update_job_state(
                        job_id=active_job_id,
                        new_state=JobState.FAILED,
                        new_stage=PipelineStage.RENDERING,
                        reason="Halted: Master Control Emergency Stop during clip render",
                    )
                    return 1

                # 05: REFRAME
                plan_key = f"clips/{clip_id}/reframe_plan.json"
                if await active_storage.exists(plan_key):
                    plan_bytes = await active_storage.download_bytes(plan_key)
                    reframe_plan = ReframePlan.model_validate_json(plan_bytes.decode("utf-8"))
                else:
                    reframe_plan = active_camera_director.generate_reframe_plan(
                        clip_id=clip_id,
                        source_width=source_w,
                        source_height=source_h,
                        clip_start=cand.start_time,
                        clip_end=cand.end_time,
                        scene_cuts=scene_cuts,
                        face_tracks=face_tracks,
                        active_speakers=active_speakers,
                        speaker_transcript=speaker_transcript,
                    )
                    await active_storage.upload_bytes(
                        data=reframe_plan.model_dump_json(indent=2).encode("utf-8"),
                        storage_key=plan_key,
                        content_type="application/json",
                    )
                reframe_plans[clip_id] = reframe_plan

                # 06: RENDER
                clip_words = cand.words
                if not clip_words and speaker_transcript:
                    clip_words = [
                        w for w in speaker_transcript.words
                        if cand.start_time <= w.start and w.end <= cand.end_time
                    ]

                render_output = await active_render_engine.render(
                    clip_id=clip_id,
                    source_video_id=source_id,
                    clip_start=cand.start_time,
                    clip_end=cand.end_time,
                    reframe_plan=reframe_plan,
                    words=clip_words,
                    storage_driver=active_storage,
                    force_recompute=False,
                )
                render_outputs[clip_id] = render_output
                logger.info(
                    "Clip rendered successfully",
                    clip_id=clip_id,
                    size_bytes=render_output.file_size_bytes,
                    duration=render_output.duration_seconds,
                )

            # =========================================================================
            # STAGE 07: QA EVALUATION
            # =========================================================================
            if await control_repo.is_emergency_stopped():
                logger.error("Emergency stop triggered before QA; halting", job_id=active_job_id)
                await state_repo.update_job_state(
                    job_id=active_job_id,
                    new_state=JobState.FAILED,
                    new_stage=PipelineStage.QA,
                    reason="Halted: Master Control Emergency Stop before QA",
                )
                return 1

            await state_repo.update_job_state(
                job_id=active_job_id,
                new_state=JobState.RUNNING_QA,
                new_stage=PipelineStage.QA,
                reason="Evaluating rendered clips against layered L1-L5 QA standards",
            )

            passing_candidates: List[RankedCandidate] = []
            qa_reports: Dict[str, QAReport] = {}

            for ranked_cand in selection_result.selected_clips:
                cand = ranked_cand.candidate
                clip_id = cand.candidate_id

                qa_report = await active_qa_engine.evaluate_rendered_clip(
                    clip_id=clip_id,
                    source_video_id=source_id,
                    storage_driver=active_storage,
                    expected_duration=cand.duration,
                    reframe_plan=reframe_plans.get(clip_id),
                    selected_clip=ranked_cand,
                )
                qa_reports[clip_id] = qa_report

                if qa_report.can_publish:
                    passing_candidates.append(ranked_cand)
                    logger.info("Clip passed QA criteria", clip_id=clip_id, status=qa_report.overall_status.value)
                else:
                    logger.warning(
                        "Clip failed QA criteria, omitting from approval gateway",
                        clip_id=clip_id,
                        status=qa_report.overall_status.value,
                        summary=qa_report.summary,
                    )

            if not passing_candidates:
                logger.warning("No clips passed QA evaluation criteria", source_video_id=source_id)
                await state_repo.update_job_state(
                    job_id=active_job_id,
                    new_state=JobState.AWAITING_APPROVAL,
                    new_stage=PipelineStage.APPROVAL,
                    reason="All rendered clips failed QA gating criteria",
                    metadata={"passing_clips_count": 0},
                )
                return 0

            # =========================================================================
            # STAGE 08: TELEGRAM APPROVAL GATEWAY
            # =========================================================================
            if await control_repo.is_emergency_stopped():
                logger.error("Emergency stop triggered before Approval dispatch; halting", job_id=active_job_id)
                await state_repo.update_job_state(
                    job_id=active_job_id,
                    new_state=JobState.FAILED,
                    new_stage=PipelineStage.APPROVAL,
                    reason="Halted: Master Control Emergency Stop before Approval",
                )
                return 1

            chat_id = active_settings.TELEGRAM_CHAT_ID or 0
            await active_approval_gateway.dispatch_candidate_clips(
                job_id=active_job_id,
                source_video_id=source_id,
                ranked_candidates=passing_candidates,
                render_outputs=render_outputs,
                chat_id=chat_id,
            )

            await state_repo.update_job_state(
                job_id=active_job_id,
                new_state=JobState.AWAITING_APPROVAL,
                new_stage=PipelineStage.APPROVAL,
                reason=f"Dispatched {len(passing_candidates)} QA-verified clips for Telegram approval",
                metadata={"passing_clips_count": len(passing_candidates)},
            )

            logger.info(
                "Pipeline job completed successfully, awaiting operator approval",
                job_id=active_job_id,
                eligible_clips=len(passing_candidates),
            )
            return 0

    except Exception as e:
        logger.error("Pipeline job failed with unhandled error", job_id=active_job_id, error=str(e))
        await state_repo.update_job_state(
            job_id=active_job_id,
            new_state=JobState.FAILED,
            error_message=str(e),
            reason=f"Pipeline failure: {e}",
        )
        return 1

    finally:
        await lease_repo.release_lease(job_id=active_job_id, worker_id=active_worker_id)


def main():
    parser = argparse.ArgumentParser(description="Clipping Automation Cloud Pipeline Runner")
    parser.add_argument("--source-uri", required=True, help="Source video URL or storage key")
    parser.add_argument("--campaign-id", default="default_campaign", help="Campaign ID")
    parser.add_argument("--job-id", default="", help="Job ID")
    parser.add_argument("--worker-id", default="", help="Worker ID")

    args = parser.parse_args()
    exit_code = asyncio.run(
        run_pipeline(
            source_uri=args.source_uri,
            campaign_id=args.campaign_id,
            job_id=args.job_id or None,
            worker_id=args.worker_id or None,
        )
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
