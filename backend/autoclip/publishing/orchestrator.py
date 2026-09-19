"""Publishing Orchestrator managing multi-account destinations, queues, and execution."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import os
from typing import Any

from ..db import models, store
from .service import PublishingService

log = logging.getLogger(__name__)


class PublishingOrchestrator:
    """Orchestrates multi-destination scheduling, safe transactional claiming,
    daily limits, spacing intervals, and quality gate re-validation.
    """

    def __init__(self, service: PublishingService | None = None) -> None:
        self.service = service or PublishingService()

    def ensure_default_destinations(self) -> list[models.DestinationRecord]:
        """Ensure standard initial destinations exist for configured platforms."""
        existing = store.list_destinations()
        platforms_present = {d.platform for d in existing}
        created: list[models.DestinationRecord] = []

        now = models.utcnow()

        if "telegram" not in platforms_present:
            tg_dest = models.DestinationRecord(
                id="dest-telegram-main",
                platform="telegram",
                display_name="Telegram Primary Channel",
                account_identifier=os.getenv("TELEGRAM_CHAT_ID", "Telegram Channel"),
                enabled=True,
                priority=10,
                config_metadata={"type": "channel"},
                daily_limit=50,
                spacing_seconds=300,
                created_at=now,
                updated_at=now,
            )
            store.create_destination(tg_dest)
            created.append(tg_dest)

        if "youtube" not in platforms_present:
            yt_dest = models.DestinationRecord(
                id="dest-youtube-main",
                platform="youtube",
                display_name="YouTube Shorts Main",
                account_identifier="Official YouTube Channel",
                enabled=True,
                priority=20,
                config_metadata={"privacy": "public", "category_id": "22"},
                daily_limit=15,
                spacing_seconds=3600,
                created_at=now,
                updated_at=now,
            )
            store.create_destination(yt_dest)
            created.append(yt_dest)

        if "instagram" not in platforms_present:
            ig_dest = models.DestinationRecord(
                id="dest-instagram-main",
                platform="instagram",
                display_name="Instagram Reels Main",
                account_identifier=os.getenv("INSTAGRAM_ACCOUNT_ID", "Instagram Account"),
                enabled=True,
                priority=15,
                config_metadata={"share_to_feed": True},
                daily_limit=25,
                spacing_seconds=1800,
                created_at=now,
                updated_at=now,
            )
            store.create_destination(ig_dest)
            created.append(ig_dest)

        return existing + created

    def calculate_scheduled_time(
        self, destination: models.DestinationRecord, requested_time: str | None = None
    ) -> str:
        """Calculate optimal scheduled_at timestamp adhering to spacing rules."""
        now_dt = datetime.now(timezone.utc)

        if requested_time:
            try:
                clean_time = requested_time.replace("Z", "+00:00")
                parsed_dt = datetime.fromisoformat(clean_time)
                if parsed_dt.tzinfo is None:
                    parsed_dt = parsed_dt.replace(tzinfo=timezone.utc)
                candidate_dt = max(now_dt, parsed_dt)
            except Exception:
                candidate_dt = now_dt
        else:
            candidate_dt = now_dt

        # Check last scheduled/published item for spacing
        latest_time_str = store.get_latest_scheduled_time_for_destination(destination.id)
        if latest_time_str:
            try:
                clean_latest = latest_time_str.replace("Z", "+00:00")
                latest_dt = datetime.fromisoformat(clean_latest)
                if latest_dt.tzinfo is None:
                    latest_dt = latest_dt.replace(tzinfo=timezone.utc)
                min_next = latest_dt + timedelta(seconds=destination.spacing_seconds)
                if min_next > candidate_dt:
                    candidate_dt = min_next
            except Exception as exc:
                log.warning("Could not parse latest scheduled time %s: %s", latest_time_str, exc)

        return candidate_dt.isoformat()

    def enqueue_publication(
        self,
        job_id: str,
        clip_id: str,
        destination_id: str,
        *,
        scheduled_at: str | None = None,
        priority: int = 0,
    ) -> models.PublishingQueueRecord:
        """Schedule a clip for publication on a specific destination."""
        # Ensure default destinations exist
        self.ensure_default_destinations()

        destination = store.get_destination(destination_id)
        if not destination:
            raise ValueError(f"Publishing destination '{destination_id}' does not exist.")

        if not destination.enabled:
            raise ValueError(f"Publishing destination '{destination.display_name}' is currently disabled.")

        # Check Step 22-24 Publishing Eligibility Gate
        is_eligible, reasons, _, _ = self.service.verify_publishing_eligibility(clip_id)
        if not is_eligible:
            raise ValueError(f"Clip '{clip_id}' cannot be scheduled: {'; '.join(reasons)}")

        # Check Daily Limit
        published_today = store.count_destination_publications_today(destination.id)
        if published_today >= destination.daily_limit:
            log.warning(
                "Destination %s reached daily limit (%d/%d). Scheduling for next day.",
                destination.display_name,
                published_today,
                destination.daily_limit,
            )
            tomorrow_utc = (datetime.now(timezone.utc) + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            scheduled_at_iso = tomorrow_utc.isoformat()
        else:
            scheduled_at_iso = self.calculate_scheduled_time(destination, scheduled_at)

        now = models.utcnow()
        queue_status: models.QueueStatus = "SCHEDULED" if scheduled_at_iso > now else "QUEUED"

        # Unique idempotency key per clip, destination, and day/schedule
        idempotency_key = f"{job_id}:{clip_id}:{destination.platform}:{destination.id}:{scheduled_at_iso[:16]}"

        existing = store.get_queue_item_by_idempotency_key(idempotency_key)
        if existing and existing.status in ("QUEUED", "SCHEDULED", "CLAIMED", "PUBLISHING", "PUBLISHED"):
            log.info("Queue item for clip %s to %s already exists [id=%s].", clip_id, destination.id, existing.id)
            return existing

        queue_item = models.PublishingQueueRecord(
            id=models.new_id(),
            job_id=job_id,
            clip_id=clip_id,
            destination_id=destination.id,
            platform=destination.platform,
            scheduled_at=scheduled_at_iso,
            priority=priority or destination.priority,
            status=queue_status,
            idempotency_key=idempotency_key,
            created_at=now,
            updated_at=now,
        )

        created = store.create_queue_item(queue_item)
        self._update_job_queue_telemetry(created.job_id)
        return created

    async def claim_and_process_next(
        self, worker_id: str = "worker-default", lease_seconds: int = 300, dry_run: bool = False
    ) -> models.PublishingQueueRecord | None:
        """Claim the next eligible queue item and execute its publication."""
        claimed = store.claim_next_queue_item(worker_id, lease_seconds=lease_seconds)
        if not claimed:
            return None

        return await self.process_queue_item(claimed.id, dry_run=dry_run)

    async def process_due_queue(
        self, worker_id: str = "worker-autonomous", batch_size: int = 10, dry_run: bool = False
    ) -> list[models.PublishingQueueRecord]:
        """Claim and process all currently due items up to batch_size."""
        processed: list[models.PublishingQueueRecord] = []
        for _ in range(batch_size):
            item = await self.claim_and_process_next(worker_id=worker_id, dry_run=dry_run)
            if not item:
                break
            processed.append(item)
        return processed

    def cancel_items_for_clip(self, clip_id: str, reason: str = "Operator revoked approval") -> int:
        """Cancel all pending or scheduled queue items for a clip."""
        items = store.list_queue_items(clip_id=clip_id)
        cancelled_count = 0
        for i in items:
            if i.status in ("QUEUED", "SCHEDULED"):
                self.cancel_item(i.id, reason=reason)
                cancelled_count += 1
        return cancelled_count

    async def process_queue_item(
        self, queue_id: str, dry_run: bool = False
    ) -> models.PublishingQueueRecord:
        """Process a specific queue item safely with gate verification and error handling."""
        item = store.get_queue_item(queue_id)
        if not item:
            raise ValueError(f"Queue item '{queue_id}' not found.")

        if item.status == "PUBLISHED":
            return item

        # Re-verify Publishing Eligibility Gate (Step 22-24)
        is_eligible, reasons, _, _ = self.service.verify_publishing_eligibility(item.clip_id)
        if not is_eligible:
            err_msg = f"Publishing gate revoked or failed: {'; '.join(reasons)}"
            log.warning("Cancelling queue item %s because clip is no longer eligible: %s", queue_id, err_msg)
            updated = store.update_queue_item_status(queue_id, "CANCELLED", error_message=err_msg)
            self._update_job_queue_telemetry(item.job_id)
            return updated or item

        # Transition to PUBLISHING
        store.update_queue_item_status(queue_id, "PUBLISHING")

        dest = store.get_destination(item.destination_id)
        dest_ref = item.destination_id if dest else (item.destination_id or "default")

        try:
            pub_record = await self.service.publish_clip(
                job_id=item.job_id,
                clip_id=item.clip_id,
                platform=item.platform,
                destination=dest_ref,
                dry_run=dry_run,
            )

            if pub_record.status == "PUBLISHED":
                updated = store.update_queue_item_status(
                    queue_id, "PUBLISHED", publication_id=pub_record.id, error_message=None
                )
            elif pub_record.status == "FAILED_RETRYABLE":
                updated = store.update_queue_item_status(
                    queue_id,
                    "FAILED_RETRYABLE",
                    publication_id=pub_record.id,
                    error_message=pub_record.error_message,
                )
            else:
                updated = store.update_queue_item_status(
                    queue_id,
                    "FAILED_PERMANENT",
                    publication_id=pub_record.id,
                    error_message=pub_record.error_message,
                )

            self._update_job_queue_telemetry(item.job_id)
            return updated or item

        except Exception as exc:
            log.exception("Execution error publishing queue item %s: %s", queue_id, exc)
            updated = store.update_queue_item_status(
                queue_id, "FAILED_RETRYABLE", error_message=str(exc)
            )
            self._update_job_queue_telemetry(item.job_id)
            return updated or item


    def reschedule_item(
        self, queue_id: str, new_scheduled_at: str
    ) -> models.PublishingQueueRecord:
        """Reschedule a queue item to a new future time."""
        res = store.reschedule_queue_item(queue_id, new_scheduled_at)
        if not res:
            raise ValueError(f"Queue item '{queue_id}' could not be rescheduled.")
        self._update_job_queue_telemetry(res.job_id)
        return res

    def cancel_item(self, queue_id: str, reason: str = "") -> models.PublishingQueueRecord:
        """Cancel a pending or scheduled queue item."""
        res = store.cancel_queue_item(queue_id, reason or "Cancelled by operator")
        if not res:
            raise ValueError(f"Queue item '{queue_id}' could not be cancelled.")
        self._update_job_queue_telemetry(res.job_id)
        return res

    def _update_job_queue_telemetry(self, job_id: str) -> None:
        """Update job settings queue_telemetry counters."""
        try:
            job = store.get_job(job_id)
            if job:
                telemetry = self.get_telemetry(job_id=job_id)
                job.settings["queue_telemetry"] = telemetry
                store.update_job_settings(job_id, job.settings)
        except Exception as exc:
            log.warning("Failed to update job %s queue telemetry: %s", job_id, exc)

    def get_telemetry(self, job_id: str | None = None) -> dict[str, int]:
        """Aggregate queue telemetry counters."""
        items = store.list_queue_items(job_id=job_id, limit=500)
        return {
            "total": len(items),
            "queued": sum(1 for i in items if i.status == "QUEUED"),
            "scheduled": sum(1 for i in items if i.status == "SCHEDULED"),
            "claimed": sum(1 for i in items if i.status == "CLAIMED"),
            "publishing": sum(1 for i in items if i.status == "PUBLISHING"),
            "published": sum(1 for i in items if i.status == "PUBLISHED"),
            "failed_retryable": sum(1 for i in items if i.status == "FAILED_RETRYABLE"),
            "failed_permanent": sum(1 for i in items if i.status == "FAILED_PERMANENT"),
            "failed": sum(1 for i in items if "FAILED" in i.status),
            "cancelled": sum(1 for i in items if i.status == "CANCELLED"),
        }

    get_queue_stats = get_telemetry

