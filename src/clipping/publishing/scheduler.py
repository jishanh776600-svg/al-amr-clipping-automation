"""Scheduled Video Publication Processing & Catch-up Engine."""

from datetime import datetime, timezone
from typing import List, Optional
from clipping.publishing.models import PublishRequest, PublishStatus
from clipping.publishing.service import PublishingService
from clipping.publishing.repository import PublishingRepository
from clipping.logging.logger import get_logger

logger = get_logger("clipping.publishing.scheduler")


class PublishingScheduler:
    """
    Evaluates scheduled publication requests stored in Google Drive.
    Identifies clips whose scheduled publish time has arrived (or is past due),
    and executes publication safely without requiring persistent in-memory queues.
    """

    def __init__(
        self,
        repository: PublishingRepository,
        publishing_service: PublishingService,
    ):
        self.repository = repository
        self.service = publishing_service

    async def process_due_scheduled_clips(
        self,
        current_time: Optional[datetime] = None,
        expected_channel_id: Optional[str] = None,
    ) -> List[PublishRequest]:
        now = current_time or datetime.now(timezone.utc)
        logger.info("Scanning for due scheduled publications", evaluation_time=now.isoformat())

        due_records = await self.repository.list_due_scheduled_records(current_time=now)
        if not due_records:
            logger.info("No scheduled publications currently due")
            return []

        logger.info(f"Found {len(due_records)} due scheduled clips for publication")
        results: List[PublishRequest] = []

        for req in due_records:
            # Publish now
            res = await self.service.publish_clip(
                job_id=req.job_id,
                clip_id=req.clip_id,
                approval_request_id=req.approval_request_id,
                video_storage_key=req.video_storage_key,
                metadata=req.metadata,
                expected_channel_id=expected_channel_id or req.channel_id,
                scheduled_publish_at=None,  # Reset schedule so it uploads now
            )
            results.append(res)
            logger.info("Processed scheduled clip", clip_id=req.clip_id, status=res.status.value)

        return results
