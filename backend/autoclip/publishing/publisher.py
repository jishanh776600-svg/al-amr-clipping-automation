"""Publishing dispatcher for AL AMR worker and backward compatibility layer."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from .base import PublishingMetadata
from .instagram import InstagramPublisher
from .service import PublishingService
from .telegram import TelegramPublisher
from .youtube import YouTubePublisher

log = logging.getLogger(__name__)


def publish_to_telegram(
    clip_path: Path,
    caption: str,
    drive_link: str | None = None,
) -> dict[str, Any]:
    """Publish clip to Telegram channel/chat using TelegramPublisher."""
    publisher = TelegramPublisher()
    meta = PublishingMetadata(title=caption)
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                res = pool.submit(asyncio.run, publisher.publish(clip_path, meta, drive_link=drive_link)).result()
        else:
            res = loop.run_until_complete(publisher.publish(clip_path, meta, drive_link=drive_link))
    except RuntimeError:
        res = asyncio.run(publisher.publish(clip_path, meta, drive_link=drive_link))

    if res.success:
        return {"status": "published", "response": res.details, "url": res.url}
    return {"status": res.status, "error": res.error}


def publish_clip(
    clip_path: str | Path,
    title: str,
    platforms: list[str],
    drive_link: str | None = None,
) -> dict[str, Any]:
    """Publish a rendered clip to all requested target platforms synchronously."""
    path = Path(clip_path)
    results: dict[str, Any] = {}

    for platform in platforms:
        norm = platform.strip().lower()
        if norm == "telegram":
            results["telegram"] = publish_to_telegram(path, title, drive_link)
        elif norm == "youtube":
            yt = YouTubePublisher()
            if not yt.is_configured():
                results["youtube"] = {"status": "skipped", "reason": "YOUTUBE credentials not configured"}
            else:
                meta = PublishingMetadata(title=title)
                try:
                    res = asyncio.run(yt.publish(path, meta, drive_link=drive_link, dry_run=True))
                    results["youtube"] = {"status": res.status, "details": res.details, "error": res.error}
                except Exception as exc:
                    results["youtube"] = {"status": "failed", "error": str(exc)}
        elif norm in ("instagram", "meta"):
            ig = InstagramPublisher()
            if not ig.is_configured():
                results["instagram"] = {"status": "skipped", "reason": "Instagram credentials not configured"}
            else:
                meta = PublishingMetadata(title=title)
                try:
                    res = asyncio.run(ig.publish(path, meta, drive_link=drive_link, dry_run=True))
                    results["instagram"] = {"status": res.status, "details": res.details, "error": res.error}
                except Exception as exc:
                    results["instagram"] = {"status": "failed", "error": str(exc)}
        else:
            results[norm] = {"status": "unsupported", "reason": f"Platform {norm} is not supported"}

    return results
