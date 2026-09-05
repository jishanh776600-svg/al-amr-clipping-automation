"""Durable Cloud Storage Repository for Campaigns."""

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from clipping.agent.campaign.models import CampaignPlatform, CampaignRecord, CampaignStatus
from clipping.logging.logger import get_logger
from clipping.storage.base import StorageDriver

logger = get_logger("clipping.agent.campaign.repository")


class CampaignRepository:
    """
    Durable storage repository for discovered and active campaigns.
    Maintains campaigns in cloud storage driver with an index for rapid retrieval.
    """

    def __init__(self, storage_driver: StorageDriver):
        self.storage = storage_driver

    async def save_campaign(self, campaign: CampaignRecord) -> None:
        key = f"campaigns/{campaign.campaign_id}/record.json"
        data = json.dumps(campaign.model_dump(mode="json"), indent=2).encode("utf-8")
        await self.storage.upload_bytes(data, key, content_type="application/json")
        await self._register_in_index(campaign)
        logger.info("Durable campaign record persisted", campaign_id=campaign.campaign_id, status=campaign.status.value)

    async def get_campaign(self, campaign_id: str) -> Optional[CampaignRecord]:
        key = f"campaigns/{campaign_id}/record.json"
        if not await self.storage.exists(key):
            return None
        data = await self.storage.download_bytes(key)
        return CampaignRecord.model_validate_json(data.decode("utf-8"))

    async def is_known_campaign(self, campaign_id: str) -> bool:
        key = f"campaigns/{campaign_id}/record.json"
        return await self.storage.exists(key)

    async def list_campaigns(
        self,
        status: Optional[CampaignStatus] = None,
        platform: Optional[CampaignPlatform] = None,
    ) -> List[CampaignRecord]:
        index = await self._load_index()
        records: List[CampaignRecord] = []
        for entry in index:
            cid = entry["campaign_id"]
            rec = await self.get_campaign(cid)
            if not rec:
                continue
            if status and rec.status != status:
                continue
            if platform and platform not in rec.required_platforms:
                continue
            records.append(rec)
        return records

    async def _register_in_index(self, campaign: CampaignRecord) -> None:
        index = await self._load_index()
        for idx, entry in enumerate(index):
            if entry["campaign_id"] == campaign.campaign_id:
                index[idx] = {
                    "campaign_id": campaign.campaign_id,
                    "name": campaign.name,
                    "status": campaign.status.value,
                    "updated_at": campaign.updated_at.isoformat(),
                }
                break
        else:
            index.append({
                "campaign_id": campaign.campaign_id,
                "name": campaign.name,
                "status": campaign.status.value,
                "updated_at": campaign.updated_at.isoformat(),
            })

        index_json = json.dumps(index, indent=2).encode("utf-8")
        await self.storage.upload_bytes(index_json, "campaigns/index.json", content_type="application/json")

    async def _load_index(self) -> List[Dict[str, Any]]:
        key = "campaigns/index.json"
        if not await self.storage.exists(key):
            return []
        data = await self.storage.download_bytes(key)
        try:
            return json.loads(data.decode("utf-8"))
        except Exception:
            return []
