"""Campaign presets management endpoints."""

from __future__ import annotations

import asyncio
from typing import Any
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from ..campaign import (
    CampaignNormalizer,
    CampaignSpecification,
    IngestedDocument,
    extract_campaign_url,
)
from ..campaign.models import CampaignBrief
from ..db import store
from ..db.models import CampaignPreset, new_id, utcnow
from .schemas import (
    CampaignSpecificationOut,
    CampaignUrlExtractIn,
    CampaignUrlExtractOut,
)

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


# --------------------------------------------------------------------------
# Campaign Intelligence & Multi-Document Ingestion (Step 14)
# --------------------------------------------------------------------------


@router.post("/intelligence/url", response_model=CampaignUrlExtractOut)
async def extract_url_intelligence(payload: CampaignUrlExtractIn) -> CampaignUrlExtractOut:
    """Safely extracts clean campaign text from a remote campaign landing page."""
    extracted = await asyncio.to_thread(extract_campaign_url, payload.url)
    return CampaignUrlExtractOut(
        url=extracted.url,
        title=extracted.title,
        description=extracted.description,
        headings=extracted.headings,
        raw_text=extracted.raw_text,
        status=extracted.status,
        error=extracted.error,
        word_count=extracted.word_count,
        char_count=extracted.char_count,
    )


@router.post("/intelligence/extract", response_model=CampaignSpecificationOut)
async def extract_campaign_intelligence(
    request: Request,
    files: list[UploadFile] = File(default=[]),
) -> CampaignSpecificationOut:
    """Ingests multiple PDF, DOCX, Drive, and URL materials into a unified CampaignSpecification."""
    campaign_url: str | None = None
    drive_urls: list[str] = []
    guideline_ids: list[str] = []
    campaign_id: str | None = None

    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        body = await request.json()
        campaign_url = body.get("campaign_url")
        campaign_id = body.get("campaign_id")
        raw_drive = body.get("drive_urls") or []
        drive_urls = [str(u).strip() for u in raw_drive if str(u).strip()]
        raw_gids = body.get("guideline_ids") or []
        guideline_ids = [str(g).strip() for g in raw_gids if str(g).strip()]
    elif "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
        form = await request.form()
        raw_url = form.get("campaign_url")
        if isinstance(raw_url, str):
            campaign_url = raw_url.strip() or None
        raw_cid = form.get("campaign_id")
        if isinstance(raw_cid, str):
            campaign_id = raw_cid.strip() or None

        raw_drive = form.get("drive_urls")
        if isinstance(raw_drive, str) and raw_drive.strip():
            try:
                import json
                drive_urls = [str(x).strip() for x in json.loads(raw_drive) if str(x).strip()]
            except Exception:
                drive_urls = [u.strip() for u in raw_drive.split(",") if u.strip()]

        raw_gids = form.get("guideline_ids")
        if isinstance(raw_gids, str) and raw_gids.strip():
            try:
                import json
                guideline_ids = [str(x).strip() for x in json.loads(raw_gids) if str(x).strip()]
            except Exception:
                guideline_ids = [g.strip() for g in raw_gids.split(",") if g.strip()]

    normalizer = CampaignNormalizer(campaign_id=campaign_id)
    all_documents: list[IngestedDocument] = []

    # 1. Ingest any uploaded files
    file_tuples: list[tuple[str, bytes]] = []
    for f in files:
        if f and hasattr(f, "filename") and f.filename:
            content = await f.read()
            if content:
                file_tuples.append((f.filename, content))
    if file_tuples:
        file_docs = await asyncio.to_thread(normalizer.ingest_files, file_tuples)
        all_documents.extend(file_docs)

    # 2. Ingest Drive URLs
    if drive_urls:
        drive_docs = await asyncio.to_thread(normalizer.ingest_drive_urls, drive_urls)
        all_documents.extend(drive_docs)

    # 3. Ingest existing guideline IDs
    if guideline_ids:
        for gid in guideline_ids:
            g = await asyncio.to_thread(store.get_guideline, gid)
            if g:
                all_documents.append(IngestedDocument(
                    doc_id=g.id,
                    source_type=g.source_type,
                    filename=g.filename,
                    sha256=g.sha256,
                    size_bytes=g.size_bytes,
                    word_count=g.word_count,
                    char_count=g.char_count,
                    raw_text=g.extracted_text,
                    status="extracted" if g.status == "extracted" else "failed",
                    error=g.error,
                    extracted_at=g.created_at,
                ))

    # 4. Ingest Campaign URL if provided
    if campaign_url:
        url_doc, _ = await asyncio.to_thread(normalizer.ingest_campaign_url, campaign_url)
        if url_doc:
            all_documents.append(url_doc)

    # 5. Normalize and detect conflicts
    spec = await asyncio.to_thread(normalizer.normalize, all_documents, campaign_url)
    return CampaignSpecificationOut.model_validate(spec.to_dict())
