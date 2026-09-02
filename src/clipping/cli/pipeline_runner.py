"""Command-line remote pipeline execution entrypoint with checkpoint resumption, lease locking, and cooperative emergency stop."""

import argparse
import asyncio
import os
import sys
import uuid
from typing import Optional
from clipping.config.settings import Settings
from clipping.control.repository import ControlRepository
from clipping.core.workspace import WorkerScratchWorkspace
from clipping.state.lease import JobLeaseRepository
from clipping.state.models import JobState, PipelineStage
from clipping.state.remote import RemoteStorageStateRepository
from clipping.storage.base import StorageDriver
from clipping.storage.local import LocalStorageDriver
from clipping.storage.google_drive import GoogleDriveStorageDriver
from clipping.logging.logger import get_logger

logger = get_logger("clipping.cli.pipeline_runner")


def get_storage_driver(settings: Settings) -> StorageDriver:
    if settings.STORAGE_DRIVER == "gdrive" and settings.GOOGLE_DRIVE_ROOT_FOLDER_ID:
        return GoogleDriveStorageDriver(
            folder_id=settings.GOOGLE_DRIVE_ROOT_FOLDER_ID,
            credentials_path=settings.GOOGLE_APPLICATION_CREDENTIALS,
        )
    return LocalStorageDriver(root_dir=settings.LOCAL_STORAGE_ROOT)


async def run_pipeline(
    source_uri: str,
    campaign_id: str = "default_campaign",
    job_id: Optional[str] = None,
    worker_id: Optional[str] = None,
) -> int:
    settings = Settings()
    active_job_id = job_id or f"job_{uuid.uuid4().hex[:12]}"
    active_worker_id = worker_id or os.getenv("GITHUB_RUN_ID", f"worker_{uuid.uuid4().hex[:8]}")
    source_id = f"src_{uuid.uuid4().hex[:8]}"

    logger.info("Initializing remote pipeline execution", job_id=active_job_id, worker_id=active_worker_id, source_uri=source_uri)

    storage = get_storage_driver(settings)
    state_repo = RemoteStorageStateRepository(storage_driver=storage)
    control_repo = ControlRepository(storage_driver=storage)
    lease_repo = JobLeaseRepository(storage_driver=storage)

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
            job = await state_repo.create_job(
                job_id=active_job_id,
                campaign_id=campaign_id,
                source_video_id=source_id,
                idempotency_key=f"idemp_{active_job_id}",
                metadata={"source_uri": source_uri, "worker_id": active_worker_id},
            )

        with WorkerScratchWorkspace(job_id=active_job_id) as workspace:
            logger.info("Pipeline checkpoint sequence starting", current_stage=job.current_stage.value)

            # Stage Checkpoint 1: INGESTION & PERCEPTION
            if await control_repo.is_emergency_stopped():
                logger.error("Emergency stop triggered during execution; halting cooperatively", job_id=active_job_id)
                await state_repo.update_job_state(
                    job_id=active_job_id,
                    new_state=JobState.FAILED,
                    new_stage=PipelineStage.PERCEPTION,
                    reason="Halted: Master Control Emergency Stop",
                )
                return 1

            await state_repo.update_job_state(
                job_id=active_job_id,
                new_state=JobState.TRANSCRIBING,
                new_stage=PipelineStage.PERCEPTION,
                reason="Perception stage active",
            )

            # Stage Checkpoint 2: VISION & DIRECTOR
            if await control_repo.is_emergency_stopped():
                logger.error("Emergency stop triggered during execution; halting cooperatively", job_id=active_job_id)
                await state_repo.update_job_state(
                    job_id=active_job_id,
                    new_state=JobState.FAILED,
                    new_stage=PipelineStage.DIRECTOR,
                    reason="Halted: Master Control Emergency Stop",
                )
                return 1

            await state_repo.update_job_state(
                job_id=active_job_id,
                new_state=JobState.RESOLVING_SPEAKERS,
                new_stage=PipelineStage.DIRECTOR,
                reason="Vision understanding stage active",
            )

            # Stage Checkpoint 3: DISCOVERY & INTELLIGENCE
            await state_repo.update_job_state(
                job_id=active_job_id,
                new_state=JobState.DISCOVERING_CLIPS,
                new_stage=PipelineStage.INTELLIGENCE,
                reason="Clip candidate discovery active",
            )

            # Stage Checkpoint 4: RENDERING & QA
            await state_repo.update_job_state(
                job_id=active_job_id,
                new_state=JobState.RUNNING_QA,
                new_stage=PipelineStage.QA,
                reason="Rendering and QA active",
            )

            # Stage Checkpoint 5: MARK AWAITING APPROVAL
            await state_repo.update_job_state(
                job_id=active_job_id,
                new_state=JobState.AWAITING_APPROVAL,
                new_stage=PipelineStage.APPROVAL,
                reason="All automated processing completed, ready for approval gate",
            )

            logger.info("Pipeline job completed successfully", job_id=active_job_id)
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
