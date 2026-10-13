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
