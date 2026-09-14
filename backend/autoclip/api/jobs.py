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
from ..campaign import (
    CampaignConflict,
    CampaignNormalizer,
    CampaignSpecification,
    IngestedDocument,
)
from ..campaign.drive_retriever import retrieve_drive_guideline
from ..campaign.extractor import (
    GuidelineExtractionError,
    compute_document_hash,
    extract_guideline_text,
    parse_guidelines_into_brief,
)
from ..config import load as load_settings
from ..db import models, store
from ..db.models import CampaignGuideline, CampaignSpecificationRecord, Job, Source, new_id
from ..jobs import orchestrator
from ..jobs.dispatcher import dispatch_job_to_github, is_github_dispatch_enabled
from ..jobs.events import Event, acquisition_event, broker
from ..jobs.queue import queue
from ..pipeline import ingest
from .auth import is_valid_token
from .schemas import (
    CampaignGuidelineOut,
    CampaignSpecificationOut,
    ClipCandidateOut,
    ClipSpecificationOut,
    DriveGuidelineIn,
    JobCreateIn,
    JobManifestOut,
    JobOut,
    JobSettingsIn,
    RetentionOptimizationOut,
    VisualCompositionOut,
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
    """One-click autonomous job creation: accepts Source (Video File or URL) + Campaign Materials (PDF, DOCX, Drive, URL)."""
    content_type = request.headers.get("content-type", "")

    url: str | None = None
    campaign_url: str | None = None
    video_file = None
    guideline_files: list[Any] = []
    guideline_ids: list[str] = []
    drive_guideline_urls: list[str] = []
    destinations: Any = None
    overrides: Any = None

    if "application/json" in content_type:
        body = await request.json()
        url = body.get("video_url") or body.get("url")
        campaign_url = body.get("campaign_url")

        if body.get("guideline_id"):
            guideline_ids.append(str(body.get("guideline_id")).strip())
        if body.get("guideline_ids"):
            gids = body.get("guideline_ids")
            if isinstance(gids, list):
                guideline_ids.extend([str(x).strip() for x in gids if str(x).strip()])
            elif isinstance(gids, str):
                guideline_ids.extend([x.strip() for x in gids.split(",") if x.strip()])

        if body.get("drive_guideline_url"):
            drive_guideline_urls.append(str(body.get("drive_guideline_url")).strip())
        if body.get("drive_guideline_urls"):
            durls = body.get("drive_guideline_urls")
            if isinstance(durls, list):
                drive_guideline_urls.extend([str(x).strip() for x in durls if str(x).strip()])
            elif isinstance(durls, str):
                drive_guideline_urls.extend([x.strip() for x in durls.split(",") if x.strip()])

        destinations = body.get("destinations")
        overrides = body.get("overrides") or body.get("settings")
    else:
        form = await request.form()
        raw_url = form.get("url") or form.get("video_url")
        if isinstance(raw_url, str):
            url = raw_url
        video_file = form.get("video_file")
        raw_campaign_url = form.get("campaign_url")
        if isinstance(raw_campaign_url, str):
            campaign_url = raw_campaign_url.strip() or None

        # Guidelines: support multi-file
        g_files = form.getlist("guideline_files")
        if not g_files and form.get("guideline_file"):
            g_files = [form.get("guideline_file")]
        guideline_files = [f for f in g_files if hasattr(f, "filename") and f.filename]

        # Guideline IDs
        g_ids = form.getlist("guideline_ids")
        if not g_ids and form.get("guideline_id"):
            g_ids = [form.get("guideline_id")]
        for item in g_ids:
            if isinstance(item, str):
                for sub in item.split(","):
                    if sub.strip():
                        guideline_ids.append(sub.strip())

        # Drive URLs
        d_urls = form.getlist("drive_guideline_urls")
        if not d_urls and form.get("drive_guideline_url"):
            d_urls = [form.get("drive_guideline_url")]
        for item in d_urls:
            if isinstance(item, str):
                for sub in item.split(","):
                    if sub.strip():
                        drive_guideline_urls.append(sub.strip())

        destinations = form.get("destinations")
        overrides = form.get("overrides")

    # 1. Ingest or resolve Source
    source = None
    if video_file and hasattr(video_file, "filename") and video_file.filename:
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
    elif url and str(url).strip():
        clean_url = str(url).strip()
        from ..pipeline.source_acquisition.security import validate_remote_url
        try:
            validate_remote_url(clean_url)
        except Exception as exc:
            hint = getattr(exc, "hint", "Please provide a valid public YouTube or remote video link.")
            msg = getattr(exc, "message", str(exc))
            raise HTTPException(
                status_code=422,
                detail={"message": msg, "hint": hint},
            ) from exc

        source = Source(
            id=new_id(),
            type="youtube",
            path="",
            title=clean_url,
            url=clean_url,
        )
        await asyncio.to_thread(store.create_source, source)
    else:
        raise HTTPException(
            status_code=400,
            detail="A source video must be provided — either upload a video file or provide a video URL.",
        )

    # 2. Ingest & Normalize Campaign Materials (Multi-document & Campaign URL)
    normalizer = CampaignNormalizer()
    ingested_docs: list[IngestedDocument] = []
    saved_guidelines: list[CampaignGuideline] = []

    storage_dir = paths.guidelines_dir()
    storage_dir.mkdir(parents=True, exist_ok=True)

    # 2a. Process uploaded guideline files
    for gfile in guideline_files:
        try:
            content = await gfile.read()
            filename = gfile.filename or "guideline.pdf"
            mime_type = getattr(gfile, "content_type", None) or "application/octet-stream"

            docs = normalizer.ingest_files([(filename, content)])
            ingested_docs.extend(docs)

            gid = new_id()
            storage_file = storage_dir / f"{gid}_{filename}"
            storage_file.write_bytes(content)

            sha256 = compute_document_hash(content)
            raw_text = docs[0].raw_text if docs and docs[0].status == "extracted" else ""
            err = docs[0].error if docs and docs[0].status == "failed" else None
            ext = Path(filename).suffix.lower()
            source_type = "upload_pdf" if ext == ".pdf" else "upload_docx"

            brief_dict = {}
            if raw_text:
                try:
                    b = parse_guidelines_into_brief(raw_text, filename)
                    brief_dict = b.model_dump(mode="json")
                except Exception:
                    pass

            g_rec = CampaignGuideline(
                id=gid,
                job_id=None,
                filename=filename,
                mime_type=mime_type,
                size_bytes=len(content),
                storage_path=str(storage_file),
                extracted_text=raw_text,
                parsed_brief=brief_dict,
                status=docs[0].status if docs else "failed",
                error=err,
                source_type=source_type,
                drive_file_id=None,
                sha256=sha256,
                word_count=len(re.findall(r"\b\w+\b", raw_text)),
                char_count=len(raw_text),
            )
            await asyncio.to_thread(store.create_guideline, g_rec)
            saved_guidelines.append(g_rec)
        except Exception as exc:
            log.warning("Failed processing guideline file %s: %s", getattr(gfile, "filename", "unknown"), exc)

    # 2b. Process pre-existing guideline IDs
    for gid in guideline_ids:
        existing = await asyncio.to_thread(store.get_guideline, gid)
        if existing:
            saved_guidelines.append(existing)
            doc = IngestedDocument(
                doc_id=existing.id,
                source_type=getattr(existing, "source_type", "pdf"),
                filename=existing.filename,
                size_bytes=existing.size_bytes,
                sha256=getattr(existing, "sha256", None),
                word_count=getattr(existing, "word_count", 0),
                char_count=getattr(existing, "char_count", 0),
                raw_text=existing.extracted_text,
                status=existing.status,
                error=existing.error,
                extracted_at=existing.created_at,
            )
            ingested_docs.append(doc)

    # 2c. Process Google Drive URLs
    if drive_guideline_urls:
        for d_url in drive_guideline_urls:
            try:
                fname, fcontent, fmime, fdrive_id = await asyncio.to_thread(retrieve_drive_guideline, d_url)
                docs = normalizer.ingest_files([(fname, fcontent)], source_type="drive")
                ingested_docs.extend(docs)

                gid = new_id()
                storage_file = storage_dir / f"{gid}_{fname}"
                storage_file.write_bytes(fcontent)
                sha256 = compute_document_hash(fcontent)
                raw_text = docs[0].raw_text if docs and docs[0].status == "extracted" else ""
                err = docs[0].error if docs and docs[0].status == "failed" else None

                brief_dict = {}
                if raw_text:
                    try:
                        b = parse_guidelines_into_brief(raw_text, fname)
                        brief_dict = b.model_dump(mode="json")
                    except Exception:
                        pass

                g_rec = CampaignGuideline(
                    id=gid,
                    job_id=None,
                    filename=fname,
                    mime_type=fmime,
                    size_bytes=len(fcontent),
                    storage_path=str(storage_file),
                    extracted_text=raw_text,
                    parsed_brief=brief_dict,
                    status=docs[0].status if docs else "failed",
                    error=err,
                    source_type="drive",
                    drive_file_id=fdrive_id,
                    sha256=sha256,
                    word_count=len(re.findall(r"\b\w+\b", raw_text)),
                    char_count=len(raw_text),
                )
                await asyncio.to_thread(store.create_guideline, g_rec)
                saved_guidelines.append(g_rec)
            except Exception as exc:
                log.warning("Direct Drive retrieval failed for %s: %s, falling back to normalizer", d_url, exc)
                drive_docs = await asyncio.to_thread(normalizer.ingest_drive_urls, [d_url])
                ingested_docs.extend(drive_docs)

    # 2d. Process Campaign URL
    if campaign_url and campaign_url.strip():
        url_doc, _ = await asyncio.to_thread(normalizer.ingest_campaign_url, campaign_url.strip())
        if url_doc:
            ingested_docs.append(url_doc)

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

    # 2e. Generate Normalized CampaignSpecification and bridge to CampaignBrief
    campaign_spec_rec: CampaignSpecificationRecord | None = None
    campaign_spec: CampaignSpecification | None = None
    primary_guideline: CampaignGuideline | None = saved_guidelines[0] if saved_guidelines else None

    if primary_guideline:
        job_settings["guideline"] = {
            "id": primary_guideline.id,
            "filename": primary_guideline.filename,
            "mime_type": primary_guideline.mime_type,
            "size_bytes": primary_guideline.size_bytes,
            "source_type": getattr(primary_guideline, "source_type", "upload_pdf"),
            "drive_file_id": getattr(primary_guideline, "drive_file_id", None),
            "sha256": getattr(primary_guideline, "sha256", None),
            "word_count": getattr(primary_guideline, "word_count", 0),
            "char_count": getattr(primary_guideline, "char_count", 0),
            "status": primary_guideline.status,
            "error": primary_guideline.error,
            "extracted_text_chars": len(primary_guideline.extracted_text),
            "parsed_brief": primary_guideline.parsed_brief,
            "created_at": primary_guideline.created_at,
        }

    if ingested_docs:
        campaign_spec = await asyncio.to_thread(normalizer.normalize, ingested_docs, campaign_url)
        spec_dict = campaign_spec.to_dict()

        # Bridge to CampaignBrief for downstream pipeline backward compatibility
        brief = campaign_spec.to_campaign_brief()
        job_settings["campaign"] = brief.model_dump(mode="json")
        job_settings["campaign_spec"] = spec_dict

        spec_id = campaign_spec.campaign_id or new_id()
        campaign_spec_rec = CampaignSpecificationRecord(
            id=spec_id,
            job_id=None,
            title=campaign_spec.title or (primary_guideline.filename if primary_guideline else "Campaign Specification"),
            spec=spec_dict,
            has_conflicts=campaign_spec.has_critical_conflicts or bool(campaign_spec.conflicts),
            conflict_count=len(campaign_spec.conflicts),
            document_count=len(campaign_spec.documents),
        )
        await asyncio.to_thread(store.create_campaign_spec, campaign_spec_rec)

    elif primary_guideline and primary_guideline.parsed_brief:
        job_settings["campaign"] = primary_guideline.parsed_brief
        job_settings["guideline"] = {
            "id": primary_guideline.id,
            "filename": primary_guideline.filename,
            "mime_type": primary_guideline.mime_type,
            "size_bytes": primary_guideline.size_bytes,
            "source_type": getattr(primary_guideline, "source_type", "upload_pdf"),
            "drive_file_id": getattr(primary_guideline, "drive_file_id", None),
            "sha256": getattr(primary_guideline, "sha256", None),
            "word_count": getattr(primary_guideline, "word_count", 0),
            "char_count": getattr(primary_guideline, "char_count", 0),
            "status": primary_guideline.status,
            "error": primary_guideline.error,
            "extracted_text_chars": len(primary_guideline.extracted_text),
            "parsed_brief": primary_guideline.parsed_brief,
            "created_at": primary_guideline.created_at,
        }

    # 4. Enforce Cloud YouTube Acquisition Safety
    is_yt = source.type == "youtube" or (source.url and ingest.is_youtube_url(source.url))
    is_cloud = bool(
        os.environ.get("RENDER")
        or os.environ.get("RENDER_EXTERNAL_URL")
        or os.environ.get("KUBERNETES_SERVICE_HOST")
    )
    from ..pipeline.source_acquisition.warp_checker import resolve_egress_proxy

    has_egress_proxy = bool(resolve_egress_proxy(base_settings.ingest.proxy))
    github_enabled = is_github_dispatch_enabled()

    if is_yt and is_cloud and not github_enabled and not has_egress_proxy:
        raise HTTPException(
            status_code=400,
            detail=(
                "Cloud YouTube acquisition requires either GitHub Actions worker dispatch (configure GITHUB_PAT in Settings or environment) "
                "or an active WARP egress proxy (socks5://127.0.0.1:1080). "
                "Direct local execution from cloud datacenter IPs is blocked by YouTube anti-bot protection."
            ),
        )

    dispatch_mode = "github" if github_enabled else "local"
    job = Job(
        id=new_id(),
        source_id=source.id,
        provider=base_settings.active_provider,
        settings=job_settings,
        dispatch_mode=dispatch_mode,
        max_attempts=int(os.environ.get("AUTOCLIP_MAX_ATTEMPTS", "3")),
        campaign_spec_id=campaign_spec_rec.id if campaign_spec_rec else None,
    )
    await asyncio.to_thread(store.create_job, job)

    if campaign_spec_rec:
        await asyncio.to_thread(store.update_campaign_spec, campaign_spec_rec.id, job_id=job.id)

    for g in saved_guidelines:
        await asyncio.to_thread(store.update_guideline, g.id, job_id=job.id)

    if dispatch_mode == "github":
        asyncio.create_task(dispatch_job_to_github(job, source))
    else:
        queue.notify()

    spec_data = campaign_spec_rec.spec if campaign_spec_rec else (campaign_spec.to_dict() if campaign_spec else None)
    return JobOut.of(job, source, primary_guideline, campaign_spec=spec_data)


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

    # Enforce Cloud YouTube Acquisition Safety
    is_yt = source.type == "youtube" or (source.url and ingest.is_youtube_url(source.url))
    is_cloud = bool(
        os.environ.get("RENDER")
        or os.environ.get("RENDER_EXTERNAL_URL")
        or os.environ.get("KUBERNETES_SERVICE_HOST")
    )
    from ..pipeline.source_acquisition.warp_checker import resolve_egress_proxy

    has_egress_proxy = bool(resolve_egress_proxy(settings.ingest.proxy))
    github_enabled = is_github_dispatch_enabled()

    if is_yt and is_cloud and not github_enabled and not has_egress_proxy:
        raise HTTPException(
            status_code=400,
            detail=(
                "Cloud YouTube acquisition requires either GitHub Actions worker dispatch (configure GITHUB_PAT in Settings or environment) "
                "or an active WARP egress proxy (socks5://127.0.0.1:1080). "
                "Direct local execution from cloud datacenter IPs is blocked by YouTube anti-bot protection."
            ),
        )

    dispatch_mode = "github" if github_enabled else "local"
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


@router.get("/{job_id}/campaign-specification", response_model=CampaignSpecificationOut)
async def get_job_campaign_specification(job_id: str) -> CampaignSpecificationOut:
    """Retrieve the multi-document normalized campaign specification for a job."""
    job = await asyncio.to_thread(store.get_job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")

    spec_record = None
    if getattr(job, "campaign_spec_id", None):
        spec_record = await asyncio.to_thread(store.get_campaign_spec, job.campaign_spec_id)
    if spec_record is None:
        spec_record = await asyncio.to_thread(store.get_campaign_spec_for_job, job_id)

    if spec_record is not None:
        return CampaignSpecificationOut.model_validate(spec_record.spec)

    if "campaign_spec" in job.settings and isinstance(job.settings["campaign_spec"], dict):
        return CampaignSpecificationOut.model_validate(job.settings["campaign_spec"])

    guideline = await asyncio.to_thread(store.get_guideline_for_job, job_id)
    if guideline and guideline.parsed_brief:
        spec = CampaignSpecification.from_campaign_brief(guideline.parsed_brief, filename=guideline.filename)
        return CampaignSpecificationOut.model_validate(spec.to_dict())

    raise HTTPException(status_code=404, detail="No campaign specification found for this job.")


@router.get("/{job_id}/candidates", response_model=list[ClipCandidateOut])
async def get_job_candidates_endpoint(
    job_id: str,
    selected_only: bool = False,
) -> list[ClipCandidateOut]:
    """Retrieve all discovered clip candidates with narrative signals, score breakdown, and rejection reasons."""
    job = await asyncio.to_thread(store.get_job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")

    candidates = await asyncio.to_thread(store.list_clip_candidates, job_id, selected_only=selected_only)
    return [ClipCandidateOut.of(c) for c in candidates]


@router.get("/{job_id}/clip-specifications", response_model=list[ClipSpecificationOut])
async def get_job_clip_specifications_endpoint(
    job_id: str,
    approved_only: bool = False,
    status: str | None = None,
) -> list[ClipSpecificationOut]:
    """Retrieve all production-ready clip specifications, boundary optimizations, and quality gate evaluations."""
    job = await asyncio.to_thread(store.get_job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")

    specs = await asyncio.to_thread(
        store.list_clip_specifications, job_id, approved_only=approved_only, status=status
    )
    return [ClipSpecificationOut.of(s) for s in specs]


@router.get("/{job_id}/visual-compositions", response_model=list[VisualCompositionOut])
async def get_job_visual_compositions_endpoint(
    job_id: str,
    status: str | None = None,
) -> list[VisualCompositionOut]:
    """Retrieve visual compositions, framing strategies, and pre-render visual quality gate evaluations."""
    job = await asyncio.to_thread(store.get_job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")

    compositions = await asyncio.to_thread(
        store.list_visual_compositions, job_id, status=status
    )
    return [VisualCompositionOut.of(c) for c in compositions]


@router.get("/{job_id}/retention-optimizations", response_model=list[RetentionOptimizationOut])
async def get_job_retention_optimizations_endpoint(
    job_id: str,
    approved_only: bool = False,
    status: str | None = None,
) -> list[RetentionOptimizationOut]:
    """Retrieve retention optimization records, pacing edits, and final quality gate evaluations."""
    job = await asyncio.to_thread(store.get_job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")

    records = await asyncio.to_thread(
        store.list_retention_optimizations, job_id, approved_only=approved_only, status=status
    )
    return [RetentionOptimizationOut.of(r) for r in records]


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
    if not is_valid_token(payload.token):
        raise HTTPException(status_code=401, detail="Unauthorized worker callback.")

    job = await asyncio.to_thread(store.get_job, job_id)
    if job is None:
        source = Source(id=new_id(), type="youtube", path="", title="Cloud Worker Ingest", url="")
        await asyncio.to_thread(store.create_source, source)
        valid_statuses = ("queued", "dispatching", "running", "processing", "uploading", "publishing", "done", "failed", "cancelled")
        initial_status = payload.status if payload.status in valid_statuses else "running"
        job = Job(
            id=job_id,
            source_id=source.id,
            status=initial_status,
            current_stage=payload.stage or "",
            progress=payload.progress or 0.0,
            dispatch_mode="github",
            github_run_id=payload.github_run_id,
        )
        await asyncio.to_thread(store.create_job, job)

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

    if payload.acquisition_event:
        event_dict = payload.acquisition_event
        broker.publish(
            acquisition_event(
                job_id=job_id,
                phase=event_dict.get("phase", "ACQUIRING"),
                status=event_dict.get("status", "active"),
                provider=event_dict.get("provider"),
                instance=event_dict.get("instance"),
                message=event_dict.get("message", ""),
                progress_percent=event_dict.get("progress_percent"),
                bytes_downloaded=event_dict.get("bytes_downloaded"),
                total_bytes=event_dict.get("total_bytes"),
                download_speed=event_dict.get("download_speed"),
                eta_seconds=event_dict.get("eta_seconds"),
                attempt=event_dict.get("attempt"),
                total_attempts=event_dict.get("total_attempts"),
                telemetry=event_dict.get("telemetry"),
            )
        )
        current_settings = dict(job.settings)
        if event_dict.get("telemetry"):
            current_settings["acquisition_telemetry"] = event_dict["telemetry"]
            update_kwargs["settings"] = current_settings

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
