"""Job lifecycle and the SSE progress stream."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from starlette.responses import FileResponse
from sse_starlette.sse import EventSourceResponse

from .. import paths
from ..campaign.drive_retriever import retrieve_drive_guideline
from ..campaign.extractor import (
    GuidelineExtractionError,
    compute_document_hash,
    extract_guideline_text,
    parse_guidelines_into_brief,
)
from ..config import load as load_settings
from ..db import models, store
from ..db.models import CampaignGuideline, Job, new_id
from ..jobs import orchestrator
from ..jobs.dispatcher import dispatch_job_to_github, is_github_dispatch_enabled
from ..jobs.events import Event, broker
from ..jobs.queue import queue
from ..pipeline import ingest
from .auth import is_valid_token
from .schemas import (
    CampaignGuidelineOut,
    DriveGuidelineIn,
    JobCreateIn,
    JobManifestOut,
    JobOut,
    JobSettingsIn,
    WorkerCallbackIn,
)

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


@router.post("/guidelines/upload", response_model=CampaignGuidelineOut, status_code=201)
async def upload_guideline(file: UploadFile = File(...)) -> CampaignGuidelineOut:
    """Upload a PDF or DOCX campaign guideline document, extract requirements, and persist."""
    content = await file.read()
    filename = file.filename or "guideline.pdf"
    mime_type = file.content_type or "application/octet-stream"

    try:
        raw_text, ext = extract_guideline_text(filename, content)
    except GuidelineExtractionError as exc:
        raise HTTPException(
            status_code=422,
            detail={"message": str(exc), "hint": exc.hint},
        ) from exc

    brief = parse_guidelines_into_brief(raw_text, filename)
    guideline_id = new_id()

    # Save original file to durable storage
    storage_dir = paths.guidelines_dir()
    storage_dir.mkdir(parents=True, exist_ok=True)
    storage_file = storage_dir / f"{guideline_id}_{filename}"
    storage_file.write_bytes(content)

    sha256 = compute_document_hash(content)
    word_count = len(re.findall(r"\b\w+\b", raw_text))
    char_count = len(raw_text)
    source_type = "upload_pdf" if ext == ".pdf" else "upload_docx"

    guideline = CampaignGuideline(
        id=guideline_id,
        job_id=None,
        filename=filename,
        mime_type=mime_type,
        size_bytes=len(content),
        storage_path=str(storage_file),
        extracted_text=raw_text,
        parsed_brief=brief.model_dump(mode="json"),
        status="extracted",
        error=None,
        source_type=source_type,
        drive_file_id=None,
        sha256=sha256,
        word_count=word_count,
        char_count=char_count,
    )
    await asyncio.to_thread(store.create_guideline, guideline)
    return CampaignGuidelineOut.of(guideline)


@router.post("/guidelines/drive", response_model=CampaignGuidelineOut, status_code=201)
async def upload_drive_guideline_endpoint(payload: DriveGuidelineIn) -> CampaignGuidelineOut:
    """Retrieve and process a campaign guideline document from Google Drive."""
    try:
        filename, content, mime_type, drive_file_id = await asyncio.to_thread(
            retrieve_drive_guideline, payload.drive_url
        )
        raw_text, ext = extract_guideline_text(filename, content)
    except GuidelineExtractionError as exc:
        raise HTTPException(
            status_code=422,
            detail={"message": str(exc), "hint": exc.hint},
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail={"message": f"Failed to retrieve Google Drive document: {exc}", "hint": "Check document link."},
        ) from exc

    brief = parse_guidelines_into_brief(raw_text, filename)
    guideline_id = new_id()

    storage_dir = paths.guidelines_dir()
    storage_dir.mkdir(parents=True, exist_ok=True)
    storage_file = storage_dir / f"{guideline_id}_{filename}"
    storage_file.write_bytes(content)

    sha256 = compute_document_hash(content)
    word_count = len(re.findall(r"\b\w+\b", raw_text))
    char_count = len(raw_text)

    guideline = CampaignGuideline(
        id=guideline_id,
        job_id=None,
        filename=filename,
        mime_type=mime_type,
        size_bytes=len(content),
        storage_path=str(storage_file),
        extracted_text=raw_text,
        parsed_brief=brief.model_dump(mode="json"),
        status="extracted",
        error=None,
        source_type="drive",
        drive_file_id=drive_file_id,
        sha256=sha256,
        word_count=word_count,
        char_count=char_count,
    )
    await asyncio.to_thread(store.create_guideline, guideline)
    return CampaignGuidelineOut.of(guideline)


@router.post("/create-autonomous", response_model=JobOut, status_code=201)
async def create_autonomous_job(
    request: Request,
) -> JobOut:
    """One-click autonomous job creation: accepts Source (URL or Video File) + Campaign Guideline (PDF, DOCX, or Drive)."""
    content_type = request.headers.get("content-type", "")

    url: str | None = None
    video_file = None
    guideline_file = None
    guideline_id: str | None = None
    drive_guideline_url: str | None = None
    destinations: Any = None
    overrides: Any = None

    if "application/json" in content_type:
        body = await request.json()
        url = body.get("video_url") or body.get("url")
        guideline_id = body.get("guideline_id")
        drive_guideline_url = body.get("drive_guideline_url")
        destinations = body.get("destinations")
        overrides = body.get("overrides") or body.get("settings")
    else:
        form = await request.form()
        raw_url = form.get("url") or form.get("video_url")
        if isinstance(raw_url, str):
            url = raw_url
        video_file = form.get("video_file")
        guideline_file = form.get("guideline_file")
        raw_gid = form.get("guideline_id")
        if isinstance(raw_gid, str):
            guideline_id = raw_gid
        raw_dg = form.get("drive_guideline_url")
        if isinstance(raw_dg, str):
            drive_guideline_url = raw_dg
        destinations = form.get("destinations")
        overrides = form.get("overrides")

    # 1. Ingest or resolve Source
    source = None
    if url and str(url).strip():
        clean_url = str(url).strip()
        settings = load_settings().ingest
        try:
            source = await asyncio.to_thread(ingest.ingest_url, clean_url, settings)
            await asyncio.to_thread(store.create_source, source)
        except ingest.IngestError as exc:
            raise HTTPException(
                status_code=422,
                detail={"message": str(exc), "hint": exc.hint},
            ) from exc
    elif video_file and hasattr(video_file, "filename") and video_file.filename:
        suffix = Path(video_file.filename).suffix.lower()
        if suffix not in ingest.ACCEPTED_SUFFIXES:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported media extension '{suffix}'. Accepted: {', '.join(sorted(ingest.ACCEPTED_SUFFIXES))}",
            )
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = Path(tmp.name)
            shutil.copyfileobj(video_file.file, tmp)
        try:
            source = await asyncio.to_thread(ingest.ingest_file, tmp_path, move=True, title=Path(video_file.filename).stem)
            await asyncio.to_thread(store.create_source, source)
        except Exception as exc:
            tmp_path.unlink(missing_ok=True)
            raise HTTPException(status_code=422, detail=f"Failed to process uploaded video: {exc}") from exc
    else:
        raise HTTPException(
            status_code=400,
            detail="A source video must be provided — either paste a URL or upload a video file.",
        )

    # 2. Ingest or resolve Campaign Guideline
    guideline = None
    if guideline_file and hasattr(guideline_file, "filename") and guideline_file.filename:
        content = await guideline_file.read()
        filename = guideline_file.filename
        try:
            raw_text, ext = extract_guideline_text(filename, content)
        except GuidelineExtractionError as exc:
            raise HTTPException(
                status_code=422,
                detail={"message": str(exc), "hint": exc.hint},
            ) from exc

        brief = parse_guidelines_into_brief(raw_text, filename)
        gid = new_id()
        storage_dir = paths.guidelines_dir()
        storage_dir.mkdir(parents=True, exist_ok=True)
        storage_file = storage_dir / f"{gid}_{filename}"
        storage_file.write_bytes(content)

        sha256 = compute_document_hash(content)
        word_count = len(re.findall(r"\b\w+\b", raw_text))
        char_count = len(raw_text)
        source_type = "upload_pdf" if ext == ".pdf" else "upload_docx"

        guideline = CampaignGuideline(
            id=gid,
            job_id=None,
            filename=filename,
            mime_type=getattr(guideline_file, "content_type", None) or "application/octet-stream",
            size_bytes=len(content),
            storage_path=str(storage_file),
            extracted_text=raw_text,
            parsed_brief=brief.model_dump(mode="json"),
            status="extracted",
            error=None,
            source_type=source_type,
            drive_file_id=None,
            sha256=sha256,
            word_count=word_count,
            char_count=char_count,
        )
        await asyncio.to_thread(store.create_guideline, guideline)
    elif drive_guideline_url and str(drive_guideline_url).strip():
        try:
            filename, content, mime_type, drive_file_id = await asyncio.to_thread(
                retrieve_drive_guideline, str(drive_guideline_url).strip()
            )
            raw_text, ext = extract_guideline_text(filename, content)
        except GuidelineExtractionError as exc:
            raise HTTPException(
                status_code=422,
                detail={"message": str(exc), "hint": exc.hint},
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail={"message": f"Failed to retrieve Google Drive document: {exc}", "hint": "Check document link."},
            ) from exc

        brief = parse_guidelines_into_brief(raw_text, filename)
        gid = new_id()
        storage_dir = paths.guidelines_dir()
        storage_dir.mkdir(parents=True, exist_ok=True)
        storage_file = storage_dir / f"{gid}_{filename}"
        storage_file.write_bytes(content)

        sha256 = compute_document_hash(content)
        word_count = len(re.findall(r"\b\w+\b", raw_text))
        char_count = len(raw_text)

        guideline = CampaignGuideline(
            id=gid,
            job_id=None,
            filename=filename,
            mime_type=mime_type,
            size_bytes=len(content),
            storage_path=str(storage_file),
            extracted_text=raw_text,
            parsed_brief=brief.model_dump(mode="json"),
            status="extracted",
            error=None,
            source_type="drive",
            drive_file_id=drive_file_id,
            sha256=sha256,
            word_count=word_count,
            char_count=char_count,
        )
        await asyncio.to_thread(store.create_guideline, guideline)
    elif guideline_id:
        guideline = await asyncio.to_thread(store.get_guideline, str(guideline_id).strip())

    # 3. Layer settings
    base_settings = load_settings()
    job_settings = base_settings.model_dump(mode="json")

    if overrides:
        if isinstance(overrides, dict):
            job_settings.update(overrides)
        elif isinstance(overrides, str):
            try:
                overrides_dict = json.loads(overrides)
                if isinstance(overrides_dict, dict):
                    job_settings.update(overrides_dict)
            except Exception:
                pass

    if destinations:
        dest_list: list[str] = []
        if isinstance(destinations, list):
            dest_list = [str(x).strip().lower() for x in destinations if str(x).strip()]
        elif isinstance(destinations, str):
            try:
                parsed = json.loads(destinations)
                if isinstance(parsed, list):
                    dest_list = [str(x).strip().lower() for x in parsed if str(x).strip()]
            except Exception:
                dest_list = [d.strip().lower() for d in destinations.split(",") if d.strip()]
        if dest_list:
            job_settings["destinations"] = dest_list

    if guideline and guideline.parsed_brief:
        job_settings["campaign"] = guideline.parsed_brief
        job_settings["guideline"] = {
            "id": guideline.id,
            "filename": guideline.filename,
            "mime_type": guideline.mime_type,
            "size_bytes": guideline.size_bytes,
            "source_type": getattr(guideline, "source_type", "upload_pdf"),
            "drive_file_id": getattr(guideline, "drive_file_id", None),
            "sha256": getattr(guideline, "sha256", None),
            "word_count": getattr(guideline, "word_count", 0),
            "char_count": getattr(guideline, "char_count", 0),
            "status": guideline.status,
            "error": guideline.error,
            "extracted_text_chars": len(guideline.extracted_text),
            "parsed_brief": guideline.parsed_brief,
            "created_at": guideline.created_at,
        }

    # 4. Create Job
    dispatch_mode = "github" if is_github_dispatch_enabled() else "local"
    job = Job(
        id=new_id(),
        source_id=source.id,
        provider=base_settings.active_provider,
        settings=job_settings,
        dispatch_mode=dispatch_mode,
        max_attempts=int(os.environ.get("AUTOCLIP_MAX_ATTEMPTS", "3")),
    )
    await asyncio.to_thread(store.create_job, job)

    if guideline:
        await asyncio.to_thread(store.update_guideline, guideline.id, job_id=job.id)

    if dispatch_mode == "github":
        asyncio.create_task(dispatch_job_to_github(job, source))
    else:
        queue.notify()

    return JobOut.of(job, source, guideline)


@router.post("", response_model=JobOut, status_code=201)
async def create_job(
    payload: JobCreateIn | None = None,
    source_id: str | None = None,
) -> JobOut:
    sid = (payload.source_id if payload else None) or source_id
    if not sid:
        raise HTTPException(status_code=400, detail="source_id is required.")

    source = await asyncio.to_thread(store.get_source, sid)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found.")

    overrides = payload.settings if payload else JobSettingsIn()
    settings = _apply_overrides(load_settings(), overrides)
    if settings.clips.min_duration_s >= settings.clips.max_duration_s:
        raise HTTPException(
            status_code=400, detail="Minimum clip length must be below the maximum."
        )

    job_settings = settings.model_dump(mode="json")
    if payload and payload.campaign is not None:
        job_settings["campaign"] = payload.campaign.model_dump(mode="json")

    guideline = None
    gid = payload.guideline_id if payload else None
    if gid:
        guideline = await asyncio.to_thread(store.get_guideline, gid)
        if guideline:
            if (not payload or payload.campaign is None) and guideline.parsed_brief:
                job_settings["campaign"] = guideline.parsed_brief
            job_settings["guideline"] = {
                "id": guideline.id,
                "filename": guideline.filename,
                "mime_type": guideline.mime_type,
                "size_bytes": guideline.size_bytes,
                "status": guideline.status,
                "error": guideline.error,
                "extracted_text_chars": len(guideline.extracted_text),
                "parsed_brief": guideline.parsed_brief,
                "created_at": guideline.created_at,
            }

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

    if guideline:
        await asyncio.to_thread(store.update_guideline, guideline.id, job_id=job.id)

    if dispatch_mode == "github":
        asyncio.create_task(dispatch_job_to_github(job, source))
    else:
        queue.notify()

    return JobOut.of(job, source, guideline)


@router.get("", response_model=list[JobOut])
async def list_jobs(limit: int = 25) -> list[JobOut]:
    jobs = await asyncio.to_thread(store.list_jobs, limit)
    out: list[JobOut] = []
    for job in jobs:
        source = await asyncio.to_thread(store.get_source, job.source_id)
        guideline = await asyncio.to_thread(store.get_guideline_for_job, job.id)
        out.append(JobOut.of(job, source, guideline))
    return out


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: str) -> JobOut:
    job = await asyncio.to_thread(store.get_job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    source = await asyncio.to_thread(store.get_source, job.source_id)
    guideline = await asyncio.to_thread(store.get_guideline_for_job, job.id)
    return JobOut.of(job, source, guideline)


@router.get("/{job_id}/guideline/file")
async def get_job_guideline_file(job_id: str):
    """Serve the original uploaded guideline PDF/DOCX file."""
    guideline = await asyncio.to_thread(store.get_guideline_for_job, job_id)
    if guideline is None or not guideline.storage_path:
        raise HTTPException(status_code=404, detail="No guideline document found for this job.")
    path = Path(guideline.storage_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Guideline file not found on server disk.")
    return FileResponse(path, filename=guideline.filename, media_type=guideline.mime_type)


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
