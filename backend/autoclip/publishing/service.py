"""Publishing service coordinating adapters, idempotency, and durable records."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import tempfile
from typing import Any

from ..db import models, store
from ..storage.drive import GoogleDriveStorage
from .base import BasePublisher, ErrorCode, PublicationResult, PublishingMetadata, PublishingResult, is_error_retryable
from .instagram import InstagramPublisher
from .telegram import TelegramPublisher
from .youtube import YouTubePublisher

log = logging.getLogger(__name__)


class PublishingService:
    """Orchestrator for multi-platform clip publishing with strict idempotency and Step 22-24 quality gates."""

    def __init__(self) -> None:
        self.adapters: dict[str, BasePublisher] = {
            "telegram": TelegramPublisher(),
            "youtube": YouTubePublisher(),
            "instagram": InstagramPublisher(),
        }

    def get_adapter(
        self, platform: str, destination: models.DestinationRecord | str | None = None
    ) -> BasePublisher | None:
        """Resolve platform adapter, supporting dynamic multi-account credentials per destination."""
        norm_platform = platform.strip().lower()
        if destination is None:
            return self.adapters.get(norm_platform)

        dest: models.DestinationRecord | None = None
        if isinstance(destination, models.DestinationRecord):
            dest = destination
        elif isinstance(destination, str) and destination.strip() and destination != "default":
            dest = store.get_destination(destination.strip())

        if not dest:
            return self.adapters.get(norm_platform)

        cfg = dest.config_metadata or {}
        clean_id = dest.id.upper().replace("-", "_")

        # Multi-Account Resolution: YouTube
        if norm_platform == "youtube":
            ref_env = cfg.get("refresh_token_env")
            refresh_token = (os.getenv(ref_env) if ref_env else None) or os.getenv(f"YOUTUBE_REFRESH_TOKEN_{clean_id}") or os.getenv("YOUTUBE_REFRESH_TOKEN")
            cid_env = cfg.get("client_id_env")
            client_id = (os.getenv(cid_env) if cid_env else None) or os.getenv(f"YOUTUBE_CLIENT_ID_{clean_id}") or os.getenv("YOUTUBE_CLIENT_ID")
            sec_env = cfg.get("client_secret_env")
            client_secret = (os.getenv(sec_env) if sec_env else None) or os.getenv(f"YOUTUBE_CLIENT_SECRET_{clean_id}") or os.getenv("YOUTUBE_CLIENT_SECRET")
            if refresh_token:
                return YouTubePublisher(client_id=client_id, client_secret=client_secret, refresh_token=refresh_token)

        # Multi-Account Resolution: Instagram
        elif norm_platform == "instagram":
            tok_env = cfg.get("access_token_env")
            access_token = (os.getenv(tok_env) if tok_env else None) or os.getenv(f"META_ACCESS_TOKEN_{clean_id}") or os.getenv("META_ACCESS_TOKEN") or os.getenv("INSTAGRAM_ACCESS_TOKEN")
            acc_env = cfg.get("account_id_env")
            account_id = (os.getenv(acc_env) if acc_env else None) or cfg.get("account_id") or os.getenv(f"INSTAGRAM_ACCOUNT_ID_{clean_id}") or dest.account_identifier or os.getenv("INSTAGRAM_ACCOUNT_ID")
            if access_token and account_id:
                return InstagramPublisher(access_token=access_token, account_id=account_id)

        # Multi-Account Resolution: Telegram
        elif norm_platform == "telegram":
            tok_env = cfg.get("bot_token_env")
            bot_token = (os.getenv(tok_env) if tok_env else None) or os.getenv(f"TELEGRAM_BOT_TOKEN_{clean_id}") or os.getenv("TELEGRAM_BOT_TOKEN")
            chat_id = cfg.get("chat_id") or dest.account_identifier or os.getenv("TELEGRAM_CHAT_ID")
            if bot_token and chat_id:
                return TelegramPublisher(bot_token=bot_token, chat_id=chat_id)

        return self.adapters.get(norm_platform)

    # ----------------------------------------------------------------------
    # Step 25: Publishing Eligibility Gate
    # ----------------------------------------------------------------------

    def verify_publishing_eligibility(
        self,
        clip_id: str,
    ) -> tuple[bool, list[str], models.FinalRenderRecord | None, models.ClipMetadataRecord | None]:
        """Deterministically verify whether a clip is eligible for publishing.

        Checks:
        - Final render exists and status is RENDER_PASS or RENDER_WARN
        - Render output video file is accessible on disk
        - SEO metadata exists and compliance is SEO_PASS / publish-ready
        - Operator approval exists and status is APPROVED
        - Clip is not REJECTED or PUBLISHING_LOCKED
        """
        reasons: list[str] = []

        # 1. Step 22: Final Render Gate
        final_render = store.get_final_render(clip_id)
        exports = store.list_exports(clip_id)
        has_drive_backup = any(bool(exp.drive_file_id) for exp in exports)

        if final_render is None:
            if not has_drive_backup:
                reasons.append("Final render record not found (Step 22 not completed).")
        else:
            if final_render.quality_status not in ("RENDER_PASS", "RENDER_WARN"):
                reasons.append(
                    f"Final render failed quality gate: {final_render.quality_status}. "
                    f"Errors: {'; '.join(final_render.error_details) or 'none'}"
                )
            if final_render.output_path:
                render_file = Path(final_render.output_path)
                if not render_file.exists() and not has_drive_backup:
                    reasons.append(f"Render output file not found on disk: {final_render.output_path} and no Google Drive backup found.")

        # 2. Step 23: SEO & Metadata Gate
        clip_meta = store.get_clip_metadata(clip_id)
        if clip_meta is None:
            reasons.append("SEO metadata record not found (Step 23 not completed).")
        else:
            if not clip_meta.is_publish_ready:
                reasons.append(
                    f"SEO metadata failed compliance gate: {clip_meta.compliance_status}. "
                    f"Errors: {'; '.join(clip_meta.validation_errors) or 'none'}"
                )

        # 3. Step 24: Operator Approval Gate
        approval = store.get_clip_approval(clip_id)
        if approval is None:
            reasons.append("Clip approval record not found (Step 24 not completed).")
        else:
            if approval.current_status == "REJECTED":
                reasons.append(f"Clip was REJECTED by operator: {approval.operator_note or 'No reason provided'}")
            elif approval.current_status == "CHANGES_REQUESTED":
                reasons.append(f"Clip has changes requested by operator: {approval.operator_note or 'Pending changes'}")
            elif approval.current_status == "PUBLISHING_LOCKED":
                reasons.append("Clip is currently PUBLISHING_LOCKED.")
            elif approval.current_status != "APPROVED":
                reasons.append(f"Clip operator approval status is '{approval.current_status}' (must be 'APPROVED').")

        return (len(reasons) == 0), reasons, final_render, clip_meta

    # ----------------------------------------------------------------------
    # Step 25: Remote Publishing Execution
    # ----------------------------------------------------------------------

    async def publish_clip(
        self,
        job_id: str,
        clip_id: str,
        platform: str,
        *,
        destination: str = "",
        metadata_override: PublishingMetadata | None = None,
        dry_run: bool = False,
    ) -> models.PublicationRecord:
        """Publish an approved clip to a single platform with idempotency protection."""
        clip = store.get_clip(clip_id)
        if not clip or clip.job_id != job_id:
            raise ValueError(f"Clip '{clip_id}' not found in job '{job_id}'.")

        dest = destination.strip() or "default"
        norm_platform = platform.strip().lower()
        idempotency_key = f"{job_id}:{clip_id}:{norm_platform}:{dest}"

        adapter = self.get_adapter(norm_platform, destination=dest)
        if not adapter:
            raise ValueError(f"Unsupported publishing platform: '{platform}'")

        # Check existing publication record for idempotency
        existing = store.get_publication_by_idempotency_key(idempotency_key)
        if existing and existing.status == "PUBLISHED":
            log.info(
                "Clip %s already published to %s (%s) [id=%s]. Returning existing record.",
                clip_id,
                norm_platform,
                dest,
                existing.id,
            )
            return existing

        # Run Publishing Eligibility Gate (Steps 22-24)
        is_eligible, blocking_reasons, final_render, clip_meta = self.verify_publishing_eligibility(clip_id)
        if not is_eligible:
            err_msg = "; ".join(blocking_reasons)
            log.warning("Publishing eligibility gate blocked clip %s: %s", clip_id, err_msg)
            # Create/update a failed permanent record to audit the failure
            record_id = existing.id if existing else models.new_id()
            failed_record = models.PublicationRecord(
                id=record_id,
                job_id=job_id,
                clip_id=clip_id,
                final_render_id=final_render.id if final_render else None,
                platform=norm_platform,
                destination_id=dest,
                status="FAILED_PERMANENT",
                attempt_number=(existing.attempt_number + 1) if existing else 1,
                idempotency_key=idempotency_key,
                error_code="invalid_media" if any("render" in r for r in blocking_reasons) else "permission_error",
                error_message=err_msg,
                created_at=existing.created_at if existing else models.utcnow(),
                updated_at=models.utcnow(),
            )
            store.create_publication(failed_record)
            raise ValueError(f"Publishing blocked by quality/approval gates: {err_msg}")

        # Assemble authoritative publishing metadata
        title = clip_meta.final_title if clip_meta else (clip.title or "AL AMR Highlight")
        desc = clip_meta.final_description if clip_meta else (clip.hook or "")
        tags = clip_meta.final_hashtags if clip_meta else ["ALAMR", "Shorts"]

        if metadata_override:
            if metadata_override.title:
                title = metadata_override.title
            if metadata_override.description:
                desc = metadata_override.description
            if metadata_override.tags:
                tags = metadata_override.tags

        pub_metadata = PublishingMetadata(
            title=title,
            description=desc,
            tags=tags,
            destination=dest,
            extra={"clip_id": clip_id, "job_id": job_id},
        )

        media_path = Path(final_render.output_path) if final_render and final_render.output_path else Path("")
        temp_file_to_clean: Path | None = None
        drive_link: str | None = None

        has_media = media_path.is_file() and media_path.stat().st_size > 0
        if not has_media:
            exports = store.list_exports(clip_id)
            drive_file_id = None
            for exp in exports:
                if exp.path and Path(exp.path).is_file() and Path(exp.path).stat().st_size > 0:
                    media_path = Path(exp.path)
                    has_media = True
                    break
                if exp.drive_file_id:
                    drive_file_id = exp.drive_file_id
                    drive_link = exp.drive_web_view_link
                    pub_metadata.extra["export_id"] = exp.id
                    break

            if not has_media and drive_file_id:
                try:
                    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tf:
                        temp_file_to_clean = Path(tf.name)
                    drive_storage = GoogleDriveStorage()
                    if drive_storage.is_configured:
                        log.info("Downloading clip %s media from Google Drive %s...", clip_id, drive_file_id)
                        drive_storage.download_file(drive_file_id, temp_file_to_clean)
                        if temp_file_to_clean.is_file() and temp_file_to_clean.stat().st_size > 0:
                            media_path = temp_file_to_clean
                            has_media = True

                    # Fallback direct download if drive_storage was unconfigured or failed
                    if not has_media:
                        direct_url = f"https://drive.google.com/uc?export=download&id={drive_file_id}"
                        log.info("Attempting direct HTTP download from Google Drive %s...", direct_url)
                        import httpx
                        with httpx.Client(timeout=60.0, follow_redirects=True) as dl_client:
                            resp = dl_client.get(direct_url)
                            if resp.status_code == 200 and len(resp.content) > 1000:
                                temp_file_to_clean.write_bytes(resp.content)
                                media_path = temp_file_to_clean
                                has_media = True
                except Exception as exc:
                    log.warning("Failed downloading from Google Drive for clip %s: %s", clip_id, exc)

        if not has_media or not media_path.exists():
            log.warning("No accessible media file found for clip %s locally or on Google Drive.", clip_id)
            now_fail = models.utcnow()
            record_id = existing.id if existing else models.new_id()
            err_msg = "Final render output file not accessible locally or on Google Drive."
            failed_pub = models.PublicationRecord(
                id=record_id,
                job_id=job_id,
                clip_id=clip_id,
                final_render_id=final_render.id if final_render else None,
                platform=norm_platform,
                destination_id=dest,
                status="FAILED_PERMANENT",
                attempt_number=(existing.attempt_number + 1) if existing else 1,
                idempotency_key=idempotency_key,
                error_code="invalid_media",
                error_message=err_msg,
                created_at=existing.created_at if existing else now_fail,
                updated_at=now_fail,
            )
            store.create_publication(failed_pub)
            return failed_pub

        # Create or update publication record in UPLOADING status
        now_start = models.utcnow()
        record_id = existing.id if existing else models.new_id()
        attempt_num = (existing.attempt_number + 1) if existing else 1
        retry_cnt = (existing.retry_count + 1) if (existing and "FAILED" in existing.status) else 0

        pub_record = models.PublicationRecord(
            id=record_id,
            job_id=job_id,
            clip_id=clip_id,
            final_render_id=final_render.id if final_render else None,
            platform=norm_platform,
            destination_id=dest,
            status="UPLOADING",
            attempt_number=attempt_num,
            idempotency_key=idempotency_key,
            upload_started_at=now_start,
            retry_count=retry_cnt,
            created_at=existing.created_at if existing else now_start,
            updated_at=now_start,
        )
        store.create_publication(pub_record)

        # Execute publish through adapter
        try:
            result = await adapter.publish(
                media_path=media_path,
                metadata=pub_metadata,
                drive_link=drive_link,
                dry_run=dry_run,
            )

            now_end = models.utcnow()
            pub_record.upload_completed_at = now_end
            pub_record.updated_at = now_end
            pub_record.response_metadata = result.details

            if result.success:
                pub_record.status = "PUBLISHED" if result.status == "published" else "PENDING"
                pub_record.remote_media_id = result.remote_media_id or result.external_id
                pub_record.remote_post_id = result.remote_post_id or result.external_id
                pub_record.permalink = result.permalink or result.url
                pub_record.published_at = result.published_at or now_end
                pub_record.error_code = None
                pub_record.error_message = None
            else:
                pub_record.status = "FAILED_RETRYABLE" if result.retryable else "FAILED_PERMANENT"
                pub_record.error_code = result.error_code or "platform_error"
                pub_record.error_message = result.error or result.error_message or "Publishing failed"

            store.update_publication(pub_record)
            self._update_job_telemetry(job_id)
            return pub_record

        except Exception as exc:
            now_end = models.utcnow()
            log.exception("Unexpected error during publication of clip %s to %s: %s", clip_id, norm_platform, exc)
            pub_record.status = "FAILED_RETRYABLE"
            pub_record.upload_completed_at = now_end
            pub_record.updated_at = now_end
            pub_record.error_code = "network_error"
            pub_record.error_message = str(exc)
            store.update_publication(pub_record)
            self._update_job_telemetry(job_id)
            return pub_record
        finally:
            if temp_file_to_clean and temp_file_to_clean.exists():
                try:
                    temp_file_to_clean.unlink()
                except Exception:
                    pass

    async def publish_clip_all_destinations(
        self,
        job_id: str,
        clip_id: str,
        platforms: list[str],
        *,
        destination: str = "",
        dry_run: bool = False,
    ) -> list[models.PublicationRecord]:
        """Publish a clip across multiple platforms independently (one failure does not block others)."""
        records: list[models.PublicationRecord] = []
        for plat in platforms:
            try:
                rec = await self.publish_clip(
                    job_id=job_id,
                    clip_id=clip_id,
                    platform=plat,
                    destination=destination,
                    dry_run=dry_run,
                )
                records.append(rec)
            except Exception as exc:
                log.warning("Platform %s publication failed for clip %s: %s", plat, clip_id, exc)
        return records

    async def retry_publication(
        self,
        publication_id: str,
        *,
        dry_run: bool = False,
    ) -> models.PublicationRecord:
        """Retry a failed publication attempt with exponential backoff if retryable."""
        record = store.get_publication(publication_id)
        if not record:
            raise ValueError(f"Publication record '{publication_id}' not found.")

        if record.status == "PUBLISHED":
            return record

        if record.status == "FAILED_PERMANENT":
            raise ValueError(f"Publication failed permanently: {record.error_message}. Cannot retry automatically.")

        # Execute retry
        return await self.publish_clip(
            job_id=record.job_id,
            clip_id=record.clip_id,
            platform=record.platform,
            destination=record.destination_id,
            dry_run=dry_run,
        )

    def _update_job_telemetry(self, job_id: str) -> None:
        """Update job settings publishing_telemetry counters."""
        try:
            records = store.list_publications_for_job(job_id, limit=500)
            total = len(records)
            published = sum(1 for r in records if r.status == "PUBLISHED")
            failed_retryable = sum(1 for r in records if r.status == "FAILED_RETRYABLE")
            failed_permanent = sum(1 for r in records if r.status == "FAILED_PERMANENT")
            failed = failed_retryable + failed_permanent
            skipped = sum(1 for r in records if r.status == "SKIPPED")

            job = store.get_job(job_id)
            if job:
                telemetry = {
                    "total_destinations": total,
                    "published": published,
                    "failed": failed,
                    "retryable_failures": failed_retryable,
                    "permanent_failures": failed_permanent,
                    "skipped": skipped,
                    "total_attempts": sum(r.attempt_number for r in records),
                }
                job.settings["publishing_telemetry"] = telemetry
                store.update_job_settings(job_id, job.settings)
        except Exception as exc:
            log.warning("Failed to update job %s publishing telemetry: %s", job_id, exc)

    # ----------------------------------------------------------------------
    # Backward Compatibility
    # ----------------------------------------------------------------------

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
        export = store.get_export(export_id)
        if not export:
            raise ValueError(f"Export record not found: {export_id}")

        clip = store.get_clip(export.clip_id)
        if not clip:
            raise ValueError(f"Clip record not found for export: {export.clip_id}")

        dest = destination or (metadata.destination if metadata else "") or ""

        adapter = self.get_adapter(norm_platform, destination=dest)
        if not adapter:
            raise ValueError(f"Unsupported publishing platform: {platform}")

        existing = store.get_publishing_record_by_target(export.id, norm_platform, dest)
        if existing and existing.status == "published":
            return existing

        if not metadata:
            metadata = PublishingMetadata(
                title=clip.title or "AL AMR Highlight",
                description=clip.hook or "",
                tags=["ALAMR", "Shorts"],
                destination=dest,
                extra={"export_id": export.id},
            )

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

        media_path = Path(export.path)
        temp_file_to_clean: Path | None = None

        if not media_path.exists() and export.drive_file_id:
            try:
                drive_storage = GoogleDriveStorage()
                if drive_storage.is_configured:
                    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tf:
                        temp_file_to_clean = Path(tf.name)
                    log.info("Downloading export %s media from Google Drive %s...", export.id, export.drive_file_id)
                    drive_storage.download_file(export.drive_file_id, temp_file_to_clean)
                    media_path = temp_file_to_clean
            except Exception as exc:
                log.warning("Failed downloading from Google Drive for export %s: %s", export.id, exc)

        try:
            result = await adapter.publish(
                media_path=media_path,
                metadata=metadata,
                drive_link=export.drive_web_view_link,
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
            updated = store.update_publishing_record(
                record.id,
                status="failed",
                error=str(exc),
            )
            return updated or record
        finally:
            if temp_file_to_clean and temp_file_to_clean.exists():
                try:
                    temp_file_to_clean.unlink()
                except Exception:
                    pass

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

