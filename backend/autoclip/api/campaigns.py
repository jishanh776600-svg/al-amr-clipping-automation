"""Campaign presets management endpoints."""

from __future__ import annotations

import asyncio
from typing import Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..campaign.models import CampaignBrief
from ..db import store
from ..db.models import CampaignPreset, new_id, utcnow

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])


class CampaignPresetIn(BaseModel):
    id: str | None = None
    name: str
    brief: dict[str, Any] = Field(default_factory=dict)


class CampaignPresetOut(BaseModel):
    id: str
    name: str
    brief: dict[str, Any]
    created_at: str
    updated_at: str

    @classmethod
    def of(cls, preset: CampaignPreset) -> CampaignPresetOut:
        return cls(
            id=preset.id,
            name=preset.name,
            brief=preset.brief,
            created_at=preset.created_at,
            updated_at=preset.updated_at,
        )


@router.get("", response_model=list[CampaignPresetOut])
async def list_campaigns() -> list[CampaignPresetOut]:
    presets = await asyncio.to_thread(store.list_campaigns)
    return [CampaignPresetOut.of(p) for p in presets]


@router.post("", response_model=CampaignPresetOut, status_code=201)
async def create_or_update_campaign(payload: CampaignPresetIn) -> CampaignPresetOut:
    campaign_id = payload.id or new_id()
    # Validate brief through CampaignBrief schema
    try:
        validated_brief = CampaignBrief.model_validate(payload.brief).model_dump(mode="json")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid campaign brief: {exc}") from exc

    preset = CampaignPreset(
        id=campaign_id,
        name=payload.name,
        brief=validated_brief,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    saved = await asyncio.to_thread(store.create_or_update_campaign, preset)
    return CampaignPresetOut.of(saved)


@router.get("/{campaign_id}", response_model=CampaignPresetOut)
async def get_campaign(campaign_id: str) -> CampaignPresetOut:
    preset = await asyncio.to_thread(store.get_campaign, campaign_id)
    if preset is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    return CampaignPresetOut.of(preset)


@router.delete("/{campaign_id}", status_code=204)
async def delete_campaign(campaign_id: str) -> None:
    deleted = await asyncio.to_thread(store.delete_campaign, campaign_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Campaign not found.")
