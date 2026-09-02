"""Durable Remote Storage Repository for Publishing Records & Audit Trails."""

import hashlib
import json
from datetime import datetime, timezone
from typing import List, Optional
from clipping.publishing.models import (
    PublishRequest,
    PublishStatus,
    PublishAuditRecord,
)
from clipping.storage.base import StorageDriver
from clipping.logging.logger import get_logger

logger = get_logger("clipping.publishing.repository")


class PublishingRepository:
    """
    Manages persistent publishing state, idempotency index pointers,
    and audit trail records within Google Drive / StorageDriver.
    """

    def __init__(self, storage_driver: StorageDriver):
        self.storage_driver = storage_driver

    def _safe_idemp_key(self, idempotency_key: str) -> str:
        safe_hash = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:32]
        return f"publishing/by_idempotency/{safe_hash}.json"

    async def save_record(self, request: PublishRequest) -> None:
        """Persists a publishing request record and maintains index pointers."""
        record_key = f"jobs/{request.job_id}/publishing/{request.clip_id}.json"
        idemp_key = self._safe_idemp_key(request.idempotency_key)
        sched_key = f"publishing/scheduled/{request.clip_id}.json"

        # 1. Upload main record
        data = request.model_dump_json(indent=2).encode("utf-8")
        await self.storage_driver.upload_bytes(data, record_key, content_type="application/json")

        # 2. Upload idempotency pointer
        idemp_payload = json.dumps({"job_id": request.job_id, "clip_id": request.clip_id})
        await self.storage_driver.upload_bytes(idemp_payload.encode("utf-8"), idemp_key, content_type="application/json")

        # 3. Maintain scheduled index
        if request.status == PublishStatus.DEFERRED and request.scheduled_publish_at:
            await self.storage_driver.upload_bytes(data, sched_key, content_type="application/json")
        elif request.status == PublishStatus.PUBLISHED:
            # Clean up from scheduled index if present
            if await self.storage_driver.exists(sched_key):
                try:
                    await self.storage_driver.delete(sched_key)
                except Exception:
                    pass

        logger.info(
            "Saved publishing record in remote storage",
            job_id=request.job_id,
            clip_id=request.clip_id,
            status=request.status.value,
        )

    async def get_record(self, job_id: str, clip_id: str) -> Optional[PublishRequest]:
        record_key = f"jobs/{job_id}/publishing/{clip_id}.json"
        if not await self.storage_driver.exists(record_key):
            return None
        try:
            raw = await self.storage_driver.download_bytes(record_key)
            return PublishRequest.model_validate_json(raw.decode("utf-8"))
        except Exception as e:
            logger.error("Failed to read publishing record", job_id=job_id, clip_id=clip_id, error=str(e))
            return None

    async def get_record_by_idempotency(self, idempotency_key: str) -> Optional[PublishRequest]:
        idemp_key = self._safe_idemp_key(idempotency_key)
        if not await self.storage_driver.exists(idemp_key):
            return None
        try:
            raw = await self.storage_driver.download_bytes(idemp_key)
            payload = json.loads(raw.decode("utf-8"))
            job_id = payload.get("job_id")
            clip_id = payload.get("clip_id")
            if job_id and clip_id:
                return await self.get_record(job_id, clip_id)
        except Exception as e:
            logger.error("Failed to resolve idempotency key", idempotency_key=idempotency_key, error=str(e))
        return None

    async def list_records_for_job(self, job_id: str) -> List[PublishRequest]:
        prefix = f"jobs/{job_id}/publishing/"
        try:
            files = await self.storage_driver.list_files(prefix)
            records: List[PublishRequest] = []
            for f in files:
                k = f.storage_key
                if "/audit/" in k or not k.endswith(".json"):
                    continue
                raw = await self.storage_driver.download_bytes(k)
                rec = PublishRequest.model_validate_json(raw.decode("utf-8"))
                records.append(rec)
            return records
        except Exception as e:
            logger.error("Failed to list publishing records for job", job_id=job_id, error=str(e))
            return []

    async def list_due_scheduled_records(self, current_time: datetime) -> List[PublishRequest]:
        """Scans the scheduled index for clips whose release time is at or before current_time."""
        prefix = "publishing/scheduled/"
        try:
            files = await self.storage_driver.list_files(prefix)
            due_records: List[PublishRequest] = []
            for f in files:
                if not f.storage_key.endswith(".json"):
                    continue
                raw = await self.storage_driver.download_bytes(f.storage_key)
                rec = PublishRequest.model_validate_json(raw.decode("utf-8"))
                if rec.scheduled_publish_at and rec.scheduled_publish_at <= current_time:
                    due_records.append(rec)
            return due_records
        except Exception as e:
            logger.error("Failed to scan scheduled records", error=str(e))
            return []

    async def record_audit(self, record: PublishAuditRecord) -> None:
        audit_key = f"jobs/{record.job_id}/publishing/audit/{record.audit_id}.json"
        data = record.model_dump_json(indent=2).encode("utf-8")
        await self.storage_driver.upload_bytes(data, audit_key, content_type="application/json")
        logger.info(
            "Recorded publishing audit event",
            audit_id=record.audit_id,
            clip_id=record.clip_id,
            status=record.new_status.value,
        )

    async def list_audits_for_job(self, job_id: str) -> List[PublishAuditRecord]:
        prefix = f"jobs/{job_id}/publishing/audit/"
        try:
            files = await self.storage_driver.list_files(prefix)
            audits: List[PublishAuditRecord] = []
            for f in files:
                if not f.storage_key.endswith(".json"):
                    continue
                raw = await self.storage_driver.download_bytes(f.storage_key)
                aud = PublishAuditRecord.model_validate_json(raw.decode("utf-8"))
                audits.append(aud)
            audits.sort(key=lambda a: a.timestamp)
            return audits
        except Exception as e:
            logger.error("Failed to list publishing audits", job_id=job_id, error=str(e))
            return []
