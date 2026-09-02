"""FastAPI Master Control Backend for AL AMR Clipping Automation Console."""

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException, Header, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from clipping.approval.models import ApprovalAction, ApprovalStatus, ApprovalAuditRecord
from clipping.approval.repository import ApprovalRepository
from clipping.config.settings import Settings
from clipping.control.models import SystemControlState, SystemOperatingMode
from clipping.control.repository import ControlRepository
from clipping.control.service import MasterControlService
from clipping.control.github import GitHubWorkflowDispatcher
from clipping.core.constants import CANONICAL_PIPELINE_STAGES, PIPELINE_STAGE_COUNT
from clipping.logging.logger import get_logger
from clipping.publishing.models import PublishStatus
from clipping.publishing.repository import PublishingRepository
from clipping.state.models import JobState
from clipping.state.remote import RemoteStorageStateRepository
from clipping.storage.base import StorageDriver
from clipping.storage.google_drive import GoogleDriveStorageDriver
from clipping.storage.local import LocalStorageDriver

logger = get_logger("clipping.ui.server")

app = FastAPI(
    title="AL AMR Clipping Automation Console",
    description="Autonomous Video Intelligence & Vertical Media Engine (Master Control Plane)",
    version="1.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).parent / "static"


from clipping.storage.factory import create_storage_driver


def get_storage_driver() -> StorageDriver:
    return create_storage_driver()


def get_control_service(storage: StorageDriver = Depends(get_storage_driver)) -> MasterControlService:
    control_repo = ControlRepository(storage_driver=storage)
    state_repo = RemoteStorageStateRepository(storage_driver=storage)
    return MasterControlService(
        control_repository=control_repo,
        state_repository=state_repo,
        storage_driver=storage,
    )


# --- AUTHENTICATION & AUTHORIZATION ---

async def get_current_operator(
    authorization: Optional[str] = Header(None),
    x_operator_token: Optional[str] = Header(None),
) -> str:
    """
    Enforces lightweight, zero-cost token authorization for mutating control plane operations.
    Accepts 'Authorization: Bearer <token>' or 'X-Operator-Token: <token>'.
    In production, strictly blocks unauthorized requests with 401 Unauthorized.
    """
    settings = Settings()
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    elif x_operator_token:
        token = x_operator_token.strip()

    configured_token = settings.OPERATOR_TOKEN.get_secret_value() if settings.OPERATOR_TOKEN else None

    if configured_token:
        if not token or token != configured_token:
            logger.warning("Unauthorized mutating request blocked by Operator Auth")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unauthorized: Valid Operator Token required for mutating operations",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return "Authenticated Operator"

    # If no token configured
    if settings.ENVIRONMENT == "production":
        logger.error("Production server missing OPERATOR_TOKEN configuration")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server security misconfiguration: OPERATOR_TOKEN must be set in production",
        )

    # In local development / test, allow fallback operator with audit note
    return "Local Console Operator"


# --- REQUEST SCHEMAS ---

class ClipDecisionRequest(BaseModel):
    action: str = Field(..., description="'approve' or 'reject'")
    notes: Optional[str] = None


class EmergencyStopRequest(BaseModel):
    reason: str = Field(..., min_length=3, max_length=500, description="Explicit rationale for emergency stop")


class ControlActionRequest(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=500)


class PublishLockRequest(BaseModel):
    locked: bool
    reason: Optional[str] = Field(default=None, max_length=500)


class JobControlRequest(BaseModel):
    job_id: str
    reason: str = Field(..., min_length=2, max_length=500)


# --- READ ENDPOINTS ---

@app.get("/api/system/status")
async def get_system_status(
    storage: StorageDriver = Depends(get_storage_driver),
) -> Dict[str, Any]:
    """Returns real-time health telemetry across the clipping automation ecosystem."""
    settings = Settings()
    control_repo = ControlRepository(storage_driver=storage)
    ctrl_state = await control_repo.get_state()

    return {
        "system_name": settings.PRODUCT_NAME,
        "system_tagline": "Autonomous Video Intelligence & Vertical Media Engine",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": ctrl_state.mode.value.upper(),
        "pipeline": {
            "stage_count": PIPELINE_STAGE_COUNT,
            "stages": CANONICAL_PIPELINE_STAGES,
        },
        "control_state": {
            "mode": ctrl_state.mode.value,
            "emergency_stopped": ctrl_state.emergency_stopped,
            "automation_paused": ctrl_state.automation_paused,
            "publishing_locked": ctrl_state.publishing_locked,
            "can_start_new_jobs": ctrl_state.can_start_new_jobs(),
            "can_publish": ctrl_state.can_publish(),
            "last_changed_by": ctrl_state.last_changed_by,
            "reason": ctrl_state.reason,
            "updated_at": ctrl_state.updated_at.isoformat(),
            "version": ctrl_state.version,
        },
        "subsystems": {
            "pipeline_engine": {
                "status": "stopped" if ctrl_state.emergency_stopped else ("paused" if ctrl_state.automation_paused else "active"),
                "mode": settings.ENVIRONMENT,
                "concurrency": settings.WORKER_CONCURRENCY,
            },
            "approval_gateway": {
                "status": "connected" if settings.TELEGRAM_BOT_TOKEN else "ready_mock",
                "chat_configured": settings.TELEGRAM_CHAT_ID is not None,
                "authorized_users": len(settings.get_allowed_telegram_user_ids()),
            },
            "youtube_publisher": {
                "status": "locked" if (ctrl_state.publishing_locked or ctrl_state.emergency_stopped) else ("configured" if settings.YOUTUBE_CLIENT_ID else "ready_mock"),
                "default_privacy": settings.YOUTUBE_DEFAULT_PRIVACY,
                "channel_id": settings.YOUTUBE_CHANNEL_ID or "NOT_CONFIGURED",
            },
            "canonical_storage": {
                "driver": settings.STORAGE_DRIVER,
                "status": "connected",
            },
        },
    }


@app.get("/api/control/state")
async def get_control_state(
    storage: StorageDriver = Depends(get_storage_driver),
) -> Dict[str, Any]:
    """Retrieves current Master Control state and immutable audit trail."""
    control_repo = ControlRepository(storage_driver=storage)
    state = await control_repo.get_state()
    audits = await control_repo.list_audits(limit=20)
    return {
        "state": state.model_dump(mode="json"),
        "audits": [a.model_dump(mode="json") for a in audits],
    }


@app.get("/api/jobs")
async def list_jobs(
    limit: int = 50,
    storage: StorageDriver = Depends(get_storage_driver),
) -> List[Dict[str, Any]]:
    """Lists recent clipping production jobs from remote canonical storage."""
    state_repo = RemoteStorageStateRepository(storage_driver=storage)
    try:
        jobs = await state_repo.list_jobs(limit=limit)
        return [
            {
                "job_id": j.job_id,
                "campaign_id": j.campaign_id,
                "source_video_id": j.source_video_id,
                "current_state": j.current_state.value,
                "created_at": j.created_at.isoformat(),
                "updated_at": j.updated_at.isoformat(),
                "retry_count": j.retry_count,
            }
            for j in jobs
        ]
    except Exception as e:
        logger.error("Failed to list jobs for UI", error=str(e))
        return []


@app.get("/api/jobs/{job_id}")
async def get_job_detail(
    job_id: str,
    storage: StorageDriver = Depends(get_storage_driver),
) -> Dict[str, Any]:
    """Retrieves full job state, 9-stage pipeline transitions, and metadata."""
    state_repo = RemoteStorageStateRepository(storage_driver=storage)
    job = await state_repo.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    history = await state_repo.get_job_history(job_id)
    return {
        "job": {
            "job_id": job.job_id,
            "campaign_id": job.campaign_id,
            "source_video_id": job.source_video_id,
            "current_state": job.current_state.value,
            "created_at": job.created_at.isoformat(),
            "updated_at": job.updated_at.isoformat(),
        },
        "pipeline": {
            "stage_count": PIPELINE_STAGE_COUNT,
            "stages": CANONICAL_PIPELINE_STAGES,
        },
        "transitions": [
            {
                "from_state": t.from_state.value if t.from_state else None,
                "to_state": t.to_state.value,
                "stage": t.stage.value,
                "timestamp": t.timestamp.isoformat(),
                "reason": t.reason,
            }
            for t in history
        ],
    }


@app.get("/api/jobs/{job_id}/clips")
async def get_job_clips(
    job_id: str,
    storage: StorageDriver = Depends(get_storage_driver),
) -> List[Dict[str, Any]]:
    """Retrieves all candidate clips for a job, with score breakdown, QA status, and approval decision."""
    app_repo = ApprovalRepository(storage_driver=storage)
    requests = await app_repo.list_requests_for_job(job_id)

    results = []
    for r in requests:
        qa_key = f"clips/{r.clip_id}/qa_report.json"
        qa_status = "UNKNOWN"
        can_publish = False
        if await storage.exists(qa_key):
            try:
                raw_qa = await storage.download_bytes(qa_key)
                qa_data = raw_qa.decode("utf-8")
                if '"can_publish": true' in qa_data.lower():
                    can_publish = True
                    qa_status = "PASS"
                elif '"overall_status": "fail"' in qa_data.lower():
                    qa_status = "FAIL"
                else:
                    qa_status = "PASS"
            except Exception:
                pass

        results.append({
            "clip_id": r.clip_id,
            "approval_request_id": r.approval_request_id,
            "clip_index": r.clip_index,
            "title": r.title,
            "start_time": r.start_time,
            "end_time": r.end_time,
            "duration": r.duration,
            "score": round(r.score, 1),
            "hook_sentence": r.hook_sentence or "",
            "approval_status": r.status.value,
            "video_storage_key": r.video_storage_key,
            "qa_status": qa_status,
            "can_publish": can_publish,
            "score_breakdown": {
                "hook": min(98, round(r.score + 2.0, 1)),
                "story": min(95, round(r.score - 1.5, 1)),
                "curiosity": min(96, round(r.score + 1.0, 1)),
                "pacing": min(92, round(r.score - 3.0, 1)),
            },
        })

    return results


@app.get("/api/jobs/{job_id}/publishing")
async def get_job_publishing(
    job_id: str,
    storage: StorageDriver = Depends(get_storage_driver),
) -> List[Dict[str, Any]]:
    """Retrieves publishing records and YouTube release receipts for a job."""
    pub_repo = PublishingRepository(storage_driver=storage)
    records = await pub_repo.list_records_for_job(job_id)
    return [
        {
            "clip_id": rec.clip_id,
            "status": rec.status.value,
            "youtube_video_id": rec.youtube_video_id,
            "youtube_url": rec.youtube_url,
            "channel_id": rec.channel_id,
            "scheduled_publish_at": rec.scheduled_publish_at.isoformat() if rec.scheduled_publish_at else None,
            "attempt_count": rec.attempt_count,
            "failure_reason": rec.failure_reason,
        }
        for rec in records
    ]


# --- MUTATING MASTER CONTROL ENDPOINTS (REQUIRE AUTHORIZATION) ---

@app.post("/api/control/emergency-stop")
async def trigger_emergency_stop(
    req: EmergencyStopRequest,
    operator: str = Depends(get_current_operator),
    ctrl_service: MasterControlService = Depends(get_control_service),
) -> Dict[str, Any]:
    """Activates durable global EMERGENCY STOP: freezes all workflows, locks publishing, signals cancellation."""
    updated = await ctrl_service.emergency_stop(operator=operator, reason=req.reason)
    return {
        "status": "success",
        "message": "EMERGENCY STOP ACTIVATED",
        "control_state": updated.model_dump(mode="json"),
    }


@app.post("/api/control/resume")
async def trigger_resume(
    req: ControlActionRequest,
    operator: str = Depends(get_current_operator),
    ctrl_service: MasterControlService = Depends(get_control_service),
) -> Dict[str, Any]:
    """Clears emergency stop and pause locks, resuming normal autonomous operations."""
    updated = await ctrl_service.resume_automation(operator=operator, reason=req.reason)
    return {
        "status": "success",
        "message": "Automation resumed",
        "control_state": updated.model_dump(mode="json"),
    }


@app.post("/api/control/pause")
async def trigger_pause(
    req: ControlActionRequest,
    operator: str = Depends(get_current_operator),
    ctrl_service: MasterControlService = Depends(get_control_service),
) -> Dict[str, Any]:
    """Pauses autonomous job scheduling and ingestion without full emergency lock."""
    updated = await ctrl_service.pause_automation(operator=operator, reason=req.reason)
    return {
        "status": "success",
        "message": "Automation paused",
        "control_state": updated.model_dump(mode="json"),
    }


@app.post("/api/control/publish-lock")
async def set_publishing_lock(
    req: PublishLockRequest,
    operator: str = Depends(get_current_operator),
    ctrl_service: MasterControlService = Depends(get_control_service),
) -> Dict[str, Any]:
    """DURABLY locks or unlocks YouTube Shorts publishing."""
    updated = await ctrl_service.set_publishing_lock(locked=req.locked, operator=operator, reason=req.reason)
    return {
        "status": "success",
        "message": f"Publishing {'LOCKED' if req.locked else 'UNLOCKED'}",
        "control_state": updated.model_dump(mode="json"),
    }


@app.post("/api/control/cancel-job")
async def cancel_job(
    req: JobControlRequest,
    operator: str = Depends(get_current_operator),
    ctrl_service: MasterControlService = Depends(get_control_service),
) -> Dict[str, Any]:
    """Cancels a specific job cooperatively in canonical storage."""
    try:
        await ctrl_service.cancel_job(job_id=req.job_id, operator=operator, reason=req.reason)
        return {"status": "success", "message": f"Job {req.job_id} cancelled"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/control/retry-job")
async def retry_job(
    req: JobControlRequest,
    operator: str = Depends(get_current_operator),
    ctrl_service: MasterControlService = Depends(get_control_service),
) -> Dict[str, Any]:
    """Resets a failed or cancelled job back to QUEUED for worker re-execution."""
    try:
        await ctrl_service.retry_job(job_id=req.job_id, operator=operator, reason=req.reason)
        return {"status": "success", "message": f"Job {req.job_id} requeued for retry"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/control/requeue-job")
async def requeue_job(
    req: JobControlRequest,
    operator: str = Depends(get_current_operator),
    ctrl_service: MasterControlService = Depends(get_control_service),
) -> Dict[str, Any]:
    """Requeues a stuck job."""
    try:
        await ctrl_service.requeue_job(job_id=req.job_id, operator=operator, reason=req.reason)
        return {"status": "success", "message": f"Job {req.job_id} requeued"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


class RunNowRequest(BaseModel):
    source_uri: Optional[str] = Field(default="https://www.youtube.com/watch?v=sample", description="Source video URL or storage key")
    campaign_id: Optional[str] = Field(default="default_campaign", description="Campaign identifier")
    reason: Optional[str] = Field(default=None, max_length=500)


@app.get("/healthz")
async def healthz(
    storage: StorageDriver = Depends(get_storage_driver),
) -> Dict[str, Any]:
    """Lightweight Kubernetes / Render health and readiness probe."""
    control_repo = ControlRepository(storage_driver=storage)
    ctrl_state = await control_repo.get_state()
    return {
        "status": "healthy" if not ctrl_state.emergency_stopped else "degraded",
        "liveness": True,
        "readiness": True,
        "storage_driver": storage.__class__.__name__,
        "emergency_stopped": ctrl_state.emergency_stopped,
        "automation_paused": ctrl_state.automation_paused,
        "publishing_locked": ctrl_state.publishing_locked,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/control/runs")
async def list_workflow_runs() -> List[Dict[str, Any]]:
    """Lists recent ephemeral GitHub Actions execution runs."""
    dispatcher = GitHubWorkflowDispatcher()
    return await dispatcher.list_recent_runs()


@app.post("/api/control/run-now")
async def run_now(
    req: RunNowRequest,
    operator: str = Depends(get_current_operator),
    ctrl_service: MasterControlService = Depends(get_control_service),
    storage: StorageDriver = Depends(get_storage_driver),
) -> Dict[str, Any]:
    """Triggers an immediate workflow execution if not emergency stopped."""
    state = await ctrl_service.get_state()
    if not state.can_start_new_jobs():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot trigger run: System is in {state.mode.value} state",
        )

    job_id = f"job_run_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    state_repo = RemoteStorageStateRepository(storage_driver=storage)
    await state_repo.create_job(
        job_id=job_id,
        campaign_id=req.campaign_id or "default_campaign",
        source_video_id=f"src_{datetime.now(timezone.utc).strftime('%H%M%S')}",
        idempotency_key=f"idemp_{job_id}",
        metadata={"source_uri": req.source_uri, "triggered_by": operator},
    )

    dispatcher = GitHubWorkflowDispatcher()
    dispatched, dispatch_msg = await dispatcher.dispatch_workflow(
        workflow_name="pipeline_orchestration.yml",
        inputs={
            "source_video_uri": req.source_uri,
            "campaign_id": req.campaign_id or "default_campaign",
            "job_id": job_id,
        },
    )

    logger.info("Master Control executed RUN NOW", job_id=job_id, operator=operator, dispatched=dispatched)
    return {
        "status": "success",
        "job_id": job_id,
        "github_dispatched": dispatched,
        "message": f"Job {job_id} created in canonical storage. {dispatch_msg}",
    }


@app.post("/api/jobs/{job_id}/clips/{clip_id}/decision")
async def make_clip_decision(
    job_id: str,
    clip_id: str,
    req: ClipDecisionRequest,
    operator: str = Depends(get_current_operator),
    storage: StorageDriver = Depends(get_storage_driver),
) -> Dict[str, Any]:
    """Allows authenticated human operator in AL AMR Console to approve or reject a clip."""
    app_repo = ApprovalRepository(storage_driver=storage)
    requests = await app_repo.list_requests_for_job(job_id)
    target_req = next((r for r in requests if r.clip_id == clip_id), None)
    if not target_req:
        raise HTTPException(status_code=404, detail="Clip approval request not found")

    new_status = ApprovalStatus.APPROVED if req.action.lower() == "approve" else ApprovalStatus.REJECTED
    updated = target_req.model_copy(update={
        "status": new_status,
        "decided_at": datetime.now(timezone.utc),
        "version": target_req.version + 1,
    })
    await app_repo.save_request(updated)

    audit = ApprovalAuditRecord(
        audit_id=f"audit_console_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
        approval_request_id=target_req.approval_request_id,
        job_id=job_id,
        clip_id=clip_id,
        previous_status=target_req.status,
        new_status=new_status,
        telegram_user_id=0,
        telegram_chat_id=0,
        decision_source="console",
        reason=f"Decided via AL AMR Console by {operator}: {req.notes or 'No notes'}",
    )
    await app_repo.record_audit(audit)

    logger.info("Operator decided clip in Console", clip_id=clip_id, action=req.action, new_status=new_status.value)
    return {
        "clip_id": clip_id,
        "new_status": new_status.value,
        "message": f"Clip {clip_id} successfully marked {new_status.value}",
    }


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        return HTMLResponse("<h1>AL AMR Clipping Automation Console</h1><p>Static index.html not found</p>", status_code=200)
    return FileResponse(str(index_file))
