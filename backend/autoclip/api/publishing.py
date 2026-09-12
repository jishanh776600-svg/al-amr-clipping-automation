"""Publishing API endpoints for distribution management and status tracking."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from ..db import models, store
from ..publishing.base import PublishingMetadata
from ..publishing.service import PublishingService
from .schemas import PublishingPlatformInfo, PublishingRecordOut, PublishRequestIn

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["publishing"])
_service = PublishingService()


@router.get("/publishing", response_model=list[PublishingRecordOut])
async def list_publishing(
    job_id: str | None = Query(default=None),
    export_id: str | None = Query(default=None),
    platform: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[PublishingRecordOut]:
    """List publishing records with optional filters."""
    records = await asyncio.to_thread(
        store.list_publishing_records,
        job_id=job_id,
        export_id=export_id,
        platform=platform,
        status=status,
        limit=limit,
    )
    return [PublishingRecordOut.of(r) for r in records]


@router.get("/publishing/platforms", response_model=list[PublishingPlatformInfo])
async def get_publishing_platforms() -> list[PublishingPlatformInfo]:
    """Check configuration and availability of publishing platforms."""
    # Telegram
    has_tg_token = bool(os.getenv("TELEGRAM_BOT_TOKEN"))
    has_tg_chat = bool(os.getenv("TELEGRAM_CHAT_ID"))
    tg_configured = has_tg_token and has_tg_chat
    tg_info = PublishingPlatformInfo(
        platform="telegram",
        available=True,
        configured=tg_configured,
        details="Bot token and Chat ID present" if tg_configured else "Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID",
    )

    # YouTube
    has_yt_id = bool(os.getenv("YOUTUBE_CLIENT_ID"))
    has_yt_sec = bool(os.getenv("YOUTUBE_CLIENT_SECRET"))
    has_yt_tok = bool(os.getenv("YOUTUBE_REFRESH_TOKEN"))
    yt_configured = has_yt_id and has_yt_sec and has_yt_tok
    yt_live = os.getenv("YOUTUBE_PUBLISH_LIVE", "false").lower() in ("true", "1", "yes")
    yt_info = PublishingPlatformInfo(
        platform="youtube",
        available=True,
        configured=yt_configured,
        details=f"OAuth2 configured (Live: {yt_live})" if yt_configured else "Missing YouTube OAuth2 credentials",
    )

    # Instagram
    has_ig_tok = bool(os.getenv("INSTAGRAM_ACCESS_TOKEN"))
    has_ig_acc = bool(os.getenv("INSTAGRAM_ACCOUNT_ID"))
    ig_configured = has_ig_tok and has_ig_acc
    ig_info = PublishingPlatformInfo(
        platform="instagram",
        available=True,
        configured=ig_configured,
        details="Meta Graph API configured" if ig_configured else "Missing INSTAGRAM_ACCESS_TOKEN or INSTAGRAM_ACCOUNT_ID",
    )

    return [tg_info, yt_info, ig_info]


@router.get("/publishing/{record_id}", response_model=PublishingRecordOut)
async def get_publishing_record(record_id: str) -> PublishingRecordOut:
    """Retrieve a single publishing record by ID."""
    record = await asyncio.to_thread(store.get_publishing_record, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Publishing record not found.")
    return PublishingRecordOut.of(record)


@router.post("/publishing/{record_id}/retry", response_model=PublishingRecordOut)
async def retry_publishing(record_id: str) -> PublishingRecordOut:
    """Retry a failed or pending publishing record."""
    record = await asyncio.to_thread(store.get_publishing_record, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Publishing record not found.")

    export = await asyncio.to_thread(store.get_export, record.export_id)
    if not export:
        raise HTTPException(status_code=404, detail="Associated export not found.")

    clip = await asyncio.to_thread(store.get_clip, export.clip_id)
    title = record.metadata.get("title") or (clip.title if clip else "AL AMR Highlight")
    desc = record.metadata.get("description") or (clip.hook if clip else "")
    tags = record.metadata.get("tags") or ["ALAMR", "Shorts"]
    dry_run = bool(record.metadata.get("dry_run", False))

    metadata = PublishingMetadata(
        title=title,
        description=desc,
        tags=tags,
        destination=record.destination,
        extra={"export_id": export.id, "retry": True},
    )

    updated_record = await _service.publish_export(
        export.id,
        record.platform,
        metadata=metadata,
        destination=record.destination,
        dry_run=dry_run,
    )
    return PublishingRecordOut.of(updated_record)


@router.get("/jobs/{job_id}/publishing", response_model=list[PublishingRecordOut])
async def get_job_publishing(job_id: str) -> list[PublishingRecordOut]:
    """Get all publishing records associated with a job."""
    job = await asyncio.to_thread(store.get_job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    records = await asyncio.to_thread(store.list_publishing_records, job_id=job_id, limit=200)
    return [PublishingRecordOut.of(r) for r in records]


@router.post("/exports/{export_id}/publish", response_model=list[PublishingRecordOut])
async def publish_export_endpoint(
    export_id: str,
    request: PublishRequestIn,
) -> list[PublishingRecordOut]:
    """Publish an export to one or more configured target platforms."""
    export = await asyncio.to_thread(store.get_export, export_id)
    if not export:
        raise HTTPException(status_code=404, detail="Export not found.")

    clip = await asyncio.to_thread(store.get_clip, export.clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Associated clip not found.")

    title = request.title or clip.title or "AL AMR Highlight"
    desc = request.description or clip.hook or ""
    tags = request.tags or ["ALAMR", "Shorts"]

    eval_row = await asyncio.to_thread(store.get_campaign_evaluation, clip.id)
    if eval_row and eval_row.campaign_id:
        campaign = await asyncio.to_thread(store.get_campaign, eval_row.campaign_id)
        if campaign and campaign.brief:
            cta = campaign.brief.get("cta_text", "")
            if cta and cta not in desc:
                desc = f"{desc}\n\n{cta}".strip()

    metadata = PublishingMetadata(
        title=title,
        description=desc,
        tags=tags,
        destination=request.destination,
        extra={"export_id": export.id},
    )

    results: list[PublishingRecordOut] = []
    for platform in request.platforms:
        record = await _service.publish_export(
            export.id,
            platform,
            metadata=metadata,
            destination=request.destination,
            dry_run=request.dry_run,
        )
        results.append(PublishingRecordOut.of(record))

    return results
