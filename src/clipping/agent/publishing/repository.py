"""Durable Repository for Campaign Submissions and State History."""

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from clipping.agent.publishing.models import CampaignSubmissionRecord, SubmissionStatus
from clipping.agent.vault.models import AccountPlatform
from clipping.logging.logger import get_logger
from clipping.storage.base import StorageDriver

logger = get_logger("clipping.agent.publishing.repository")


class CampaignSubmissionRepository:
    """
    Durable storage repository for campaign submissions.
    Guarantees at-least-once persistence, crash-resilience, and fast lookups by:
    - submission_id
    - campaign_id + clip_id
    - idempotency_key
    """

    def __init__(self, storage_driver: StorageDriver):
        self.storage = storage_driver

    def _record_key(self, campaign_id: str, submission_id: str) -> str:
        return f"submissions/records/{campaign_id}/{submission_id}.json"

    def _clip_ptr_key(self, campaign_id: str, clip_id: str) -> str:
        return f"submissions/by_clip/{campaign_id}_{clip_id}.json"

    def _idempotency_ptr_key(self, idempotency_key: str) -> str:
        safe_key = idempotency_key.replace(":", "_").replace("/", "_")
        return f"submissions/by_idempotency/{safe_key}.json"

    async def save_submission(self, record: CampaignSubmissionRecord) -> None:
        """Persists submission record and updates lookup indices."""
        key = self._record_key(record.campaign_id, record.submission_id)
        data = record.model_dump_json(indent=2).encode("utf-8")
        await self.storage.upload_bytes(data, key, content_type="application/json")

        # Pointer for clip duplicate checking
        clip_ptr = self._clip_ptr_key(record.campaign_id, record.clip_id)
        ptr_payload = json.dumps({
            "campaign_id": record.campaign_id,
            "submission_id": record.submission_id,
        }).encode("utf-8")
        await self.storage.upload_bytes(ptr_payload, clip_ptr, content_type="application/json")

        # Pointer for idempotency checking
        if record.idempotency_key:
            idemp_ptr = self._idempotency_ptr_key(record.idempotency_key)
            await self.storage.upload_bytes(ptr_payload, idemp_ptr, content_type="application/json")

        logger.info(
            "Saved campaign submission",
            submission_id=record.submission_id,
            campaign_id=record.campaign_id,
            status=record.current_status.value,
        )

    async def get_submission(self, campaign_id: str, submission_id: str) -> Optional[CampaignSubmissionRecord]:
        """Retrieves submission record by campaign and submission ID."""
        key = self._record_key(campaign_id, submission_id)
        if not await self.storage.exists(key):
            return None
        try:
            data = await self.storage.download_bytes(key)
            return CampaignSubmissionRecord.model_validate_json(data.decode("utf-8"))
        except Exception as e:
            logger.error("Failed to read submission record", key=key, error=str(e))
            return None

    async def get_submission_by_clip(self, campaign_id: str, clip_id: str) -> Optional[CampaignSubmissionRecord]:
        """Retrieves existing submission for a specific clip in a campaign."""
        ptr_key = self._clip_ptr_key(campaign_id, clip_id)
        if not await self.storage.exists(ptr_key):
            return None
        try:
            ptr_raw = await self.storage.download_bytes(ptr_key)
            ptr_data = json.loads(ptr_raw.decode("utf-8"))
            return await self.get_submission(ptr_data["campaign_id"], ptr_data["submission_id"])
        except Exception as e:
            logger.error("Failed to read clip submission pointer", key=ptr_key, error=str(e))
            return None

    async def get_submission_by_idempotency(self, idempotency_key: str) -> Optional[CampaignSubmissionRecord]:
        """Retrieves submission using deterministic idempotency key."""
        ptr_key = self._idempotency_ptr_key(idempotency_key)
        if not await self.storage.exists(ptr_key):
            return None
        try:
            ptr_raw = await self.storage.download_bytes(ptr_key)
            ptr_data = json.loads(ptr_raw.decode("utf-8"))
            return await self.get_submission(ptr_data["campaign_id"], ptr_data["submission_id"])
        except Exception as e:
            logger.error("Failed to read idempotency submission pointer", key=ptr_key, error=str(e))
            return None

    async def list_submissions(
        self,
        campaign_id: Optional[str] = None,
        platform: Optional[AccountPlatform] = None,
        status: Optional[SubmissionStatus] = None,
        limit: int = 100,
    ) -> List[CampaignSubmissionRecord]:
        """Lists submissions with optional filtering."""
        prefix = f"submissions/records/{campaign_id}/" if campaign_id else "submissions/records/"
        files = await self.storage.list_files(prefix)
        records: List[CampaignSubmissionRecord] = []

        for f in sorted(files, key=lambda x: x.last_modified or datetime.min, reverse=True):
            if not f.storage_key.endswith(".json"):
                continue
            try:
                data = await self.storage.download_bytes(f.storage_key)
                rec = CampaignSubmissionRecord.model_validate_json(data.decode("utf-8"))
                if platform and rec.platform != platform:
                    continue
                if status and rec.current_status != status:
                    continue
                records.append(rec)
                if len(records) >= limit:
                    break
            except Exception as e:
                logger.warning("Error deserializing submission file", key=f.storage_key, error=str(e))

        return records

    async def count_submissions_today(
        self,
        account_id: str,
        campaign_id: Optional[str] = None,
    ) -> int:
        """Counts how many submissions were made today by an account/creator."""
        now = datetime.now(timezone.utc)
        start_of_day = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)

        all_subs = await self.list_submissions(campaign_id=campaign_id, limit=200)
        count = 0
        for s in all_subs:
            if s.account_id == account_id and s.created_at >= start_of_day:
                if s.current_status not in (SubmissionStatus.FAILED, SubmissionStatus.CANCELLED, SubmissionStatus.REJECTED):
                    count += 1
        return count
