"""Durable Repository for Autonomous Campaign Orchestration state."""

from typing import List, Optional
from clipping.agent.orchestration.models import (
    CampaignOrchestrationRecord,
    OrchestrationCycleSummary,
    OrchestrationStage,
)
from clipping.logging.logger import get_logger
from clipping.storage.base import StorageDriver

logger = get_logger("clipping.agent.orchestration.repository")


class OrchestrationRepository:
    """
    Durable storage engine for campaign orchestration records, checkpoints,
    and cycle execution summaries. Backed by StorageDriver (Google Drive or Local Vault).
    """

    def __init__(self, storage_driver: StorageDriver):
        self.storage = storage_driver

    def _record_path(self, campaign_id: str) -> str:
        return f"orchestration/records/{campaign_id}.json"

    def _cycle_path(self, cycle_id: str) -> str:
        return f"orchestration/cycles/{cycle_id}.json"

    async def save_record(self, record: CampaignOrchestrationRecord) -> None:
        """Persists or updates a campaign orchestration record."""
        path = self._record_path(record.campaign_id)
        data = record.model_dump_json(indent=2).encode("utf-8")
        await self.storage.upload_bytes(data, path, content_type="application/json")
        logger.debug(
            "Saved campaign orchestration record",
            campaign_id=record.campaign_id,
            stage=record.current_stage.value,
        )

    async def get_record(self, campaign_id: str) -> Optional[CampaignOrchestrationRecord]:
        """Retrieves a campaign orchestration record by campaign_id."""
        path = self._record_path(campaign_id)
        if not await self.storage.exists(path):
            return None
        try:
            data = await self.storage.download_bytes(path)
            return CampaignOrchestrationRecord.model_validate_json(data.decode("utf-8"))
        except Exception as e:
            logger.warning("Failed to retrieve orchestration record", campaign_id=campaign_id, error=str(e))
            return None

    async def list_records(
        self,
        stage: Optional[OrchestrationStage] = None,
        limit: int = 100,
    ) -> List[CampaignOrchestrationRecord]:
        """Lists orchestration records, optionally filtered by current stage."""
        prefix = "orchestration/records/"
        try:
            files = await self.storage.list_files(prefix)
        except Exception:
            return []

        records: List[CampaignOrchestrationRecord] = []
        for f in files:
            storage_key = f.storage_key if hasattr(f, "storage_key") else str(f)
            if not storage_key.endswith(".json"):
                continue
            try:
                data = await self.storage.download_bytes(storage_key)
                rec = CampaignOrchestrationRecord.model_validate_json(data.decode("utf-8"))
                if stage is None or rec.current_stage == stage:
                    records.append(rec)
            except Exception as e:
                logger.warning("Failed parsing orchestration record", path=storage_key, error=str(e))

        # Sort newest first
        records.sort(key=lambda r: r.updated_at, reverse=True)
        return records[:limit]

    async def save_cycle_summary(self, summary: OrchestrationCycleSummary) -> None:
        """Persists an orchestration cycle execution summary."""
        path = self._cycle_path(summary.cycle_id)
        data = summary.model_dump_json(indent=2).encode("utf-8")
        await self.storage.upload_bytes(data, path, content_type="application/json")
        logger.info(
            "Saved orchestration cycle summary",
            cycle_id=summary.cycle_id,
            status=summary.status,
            discovered=summary.campaigns_discovered,
            dispatched=summary.production_tasks_dispatched,
        )

    async def list_cycle_summaries(self, limit: int = 50) -> List[OrchestrationCycleSummary]:
        """Lists recent orchestration cycle summaries."""
        prefix = "orchestration/cycles/"
        try:
            files = await self.storage.list_files(prefix)
        except Exception:
            return []

        summaries: List[OrchestrationCycleSummary] = []
        for f in files:
            storage_key = f.storage_key if hasattr(f, "storage_key") else str(f)
            if not storage_key.endswith(".json"):
                continue
            try:
                data = await self.storage.download_bytes(storage_key)
                s = OrchestrationCycleSummary.model_validate_json(data.decode("utf-8"))
                summaries.append(s)
            except Exception as e:
                logger.warning("Failed parsing cycle summary", path=storage_key, error=str(e))

        summaries.sort(key=lambda s: s.started_at, reverse=True)
        return summaries[:limit]

    async def get_latest_cycle_summary(self) -> Optional[OrchestrationCycleSummary]:
        """Retrieves the most recent cycle summary."""
        summaries = await self.list_cycle_summaries(limit=1)
        return summaries[0] if summaries else None
