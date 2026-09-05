"""Durable Cloud Task Queue backed by Canonical Storage."""

import json
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field, ConfigDict

from clipping.agent.cloud.lease import WorkerLeaseEngine
from clipping.agent.models import TaskPriority
from clipping.storage.base import StorageDriver
from clipping.logging.logger import get_logger

logger = get_logger("clipping.agent.cloud.queue")


class QueueItemStatus(str, Enum):
    """Lifecycle state of an item inside the Cloud Task Queue."""
    PENDING = "pending"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    FAILED = "failed"
    DEFERRED = "deferred"
    CANCELLED = "cancelled"


class QueueItem(BaseModel):
    """Durable queue envelope stored at queue/items/{task_id}.json."""
    model_config = ConfigDict(frozen=True)

    task_id: str
    priority: int = Field(default=int(TaskPriority.NORMAL), ge=0, le=1000)
    status: QueueItemStatus = QueueItemStatus.PENDING
    enqueued_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    scheduled_for: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    claimed_by: Optional[str] = None
    claimed_at: Optional[datetime] = None
    lease_expires_at: Optional[datetime] = None
    attempt_count: int = 0
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def is_ready_at(self, current_time: datetime) -> bool:
        """Determines if the queue item is eligible for worker claim."""
        return self.status == QueueItemStatus.PENDING and current_time >= self.scheduled_for


class CloudTaskQueue:
    """
    Durable, serverless task queue operating directly over StorageDriver.
    Requires zero external Redis/Postgres infrastructure.
    Guarantees at-least-once execution and crash recovery across ephemeral cloud runners.
    """

    def __init__(self, storage_driver: StorageDriver, lease_engine: Optional[WorkerLeaseEngine] = None):
        self.storage = storage_driver
        self.lease_engine = lease_engine or WorkerLeaseEngine(storage_driver)

    def _item_key(self, task_id: str) -> str:
        return f"queue/items/{task_id}.json"

    def _pending_pointer_key(self, priority: int, scheduled_at: datetime, task_id: str) -> str:
        # Invert priority (1000 - priority) so standard ascending alphanumeric sorting delivers highest priority first
        inverted_prio = max(0, 1000 - priority)
        ts = int(scheduled_at.timestamp())
        return f"queue/pending/{inverted_prio:04d}_{ts:012d}_{task_id}.json"

    async def get_item(self, task_id: str) -> Optional[QueueItem]:
        """Loads a queue item document."""
        key = self._item_key(task_id)
        if not await self.storage.exists(key):
            return None
        try:
            raw = await self.storage.download_bytes(key)
            return QueueItem.model_validate_json(raw.decode("utf-8"))
        except Exception as e:
            logger.error("Failed to read queue item", task_id=task_id, error=str(e))
            return None

    async def enqueue(
        self,
        task_id: str,
        priority: int = int(TaskPriority.NORMAL),
        delay_seconds: float = 0.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> QueueItem:
        """Places a task into the durable queue."""
        now = datetime.now(timezone.utc)
        scheduled_for = now + timedelta(seconds=max(0.0, delay_seconds))

        item = QueueItem(
            task_id=task_id,
            priority=priority,
            status=QueueItemStatus.PENDING,
            enqueued_at=now,
            scheduled_for=scheduled_for,
            metadata=metadata or {},
        )

        # Save item source-of-truth
        item_data = item.model_dump_json(indent=2).encode("utf-8")
        await self.storage.upload_bytes(item_data, self._item_key(task_id), content_type="application/json")

        # Save pending pointer
        ptr_key = self._pending_pointer_key(priority, scheduled_for, task_id)
        ptr_data = json.dumps({"task_id": task_id, "priority": priority}).encode("utf-8")
        await self.storage.upload_bytes(ptr_data, ptr_key, content_type="application/json")

        logger.info("Enqueued task in cloud queue", task_id=task_id, priority=priority, delay=delay_seconds)
        return item

    async def claim(
        self,
        worker_id: str,
        lease_duration_seconds: int = 300,
    ) -> Optional[QueueItem]:
        """
        Scans pending queue pointers in priority and temporal order,
        acquires a mutually exclusive distributed lease, and returns the claimed item.
        """
        now = datetime.now(timezone.utc)

        try:
            pointer_files = await self.storage.list_files("queue/pending/")
            pointer_keys = sorted([f.storage_key for f in pointer_files if f.storage_key.endswith(".json")])
        except Exception as e:
            logger.error("Failed to list pending queue pointers", error=str(e))
            return None

        for ptr_key in pointer_keys:
            # Extract task_id from pointer filename: queue/pending/{prio}_{ts}_{task_id}.json
            filename = ptr_key.split("/")[-1]
            parts = filename[:-5].split("_", 2)
            if len(parts) < 3:
                continue
            task_id = parts[2]

            item = await self.get_item(task_id)
            if not item:
                # Clean up dangling pointer
                await self.storage.delete(ptr_key)
                continue

            if not item.is_ready_at(now):
                # Scheduled for future time
                continue

            # Attempt distributed lease acquisition
            acquired, collision = await self.lease_engine.acquire_lease(
                task_id=task_id,
                worker_id=worker_id,
                ttl_seconds=lease_duration_seconds,
            )
            if not acquired:
                # Another worker claimed it concurrently; continue scanning
                continue

            # Remove pointer from pending
            await self.storage.delete(ptr_key)

            # Update item status to CLAIMED
            claimed_item = item.model_copy(update={
                "status": QueueItemStatus.CLAIMED,
                "claimed_by": worker_id,
                "claimed_at": now,
                "lease_expires_at": now + timedelta(seconds=lease_duration_seconds),
                "attempt_count": item.attempt_count + 1,
            })
            data = claimed_item.model_dump_json(indent=2).encode("utf-8")
            await self.storage.upload_bytes(data, self._item_key(task_id), content_type="application/json")

            logger.info("Worker claimed task from queue", task_id=task_id, worker_id=worker_id)
            return claimed_item

        return None

    async def heartbeat(
        self,
        task_id: str,
        worker_id: str,
        extend_seconds: int = 300,
    ) -> bool:
        """Renews active worker lease and updates queue item record."""
        renewed = await self.lease_engine.heartbeat(task_id=task_id, worker_id=worker_id, extend_seconds=extend_seconds)
        if not renewed:
            return False

        item = await self.get_item(task_id)
        if item and item.status == QueueItemStatus.CLAIMED and item.claimed_by == worker_id:
            now = datetime.now(timezone.utc)
            updated = item.model_copy(update={"lease_expires_at": now + timedelta(seconds=extend_seconds)})
            data = updated.model_dump_json(indent=2).encode("utf-8")
            await self.storage.upload_bytes(data, self._item_key(task_id), content_type="application/json")
        return True

    async def complete(self, task_id: str, worker_id: str) -> None:
        """Marks a queue item as COMPLETED and releases worker lease."""
        await self.lease_engine.release_lease(task_id=task_id, worker_id=worker_id)
        item = await self.get_item(task_id)
        if item:
            updated = item.model_copy(update={"status": QueueItemStatus.COMPLETED})
            data = updated.model_dump_json(indent=2).encode("utf-8")
            await self.storage.upload_bytes(data, self._item_key(task_id), content_type="application/json")
            logger.info("Marked queue task completed", task_id=task_id, worker_id=worker_id)

    async def fail(
        self,
        task_id: str,
        worker_id: str,
        error_message: str,
        should_retry: bool = False,
        retry_delay_seconds: float = 5.0,
    ) -> None:
        """Handles task execution failure: schedules retry or marks FAILED."""
        await self.lease_engine.release_lease(task_id=task_id, worker_id=worker_id)
        item = await self.get_item(task_id)
        if not item:
            return

        if should_retry:
            logger.warning("Re-queueing task for retry after failure", task_id=task_id, delay=retry_delay_seconds)
            await self.enqueue(
                task_id=task_id,
                priority=item.priority,
                delay_seconds=retry_delay_seconds,
                metadata={**item.metadata, "last_error": error_message},
            )
        else:
            updated = item.model_copy(update={
                "status": QueueItemStatus.FAILED,
                "error_message": error_message,
            })
            data = updated.model_dump_json(indent=2).encode("utf-8")
            await self.storage.upload_bytes(data, self._item_key(task_id), content_type="application/json")
            logger.info("Marked queue task failed permanently", task_id=task_id, error=error_message)

    async def defer(self, task_id: str, worker_id: str, until_timestamp: datetime) -> None:
        """Defers task execution until a future time and releases lease."""
        await self.lease_engine.release_lease(task_id=task_id, worker_id=worker_id)
        item = await self.get_item(task_id)
        if not item:
            return

        delay = max(0.0, (until_timestamp - datetime.now(timezone.utc)).total_seconds())
        await self.enqueue(
            task_id=task_id,
            priority=item.priority,
            delay_seconds=delay,
            metadata=item.metadata,
        )
        logger.info("Deferred queue task", task_id=task_id, until=until_timestamp.isoformat())

    async def cancel(self, task_id: str) -> None:
        """Cancels a pending or deferred queue item."""
        item = await self.get_item(task_id)
        if item and item.status != QueueItemStatus.COMPLETED:
            updated = item.model_copy(update={"status": QueueItemStatus.CANCELLED})
            data = updated.model_dump_json(indent=2).encode("utf-8")
            await self.storage.upload_bytes(data, self._item_key(task_id), content_type="application/json")
            # Remove pointer if any
            try:
                files = await self.storage.list_files("queue/pending/")
                for f in files:
                    if task_id in f.storage_key:
                        await self.storage.delete(f.storage_key)
            except Exception:
                pass
            logger.info("Cancelled queue task", task_id=task_id)

    async def reclaim_stale_tasks(self, stale_threshold_seconds: int = 0) -> List[str]:
        """
        Scans all CLAIMED queue items whose lease has expired or whose worker crashed.
        Re-enqueues them to PENDING so another healthy worker can pick them up.
        """
        reclaimed: List[str] = []
        now = datetime.now(timezone.utc)

        try:
            files = await self.storage.list_files("queue/items/")
            item_keys = [f.storage_key for f in files if f.storage_key.endswith(".json")]
        except Exception as e:
            logger.error("Failed to list queue items during stale scan", error=str(e))
            return []

        for key in item_keys:
            task_id = key.split("/")[-1][:-5]
            item = await self.get_item(task_id)
            if not item or item.status != QueueItemStatus.CLAIMED:
                continue

            lease = await self.lease_engine.get_lease(task_id)
            is_stale = False

            if not lease:
                is_stale = True
            elif lease.is_stale_at(now + timedelta(seconds=stale_threshold_seconds)):
                is_stale = True
            elif item.lease_expires_at and (now >= item.lease_expires_at + timedelta(seconds=stale_threshold_seconds)):
                is_stale = True

            if is_stale:
                logger.warning(
                    "Detected stale worker on task; reclaiming to queue",
                    task_id=task_id,
                    former_worker=item.claimed_by,
                )
                if lease and item.claimed_by:
                    await self.lease_engine.release_lease(task_id=task_id, worker_id=item.claimed_by)

                # Re-enqueue item
                await self.enqueue(
                    task_id=task_id,
                    priority=item.priority,
                    delay_seconds=0.0,
                    metadata={**item.metadata, "reclaimed_from_stale_worker": item.claimed_by},
                )
                reclaimed.append(task_id)

        return reclaimed

    async def get_queue_depth(self) -> Dict[str, int]:
        """Returns aggregate metrics on queue occupancy."""
        depth = {"pending": 0, "claimed": 0, "completed": 0, "failed": 0, "deferred": 0, "cancelled": 0}
        try:
            files = await self.storage.list_files("queue/items/")
            for f in files:
                if f.storage_key.endswith(".json"):
                    item = await self.get_item(f.storage_key.split("/")[-1][:-5])
                    if item and item.status.value in depth:
                        depth[item.status.value] += 1
        except Exception as e:
            logger.error("Failed to calculate queue depth", error=str(e))
        return depth

    async def list_pending_items(self, limit: int = 100) -> List[QueueItem]:
        """Lists pending queue items up to limit."""
        results: List[QueueItem] = []
        try:
            pointers = await self.storage.list_files("queue/pending/")
            pointers.sort(key=lambda x: x.storage_key)
            for p in pointers[:limit]:
                task_id = p.storage_key.split("_")[-1].replace(".json", "")
                item = await self.get_item(task_id)
                if item and item.status == QueueItemStatus.PENDING:
                    results.append(item)
        except Exception as e:
            logger.error("Failed to list pending queue items", error=str(e))
        return results
