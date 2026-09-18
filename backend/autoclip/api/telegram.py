"""Telegram Webhook & Review APIs."""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from ..telegram.review_bot import handle_telegram_update

log = logging.getLogger(__name__)

router = APIRouter(tags=["telegram"])


@router.post("/api/telegram/webhook")
@router.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """Receive live interactive approval callbacks from Telegram Bot API."""
    expected_secret = (os.getenv("TELEGRAM_WEBHOOK_SECRET") or "").strip()
    if not expected_secret:
        try:
            from ..security.vault import get_vault
            v_sec = (
                get_vault().retrieve_secret("telegram_webhook_secret")
                or get_vault().retrieve_secret("TELEGRAM_WEBHOOK_SECRET")
            )
            if v_sec:
                expected_secret = v_sec.strip()
        except Exception:
            pass

    if expected_secret and x_telegram_bot_api_secret_token != expected_secret:
        raise HTTPException(status_code=403, detail="Invalid Telegram webhook secret token.")

    try:
        update = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {exc}")

    result = await handle_telegram_update(update)
    return result
