"""Publishing API endpoints for distribution management and status tracking."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query

from ..db import models, store
from ..publishing.base import PublishingMetadata
from ..publishing.orchestrator import PublishingOrchestrator
from ..publishing.service import PublishingService
from .schemas import (
    CancelQueueIn,
    DestinationIn,
    DestinationOut,
    DestinationUpdateIn,
    PlatformValidationResponse,
    PublicationOut,
    PublishAllClipsIn,
    PublishClipIn,
    PublishingPlatformInfo,
    PublishingRecordOut,
    PublishingTelemetryOut,
    PublishRequestIn,
    QueueItemOut,
    QueueTelemetryOut,
    RescheduleQueueIn,
    SchedulePublicationIn,
    YouTubeAuthUrlResponse,
    YouTubeCallbackRequest,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["publishing"])
_service = PublishingService()
_orchestrator = PublishingOrchestrator(_service)



@router.get("/publishing", response_model=list[PublishingRecordOut])
async def list_publishing(
    job_id: str | None = Query(default=None),
    export_id: str | None = Query(default=None),
    platform: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[PublishingRecordOut]:
    """List publishing records with optional filters."""
    records = await asyncio.to_thread(
        store.list_publishing_records,
        job_id=job_id,
        export_id=export_id,
        platform=platform,
        status=status,
        limit=limit,
    )
    return [PublishingRecordOut.of(r) for r in records]


@router.get("/publishing/platforms", response_model=list[PublishingPlatformInfo])
async def get_publishing_platforms() -> list[PublishingPlatformInfo]:
    """Check configuration, authentication, and live status of all publishing platforms."""
    from datetime import datetime, timezone
    from ..security.vault import get_vault
    from ..publishing.telegram import TelegramPublisher, validate_telegram_credentials
    from ..publishing.youtube import YouTubePublisher, validate_youtube_credentials
    from ..publishing.instagram import InstagramPublisher, validate_instagram_credentials

    now_iso = datetime.now(timezone.utc).isoformat()
    vault = get_vault()

    # 1. Telegram Bot
    tg_pub = TelegramPublisher()
    tg_configured = tg_pub.is_configured()
    tg_auth = False
    tg_name = None
    tg_ident = tg_pub.chat_id if tg_configured else None
    tg_err = None
    tg_details = "Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID"

    if tg_configured:
        try:
            tg_val = await asyncio.wait_for(
                validate_telegram_credentials(tg_pub.bot_token, tg_pub.chat_id),
                timeout=4.0,
            )
            if tg_val.get("valid"):
                tg_auth = True
                tg_name = f"@{tg_val.get('bot_username')}" if tg_val.get("bot_username") else tg_val.get("bot_name")
                tg_details = f"Connected: {tg_name} (Chat: {tg_val.get('chat_title') or tg_ident})"
            else:
                tg_err = tg_val.get("error")
                tg_details = f"Configured (Auth failed: {tg_err})"
        except Exception as exc:
            tg_auth = False
            tg_err = str(exc)
            tg_details = "Configured (Validation timed out or unreachable)"

    tg_info = PublishingPlatformInfo(
        platform="telegram",
        available=True,
        configured=tg_configured,
        authenticated=tg_auth,
        account_identifier=tg_ident,
        account_name=tg_name,
        last_validated_at=now_iso if tg_configured else None,
        details=tg_details,
        error=tg_err,
    )

    # 2. YouTube Shorts
    yt_pub = YouTubePublisher()
    yt_configured = yt_pub.is_configured() and bool(yt_pub.client_id and yt_pub.client_secret)
    yt_auth = False
    yt_name = None
    yt_ident = None
    yt_err = None
    yt_details = "Missing YouTube OAuth2 credentials (Client ID, Secret, or Refresh Token)"

    if yt_configured:
        try:
            yt_val = await asyncio.wait_for(
                validate_youtube_credentials(yt_pub.client_id, yt_pub.client_secret, yt_pub.refresh_token),
                timeout=5.0,
            )
            if yt_val.get("valid"):
                yt_auth = True
                yt_name = yt_val.get("channel_title")
                yt_ident = yt_val.get("channel_id")
                yt_details = f"Connected: {yt_name}"
            else:
                yt_err = yt_val.get("error")
                yt_details = f"Configured (Auth failed: {yt_err})"
        except Exception as exc:
            yt_auth = False
            yt_err = str(exc)
            yt_details = "Configured (Validation timed out or unreachable)"

    yt_info = PublishingPlatformInfo(
        platform="youtube",
        available=True,
        configured=yt_configured,
        authenticated=yt_auth,
        account_identifier=yt_ident,
        account_name=yt_name,
        last_validated_at=now_iso if yt_configured else None,
        details=yt_details,
        error=yt_err,
    )

    # 3. Instagram Reels
    ig_pub = InstagramPublisher()
    ig_configured = ig_pub.is_configured()
    ig_auth = False
    ig_name = None
    ig_ident = ig_pub.account_id if ig_configured else None
    ig_err = None
    ig_details = "Missing INSTAGRAM_ACCESS_TOKEN or INSTAGRAM_ACCOUNT_ID"

    if ig_configured:
        try:
            ig_val = await asyncio.wait_for(
                validate_instagram_credentials(ig_pub.access_token, ig_pub.account_id),
                timeout=5.0,
            )
            if ig_val.get("valid"):
                ig_auth = True
                ig_name = ig_val.get("account_name")
                ig_ident = ig_val.get("account_id")
                ig_details = f"Connected: {ig_name}"
            else:
                ig_err = ig_val.get("error")
                ig_details = f"Configured (Auth failed: {ig_err})"
        except Exception as exc:
            ig_auth = False
            ig_err = str(exc)
            ig_details = "Configured (Validation timed out or unreachable)"

    ig_info = PublishingPlatformInfo(
        platform="instagram",
        available=True,
        configured=ig_configured,
        authenticated=ig_auth,
        account_identifier=ig_ident,
        account_name=ig_name,
        last_validated_at=now_iso if ig_configured else None,
        details=ig_details,
        error=ig_err,
    )

    return [tg_info, yt_info, ig_info]


@router.get("/publishing/youtube/auth-url", response_model=YouTubeAuthUrlResponse)
async def get_youtube_auth_url(redirect_uri: str = Query(...)) -> YouTubeAuthUrlResponse:
    """Generate the Google OAuth consent URL using configured client_id."""
    from ..publishing.youtube import YouTubePublisher, generate_youtube_auth_url

    pub = YouTubePublisher()
    if not pub.client_id:
        raise HTTPException(
            status_code=400,
            detail="YouTube Client ID is not configured. Please save it in Settings first.",
        )
    url = generate_youtube_auth_url(client_id=pub.client_id, redirect_uri=redirect_uri)
    return YouTubeAuthUrlResponse(auth_url=url, redirect_uri=redirect_uri)


@router.post("/publishing/youtube/callback", response_model=PlatformValidationResponse)
async def handle_youtube_callback(payload: YouTubeCallbackRequest) -> PlatformValidationResponse:
    """Exchange authorization code for refresh token, save in vault, and validate channel."""
    from ..publishing.youtube import YouTubePublisher, exchange_youtube_code

    pub = YouTubePublisher()
    if not pub.client_id or not pub.client_secret:
        raise HTTPException(
            status_code=400,
            detail="YouTube Client ID and Client Secret must be configured in Settings before completing OAuth.",
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

    return PlatformValidationResponse(
        platform="youtube",
        valid=True,
        configured=True,
        account_name=res.get("channel_title"),
        details=f"Connected YouTube channel: {res.get('channel_title')}",
        error=None,
    )


@router.post("/publishing/youtube/validate", response_model=PlatformValidationResponse)
async def validate_youtube() -> PlatformValidationResponse:
    """Validate current YouTube credentials from vault."""
    from ..publishing.youtube import validate_youtube_credentials

    res = await validate_youtube_credentials()
    return PlatformValidationResponse(
        platform="youtube",
        valid=res.get("valid", False),
        configured=res.get("configured", False),
        account_name=res.get("channel_title"),
        details=res.get("channel_title") or res.get("error") or "",
        error=res.get("error"),
    )


@router.post("/publishing/youtube/disconnect", status_code=204)
async def disconnect_youtube() -> None:
    """Remove YouTube refresh token from encrypted vault."""
    from ..config import delete_secret

    delete_secret("youtube_refresh_token")
    log.info("YouTube account successfully disconnected by operator.")


@router.post("/publishing/telegram/validate", response_model=PlatformValidationResponse)
async def validate_telegram() -> PlatformValidationResponse:
    """Validate current Telegram credentials from vault."""
    from ..publishing.telegram import validate_telegram_credentials

    res = await validate_telegram_credentials()
    return PlatformValidationResponse(
        platform="telegram",
        valid=res.get("valid", False),
        configured=res.get("configured", False),
        account_name=res.get("bot_username") or res.get("bot_name"),
        details=f"Bot @{res.get('bot_username')}" if res.get("bot_username") else (res.get("error") or ""),
        error=res.get("error"),
    )


@router.post("/publishing/instagram/validate", response_model=PlatformValidationResponse)
async def validate_instagram() -> PlatformValidationResponse:
    """Validate current Instagram credentials from vault."""
    from ..publishing.instagram import validate_instagram_credentials

    res = await validate_instagram_credentials()
    return PlatformValidationResponse(
        platform="instagram",
        valid=res.get("valid", False),
        configured=res.get("configured", False),
        account_name=res.get("account_name"),
        details=res.get("account_name") or res.get("error") or "",
        error=res.get("error"),
    )


@router.get("/publishing/{record_id}", response_model=PublishingRecordOut)
async def get_publishing_record(record_id: str) -> PublishingRecordOut:
    """Retrieve a single publishing record by ID."""
    record = await asyncio.to_thread(store.get_publishing_record, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Publishing record not found.")
    return PublishingRecordOut.of(record)


@router.post("/publishing/{record_id}/retry", response_model=PublishingRecordOut)
async def retry_publishing(record_id: str) -> PublishingRecordOut:
    """Retry a failed or pending publishing record."""
    record = await asyncio.to_thread(store.get_publishing_record, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Publishing record not found.")

    export = await asyncio.to_thread(store.get_export, record.export_id)
    if not export:
        raise HTTPException(status_code=404, detail="Associated export not found.")

    clip = await asyncio.to_thread(store.get_clip, export.clip_id)
    title = record.metadata.get("title") or (clip.title if clip else "AL AMR Highlight")
    desc = record.metadata.get("description") or (clip.hook if clip else "")
    tags = record.metadata.get("tags") or ["ALAMR", "Shorts"]
    dry_run = bool(record.metadata.get("dry_run", False))

    metadata = PublishingMetadata(
        title=title,
        description=desc,
        tags=tags,
        destination=record.destination,
        extra={"export_id": export.id, "retry": True},
    )

    updated_record = await _service.publish_export(
        export.id,
        record.platform,
        metadata=metadata,
        destination=record.destination,
        dry_run=dry_run,
    )
    return PublishingRecordOut.of(updated_record)


@router.get("/jobs/{job_id}/publishing", response_model=list[PublishingRecordOut])
async def get_job_publishing(job_id: str) -> list[PublishingRecordOut]:
    """Get all publishing records associated with a job."""
    job = await asyncio.to_thread(store.get_job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    records = await asyncio.to_thread(store.list_publishing_records, job_id=job_id, limit=200)
    return [PublishingRecordOut.of(r) for r in records]


@router.post("/exports/{export_id}/publish", response_model=list[PublishingRecordOut])
async def publish_export_endpoint(
    export_id: str,
    request: PublishRequestIn,
) -> list[PublishingRecordOut]:
    """Publish an export to one or more configured target platforms."""
    export = await asyncio.to_thread(store.get_export, export_id)
    if not export:
        raise HTTPException(status_code=404, detail="Export not found.")

    clip = await asyncio.to_thread(store.get_clip, export.clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Associated clip not found.")

    # Step 22 Quality Gate Publishing Guard
    final_render = await asyncio.to_thread(store.get_final_render, clip.id)
    if final_render and not final_render.is_approved:
        rejection_msg = "; ".join(final_render.error_details) or "Quality gate rejected this clip render."
        raise HTTPException(
            status_code=400,
            detail=f"Cannot publish clip '{clip.id}': Final render failed quality gate ({rejection_msg}).",
        )

    # Step 23 SEO & Metadata Compliance Guard
    clip_metadata = await asyncio.to_thread(store.get_clip_metadata, clip.id)
    if clip_metadata is not None:
        if not clip_metadata.is_publish_ready:
            reasons = "; ".join(clip_metadata.validation_errors) or "Metadata failed compliance quality gate."
            raise HTTPException(
                status_code=400,
                detail=f"Cannot publish clip '{clip.id}': SEO metadata failed compliance check ({reasons}).",
            )
        # Authoritative operator final_* metadata takes precedence
        title = request.title or clip_metadata.final_title or clip.title or "AL AMR Highlight"
        desc = request.description or clip_metadata.final_description or clip.hook or ""
        tags = request.tags or clip_metadata.final_hashtags or ["ALAMR", "Shorts"]
    else:
        title = request.title or clip.title or "AL AMR Highlight"
        desc = request.description or clip.hook or ""
        tags = request.tags or ["ALAMR", "Shorts"]

    # Step 24 Operator Approval Guard
    clip_approval = await asyncio.to_thread(store.get_clip_approval, clip.id)
    if clip_approval is not None:
        approved, approval_reasons = store.is_clip_approved_for_publishing(clip.id)
        if not approved:
            reasons_str = "; ".join(approval_reasons) or "Clip has not been approved by operator."
            raise HTTPException(
                status_code=400,
                detail=f"Cannot publish clip '{clip.id}': {reasons_str}",
            )

    eval_row = await asyncio.to_thread(store.get_campaign_evaluation, clip.id)
    if eval_row and eval_row.campaign_id:
        campaign = await asyncio.to_thread(store.get_campaign, eval_row.campaign_id)
        if campaign and campaign.brief:
            cta = campaign.brief.get("cta_text", "")
            if cta and cta not in desc:
                desc = f"{desc}\n\n{cta}".strip()

    metadata = PublishingMetadata(
        title=title,
        description=desc,
        tags=tags,
        destination=request.destination,
        extra={"export_id": export.id},
    )

    results: list[PublishingRecordOut] = []
    for platform in request.platforms:
        record = await _service.publish_export(
            export.id,
            platform,
            metadata=metadata,
            destination=request.destination,
            dry_run=request.dry_run,
        )
        results.append(PublishingRecordOut.of(record))

    return results


# ---------------------------------------------------------------------------
# Step 25: Remote Publishing APIs (Clips + Publications)
# ---------------------------------------------------------------------------


@router.get("/jobs/{job_id}/publications", response_model=list[PublicationOut])
async def list_job_publications(
    job_id: str,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[PublicationOut]:
    """Retrieve all remote publication records for a job."""
    job = await asyncio.to_thread(store.get_job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    records = await asyncio.to_thread(store.list_publications_for_job, job_id, limit)
    return [PublicationOut.of(r) for r in records]


@router.get("/jobs/{job_id}/publications/telemetry", response_model=PublishingTelemetryOut)
async def get_job_publishing_telemetry(job_id: str) -> PublishingTelemetryOut:
    """Retrieve aggregate publishing telemetry for a job."""
    job = await asyncio.to_thread(store.get_job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    records = await asyncio.to_thread(store.list_publications_for_job, job_id, 500)
    total = len(records)
    published = sum(1 for r in records if r.status == "PUBLISHED")
    failed_retryable = sum(1 for r in records if r.status == "FAILED_RETRYABLE")
    failed_permanent = sum(1 for r in records if r.status == "FAILED_PERMANENT")
    skipped = sum(1 for r in records if r.status == "SKIPPED")
    total_attempts = sum(r.attempt_number for r in records)

    return PublishingTelemetryOut(
        total_destinations=total,
        published=published,
        failed=failed_retryable + failed_permanent,
        retryable_failures=failed_retryable,
        permanent_failures=failed_permanent,
        skipped=skipped,
        total_attempts=total_attempts,
    )


@router.get("/jobs/{job_id}/publications/{publication_id}", response_model=PublicationOut)
async def get_single_publication(job_id: str, publication_id: str) -> PublicationOut:
    """Retrieve a single remote publication record by ID."""
    record = await asyncio.to_thread(store.get_publication, publication_id)
    if not record or record.job_id != job_id:
        raise HTTPException(status_code=404, detail="Publication record not found.")
    return PublicationOut.of(record)


@router.post("/jobs/{job_id}/clips/{clip_id}/publish", response_model=list[PublicationOut])
async def publish_clip_endpoint(
    job_id: str,
    clip_id: str,
    request: PublishClipIn,
) -> list[PublicationOut]:
    """Publish an approved clip to one or more remote destinations with quality gate enforcement."""
    job = await asyncio.to_thread(store.get_job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    clip = await asyncio.to_thread(store.get_clip, clip_id)
    if not clip or clip.job_id != job_id:
        raise HTTPException(status_code=404, detail="Clip not found.")

    # Check eligibility first to return clear 400 on gate failures
    is_eligible, reasons, _, _ = _service.verify_publishing_eligibility(clip_id)
    if not is_eligible:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot publish clip '{clip_id}': {'; '.join(reasons)}",
        )

    results: list[PublicationOut] = []
    for platform in request.platforms:
        try:
            record = await _service.publish_clip(
                job_id=job_id,
                clip_id=clip_id,
                platform=platform,
                destination=request.destination,
                dry_run=request.dry_run,
            )
            results.append(PublicationOut.of(record))
        except Exception as exc:
            log.warning("Platform %s failed during publish of clip %s: %s", platform, clip_id, exc)
            # Fetch latest record if created
            dest = request.destination.strip() or "default"
            key = f"{job_id}:{clip_id}:{platform.strip().lower()}:{dest}"
            rec = await asyncio.to_thread(store.get_publication_by_idempotency_key, key)
            if rec:
                results.append(PublicationOut.of(rec))
            else:
                raise HTTPException(status_code=500, detail=str(exc))

    return results


@router.post("/jobs/{job_id}/clips/{clip_id}/publish-all", response_model=list[PublicationOut])
async def publish_all_endpoint(
    job_id: str,
    clip_id: str,
    request: PublishAllClipsIn,
) -> list[PublicationOut]:
    """Publish a single clip across all specified platforms."""
    return await publish_clip_endpoint(
        job_id=job_id,
        clip_id=clip_id,
        request=PublishClipIn(
            platforms=request.platforms,
            destination=request.destination,
            dry_run=request.dry_run,
        ),
    )


@router.post("/publications/{publication_id}/retry", response_model=PublicationOut)
async def retry_publication_endpoint(
    publication_id: str,
    dry_run: bool = Query(default=False),
) -> PublicationOut:
    """Retry a failed retryable publication attempt."""
    record = await asyncio.to_thread(store.get_publication, publication_id)
    if not record:
        raise HTTPException(status_code=404, detail="Publication record not found.")

    if record.status == "PUBLISHED":
        return PublicationOut.of(record)

    if record.status == "FAILED_PERMANENT":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot retry permanent failure: {record.error_message}",
        )

    try:
        updated = await _service.retry_publication(publication_id, dry_run=dry_run)
        return PublicationOut.of(updated)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ---------------------------------------------------------------------------
# Step 26: Multi-Account Destinations API
# ---------------------------------------------------------------------------


@router.get("/publishing/destinations", response_model=list[DestinationOut])
@router.get("/destinations", response_model=list[DestinationOut])
async def list_publishing_destinations(
    platform: str | None = Query(default=None),
    enabled_only: bool = Query(default=False),
) -> list[DestinationOut]:
    """List multi-account publishing destinations, initializing defaults if empty."""
    # Ensure defaults exist
    await asyncio.to_thread(_orchestrator.ensure_default_destinations)
    destinations = await asyncio.to_thread(
        store.list_destinations, platform=platform, enabled_only=enabled_only
    )
    return [DestinationOut.of(d) for d in destinations]


@router.post("/publishing/destinations", response_model=DestinationOut)
@router.post("/destinations", response_model=DestinationOut)
async def create_publishing_destination(payload: DestinationIn) -> DestinationOut:
    """Create a new publishing destination account/channel."""
    now = models.utcnow()
    dest = models.DestinationRecord(
        id=models.new_id(),
        platform=payload.platform,
        display_name=payload.display_name.strip(),
        account_identifier=payload.account_identifier.strip(),
        enabled=payload.enabled,
        priority=payload.priority,
        config_metadata=payload.config_metadata,
        daily_limit=payload.daily_limit,
        spacing_seconds=payload.spacing_seconds,
        created_at=now,
        updated_at=now,
    )
    created = await asyncio.to_thread(store.create_destination, dest)
    return DestinationOut.of(created)


@router.get("/publishing/destinations/{destination_id}", response_model=DestinationOut)
@router.get("/destinations/{destination_id}", response_model=DestinationOut)
async def get_publishing_destination(destination_id: str) -> DestinationOut:
    """Get single destination by ID."""
    dest = await asyncio.to_thread(store.get_destination, destination_id)
    if not dest:
        raise HTTPException(status_code=404, detail="Destination not found.")
    return DestinationOut.of(dest)


@router.patch("/publishing/destinations/{destination_id}", response_model=DestinationOut)
@router.patch("/destinations/{destination_id}", response_model=DestinationOut)
async def update_publishing_destination(
    destination_id: str, payload: DestinationUpdateIn
) -> DestinationOut:
    """Update settings on a destination."""
    dest = await asyncio.to_thread(store.get_destination, destination_id)
    if not dest:
        raise HTTPException(status_code=404, detail="Destination not found.")

    updated = await asyncio.to_thread(
        store.update_destination,
        destination_id,
        display_name=payload.display_name,
        account_identifier=payload.account_identifier,
        enabled=payload.enabled,
        priority=payload.priority,
        config_metadata=payload.config_metadata,
        daily_limit=payload.daily_limit,
        spacing_seconds=payload.spacing_seconds,
    )
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to update destination.")
    return DestinationOut.of(updated)


@router.delete("/publishing/destinations/{destination_id}")
@router.delete("/destinations/{destination_id}")
async def delete_publishing_destination(destination_id: str) -> dict[str, bool]:
    """Delete a publishing destination."""
    dest = await asyncio.to_thread(store.get_destination, destination_id)
    if not dest:
        raise HTTPException(status_code=404, detail="Destination not found.")
    success = await asyncio.to_thread(store.delete_destination, destination_id)
    return {"ok": success}


# ---------------------------------------------------------------------------
# Step 26: Publishing Queue & Scheduling API
# ---------------------------------------------------------------------------


@router.get("/publishing/queue", response_model=list[QueueItemOut])
async def list_queue(
    job_id: str | None = Query(default=None),
    clip_id: str | None = Query(default=None),
    destination_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[QueueItemOut]:
    """List publishing queue items ordered by scheduled time."""
    items = await asyncio.to_thread(
        store.list_queue_items,
        job_id=job_id,
        clip_id=clip_id,
        destination_id=destination_id,
        status=status,
        limit=limit,
    )
    dest_map: dict[str, str] = {}
    destinations = await asyncio.to_thread(store.list_destinations)
    for d in destinations:
        dest_map[d.id] = d.display_name

    return [QueueItemOut.of(i, destination_name=dest_map.get(i.destination_id, "")) for i in items]


@router.get("/publishing/queue/telemetry", response_model=QueueTelemetryOut)
async def get_queue_telemetry(job_id: str | None = Query(default=None)) -> QueueTelemetryOut:
    """Retrieve summary telemetry counts for the publishing queue."""
    counts = await asyncio.to_thread(_orchestrator.get_telemetry, job_id=job_id)
    return QueueTelemetryOut(**counts)


@router.post("/jobs/{job_id}/clips/{clip_id}/schedule", response_model=QueueItemOut)
async def schedule_clip_publication_endpoint(
    job_id: str,
    clip_id: str,
    payload: SchedulePublicationIn,
) -> QueueItemOut:
    """Schedule an approved clip for publication on a specific destination."""
    job = await asyncio.to_thread(store.get_job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    clip = await asyncio.to_thread(store.get_clip, clip_id)
    if not clip or clip.job_id != job_id:
        raise HTTPException(status_code=404, detail="Clip not found.")

    try:
        item = await asyncio.to_thread(
            _orchestrator.enqueue_publication,
            job_id=job_id,
            clip_id=clip_id,
            destination_id=payload.destination_id,
            scheduled_at=payload.scheduled_at,
            priority=payload.priority,
        )
        dest = await asyncio.to_thread(store.get_destination, item.destination_id)
        return QueueItemOut.of(item, destination_name=dest.display_name if dest else "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/publishing/queue/{queue_id}/cancel", response_model=QueueItemOut)
async def cancel_queue_item_endpoint(
    queue_id: str,
    payload: CancelQueueIn = CancelQueueIn(),
) -> QueueItemOut:
    """Cancel a pending or scheduled queue item."""
    try:
        item = await asyncio.to_thread(_orchestrator.cancel_item, queue_id, payload.reason)
        dest = await asyncio.to_thread(store.get_destination, item.destination_id)
        return QueueItemOut.of(item, destination_name=dest.display_name if dest else "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/publishing/queue/{queue_id}/reschedule", response_model=QueueItemOut)
async def reschedule_queue_item_endpoint(
    queue_id: str,
    payload: RescheduleQueueIn,
) -> QueueItemOut:
    """Reschedule a queue item to a new timestamp."""
    try:
        item = await asyncio.to_thread(
            _orchestrator.reschedule_item, queue_id, payload.new_scheduled_at
        )
        dest = await asyncio.to_thread(store.get_destination, item.destination_id)
        return QueueItemOut.of(item, destination_name=dest.display_name if dest else "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/publishing/queue/process-next", response_model=QueueItemOut | None)
async def process_next_queue_item_endpoint(
    worker_id: str = Query(default="worker-manual"),
    dry_run: bool = Query(default=False),
) -> QueueItemOut | None:
    """Manually trigger claim and execution of the next due queue item."""
    item = await _orchestrator.claim_and_process_next(worker_id=worker_id, dry_run=dry_run)
    if not item:
        return None
    dest = await asyncio.to_thread(store.get_destination, item.destination_id)
    return QueueItemOut.of(item, destination_name=dest.display_name if dest else "")


@router.post("/publishing/queue/{queue_id}/process", response_model=QueueItemOut)
async def process_queue_item_endpoint(
    queue_id: str,
    dry_run: bool = Query(default=False),
) -> QueueItemOut:
    """Manually trigger execution of a specific queue item."""
    try:
        item = await _orchestrator.process_queue_item(queue_id, dry_run=dry_run)
        dest = await asyncio.to_thread(store.get_destination, item.destination_id)
        return QueueItemOut.of(item, destination_name=dest.display_name if dest else "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/publishing/orchestration-telemetry")
async def get_orchestration_telemetry() -> dict[str, Any]:
    """Comprehensive autonomous orchestration, queue health, reserve status, and learning telemetry."""
    from ..analytics.engine import LearningEngine

    engine = LearningEngine()

    destinations = await asyncio.to_thread(_orchestrator.ensure_default_destinations)
    dest_telemetry = []
    for d in destinations:
        dest_telemetry.append({
            "id": d.id,
            "platform": d.platform,
            "display_name": d.display_name,
            "enabled": d.enabled,
            "daily_limit": d.daily_limit,
            "published_today": d.published_today,
            "rate_limit_remaining": max(0, d.daily_limit - d.published_today),
            "min_spacing_minutes": d.min_spacing_minutes,
            "account_identifier": d.account_identifier,
        })

    queue_stats = await asyncio.to_thread(_orchestrator.get_queue_stats)
    reserve_status = await asyncio.to_thread(engine.get_reserve_status)
    learning_eval = await asyncio.to_thread(engine.evaluate_learning)
    recent_audits = await asyncio.to_thread(store.list_learning_audits, limit=10)

    return {
        "status": "operational",
        "queue": queue_stats,
        "destinations": dest_telemetry,
        "reserve": reserve_status,
        "learning": {
            "evaluation": learning_eval,
            "recent_audits": [a.to_dict() for a in recent_audits],
        },
    }



