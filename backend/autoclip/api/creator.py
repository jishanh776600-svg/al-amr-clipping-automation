"""Creator Multi-Tenant & Channel Connection API endpoints.

Enables one-click social account linking (YouTube OAuth, Instagram Meta Graph, and dedicated Telegram bot)
with per-creator passcode authentication and isolated settings.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

from ..db import models, store
from ..publishing.instagram import validate_instagram_credentials
from ..publishing.youtube import (
    YouTubePublisher,
    exchange_youtube_code,
    generate_youtube_auth_url,
    poll_youtube_device_token,
    request_youtube_device_code,
    validate_youtube_credentials,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/creator", tags=["creator"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class CreatorLoginIn(BaseModel):
    passcode: str = Field(..., min_length=4, description="Secret access key provided by admin")


class CreatorCreateIn(BaseModel):
    display_name: str = Field(..., min_length=2)
    passcode: str = Field(default_factory=lambda: f"clp_{uuid.uuid4().hex[:8]}")
    role: str = Field(default="creator")
    daily_quota: int = Field(default=20, ge=1, le=100)


class TelegramConnectIn(BaseModel):
    bot_token: str = Field(..., min_length=15, description="Telegram bot token from @BotFather")
    chat_id: str = Field(..., min_length=3, description="Personal or channel chat ID")


class InstagramConnectIn(BaseModel):
    access_token: str = Field(..., min_length=20)
    account_id: str = Field(..., min_length=5)


class CreatorYouTubeAuthResponse(BaseModel):
    auth_url: str
    redirect_uri: str


class CreatorYouTubeCallbackIn(BaseModel):
    code: str
    redirect_uri: str


class DevicePollIn(BaseModel):
    device_code: str = Field(..., min_length=5)


class CreatorYouTubeDirectIn(BaseModel):
    refresh_token: str = Field(..., min_length=10)
    channel_title: str | None = None
    channel_id: str | None = None


# ---------------------------------------------------------------------------
# Auth Dependency for Creator Accounts
# ---------------------------------------------------------------------------


async def get_current_creator(
    x_creator_passcode: str | None = Header(default=None, alias="X-Creator-Passcode"),
    authorization: str | None = Header(default=None),
) -> models.CreatorAccount:
    """Resolve creator from passcode header or bearer token."""
    passcode = x_creator_passcode
    if not passcode and authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            passcode = parts[1]

    if not passcode:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Creator authentication required. Provide X-Creator-Passcode header or Bearer token.",
        )

    creator = await asyncio.to_thread(store.get_creator_account_by_passcode, passcode)
    if not creator:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or deactivated creator passcode.",
        )
    return creator


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/login")
async def creator_login(payload: CreatorLoginIn) -> dict[str, Any]:
    """Validate creator passcode and return account profile."""
    creator = await asyncio.to_thread(store.get_creator_account_by_passcode, payload.passcode)
    if not creator:
        raise HTTPException(status_code=401, detail="Invalid access passcode.")
    return {
        "success": True,
        "token": creator.passcode,
        "creator": creator.to_dict(),
    }


@router.get("/profile")
async def get_profile(creator: models.CreatorAccount = Depends(get_current_creator)) -> dict[str, Any]:
    """Get active creator account profile with platform connection status."""
    return creator.to_dict()


# --- YouTube One-Click Connect ---


@router.get("/connect/youtube/auth-url", response_model=CreatorYouTubeAuthResponse)
async def get_creator_youtube_auth_url(
    redirect_uri: str = Query(...),
    creator: models.CreatorAccount = Depends(get_current_creator),
) -> CreatorYouTubeAuthResponse:
    """Generate official Google OAuth URL for the creator."""
    pub = YouTubePublisher()
    if not pub.client_id:
        raise HTTPException(
            status_code=400,
            detail="Server Google Client ID is not configured. Admin must set it once in Settings.",
        )
    url = generate_youtube_auth_url(client_id=pub.client_id, redirect_uri=redirect_uri)
    return CreatorYouTubeAuthResponse(auth_url=url, redirect_uri=redirect_uri)


@router.post("/connect/youtube/callback")
async def handle_creator_youtube_callback(
    payload: CreatorYouTubeCallbackIn,
    creator: models.CreatorAccount = Depends(get_current_creator),
) -> dict[str, Any]:
    """Exchange OAuth code and link YouTube channel directly to this creator account."""
    pub = YouTubePublisher()
    if not pub.client_id or not pub.client_secret:
        raise HTTPException(
            status_code=400,
            detail="Server Google OAuth credentials are not configured.",
        )

    res = await exchange_youtube_code(
        client_id=pub.client_id,
        client_secret=pub.client_secret,
        code=payload.code,
        redirect_uri=payload.redirect_uri,
    )

    if not res.get("success"):
        raise HTTPException(
            status_code=400,
            detail=res.get("error", "Failed to exchange YouTube authorization code."),
        )

    # Save to creator's record
    creator.youtube_channel_title = res.get("channel_title")
    creator.youtube_channel_id = res.get("channel_id")
    creator.youtube_refresh_token = res.get("refresh_token")
    await asyncio.to_thread(store.update_creator_account, creator)

    return {
        "success": True,
        "platform": "youtube",
        "channel_title": creator.youtube_channel_title,
        "channel_id": creator.youtube_channel_id,
        "message": f"Successfully connected YouTube channel: {creator.youtube_channel_title}",
    }


@router.post("/connect/youtube/disconnect")
async def disconnect_creator_youtube(
    creator: models.CreatorAccount = Depends(get_current_creator),
) -> dict[str, Any]:
    """Disconnect YouTube channel for this creator."""
    creator.youtube_channel_title = None
    creator.youtube_channel_id = None
    creator.youtube_refresh_token = None
    await asyncio.to_thread(store.update_creator_account, creator)
    return {"success": True, "message": "YouTube channel disconnected."}


@router.post("/connect/youtube/device-code")
async def start_creator_youtube_device_code(
    creator: models.CreatorAccount = Depends(get_current_creator),
) -> dict[str, Any]:
    """Request Google Device Authorization code (google.com/device) with no redirect URIs needed."""
    pub = YouTubePublisher()
    if not pub.client_id:
        raise HTTPException(
            status_code=400,
            detail="Server Google Client ID is not configured. Admin must set it once in Settings.",
        )
    res = await request_youtube_device_code(client_id=pub.client_id)
    if not res.get("success"):
        raise HTTPException(
            status_code=400,
            detail=res.get("error", "Failed to get Google device authorization code."),
        )
    return res


@router.post("/connect/youtube/device-poll")
async def check_creator_youtube_device_poll(
    payload: DevicePollIn,
    creator: models.CreatorAccount = Depends(get_current_creator),
) -> dict[str, Any]:
    """Poll Google OAuth endpoint to see if user has entered their device code."""
    pub = YouTubePublisher()
    if not pub.client_id or not pub.client_secret:
        raise HTTPException(
            status_code=400,
            detail="Server Google OAuth credentials are not configured.",
        )
    res = await poll_youtube_device_token(
        client_id=pub.client_id,
        client_secret=pub.client_secret,
        device_code=payload.device_code,
    )
    if res.get("status") == "authorized":
        creator.youtube_channel_title = res.get("channel_title")
        creator.youtube_channel_id = res.get("channel_id")
        creator.youtube_refresh_token = res.get("refresh_token")
        await asyncio.to_thread(store.update_creator_account, creator)
        return {
            "success": True,
            "status": "authorized",
            "channel_title": creator.youtube_channel_title,
            "channel_id": creator.youtube_channel_id,
            "message": f"Successfully connected YouTube channel: {creator.youtube_channel_title}",
        }
    elif res.get("status") == "pending":
        return {
            "success": True,
            "status": "pending",
            "message": "Waiting for user to enter code...",
        }
    else:
        return {
            "success": False,
            "status": "failed",
            "error": res.get("error", "Authorization failed or expired."),
        }


@router.post("/connect/youtube/direct")
async def connect_creator_youtube_direct(
    payload: CreatorYouTubeDirectIn,
    creator: models.CreatorAccount = Depends(get_current_creator),
) -> dict[str, Any]:
    """Directly link YouTube channel using Refresh Token or OAuth credentials."""
    val = await validate_youtube_credentials(refresh_token=payload.refresh_token.strip())
    channel_title = val.get("channel_title") or payload.channel_title or "YouTube Channel"
    channel_id = val.get("channel_id") or payload.channel_id

    creator.youtube_channel_title = channel_title
    creator.youtube_channel_id = channel_id
    creator.youtube_refresh_token = payload.refresh_token.strip()
    await asyncio.to_thread(store.update_creator_account, creator)

    return {
        "success": True,
        "platform": "youtube",
        "channel_title": creator.youtube_channel_title,
        "channel_id": creator.youtube_channel_id,
        "message": f"Successfully connected YouTube channel: {creator.youtube_channel_title}",
    }


# --- Instagram Connect ---


@router.post("/connect/instagram")
async def connect_creator_instagram(
    payload: InstagramConnectIn,
    creator: models.CreatorAccount = Depends(get_current_creator),
) -> dict[str, Any]:
    """Connect and validate Instagram Professional / Creator account."""
    val = await validate_instagram_credentials(
        access_token=payload.access_token,
        account_id=payload.account_id,
    )
    if not val.get("valid"):
        raise HTTPException(
            status_code=400,
            detail=f"Instagram validation failed: {val.get('error', 'Invalid token or account ID')}",
        )

    creator.instagram_access_token = payload.access_token
    creator.instagram_account_id = payload.account_id
    creator.instagram_account_name = val.get("account_name")
    await asyncio.to_thread(store.update_creator_account, creator)

    return {
        "success": True,
        "platform": "instagram",
        "account_name": creator.instagram_account_name,
        "account_id": creator.instagram_account_id,
        "message": f"Successfully connected Instagram: {creator.instagram_account_name}",
    }


@router.post("/connect/instagram/disconnect")
async def disconnect_creator_instagram(
    creator: models.CreatorAccount = Depends(get_current_creator),
) -> dict[str, Any]:
    """Disconnect Instagram account for this creator."""
    creator.instagram_account_name = None
    creator.instagram_account_id = None
    creator.instagram_access_token = None
    await asyncio.to_thread(store.update_creator_account, creator)
    return {"success": True, "message": "Instagram disconnected."}


# --- Telegram Personal Notification Connect ---


@router.post("/connect/telegram")
async def connect_creator_telegram(
    payload: TelegramConnectIn,
    creator: models.CreatorAccount = Depends(get_current_creator),
) -> dict[str, Any]:
    """Connect dedicated personal Telegram Bot & Chat ID for notifications/reviews."""
    import httpx

    # Validate bot token with Telegram getMe API
    url = f"https://api.telegram.org/bot{payload.bot_token}/getMe"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(url)
            data = resp.json()
            if not data.get("ok"):
                raise HTTPException(status_code=400, detail="Invalid Telegram Bot Token.")
            bot_username = data.get("result", {}).get("username", "bot")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Telegram validation failed: {exc}")

    creator.telegram_bot_token = payload.bot_token
    creator.telegram_chat_id = payload.chat_id
    await asyncio.to_thread(store.update_creator_account, creator)

    return {
        "success": True,
        "platform": "telegram",
        "bot_username": f"@{bot_username}",
        "chat_id": creator.telegram_chat_id,
        "message": f"Connected Telegram bot @{bot_username} to chat {creator.telegram_chat_id}",
    }


@router.post("/connect/telegram/disconnect")
async def disconnect_creator_telegram(
    creator: models.CreatorAccount = Depends(get_current_creator),
) -> dict[str, Any]:
    """Disconnect Telegram for this creator."""
    creator.telegram_bot_token = None
    creator.telegram_chat_id = None
    await asyncio.to_thread(store.update_creator_account, creator)
    return {"success": True, "message": "Telegram notifications disconnected."}


# --- Creator Jobs & Renders ---


@router.get("/jobs")
async def get_creator_jobs(
    limit: int = 15,
    creator: models.CreatorAccount = Depends(get_current_creator),
) -> list[dict[str, Any]]:
    """List recent jobs and generated clips for this creator."""
    all_jobs = await asyncio.to_thread(store.list_jobs, 100)

    creator_jobs = []
    for j in all_jobs:
        cid = (j.settings or {}).get("creator_id")
        if creator.role == "admin" or cid == creator.id:
            creator_jobs.append(j)
        if len(creator_jobs) >= limit:
            break

    results = []
    for j in creator_jobs:
        source = await asyncio.to_thread(store.get_source, j.source_id)
        clips = await asyncio.to_thread(store.list_clips, j.id)
        exports = await asyncio.to_thread(store.list_exports_for_job, j.id)

        exports_by_clip: dict[str, list[dict[str, Any]]] = {}
        for exp in exports:
            exp_created = exp.created_at.isoformat() if hasattr(exp.created_at, "isoformat") else (str(exp.created_at) if exp.created_at else None)
            exports_by_clip.setdefault(exp.clip_id, []).append({
                "id": exp.id,
                "path": exp.path,
                "ratio": exp.ratio,
                "style": exp.style,
                "stream_url": f"/api/exports/{exp.id}/stream",
                "created_at": exp_created,
            })

        clips_data = []
        for c in clips:
            c_dict = {
                "id": c.id,
                "title": c.title,
                "score": c.score,
                "duration_s": round(c.end_s - c.start_s, 1),
                "status": c.status,
                "exports": exports_by_clip.get(c.id, []),
            }
            clips_data.append(c_dict)

        job_created = j.created_at.isoformat() if hasattr(j.created_at, "isoformat") else (str(j.created_at) if j.created_at else None)
        results.append({
            "id": j.id,
            "status": j.status,
            "current_stage": j.current_stage,
            "progress": round(j.progress, 2),
            "error": j.error,
            "created_at": job_created,
            "source_title": source.title if source else "Video Ingest",
            "source_url": source.url if source else None,
            "clips": clips_data,
        })

    return results


# --- Admin User Management ---


@router.get("/admin/users")
async def list_users(creator: models.CreatorAccount = Depends(get_current_creator)) -> list[dict[str, Any]]:
    """List all creator accounts (admin only)."""
    if creator.role != "admin":
        raise HTTPException(status_code=403, detail="Forbidden. Admin access required.")
    users = await asyncio.to_thread(store.list_creator_accounts)
    return [u.to_dict(include_secrets=True) for u in users]


@router.post("/admin/users")
async def create_user(
    payload: CreatorCreateIn,
    creator: models.CreatorAccount = Depends(get_current_creator),
) -> dict[str, Any]:
    """Create a new creator invite passcode (admin only)."""
    if creator.role != "admin":
        raise HTTPException(status_code=403, detail="Forbidden. Admin access required.")

    new_acc = models.CreatorAccount(
        id=f"usr_{uuid.uuid4().hex[:10]}",
        passcode=payload.passcode,
        display_name=payload.display_name,
        role=payload.role,
        daily_quota=payload.daily_quota,
    )
    saved = await asyncio.to_thread(store.create_creator_account, new_acc)
    return saved.to_dict(include_secrets=True)
