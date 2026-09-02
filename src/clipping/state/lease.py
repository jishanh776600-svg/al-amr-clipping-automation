"""Durable Job Claim and Lease Management in Google Drive for Distributed Workers."""

import json
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
from pydantic import BaseModel, Field, ConfigDict

from clipping.storage.base import StorageDriver
from clipping.logging.logger import get_logger

logger = get_logger("clipping.state.lease")


class JobLease(BaseModel):
    """
    Durable lease contract stored at jobs/{job_id}/lease.json.
    Prevents duplicate processing by concurrent ephemeral GitHub Actions runners.
    """
    model_config = ConfigDict(frozen=True)

    job_id: str
    worker_id: str
    claimed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    lease_expires_at: datetime
    status: str = "active"  # "active" or "released"

    def is_valid_at(self, current_time: datetime) -> bool:
        return self.status == "active" and current_time < self.lease_expires_at


class JobLeaseRepository:
    """Manages cloud-safe distributed leases backed by canonical remote storage."""

    def __init__(self, storage_driver: StorageDriver):
        self.storage_driver = storage_driver

    def _lease_key(self, job_id: str) -> str:
        return f"jobs/{job_id}/lease.json"

    async def get_lease(self, job_id: str) -> Optional[JobLease]:
        key = self._lease_key(job_id)
        if not await self.storage_driver.exists(key):
            return None
        try:
            raw = await self.storage_driver.download_bytes(key)
            return JobLease.model_validate_json(raw.decode("utf-8"))
        except Exception as e:
            logger.error("Failed to read job lease", job_id=job_id, error=str(e))
            return None

    async def acquire_lease(
        self,
        job_id: str,
        worker_id: str,
        ttl_seconds: int = 1800,
    ) -> Tuple[bool, Optional[str]]:
        """
        Attempts to acquire or reclaim a lease for the specified job.
        Returns (acquired: bool, reason: Optional[str]).
        """
        now = datetime.now(timezone.utc)
        existing = await self.get_lease(job_id)

        if existing and existing.is_valid_at(now):
            if existing.worker_id != worker_id:
                reason = f"Job {job_id} already locked by worker {existing.worker_id} until {existing.lease_expires_at.isoformat()}"
                logger.warning("Lease collision detected", job_id=job_id, holder=existing.worker_id)
                return False, reason

        # Create or renew lease
        lease = JobLease(
            job_id=job_id,
            worker_id=worker_id,
            claimed_at=now,
            lease_expires_at=now + timedelta(seconds=ttl_seconds),
            status="active",
        )
        data = lease.model_dump_json(indent=2).encode("utf-8")
        await self.storage_driver.upload_bytes(data, self._lease_key(job_id), content_type="application/json")
        logger.info("Acquired job lease", job_id=job_id, worker_id=worker_id, ttl=ttl_seconds)
        return True, None

    async def release_lease(self, job_id: str, worker_id: str) -> bool:
        """Explicitly marks a lease as released upon job completion or termination."""
        existing = await self.get_lease(job_id)
        if not existing:
            return True

        if existing.worker_id == worker_id:
            released = existing.model_copy(update={"status": "released"})
            data = released.model_dump_json(indent=2).encode("utf-8")
            await self.storage_driver.upload_bytes(data, self._lease_key(job_id), content_type="application/json")
            logger.info("Released job lease", job_id=job_id, worker_id=worker_id)
            return True

        logger.warning("Cannot release lease held by different worker", job_id=job_id, holder=existing.worker_id)
        return False
