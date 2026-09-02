"""CLI runner entrypoint for YouTube Publishing & Scheduling in Cloud Workflows."""

import argparse
import asyncio
import sys
from typing import Optional
from clipping.config.settings import Settings
from clipping.publishing.oauth import OAuthCredentials, OAuthTokenManager
from clipping.publishing.client import HttpYouTubeClient, MockYouTubeClient
from clipping.publishing.repository import PublishingRepository
from clipping.publishing.gates import PublishingGateEnforcer
from clipping.publishing.service import PublishingService
from clipping.publishing.scheduler import PublishingScheduler
from clipping.approval.repository import ApprovalRepository
from clipping.storage.base import StorageDriver
from clipping.storage.factory import create_storage_driver
from clipping.logging.logger import get_logger

logger = get_logger("clipping.cli.publish_runner")


def resolve_storage(settings: Settings) -> StorageDriver:
    return create_storage_driver(settings)


async def run_publisher(job_id: Optional[str] = None, run_scheduler: bool = False) -> int:
    settings = Settings()
    storage = resolve_storage(settings)

    # Initialize Repositories
    pub_repo = PublishingRepository(storage_driver=storage)
    app_repo = ApprovalRepository(storage_driver=storage)
    gates = PublishingGateEnforcer(approval_repository=app_repo, storage_driver=storage)

    # Initialize YouTube Client
    if (
        settings.YOUTUBE_CLIENT_ID
        and settings.YOUTUBE_CLIENT_SECRET
        and settings.YOUTUBE_REFRESH_TOKEN
    ):
        creds = OAuthCredentials(
            client_id=settings.YOUTUBE_CLIENT_ID,
            client_secret=settings.YOUTUBE_CLIENT_SECRET,
            refresh_token=settings.YOUTUBE_REFRESH_TOKEN,
        )
        token_mgr = OAuthTokenManager(credentials=creds)
        client = HttpYouTubeClient(token_manager=token_mgr)
    else:
        logger.warning("No YouTube OAuth credentials configured. Using mock client for smoke validation.")
        client = MockYouTubeClient(expected_channel_id=settings.YOUTUBE_CHANNEL_ID or "UC_MOCK_CHANNEL")

    service = PublishingService(
        repository=pub_repo,
        client=client,
        gate_enforcer=gates,
        storage_driver=storage,
    )

    if run_scheduler:
        scheduler = PublishingScheduler(repository=pub_repo, publishing_service=service)
        results = await scheduler.process_due_scheduled_clips(expected_channel_id=settings.YOUTUBE_CHANNEL_ID)
        logger.info(f"Scheduler processed {len(results)} due clips")
        return 0

    if job_id:
        # Publish all approved clips for the job
        approved_requests = await app_repo.list_requests_for_job(job_id)
        logger.info(f"Found {len(approved_requests)} total clips in job {job_id}")

        items = []
        for r in approved_requests:
            items.append({
                "clip_id": r.clip_id,
                "approval_request_id": r.approval_request_id,
                "video_storage_key": r.video_storage_key,
                "metadata": {
                    "title": r.title,
                    "description": r.hook_sentence or "",
                    "tags": ["Shorts"],
                    "privacy_status": settings.YOUTUBE_DEFAULT_PRIVACY,
                },
            })

        summary = await service.batch_publish_approved_clips(
            job_id=job_id,
            items=items,
            expected_channel_id=settings.YOUTUBE_CHANNEL_ID,
        )
        logger.info("Batch publishing completed", summary=summary.model_dump())
        return 0

    logger.error("Must provide either --job-id or --scheduled")
    return 1


def main():
    parser = argparse.ArgumentParser(description="Clipping Automation YouTube Publishing Runner")
    parser.add_argument("--job-id", default=None, help="Job ID to publish approved clips for")
    parser.add_argument("--scheduled", action="store_true", help="Process due scheduled publications")

    args = parser.parse_args()
    exit_code = asyncio.run(run_publisher(job_id=args.job_id, run_scheduler=args.scheduled))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
