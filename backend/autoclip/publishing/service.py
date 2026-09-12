"""Publishing service coordinating adapters, idempotency, and durable records."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..db import models, store
from .base import BasePublisher, PublishingMetadata, PublishingResult
from .instagram import InstagramPublisher
from .telegram import TelegramPublisher
from .youtube import YouTubePublisher

log = logging.getLogger(__name__)


class PublishingService:
    """Orchestrator for multi-platform clip publishing with strict idempotency."""

    def __init__(self) -> None:
        self.adapters: dict[str, BasePublisher] = {
            "telegram": TelegramPublisher(),
            "youtube": YouTubePublisher(),
            "instagram": InstagramPublisher(),
        }

    def get_adapter(self, platform: str) -> BasePublisher | None:
        return self.adapters.get(platform.strip().lower())

    async def publish_export(
        self,
        export_id: str,
        platform: str,
        *,
        metadata: PublishingMetadata | None = None,
        destination: str = "",
        dry_run: bool = False,
    ) -> models.PublishingRecord:
        norm_platform = platform.strip().lower()
        adapter = self.get_adapter(norm_platform)
        if not adapter:
            raise ValueError(f"Unsupported publishing platform: {platform}")

        export = store.get_export(export_id)
        if not export:
            raise ValueError(f"Export record not found: {export_id}")

        clip = store.get_clip(export.clip_id)
        if not clip:
            raise ValueError(f"Clip record not found for export: {export.clip_id}")

        dest = destination or metadata.destination if metadata else ""
        dest = dest or ""

        # Check existing record for idempotency
        existing = store.get_publishing_record_by_target(export.id, norm_platform, dest)
        if existing and existing.status == "published":
            log.info(
                "Export %s already published to %s (%s); skipping duplicate publish.",
                export.id,
                norm_platform,
                dest,
            )
            return existing

        # Build default metadata if not provided
        if not metadata:
            title = clip.title or "AL AMR Highlight"
            desc = clip.hook or ""
            tags = ["ALAMR", "Shorts"]

            # Check campaign evaluation or brief if available
            eval_row = store.get_campaign_evaluation(clip.id)
            if eval_row and eval_row.campaign_id:
                campaign = store.get_campaign(eval_row.campaign_id)
                if campaign and campaign.brief:
                    cta = campaign.brief.get("cta_text", "")
                    if cta:
                        desc = f"{desc}\n\n{cta}".strip()

            metadata = PublishingMetadata(
                title=title,
                description=desc,
                tags=tags,
                destination=dest,
                extra={"export_id": export.id},
            )
        else:
            metadata.destination = dest
            metadata.extra["export_id"] = export.id

        # Create or update record in 'publishing' state
        record_id = existing.id if existing else models.new_id()
        record = models.PublishingRecord(
            id=record_id,
            export_id=export.id,
            job_id=clip.job_id,
            platform=norm_platform,
            status="publishing",
            destination=dest,
            metadata={"title": metadata.title, "dry_run": dry_run},
        )
        store.create_or_update_publishing_record(record)

        # Resolve media path
        media_path = Path(export.path)
        drive_link = export.drive_web_view_link

        # Execute publish via adapter
        try:
            result = await adapter.publish(
                media_path=media_path,
                metadata=metadata,
                drive_link=drive_link,
                dry_run=dry_run,
            )

            if result.success:
                final_status = "published" if result.status == "published" else "pending"
                meta_dict = {"url": result.url, **result.details}
                updated = store.update_publishing_record(
                    record.id,
                    status=final_status,
                    external_id=result.external_id,
                    metadata=meta_dict,
                    error=None,
                )
                return updated or record
            else:
                updated = store.update_publishing_record(
                    record.id,
                    status="failed",
                    error=result.error or "Unknown error",
                    metadata={"details": result.details},
                )
                return updated or record
        except Exception as exc:
            log.exception("Unexpected exception publishing export %s to %s: %s", export.id, norm_platform, exc)
            updated = store.update_publishing_record(
                record.id,
                status="failed",
                error=str(exc),
            )
            return updated or record

    async def publish_all_for_job(
        self,
        job_id: str,
        platforms: list[str],
        *,
        dry_run: bool = False,
    ) -> list[models.PublishingRecord]:
        clips = store.list_clips_for_job(job_id)
        records: list[models.PublishingRecord] = []

        for clip in clips:
            exports = store.list_exports(clip.id)
            for exp in exports:
                for plat in platforms:
                    try:
                        rec = await self.publish_export(exp.id, plat, dry_run=dry_run)
                        records.append(rec)
                    except Exception as exc:
                        log.warning("Failed to publish export %s to %s: %s", exp.id, plat, exc)

        return records
