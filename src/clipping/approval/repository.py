"""Durable Remote Storage Repository for Approval Requests & Audit Records."""

import json
from typing import List, Optional
from clipping.approval.models import ApprovalRequest, ApprovalAuditRecord
from clipping.storage.base import StorageDriver
from clipping.logging.logger import get_logger

logger = get_logger("clipping.approval.repository")


class ApprovalRepository:
    """
    Persists approval requests and audit records into the canonical StorageDriver
    (Google Drive / Local Vault). Ensures zero data loss across ephemeral GitHub Actions jobs.
    """

    def __init__(self, storage_driver: StorageDriver):
        self.storage_driver = storage_driver

    async def save_request(self, request: ApprovalRequest) -> None:
        """Persists or updates an approval request and registers its lookup index."""
        state_key = f"jobs/{request.job_id}/approvals/{request.approval_request_id}.json"
        index_key = f"approvals/by_id/{request.approval_request_id}.json"

        # 1. Upload request document
        data = request.model_dump_json(indent=2).encode("utf-8")
        await self.storage_driver.upload_bytes(data, state_key, content_type="application/json")

        # 2. Upload index pointer for O(1) callback resolution
        pointer = json.dumps({"job_id": request.job_id, "approval_request_id": request.approval_request_id})
        await self.storage_driver.upload_bytes(pointer.encode("utf-8"), index_key, content_type="application/json")

        logger.info(
            "Saved approval request in remote storage",
            approval_request_id=request.approval_request_id,
            job_id=request.job_id,
            status=request.status.value,
        )

    async def get_request(self, job_id: str, request_id: str) -> Optional[ApprovalRequest]:
        """Retrieves an approval request given job_id and request_id."""
        state_key = f"jobs/{job_id}/approvals/{request_id}.json"
        if not await self.storage_driver.exists(state_key):
            return None
        try:
            raw = await self.storage_driver.download_bytes(state_key)
            return ApprovalRequest.model_validate_json(raw.decode("utf-8"))
        except Exception as e:
            logger.error("Failed to read approval request", request_id=request_id, job_id=job_id, error=str(e))
            return None

    async def get_request_by_id(self, request_id: str) -> Optional[ApprovalRequest]:
        """Resolves an approval request directly by request_id via global index lookup."""
        index_key = f"approvals/by_id/{request_id}.json"
        if not await self.storage_driver.exists(index_key):
            return None
        try:
            raw = await self.storage_driver.download_bytes(index_key)
            payload = json.loads(raw.decode("utf-8"))
            job_id = payload.get("job_id")
            if not job_id:
                return None
            return await self.get_request(job_id, request_id)
        except Exception as e:
            logger.error("Failed to resolve approval index", request_id=request_id, error=str(e))
            return None

    async def list_requests_for_job(self, job_id: str) -> List[ApprovalRequest]:
        """Lists all approval requests for a specific job."""
        prefix = f"jobs/{job_id}/approvals/"
        try:
            files = await self.storage_driver.list_files(prefix)
            requests: List[ApprovalRequest] = []
            for f in files:
                k = f.storage_key
                # Exclude audit subfolder
                if "/audit/" in k or not k.endswith(".json"):
                    continue
                raw = await self.storage_driver.download_bytes(k)
                req = ApprovalRequest.model_validate_json(raw.decode("utf-8"))
                requests.append(req)
            requests.sort(key=lambda r: r.clip_index)
            return requests
        except Exception as e:
            logger.error("Failed to list approval requests for job", job_id=job_id, error=str(e))
            return []

    async def record_audit(self, record: ApprovalAuditRecord) -> None:
        """Appends an immutable audit log record to remote storage."""
        audit_key = f"jobs/{record.job_id}/approvals/audit/{record.audit_id}.json"
        data = record.model_dump_json(indent=2).encode("utf-8")
        await self.storage_driver.upload_bytes(data, audit_key, content_type="application/json")
        logger.info(
            "Recorded approval audit event",
            audit_id=record.audit_id,
            approval_request_id=record.approval_request_id,
            new_status=record.new_status.value,
        )

    async def list_audits_for_job(self, job_id: str) -> List[ApprovalAuditRecord]:
        """Retrieves audit trail entries for a specific job."""
        prefix = f"jobs/{job_id}/approvals/audit/"
        try:
            files = await self.storage_driver.list_files(prefix)
            audits: List[ApprovalAuditRecord] = []
            for f in files:
                k = f.storage_key
                if not k.endswith(".json"):
                    continue
                raw = await self.storage_driver.download_bytes(k)
                rec = ApprovalAuditRecord.model_validate_json(raw.decode("utf-8"))
                audits.append(rec)
            audits.sort(key=lambda a: a.timestamp)
            return audits
        except Exception as e:
            logger.error("Failed to list approval audits for job", job_id=job_id, error=str(e))
            return []

    async def list_all_pending_requests(self, limit: int = 50) -> List[ApprovalRequest]:
        """Lists pending approval requests across all jobs."""
        from clipping.approval.models import ApprovalStatus
        try:
            files = await self.storage_driver.list_files("approvals/by_id/")
            pending: List[ApprovalRequest] = []
            for f in files:
                if not f.storage_key.endswith(".json"):
                    continue
                req_id = f.storage_key.split("/")[-1].replace(".json", "")
                req = await self.get_request_by_id(req_id)
                if req and req.status == ApprovalStatus.AWAITING_APPROVAL:
                    pending.append(req)
                    if len(pending) >= limit:
                        break
            pending.sort(key=lambda r: r.score, reverse=True)
            return pending
        except Exception as e:
            logger.error("Failed to list pending approval requests", error=str(e))
            return []

    async def list_all_audits(self, limit: int = 50) -> List[ApprovalAuditRecord]:
        """Lists recent approval decisions and audit trail records across all jobs."""
        try:
            files = await self.storage_driver.list_files("jobs/")
            audit_files = [f for f in files if "/approvals/audit/" in f.storage_key and f.storage_key.endswith(".json")]
            audits: List[ApprovalAuditRecord] = []
            for f in audit_files[:limit]:
                raw = await self.storage_driver.download_bytes(f.storage_key)
                rec = ApprovalAuditRecord.model_validate_json(raw.decode("utf-8"))
                audits.append(rec)
            audits.sort(key=lambda a: a.timestamp, reverse=True)
            return audits[:limit]
        except Exception as e:
            logger.error("Failed to list all approval audits", error=str(e))
            return []
