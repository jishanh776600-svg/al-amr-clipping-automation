"""REST endpoints for managing BGM Vault audio assets."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

from autoclip.bgm import BGMUploadMetadata, BGMValidationError, BGMVault
from autoclip.api.schemas import BGMAssetOut, BGMAssetUpdateIn

router = APIRouter(prefix="/api/bgm", tags=["bgm"])


@router.get("", response_model=list[BGMAssetOut])
async def list_bgm(enabled_only: bool = False, genre: str | None = None) -> list[BGMAssetOut]:
    """List background music tracks from the vault."""
    vault = BGMVault()
    assets = await asyncio.to_thread(vault.list_assets, enabled_only=enabled_only, genre=genre)
    return [BGMAssetOut.of(a) for a in assets]


@router.post("", response_model=BGMAssetOut, status_code=status.HTTP_201_CREATED)
async def upload_bgm(
    file: UploadFile = File(...),
    name: str = Form(""),
    genre: str = Form(""),
    mood: str = Form(""),
    tags: str = Form(""),
) -> BGMAssetOut:
    """Upload a new background music track into the vault."""
    vault = BGMVault()
    content = await file.read()
    metadata = BGMUploadMetadata(
        name=name,
        genre=genre,
        mood=mood,
        tags=[t.strip() for t in tags.split(",") if t.strip()],
    )
    try:
        asset = await asyncio.to_thread(
            vault.register_asset,
            source=content,
            filename=file.filename or "bgm.mp3",
            metadata=metadata,
        )
    except BGMValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return BGMAssetOut.of(asset)


@router.get("/{id}", response_model=BGMAssetOut)
async def get_bgm(id: str) -> BGMAssetOut:
    """Retrieve metadata for a specific BGM asset."""
    vault = BGMVault()
    asset = await asyncio.to_thread(vault.get_asset, id)
    if not asset:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"BGM asset {id} not found.")
    return BGMAssetOut.of(asset)


@router.patch("/{id}", response_model=BGMAssetOut)
async def update_bgm(id: str, payload: BGMAssetUpdateIn) -> BGMAssetOut:
    """Update editable metadata or enabled status for a BGM asset."""
    vault = BGMVault()
    asset = await asyncio.to_thread(
        vault.update_asset,
        asset_id=id,
        name=payload.name,
        genre=payload.genre,
        mood=payload.mood,
        tags=payload.tags,
        enabled=payload.enabled,
    )
    if not asset:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"BGM asset {id} not found.")
    return BGMAssetOut.of(asset)


@router.delete("/{id}")
async def delete_bgm(id: str) -> dict[str, Any]:
    """Delete a BGM asset from the vault and disk."""
    vault = BGMVault()
    deleted = await asyncio.to_thread(vault.delete_asset, id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"BGM asset {id} not found.")
    return {"status": "deleted", "id": id}


@router.get("/{id}/stream")
async def stream_bgm(id: str) -> FileResponse:
    """Stream the audio file for browser playback preview."""
    vault = BGMVault()
    asset = await asyncio.to_thread(vault.get_asset, id)
    if not asset:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"BGM asset {id} not found.")
    p = Path(asset.file_path)
    if not p.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Audio file for {id} missing on disk.")
    return FileResponse(path=p, media_type=asset.mime_type, filename=f"{asset.name}{p.suffix}")
