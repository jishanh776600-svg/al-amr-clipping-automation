"""Job lifecycle and the SSE progress stream."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from ..config import load as load_settings
from ..db import models, store
from ..db.models import Job, new_id
from ..jobs import orchestrator
from ..jobs.dispatcher import dispatch_job_to_github, is_github_dispatch_enabled
from ..jobs.events import Event, broker
from ..jobs.queue import queue
from .auth import is_valid_token
from .schemas import JobCreateIn, JobManifestOut, JobOut, JobSettingsIn, WorkerCallbackIn

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _apply_overrides(settings, overrides: JobSettingsIn):
    """Layer per-job overrides on top of saved settings."""
    merged = settings.model_copy(deep=True)

    if overrides.provider:
        merged.active_provider = overrides.provider
    if overrides.whisper_model:
        merged.whisper.model = overrides.whisper_model
    if overrides.language is not None:
        merged.whisper.language = overrides.language
    if overrides.diarization is not None:
        merged.whisper.diarization = overrides.diarization
    if overrides.min_duration_s is not None:
        merged.clips.min_duration_s = overrides.min_duration_s
    if overrides.max_duration_s is not None:
        merged.clips.max_duration_s = overrides.max_duration_s
    if overrides.max_clips is not None:
        merged.clips.max_clips = overrides.max_clips
    if overrides.caption_style:
        merged.export.caption_style = overrides.caption_style
    if overrides.ratio:
        merged.export.ratio = overrides.ratio

    return merged


@router.post("", response_model=JobOut, status_code=201)
async def create_job(payload: JobCreateIn) -> JobOut:
    source = await asyncio.to_thread(store.get_source, payload.source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found.")

    settings = _apply_overrides(load_settings(), payload.settings)
    if settings.clips.min_duration_s >= settings.clips.max_duration_s:
        raise HTTPException(
            status_code=400, detail="Minimum clip length must be below the maximum."
        )

    job_settings = settings.model_dump(mode="json")
    if payload.campaign is not None:
        job_settings["campaign"] = payload.campaign.model_dump(mode="json")

    dispatch_mode = "github" if is_github_dispatch_enabled() else "local"
    job = Job(
        id=new_id(),
        source_id=source.id,
        provider=settings.active_provider,
        settings=job_settings,
        dispatch_mode=dispatch_mode,
        max_attempts=int(os.environ.get("AUTOCLIP_MAX_ATTEMPTS", "3")),
    )
    await asyncio.to_thread(store.create_job, job)

    if dispatch_mode == "github":
        # Launch dispatch asynchronously so HTTP response is returned immediately
        asyncio.create_task(dispatch_job_to_github(job, source))
    else:
        queue.notify()

    return JobOut.of(job, source)


@router.get("", response_model=list[JobOut])
async def list_jobs(limit: int = 25) -> list[JobOut]:
    jobs = await asyncio.to_thread(store.list_jobs, limit)
    out: list[JobOut] = []
    for job in jobs:
        source = await asyncio.to_thread(store.get_source, job.source_id)
        out.append(JobOut.of(job, source))
    return out


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: str) -> JobOut:
    job = await asyncio.to_thread(store.get_job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    source = await asyncio.to_thread(store.get_source, job.source_id)
    return JobOut.of(job, source)


@router.get("/{job_id}/manifest", response_model=JobManifestOut)
async def get_job_manifest_endpoint(job_id: str) -> JobManifestOut:
    """Retrieve authoritative forensic job manifest answering 'What happened to this clip?'."""
    manifest = await asyncio.to_thread(orchestrator.get_job_manifest, job_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return JobManifestOut(**manifest)


@router.get("/{job_id}/events")
async def job_events(job_id: str, request: Request) -> EventSourceResponse:
    """Stream progress for a job as Server-Sent Events."""
    job = await asyncio.to_thread(store.get_job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")

    async def stream():
        current = await asyncio.to_thread(store.get_job, job_id)
        if current is not None:
            source = await asyncio.to_thread(store.get_source, current.source_id)
            yield {
                "event": "snapshot",
                "data": JobOut.of(current, source).model_dump_json(),
            }
            if current.status in ("done", "failed", "cancelled"):
                return

        async for event in broker.subscribe(job_id):
            if await request.is_disconnected():
                break
            yield {"event": event.type, "data": event.to_sse().split("data: ", 1)[-1].strip()}

    return EventSourceResponse(stream())


@router.post("/{job_id}/cancel", response_model=JobOut)
async def cancel_job(job_id: str) -> JobOut:
    """Request graceful job cancellation."""
    job = await asyncio.to_thread(store.get_job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")

    if job.status in ("done", "cancelled", "failed"):
        raise HTTPException(
            status_code=409, detail=f"Job is already {job.status}; nothing to cancel."
        )

    updated = await orchestrator.request_job_cancellation(job_id)
    source = await asyncio.to_thread(store.get_source, job.source_id)
    return JobOut.of(updated, source)


@router.post("/{job_id}/retry", response_model=JobOut)
async def retry_job(job_id: str) -> JobOut:
    """Requeue a failed or cancelled job with exponential backoff and attempt tracking."""
    job = await asyncio.to_thread(store.get_job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.status not in ("failed", "cancelled"):
        raise HTTPException(
            status_code=409,
            detail=f"Only failed or cancelled jobs can be retried (this one is {job.status}).",
        )

    if job.attempt >= job.max_attempts:
        raise HTTPException(
            status_code=400,
            detail=f"Job {job_id} has exceeded maximum allowed attempts ({job.max_attempts}).",
        )

    next_attempt = job.attempt + 1
    now = store.utcnow()
    await asyncio.to_thread(
        store.update_job,
        job_id,
        status="queued",
        attempt=next_attempt,
        error=None,
        progress=0.0,
        current_stage="requeued",
        last_heartbeat_at=None,
        stale_at=None,
        failed_at=None,
        cancelled_at=None,
        cancel_requested_at=None,
    )
    broker.publish(Event(type="retried", job_id=job_id, data={"attempt": next_attempt}))

    source = await asyncio.to_thread(store.get_source, job.source_id)
    if job.dispatch_mode == "github" or is_github_dispatch_enabled():
        if source:
            asyncio.create_task(dispatch_job_to_github(job, source))
    else:
        queue.notify()

    updated = await asyncio.to_thread(store.get_job, job_id)
    return JobOut.of(updated or job, source)


@router.get("/{job_id}/clips")
async def job_clips(job_id: str):
    from .clips import list_clips_for_job

    return await list_clips_for_job(job_id)


@router.post("/{job_id}/worker-callback", response_model=JobOut)
async def worker_callback(
    job_id: str,
    payload: WorkerCallbackIn,
) -> JobOut:
    """Receive live execution, heartbeat, and completion updates from on-demand worker."""
    job = await asyncio.to_thread(store.get_job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")

    if not is_valid_token(payload.token):
        raise HTTPException(status_code=401, detail="Unauthorized worker callback.")

    now = store.utcnow()
    update_kwargs: dict[str, Any] = {
        "last_heartbeat_at": now,
    }

    # State transition & duplicate check
    if payload.status is not None:
        if job.status == "done" and payload.status == "done":
            # Duplicate completion callback: idempotent no-op for status
            pass
        elif not orchestrator.can_transition(job.status, payload.status):
            log.warning(
                "Ignoring illegal callback status transition for job %s: '%s' -> '%s'",
                job_id,
                job.status,
                payload.status,
            )
        else:
            update_kwargs["status"] = payload.status
            if payload.status == "running" and not job.started_at:
                update_kwargs["started_at"] = now
            elif payload.status == "done":
                update_kwargs["completed_at"] = now
                update_kwargs["finished_at"] = now
            elif payload.status == "failed":
                update_kwargs["failed_at"] = now
                update_kwargs["finished_at"] = now

    if payload.stage is not None:
        update_kwargs["current_stage"] = payload.stage
    if payload.progress is not None:
        if payload.progress >= job.progress or payload.status in ("queued", "failed"):
            update_kwargs["progress"] = payload.progress
    if payload.error is not None:
        update_kwargs["error"] = payload.error
    if payload.github_run_id is not None:
        update_kwargs["github_run_id"] = payload.github_run_id
        update_kwargs["github_run_url"] = f"https://github.com/jishanh776600-svg/al-amr-clipping-automation/actions/runs/{payload.github_run_id}"

    if update_kwargs:
        await asyncio.to_thread(store.update_job, job_id, **update_kwargs)

    # Ingest clips if reported
    if payload.clips:
        for c in payload.clips:
            clip_row = models.Clip(
                id=c.get("id", new_id()),
                job_id=job_id,
                start_s=float(c.get("start_s", 0.0)),
                end_s=float(c.get("end_s", 0.0)),
                rank=int(c.get("rank", 0)),
                start_word=int(c.get("start_word", 0)),
                end_word=int(c.get("end_word", 0)),
                title=str(c.get("title", "")),
                hook=str(c.get("hook", "")),
                score=int(c.get("score", 0)),
                reason=str(c.get("reason", "")),
                status=c.get("status", "candidate"),
            )
            existing = await asyncio.to_thread(store.get_clip, clip_row.id)
            if not existing:
                await asyncio.to_thread(store.create_clip, clip_row)

    # Ingest evaluations if reported
    if payload.evaluations:
        for ev in payload.evaluations:
            eval_row = models.CampaignEvaluationRow(
                clip_id=ev["clip_id"],
                campaign_id=ev.get("campaign_id", ""),
                approved=bool(ev.get("approved", True)),
                final_score=float(ev.get("final_score", 0.0)),
                hook_score=float(ev.get("hook_score", 0.0)),
                cta_score=float(ev.get("cta_score", 0.0)),
                viral_score=float(ev.get("viral_score", 0.0)),
                density_score=float(ev.get("density_score", 0.0)),
                hard_failures=ev.get("hard_failures", []),
                soft_warnings=ev.get("soft_warnings", []),
                rule_results=ev.get("rule_results", {}),
            )
            existing_eval = await asyncio.to_thread(store.get_campaign_evaluation, eval_row.clip_id)
            if not existing_eval:
                await asyncio.to_thread(store.create_campaign_evaluation, eval_row)

    # Ingest exports if reported
    if payload.exports:
        for exp in payload.exports:
            exp_row = models.Export(
                id=exp.get("id", new_id()),
                clip_id=exp["clip_id"],
                path=exp.get("path", ""),
                ratio=exp.get("ratio", "9:16"),
                style=exp.get("style", "bold_pop"),
                size_bytes=int(exp.get("size_bytes", 0)),
                drive_file_id=exp.get("drive_file_id"),
                drive_web_view_link=exp.get("drive_web_view_link"),
                drive_storage_key=exp.get("drive_storage_key"),
            )
            existing_exp = await asyncio.to_thread(store.get_export, exp_row.id)
            if not existing_exp:
                await asyncio.to_thread(store.create_export, exp_row)
            else:
                await asyncio.to_thread(
                    store.update_export_drive_info,
                    exp_row.id,
                    drive_file_id=exp_row.drive_file_id,
                    drive_web_view_link=exp_row.drive_web_view_link,
                    drive_storage_key=exp_row.drive_storage_key,
                )

    # Ingest publishing records if reported
    if payload.publishing_records:
        for pub in payload.publishing_records:
            pub_row = models.PublishingRecord(
                id=pub.get("id", new_id()),
                export_id=pub["export_id"],
                job_id=job_id,
                platform=pub["platform"],
                status=pub.get("status", "pending"),
                external_id=pub.get("external_id"),
                destination=pub.get("destination", ""),
                metadata=pub.get("metadata", {}),
                error=pub.get("error"),
                created_at=pub.get("created_at", store.utcnow()),
                updated_at=pub.get("updated_at", store.utcnow()),
            )
            await asyncio.to_thread(store.create_or_update_publishing_record, pub_row)

    # Broadcast real-time SSE event to all connected clients
    event_type = "progress"
    if payload.status == "done":
        event_type = "completed"
    elif payload.status == "failed":
        event_type = "failed"

    event_data = {
        "stage": payload.stage or job.current_stage,
        "progress": payload.progress if payload.progress is not None else job.progress,
    }
    if payload.error:
        event_data["error"] = payload.error
    if payload.github_run_id:
        event_data["github_run_id"] = payload.github_run_id

    broker.publish(Event(type=event_type, job_id=job_id, data=event_data))

    updated_job = await asyncio.to_thread(store.get_job, job_id)
    source = await asyncio.to_thread(store.get_source, job.source_id)
    return JobOut.of(updated_job or job, source)
