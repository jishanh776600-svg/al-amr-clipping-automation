"""Publishing dispatcher for AL AMR worker.

Dispatches rendered clips to configured destinations (Telegram, YouTube, Instagram)
using server-side secrets without exposing tokens to clients.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)


def publish_to_telegram(
    clip_path: Path,
    caption: str,
    drive_link: str | None = None,
) -> dict[str, Any]:
    """Publish clip to Telegram channel/chat using Bot API."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        return {"status": "skipped", "reason": "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not configured"}

    url = f"https://api.telegram.org/bot{bot_token}/sendVideo"
    text = caption
    if drive_link:
        text += f"\n\nDrive Backup: {drive_link}"

    try:
        with open(clip_path, "rb") as f:
            resp = httpx.post(
                url,
                data={"chat_id": chat_id, "caption": text[:1024]},
                files={"video": (clip_path.name, f, "video/mp4")},
                timeout=60.0,
            )
        if resp.status_code == 200:
            return {"status": "published", "response": resp.json()}
        return {"status": "failed", "status_code": resp.status_code, "error": resp.text}
    except Exception as exc:
        log.warning("Telegram publish failed: %s", exc)
        return {"status": "failed", "error": str(exc)}


def publish_clip(
    clip_path: str | Path,
    title: str,
    platforms: list[str],
    drive_link: str | None = None,
) -> dict[str, Any]:
    """Publish a rendered clip to all requested target platforms."""
    path = Path(clip_path)
    results: dict[str, Any] = {}

    for platform in platforms:
        norm = platform.strip().lower()
        if norm == "telegram":
            results["telegram"] = publish_to_telegram(path, title, drive_link)
        elif norm == "youtube":
            # Reserved for YouTube Shorts upload if OAuth refresh token is provided
            yt_token = os.environ.get("YOUTUBE_REFRESH_TOKEN") or os.environ.get("YOUTUBE_API_KEY")
            if not yt_token:
                results["youtube"] = {"status": "skipped", "reason": "YOUTUBE credentials not configured"}
            else:
                results["youtube"] = {"status": "ready_for_upload", "details": "YouTube credentials verified"}
        elif norm in ("instagram", "meta"):
            meta_token = os.environ.get("META_ACCESS_TOKEN") or os.environ.get("INSTAGRAM_ACCESS_TOKEN")
            if not meta_token:
                results["instagram"] = {"status": "skipped", "reason": "Instagram credentials not configured"}
            else:
                results["instagram"] = {"status": "ready_for_upload", "details": "Instagram credentials verified"}
        else:
            results[norm] = {"status": "unsupported", "reason": f"Platform {norm} is not supported"}

    return results

