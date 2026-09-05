"""Cloud Worker Distributed Lease and Heartbeat Management."""

import json
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple
from pydantic import BaseModel, Field, ConfigDict

from clipping.storage.base import StorageDriver
from clipping.logging.logger import get_logger

logger = get_logger("clipping.agent.cloud.lease")


class WorkerLease(BaseModel):
    """
    Durable lease contract stored at leases/{task_id}.json.
    Ensures mutually exclusive task execution across ephemeral cloud runners.
    """
    model_config = ConfigDict(frozen=True)

    task_id: str
    worker_id: str
    claimed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_heartbeat_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    lease_expires_at: datetime
    heartbeat_count: int = Field(default=0, ge=0)
    max_heartbeats: int = Field(default=120, ge=1, description="Upper bound on heartbeat renewals to prevent zombie loops")
    status: str = Field(default="active", description="active, released, expired, or revoked")

    def is_valid_at(self, current_time: datetime) -> bool:
        """Checks if lease is currently active and within expiry bounds."""
        return self.status == "active" and current_time < self.lease_expires_at

    def is_stale_at(self, current_time: datetime) -> bool:
        """Checks if lease has expired or heartbeat renewals exceeded max threshold."""
        if self.status != "active":
            return False
        return current_time >= self.lease_expires_at or self.heartbeat_count >= self.max_heartbeats


class WorkerLeaseEngine:
    """
    Coordinates distributed worker claims, heartbeat renewals, and stale lease reclamation.
    Backed by canonical StorageDriver.
    """

    def __init__(self, storage_driver: StorageDriver):
        self.storage = storage_driver

    def _lease_key(self, task_id: str) -> str:
        return f"leases/{task_id}.json"

    async def get_lease(self, task_id: str) -> Optional[WorkerLease]:
        """Loads the current lease record for a task."""
        key = self._lease_key(task_id)
        if not await self.storage.exists(key):
            return None
        try:
            raw = await self.storage.download_bytes(key)
            return WorkerLease.model_validate_json(raw.decode("utf-8"))
        except Exception as e:
            logger.error("Failed to read worker lease", task_id=task_id, error=str(e))
            return None

    async def acquire_lease(
        self,
        task_id: str,
        worker_id: str,
        ttl_seconds: int = 300,
        max_heartbeats: int = 120,
    ) -> Tuple[bool, Optional[str]]:
        """
        Attempts to acquire or reclaim a lease.
        Returns (acquired: bool, reason: Optional[str]).
        """
        now = datetime.now(timezone.utc)
        existing = await self.get_lease(task_id)

        if existing and existing.is_valid_at(now):
            if existing.worker_id != worker_id:
                reason = f"Task {task_id} is locked by worker {existing.worker_id} until {existing.lease_expires_at.isoformat()}"
                logger.warning("Worker lease collision detected", task_id=task_id, holder=existing.worker_id)
                return False, reason

        lease = WorkerLease(
            task_id=task_id,
            worker_id=worker_id,
            claimed_at=now,
            last_heartbeat_at=now,
            lease_expires_at=now + timedelta(seconds=ttl_seconds),
            heartbeat_count=0,
            max_heartbeats=max_heartbeats,
            status="active",
        )
        data = lease.model_dump_json(indent=2).encode("utf-8")
        await self.storage.upload_bytes(data, self._lease_key(task_id), content_type="application/json")
        logger.info("Acquired cloud worker lease", task_id=task_id, worker_id=worker_id, ttl=ttl_seconds)
        return True, None

    async def heartbeat(
        self,
        task_id: str,
        worker_id: str,
        extend_seconds: int = 300,
    ) -> bool:
        """
        Renews an active worker lease.
        Enforces maximum heartbeat ceiling to prevent runaway worker loops.
        """
        now = datetime.now(timezone.utc)
        existing = await self.get_lease(task_id)

        if not existing or existing.status != "active":
            logger.warning("Cannot heartbeat inactive or absent lease", task_id=task_id)
            return False

        if existing.worker_id != worker_id:
            logger.warning("Worker heartbeat rejected: lease held by another worker", task_id=task_id, current_holder=existing.worker_id)
            return False

        if existing.heartbeat_count >= existing.max_heartbeats:
            logger.error("Worker reached maximum heartbeat threshold; lease cannot be renewed", task_id=task_id, count=existing.heartbeat_count)
            return False

        updated = existing.model_copy(update={
            "last_heartbeat_at": now,
            "lease_expires_at": now + timedelta(seconds=extend_seconds),
            "heartbeat_count": existing.heartbeat_count + 1,
        })
        data = updated.model_dump_json(indent=2).encode("utf-8")
        await self.storage.upload_bytes(data, self._lease_key(task_id), content_type="application/json")
        logger.info("Heartbeat extended lease", task_id=task_id, worker_id=worker_id, count=updated.heartbeat_count)
        return True

    async def release_lease(self, task_id: str, worker_id: str) -> bool:
        """Releases the lease when a worker finishes or terminates cleanly."""
        existing = await self.get_lease(task_id)
        if not existing:
            return True

        if existing.worker_id == worker_id or existing.is_stale_at(datetime.now(timezone.utc)):
            released = existing.model_copy(update={"status": "released"})
            data = released.model_dump_json(indent=2).encode("utf-8")
            await self.storage.upload_bytes(data, self._lease_key(task_id), content_type="application/json")
            logger.info("Released worker lease", task_id=task_id, worker_id=worker_id)
            return True

        logger.warning("Cannot release lease held by different active worker", task_id=task_id, holder=existing.worker_id)
        return False

    async def reclaim_expired_lease(
        self,
        task_id: str,
        new_worker_id: str,
        ttl_seconds: int = 300,
    ) -> Tuple[bool, Optional[WorkerLease]]:
        """
        Safely reclaims a stale/expired lease held by a crashed or timed-out worker.
        """
        now = datetime.now(timezone.utc)
        existing = await self.get_lease(task_id)

        if existing and existing.is_valid_at(now):
            return False, existing

        logger.warning(
            "Reclaiming expired lease from previous worker",
            task_id=task_id,
            previous_worker=existing.worker_id if existing else "none",
            new_worker=new_worker_id,
        )

        reclaimed = WorkerLease(
            task_id=task_id,
            worker_id=new_worker_id,
            claimed_at=now,
            last_heartbeat_at=now,
            lease_expires_at=now + timedelta(seconds=ttl_seconds),
            heartbeat_count=0,
            status="active",
        )
        data = reclaimed.model_dump_json(indent=2).encode("utf-8")
        await self.storage.upload_bytes(data, self._lease_key(task_id), content_type="application/json")
        return True, reclaimed

    async def list_leases(self, limit: int = 50) -> List[WorkerLease]:
        """Lists stored worker lease records."""
        leases: List[WorkerLease] = []
        try:
            files = await self.storage.list_files("leases/")
            for f in files[:limit]:
                if f.storage_key.endswith(".json"):
                    task_id = f.storage_key.split("/")[-1].replace(".json", "")
                    lease = await self.get_lease(task_id)
                    if lease:
                        leases.append(lease)
        except Exception as e:
            logger.error("Failed to list worker leases", error=str(e))
        return leases
