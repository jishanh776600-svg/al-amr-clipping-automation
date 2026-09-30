"""Publishing service coordinating adapters, idempotency, and durable records."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import re
import tempfile
from typing import Any

from ..db import models, store
from .. import paths
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
        dest: models.DestinationRecord | None = None

        if isinstance(destination, models.DestinationRecord):
            dest = destination
        elif isinstance(destination, str) and destination.strip() and destination != "default":
            dest = store.get_destination(destination.strip())

        if not dest:
            # Fall back to canonical destination if registered
            dest = store.get_destination(f"dest-{norm_platform}-main")

        if not dest:
            return self.adapters.get(norm_platform)

        cfg = dest.config_metadata or {}
        clean_id = dest.id.upper().replace("-", "_")

        # Multi-Account Resolution: YouTube
        if norm_platform == "youtube":
            ref_env = cfg.get("refresh_token_env")
            refresh_token = (os.getenv(ref_env) if ref_env else None) or os.getenv(f"YOUTUBE_REFRESH_TOKEN_{clean_id}")
            cid_env = cfg.get("client_id_env")
            client_id = (os.getenv(cid_env) if cid_env else None) or os.getenv(f"YOUTUBE_CLIENT_ID_{clean_id}")
            sec_env = cfg.get("client_secret_env")
            client_secret = (os.getenv(sec_env) if sec_env else None) or os.getenv(f"YOUTUBE_CLIENT_SECRET_{clean_id}")
            return YouTubePublisher(client_id=client_id, client_secret=client_secret, refresh_token=refresh_token)

        # Multi-Account Resolution: Instagram
        elif norm_platform == "instagram":
            tok_env = cfg.get("access_token_env")
            access_token = (os.getenv(tok_env) if tok_env else None) or os.getenv(f"META_ACCESS_TOKEN_{clean_id}")
            acc_env = cfg.get("account_id_env")
            account_id = (os.getenv(acc_env) if acc_env else None) or cfg.get("account_id") or os.getenv(f"INSTAGRAM_ACCOUNT_ID_{clean_id}")
            if dest and dest.account_identifier and dest.account_identifier not in ("Instagram Account", "default"):
                account_id = account_id or dest.account_identifier
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
        - Render output video file is accessible on disk or via Google Drive backup
        - SEO metadata exists and compliance is SEO_PASS / publish-ready
        - Operator approval exists and status is APPROVED
        - Clip is not REJECTED or PUBLISHING_LOCKED
        """
        reasons: list[str] = []

        # 1. Step 22: Final Render Gate
        final_render = store.get_final_render(clip_id)
        approval = store.get_clip_approval(clip_id)
        exports = store.list_exports(clip_id)
        has_drive_backup = any(bool(exp.drive_file_id) for exp in exports) or bool(
            final_render and final_render.telemetry and final_render.telemetry.get("drive_file_id")
        )
        has_local_media = any(bool(exp.path and Path(exp.path).is_file()) for exp in exports)
        has_telegram_media = bool(
            (final_render and final_render.telemetry and final_render.telemetry.get("telegram_file_id"))
            or (approval and approval.telemetry and approval.telemetry.get("telegram_file_id"))
        )

        if final_render is None:
            if not has_drive_backup and not has_local_media and not has_telegram_media:
                reasons.append("Final render record not found (Step 22 not completed).")
        else:
            if final_render.quality_status not in ("RENDER_PASS", "RENDER_WARN"):
                reasons.append(
                    f"Final render failed quality gate: {final_render.quality_status}. "
                    f"Errors: {'; '.join(final_render.error_details) or 'none'}"
                )
            if final_render.output_path:
                render_file = Path(final_render.output_path)
                if not render_file.exists() and not has_drive_backup and not has_local_media and not has_telegram_media:
                    reasons.append(f"Render output file not found on disk: {final_render.output_path} and no Google Drive or Telegram backup found.")

        # 2. Step 23: SEO & Metadata Gate
        clip_meta = store.get_clip_metadata(clip_id)
        if clip_meta is None:
            reasons.append("SEO metadata record not found (Step 23 not completed).")
        else:
            if clip_meta.compliance_status not in ("SEO_PASS", "SEO_WARN"):
                reasons.append(
                    f"SEO metadata failed compliance gate: {clip_meta.compliance_status}. "
                    f"Errors: {'; '.join(clip_meta.validation_errors) or 'none'}"
                )

        # 3. Step 24: Operator Approval Gate
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

        norm_platform = platform.strip().lower()
        dest = destination.strip() or f"dest-{norm_platform}-main"
        idempotency_key = f"{job_id}:{clip_id}:{norm_platform}:{dest}"

        adapter = self.get_adapter(norm_platform, destination=dest)
        if not adapter:
            raise ValueError(f"Unsupported publishing platform: '{platform}'")

        # Check existing publication record for idempotency & retry safety
        existing = store.get_publication_by_idempotency_key(idempotency_key)
        if existing and existing.status == "PUBLISHED":
            # For YouTube, post-verify that the existing video is genuinely valid and public on the expected channel
            if norm_platform == "youtube" and existing.remote_media_id and isinstance(adapter, YouTubePublisher):
                try:
                    from .youtube import resolve_expected_youtube_channel_id
                    exp_ch = resolve_expected_youtube_channel_id()
                    if exp_ch and adapter.is_configured():
                        creds = adapter._get_credentials()
                        from googleapiclient.discovery import build
                        yt_service = build("youtube", "v3", credentials=creds, cache_discovery=False)
                        verified, v_stat, v_info = await adapter.verify_video_publication(
                            yt_service, existing.remote_media_id, exp_ch, wait_for_processing=False
                        )
                        if verified:
                            log.info("Clip %s verified existing YouTube publication [id=%s].", clip_id, existing.id)
                            return existing
                        elif v_stat == "channel_mismatch":
                            log.warning("Existing YouTube publication is on WRONG channel (%s). Automatic modification prohibited.", v_info.get("channelId"))
                            existing.status = "FAILED_PERMANENT"
                            existing.error_code = "channel_mismatch"
                            existing.error_message = f"Existing video is on wrong channel ({v_info.get('channelId')}). Automatic modification prohibited."
                            store.update_publication(existing)
                            return existing
                        elif v_stat == "visibility_incorrect":
                            log.warning("Existing YouTube publication visibility is %s and could not be corrected.", v_info.get("privacyStatus"))
                            existing.status = "FAILED_PERMANENT"
                            existing.error_code = "visibility_incorrect"
                            existing.error_message = f"Existing video visibility is '{v_info.get('privacyStatus')}'; expected public."
                            store.update_publication(existing)
                            return existing
                except Exception as ex_ver:
                    log.warning("Could not post-verify existing YouTube record: %s", ex_ver)
            else:
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

        # Assemble authoritative publishing metadata (platform-specific resolution)
        raw_title = clip_meta.final_title if clip_meta else (clip.title or "Key Insight & Breakdown")
        raw_title = re.sub(r"\bAL\s*AMR\s*Highlight\b", "Key Insight", raw_title, flags=re.IGNORECASE)
        raw_title = re.sub(r"\b[a-f0-9]{8,16}\b", "", raw_title).strip()
        raw_title = re.sub(r"\.{2,}", "", raw_title).strip().rstrip(".!?,;: ")
        if raw_title.islower():
            raw_title = raw_title.title()
        title = raw_title or (clip.hook.title() if clip and clip.hook else "Key Insight & Breakdown")

        raw_desc = clip_meta.final_description if clip_meta else (clip.hook or "")
        raw_desc = re.sub(r"Archive Backup:[^\n\r]+", "", raw_desc, flags=re.IGNORECASE).strip()
        raw_desc = re.sub(r"Reconciled from Telegram[^\n\r]*", "", raw_desc, flags=re.IGNORECASE).strip()
        desc = raw_desc or (clip.hook if clip else "")

        raw_tags = clip_meta.final_hashtags if clip_meta else ["Shorts", "Trending", "Viral"]
        tags = [t for t in raw_tags if not any(b in t.lower() for b in ("alamr", "autoclip"))]
        if not tags:
            tags = ["Shorts", "Trending", "Viral"]

        # Platform-specific resolution: NEVER assume YouTube SEO == Instagram SEO
        if clip_meta and clip_meta.telemetry:
            if norm_platform == "instagram":
                ig_data = clip_meta.telemetry.get("instagram") or {}
                if ig_data.get("caption"):
                    desc = ig_data["caption"]
                if ig_data.get("hashtags"):
                    tags = list(ig_data["hashtags"])
                if ig_data.get("first_line_hook"):
                    title = ig_data["first_line_hook"]
            elif norm_platform == "youtube":
                yt_data = clip_meta.telemetry.get("youtube") or {}
                if yt_data.get("title"):
                    title = yt_data["title"]
                if yt_data.get("description"):
                    desc = yt_data["description"]
                if yt_data.get("tags") or yt_data.get("hashtags"):
                    tags = list(yt_data.get("tags") or yt_data.get("hashtags"))

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
            privacy="public",
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

            if not drive_file_id and final_render and final_render.telemetry:
                drive_file_id = final_render.telemetry.get("drive_file_id")
                drive_link = final_render.telemetry.get("drive_web_view_link")

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

            # Fallback direct download from Telegram Bot API if file_id is available
            if not has_media:
                tg_file_id = None
                if final_render and final_render.telemetry:
                    tg_file_id = final_render.telemetry.get("telegram_file_id")
                if not tg_file_id:
                    appr = store.get_clip_approval(clip_id)
                    if appr and appr.telemetry:
                        tg_file_id = appr.telemetry.get("telegram_file_id")

                if tg_file_id:
                    from ..telegram.review_bot import get_telegram_config
                    tg_token, _, _ = get_telegram_config()
                    if tg_token:
                        try:
                            import httpx
                            if not temp_file_to_clean:
                                with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tf:
                                    temp_file_to_clean = Path(tf.name)
                            log.info("Downloading clip %s media from Telegram Bot API (file_id=%s)...", clip_id, tg_file_id)
                            with httpx.Client(timeout=60.0) as dl_client:
                                info_resp = dl_client.get(
                                    f"https://api.telegram.org/bot{tg_token}/getFile",
                                    params={"file_id": tg_file_id},
                                )
                                if info_resp.status_code == 200 and info_resp.json().get("ok"):
                                    rel_path = info_resp.json()["result"].get("file_path")
                                    if rel_path:
                                        file_url = f"https://api.telegram.org/file/bot{tg_token}/{rel_path}"
                                        file_resp = dl_client.get(file_url)
                                        if file_resp.status_code == 200 and len(file_resp.content) > 1000:
                                            temp_file_to_clean.write_bytes(file_resp.content)
                                            media_path = temp_file_to_clean
                                            has_media = True
                                            log.info(
                                                "Successfully downloaded media for clip %s from Telegram (%d bytes)",
                                                clip_id,
                                                len(file_resp.content),
                                            )
                        except Exception as exc:
                            log.warning("Failed downloading from Telegram for clip %s: %s", clip_id, exc)

        if not has_media or not media_path.exists():
            log.warning("No accessible media file found for clip %s locally, on Google Drive, or on Telegram.", clip_id)
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

        # Ensure media is cached locally in media_cache for public platform crawlers (Meta Instagram Reels)
        cache_dir = paths.root() / "media_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cached_dest = cache_dir / f"clip_{clip_id}.mp4"
        if media_path and media_path.is_file() and media_path.stat().st_size > 1000:
            if not cached_dest.is_file() or cached_dest.stat().st_size != media_path.stat().st_size:
                try:
                    import shutil
                    shutil.copy2(media_path, cached_dest)
                    log.info("Persisted clip %s media to %s (%d bytes)", clip_id, cached_dest, media_path.stat().st_size)
                except Exception as c_err:
                    log.warning("Could not copy clip %s to media_cache: %s", clip_id, c_err)

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

            if result.success and result.status == "published":
                pub_record.status = "PUBLISHED"
                pub_record.remote_media_id = result.remote_media_id or result.external_id
                pub_record.remote_post_id = result.remote_post_id or result.external_id
                pub_record.permalink = result.permalink or result.url
                pub_record.published_at = result.published_at or now_end
                pub_record.error_code = None
                pub_record.error_message = None
            elif result.error_code == "processing_incomplete" or result.status == "processing":
                pub_record.status = "PROCESSING"
                pub_record.remote_media_id = result.remote_media_id or result.external_id
                pub_record.remote_post_id = result.remote_post_id or result.external_id
                pub_record.permalink = result.permalink or result.url
                pub_record.error_code = result.error_code
                pub_record.error_message = result.error or "Upload accepted, video still processing"
            else:
                pub_record.status = "FAILED_RETRYABLE" if result.retryable else "FAILED_PERMANENT"
                pub_record.remote_media_id = result.remote_media_id or result.external_id
                pub_record.remote_post_id = result.remote_post_id or result.external_id
                pub_record.permalink = result.permalink or result.url
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
            norm_plat = plat.strip().lower()
            target_dest = destination.strip() or f"dest-{norm_plat}-main"
            try:
                rec = await self.publish_clip(
                    job_id=job_id,
                    clip_id=clip_id,
                    platform=norm_plat,
                    destination=target_dest,
                    dry_run=dry_run,
                )
                records.append(rec)
            except Exception as exc:
                log.warning("Platform %s publication failed for clip %s: %s", plat, clip_id, exc)
                key = f"{job_id}:{clip_id}:{norm_plat}:{target_dest}"
                failed_rec = store.get_publication_by_idempotency_key(key)
                if failed_rec:
                    records.append(failed_rec)
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
            clean_title = (clip.title or (clip.hook.title() if clip.hook else "Key Insight & Lesson")).strip()
            clean_title = re.sub(r"\bAL\s*AMR\s*Highlight\b", "Key Insight", clean_title, flags=re.IGNORECASE)
            clean_title = re.sub(r"\.{2,}", "", clean_title).strip().rstrip(".!?,;: ")
            metadata = PublishingMetadata(
                title=clean_title.title() if clean_title.islower() else (clean_title or "Key Insight & Lesson"),
                description=clip.hook or "",
                tags=["Shorts", "Trending", "Viral"],
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

