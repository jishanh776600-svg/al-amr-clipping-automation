"""FastAPI Master Control Backend for AL AMR Clipping Automation Console."""

import json
import os
from datetime import datetime, timezone, timedelta
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


class TaskRetryRequest(BaseModel):
    reason: Optional[str] = None


class TaskCancelRequest(BaseModel):
    reason: Optional[str] = None


class ReclaimStaleWorkersRequest(BaseModel):
    stale_threshold_seconds: int = Field(default=0, ge=0)


class AccountStatusUpdateRequest(BaseModel):
    status: str = Field(..., description="'active', 'suspended', 'rate_limited', 'cooldown'")


class CampaignStatusUpdateRequest(BaseModel):
    status: str = Field(..., description="'discovered', 'active', 'paused', 'completed', 'rejected'")
    reason: Optional[str] = None


class EscalationResolveRequest(BaseModel):
    action: str = Field(..., description="'resolve' or 'reject' or operator action")
    notes: Optional[str] = None


class LaunchCampaignDiscoveryRequest(BaseModel):
    source: str = Field(default="https://campaigns.internal/discover", max_length=512)
    platform: str = Field(default="youtube_shorts", max_length=64)
    niche: Optional[str] = Field(default=None, max_length=128)
    priority: str = Field(default="normal", max_length=32)


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
    """Retrieves all candidate clips for a job, with real score breakdown, QA status, and approval decision."""
    app_repo = ApprovalRepository(storage_driver=storage)
    requests = await app_repo.list_requests_for_job(job_id)

    # Attempt to load discovery selection result for real score breakdown
    selected_clips_map: Dict[str, Any] = {}
    if requests:
        src_id = requests[0].source_video_id
        selected_key = f"sources/{src_id}/selected_clips.json"
        if await storage.exists(selected_key):
            try:
                sel_bytes = await storage.download_bytes(selected_key)
                sel_data = json.loads(sel_bytes.decode("utf-8"))
                for sc in sel_data.get("selected_clips", []):
                    cand_id = sc.get("candidate", {}).get("candidate_id")
                    if cand_id:
                        selected_clips_map[cand_id] = sc
            except Exception:
                pass

    results = []
    for r in requests:
        qa_key = f"clips/{r.clip_id}/qa_report.json"
        qa_status = r.qa_status or "UNKNOWN"
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

        # Real score breakdown from discovery engine if available
        matched_sc = selected_clips_map.get(r.clip_id)
        score_breakdown = None
        if matched_sc and "score" in matched_sc:
            score_obj = matched_sc["score"]
            score_breakdown = {
                "hook": round(score_obj.get("hook_strength", 0.0), 1),
                "story": round(score_obj.get("narrative_completeness", 0.0), 1),
                "curiosity": round(score_obj.get("curiosity_factor", 0.0), 1),
                "virality": round(score_obj.get("overall_virality_score", r.score), 1),
            }

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
            "score_breakdown": score_breakdown,
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
    return {
        "status": "success",
        "job_id": job_id,
        "clip_id": clip_id,
        "new_status": new_status.value,
        "decided_by": operator,
    }


# --- FUNCTIONAL CONTROL LAYER & DASHBOARD BACKEND ENDPOINTS ---

@app.get("/api/pipeline/stages")
async def get_pipeline_stages() -> Dict[str, Any]:
    """Retrieves the canonical 9-stage sequence and technical descriptions."""
    descriptions = {
        "01_INGESTION": "Download source video, validate format, extract audio, probe streams",
        "02_TRANSCRIPTION": "Run Faster-Whisper, generate word-level timestamped transcripts",
        "03_UNDERSTANDING": "Active speaker detection, face tracking, PySceneDetect shot cuts",
        "04_DISCOVERY": "Candidate generation, heuristic multi-factor virality scoring, deduplication",
        "05_REFRAME": "Speaker tracking bounding box crop, 9:16 layout composition",
        "06_RENDER": "FFmpeg GPU/CPU rendering, subtitle burn-in, EBU R128 loudness normalization",
        "07_QA": "ffprobe technical verification, video bitstream validation, audio loudness check",
        "08_APPROVAL": "Telegram human review & approval gateway, inline interactive keyboards",
        "09_PUBLISH": "Multi-platform distribution with idempotency and safety gate enforcement",
    }
    return {
        "stage_count": PIPELINE_STAGE_COUNT,
        "stages": [
            {
                "index": i + 1,
                "name": stage,
                "description": descriptions.get(stage, ""),
            }
            for i, stage in enumerate(CANONICAL_PIPELINE_STAGES)
        ],
    }


@app.get("/api/agent/status")
async def get_agent_status(
    storage: StorageDriver = Depends(get_storage_driver),
) -> Dict[str, Any]:
    """Retrieves operational status of the Master Agent and Cloud Worker subsystems."""
    from clipping.agent.cloud.queue import CloudTaskQueue
    from clipping.agent.repository import TaskRepository

    control_repo = ControlRepository(storage_driver=storage)
    control_state = await control_repo.get_state()

    queue = CloudTaskQueue(storage_driver=storage)
    pending_items = await queue.list_pending_items(limit=100)

    task_repo = TaskRepository(storage_driver=storage)
    recent_tasks = await task_repo.list_tasks(limit=50)

    active_tasks = [t for t in recent_tasks if t.status.value in ["running", "pending"]]
    failed_tasks = [t for t in recent_tasks if t.status.value in ["failed", "escalated"]]

    return {
        "status": "operational" if not control_state.emergency_stopped else "emergency_stopped",
        "operating_mode": control_state.mode.value,
        "queue_depth": len(pending_items),
        "active_tasks_count": len(active_tasks),
        "recent_failures_count": len(failed_tasks),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/agent/tasks")
async def list_agent_tasks_api(
    task_type: Optional[str] = None,
    task_status: Optional[str] = None,
    limit: int = 50,
    storage: StorageDriver = Depends(get_storage_driver),
) -> List[Dict[str, Any]]:
    """Lists recent Master Agent tasks with optional filtering."""
    from clipping.agent.repository import TaskRepository
    from clipping.agent.models import TaskType
    from clipping.agent.state import TaskState

    repo = TaskRepository(storage_driver=storage)
    type_filter = TaskType(task_type) if task_type else None
    status_filter = TaskState(task_status) if task_status else None
    tasks = await repo.list_tasks(status=status_filter, limit=limit)
    if type_filter:
        tasks = [t for t in tasks if t.task_type == type_filter]
    return [t.model_dump(mode="json") for t in tasks]


@app.get("/api/agent/tasks/{task_id}")
async def get_agent_task_api(
    task_id: str,
    storage: StorageDriver = Depends(get_storage_driver),
) -> Dict[str, Any]:
    """Retrieves full details for a specific Master Agent task."""
    from clipping.agent.repository import TaskRepository
    repo = TaskRepository(storage_driver=storage)
    task = await repo.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task.model_dump(mode="json")


@app.post("/api/agent/tasks/{task_id}/retry")
async def retry_agent_task_api(
    task_id: str,
    req: TaskRetryRequest = TaskRetryRequest(),
    operator: str = Depends(get_current_operator),
    storage: StorageDriver = Depends(get_storage_driver),
) -> Dict[str, Any]:
    """Retries a failed or escalated task and re-enqueues it to the cloud queue."""
    from clipping.agent.repository import TaskRepository
    from clipping.agent.state import TaskState
    from clipping.agent.cloud.queue import CloudTaskQueue

    repo = TaskRepository(storage_driver=storage)
    task = await repo.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    updated = task.transition_to(
        new_state=TaskState.PENDING,
        reason=req.reason or f"Manually retried by operator: {operator}",
        actor=operator,
    )
    await repo.save_task(updated)

    queue = CloudTaskQueue(storage_driver=storage)
    await queue.enqueue(
        task_id=task_id,
        priority=task.priority,
        metadata={"retried_by": operator, "retry_reason": req.reason or "manual_retry"},
    )
    logger.info("Operator retried task", task_id=task_id, operator=operator)
    return {
        "status": "success",
        "task_id": task_id,
        "task_status": updated.status.value,
        "message": f"Task {task_id} re-enqueued for cloud worker execution",
    }


@app.post("/api/agent/tasks/{task_id}/cancel")
async def cancel_agent_task_api(
    task_id: str,
    req: TaskCancelRequest = TaskCancelRequest(),
    operator: str = Depends(get_current_operator),
    storage: StorageDriver = Depends(get_storage_driver),
) -> Dict[str, Any]:
    """Cancels an active or pending task."""
    from clipping.agent.repository import TaskRepository
    from clipping.agent.state import TaskState
    from clipping.agent.cloud.queue import CloudTaskQueue

    repo = TaskRepository(storage_driver=storage)
    task = await repo.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    updated = task.transition_to(
        new_state=TaskState.CANCELLED,
        reason=req.reason or f"Cancelled by operator: {operator}",
        actor=operator,
    )
    await repo.save_task(updated)

    queue = CloudTaskQueue(storage_driver=storage)
    await queue.cancel(task_id=task_id)
    logger.info("Operator cancelled task", task_id=task_id, operator=operator)
    return {
        "status": "success",
        "task_id": task_id,
        "task_status": updated.status.value,
        "message": f"Task {task_id} cancelled",
    }


@app.get("/api/agent/queue")
async def get_agent_queue_status_api(
    storage: StorageDriver = Depends(get_storage_driver),
) -> Dict[str, Any]:
    """Retrieves current Cloud Task Queue items and backlog."""
    from clipping.agent.cloud.queue import CloudTaskQueue
    queue = CloudTaskQueue(storage_driver=storage)
    pending = await queue.list_pending_items(limit=100)
    return {
        "depth": len(pending),
        "pending_items": [item.model_dump(mode="json") for item in pending],
    }


@app.get("/api/agent/workers")
async def list_workers_api(
    limit: int = 50,
    storage: StorageDriver = Depends(get_storage_driver),
) -> List[Dict[str, Any]]:
    """Lists current worker leases, status, and health metrics."""
    from clipping.agent.cloud.lease import WorkerLeaseEngine
    engine = WorkerLeaseEngine(storage_driver=storage)
    leases = await engine.list_leases(limit=limit)
    now = datetime.now(timezone.utc)
    return [
        {
            "task_id": l.task_id,
            "worker_id": l.worker_id,
            "status": l.status,
            "claimed_at": l.claimed_at.isoformat(),
            "last_heartbeat_at": l.last_heartbeat_at.isoformat(),
            "lease_expires_at": l.lease_expires_at.isoformat(),
            "heartbeat_count": l.heartbeat_count,
            "is_valid": l.is_valid_at(now),
            "is_stale": l.is_stale_at(now),
        }
        for l in leases
    ]


@app.post("/api/agent/workers/reclaim-stale")
async def reclaim_stale_workers_api(
    req: ReclaimStaleWorkersRequest = ReclaimStaleWorkersRequest(),
    operator: str = Depends(get_current_operator),
    storage: StorageDriver = Depends(get_storage_driver),
) -> Dict[str, Any]:
    """Scans and reclaims stale worker leases back into the pending queue."""
    from clipping.agent.cloud.queue import CloudTaskQueue
    queue = CloudTaskQueue(storage_driver=storage)
    reclaimed = await queue.reclaim_stale_tasks(stale_threshold_seconds=req.stale_threshold_seconds)
    logger.info("Operator triggered stale task reclamation", operator=operator, reclaimed_count=len(reclaimed))
    return {
        "status": "success",
        "reclaimed_count": len(reclaimed),
        "reclaimed_task_ids": reclaimed,
    }


@app.get("/api/campaigns")
async def list_campaigns_api(
    storage: StorageDriver = Depends(get_storage_driver),
) -> List[Dict[str, Any]]:
    """Lists all discovered and active campaigns."""
    from clipping.agent.campaign.repository import CampaignRepository
    repo = CampaignRepository(storage_driver=storage)
    campaigns = await repo.list_campaigns()
    return [c.model_dump(mode="json") for c in campaigns]


@app.get("/api/campaigns/{campaign_id}")
async def get_campaign_detail_api(
    campaign_id: str,
    storage: StorageDriver = Depends(get_storage_driver),
) -> Dict[str, Any]:
    """Retrieves detailed record for a specific campaign."""
    from clipping.agent.campaign.repository import CampaignRepository
    repo = CampaignRepository(storage_driver=storage)
    campaign = await repo.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return campaign.model_dump(mode="json")


@app.post("/api/campaigns/{campaign_id}/status")
async def update_campaign_status_api(
    campaign_id: str,
    req: CampaignStatusUpdateRequest,
    operator: str = Depends(get_current_operator),
    storage: StorageDriver = Depends(get_storage_driver),
) -> Dict[str, Any]:
    """Updates status for an existing campaign."""
    from clipping.agent.campaign.repository import CampaignRepository
    from clipping.agent.campaign.models import CampaignStatus
    repo = CampaignRepository(storage_driver=storage)
    camp = await repo.get_campaign(campaign_id)
    if not camp:
        raise HTTPException(status_code=404, detail="Campaign not found")
    try:
        new_status = CampaignStatus(req.status.lower())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unsupported campaign status: {req.status}")

    updated = camp.model_copy(update={
        "status": new_status,
        "updated_at": datetime.now(timezone.utc),
    })
    await repo.save_campaign(updated)
    logger.info("Operator updated campaign status", campaign_id=campaign_id, status=req.status, operator=operator)
    return {
        "status": "success",
        "campaign_id": campaign_id,
        "new_status": new_status.value,
    }


@app.post("/api/campaigns/discover")
async def launch_campaign_discovery_api(
    req: LaunchCampaignDiscoveryRequest = LaunchCampaignDiscoveryRequest(),
    operator: str = Depends(get_current_operator),
    storage: StorageDriver = Depends(get_storage_driver),
) -> Dict[str, Any]:
    """Enqueues an autonomous campaign discovery task into the Cloud Task Queue."""
    import uuid
    from clipping.agent.cloud.queue import CloudTaskQueue
    from clipping.agent.cloud.telemetry import CloudTelemetryEngine, TelemetryEventType
    from clipping.agent.models import AgentTask, TaskPriority, TaskType
    from clipping.agent.repository import TaskRepository

    control_repo = ControlRepository(storage_driver=storage)
    ctrl = await control_repo.get_state()
    if ctrl.emergency_stopped:
        raise HTTPException(status_code=403, detail="Cannot launch discovery: Global emergency stop active")
    if ctrl.automation_paused:
        raise HTTPException(status_code=403, detail="Cannot launch discovery: Automation is currently paused")

    prio_map = {
        "low": TaskPriority.LOW,
        "normal": TaskPriority.NORMAL,
        "high": TaskPriority.HIGH,
        "critical": TaskPriority.CRITICAL,
    }
    prio = prio_map.get(req.priority.lower(), TaskPriority.NORMAL)
    task_id = f"task_disc_{uuid.uuid4().hex[:10]}"

    task = AgentTask(
        task_id=task_id,
        objective=f"Autonomous campaign discovery: {req.platform} via {req.source}",
        task_type=TaskType.CAMPAIGN_DISCOVERY,
        priority=prio,
        inputs={
            "capability": "campaign_discovery",
            "source": req.source,
            "platform": req.platform,
            "niche": req.niche,
            "launched_by": operator,
        },
    )

    repo = TaskRepository(storage_driver=storage)
    await repo.save_task(task)

    queue = CloudTaskQueue(storage_driver=storage)
    await queue.enqueue(
        task_id=task.task_id,
        priority=int(prio),
        metadata={"platform": req.platform, "source": req.source, "launched_by": operator},
    )

    telemetry = CloudTelemetryEngine(storage_driver=storage)
    await telemetry.record(
        event_type=TelemetryEventType.TASK_CLAIMED,
        task_id=task.task_id,
        worker_id="operator_console",
        capability_name="campaign_discovery",
        metadata={"platform": req.platform, "source": req.source, "priority": req.priority, "operator": operator},
    )
    logger.info("Operator launched campaign discovery task", task_id=task.task_id, operator=operator)
    return {
        "status": "success",
        "task_id": task.task_id,
        "message": f"Campaign discovery task {task.task_id} enqueued in Cloud Task Queue",
    }


@app.get("/api/accounts")
async def list_accounts_api(
    storage: StorageDriver = Depends(get_storage_driver),
) -> List[Dict[str, Any]]:
    """Lists safe account metadata from the Encrypted Credential Vault (zero credentials exposed)."""
    from clipping.agent.vault.vault import EncryptedCredentialVault
    vault = EncryptedCredentialVault(storage_driver=storage)
    accounts = await vault.list_accounts()
    return [a.to_safe_dict() for a in accounts]


@app.get("/api/accounts/{platform}/{account_id}")
async def get_account_detail_api(
    platform: str,
    account_id: str,
    storage: StorageDriver = Depends(get_storage_driver),
) -> Dict[str, Any]:
    """Retrieves safe account metadata (zero credentials exposed)."""
    from clipping.agent.vault.vault import EncryptedCredentialVault
    from clipping.agent.vault.models import AccountPlatform
    vault = EncryptedCredentialVault(storage_driver=storage)
    try:
        p_enum = AccountPlatform(platform.lower())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unsupported platform: {platform}")
    meta = await vault.get_account_metadata(platform=p_enum, account_id=account_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Account not found")
    return meta.to_safe_dict()


@app.post("/api/accounts/{platform}/{account_id}/status")
async def update_account_status_api(
    platform: str,
    account_id: str,
    req: AccountStatusUpdateRequest,
    operator: str = Depends(get_current_operator),
    storage: StorageDriver = Depends(get_storage_driver),
) -> Dict[str, Any]:
    """Updates account operating status in the credential vault."""
    from clipping.agent.vault.vault import EncryptedCredentialVault
    from clipping.agent.vault.models import AccountPlatform, AccountStatus
    vault = EncryptedCredentialVault(storage_driver=storage)
    try:
        p_enum = AccountPlatform(platform.lower())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unsupported platform: {platform}")
    try:
        st_enum = AccountStatus(req.status.lower())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unsupported status: {req.status}")

    updated = await vault.update_account_status(platform=p_enum, account_id=account_id, new_status=st_enum)
    if not updated:
        raise HTTPException(status_code=404, detail="Account not found")
    logger.info("Operator updated account status", platform=platform, account_id=account_id, status=req.status, operator=operator)
    return {
        "status": "success",
        "platform": platform,
        "account_id": account_id,
        "new_status": st_enum.value,
    }


@app.get("/api/approvals/pending")
async def list_pending_approvals_api(
    limit: int = 50,
    storage: StorageDriver = Depends(get_storage_driver),
) -> List[Dict[str, Any]]:
    """Lists pending clip approvals awaiting human review across all jobs."""
    app_repo = ApprovalRepository(storage_driver=storage)
    requests = await app_repo.list_all_pending_requests(limit=limit)
    return [r.model_dump(mode="json") for r in requests]


@app.get("/api/approvals/history")
async def list_approval_history_api(
    limit: int = 50,
    storage: StorageDriver = Depends(get_storage_driver),
) -> List[Dict[str, Any]]:
    """Lists past approval audit records across all jobs."""
    app_repo = ApprovalRepository(storage_driver=storage)
    audits = await app_repo.list_all_audits(limit=limit)
    return [a.model_dump(mode="json") for a in audits]


@app.get("/api/publishing/queue")
async def list_publishing_queue_api(
    limit: int = 50,
    storage: StorageDriver = Depends(get_storage_driver),
) -> List[Dict[str, Any]]:
    """Lists all publishing requests across jobs with publish gate status."""
    pub_repo = PublishingRepository(storage_driver=storage)
    control_repo = ControlRepository(storage_driver=storage)
    control_state = await control_repo.get_state()
    records = await pub_repo.list_all_records(limit=limit)
    return [
        {
            **r.model_dump(mode="json"),
            "publish_lock_active": control_state.publishing_locked,
            "emergency_stopped": control_state.emergency_stopped,
            "can_publish": control_state.can_publish(),
        }
        for r in records
    ]


@app.get("/api/agent/escalations")
async def list_agent_escalations_api(
    status: Optional[str] = None,
    limit: int = 50,
    storage: StorageDriver = Depends(get_storage_driver),
) -> List[Dict[str, Any]]:
    """Lists operator escalations requiring human intervention."""
    from clipping.agent.repository import TaskRepository
    from clipping.agent.escalation import EscalationStatus
    repo = TaskRepository(storage_driver=storage)
    st_filter = EscalationStatus(status.lower()) if status else None
    escalations = await repo.list_escalations(status=st_filter, limit=limit)
    return [e.model_dump(mode="json") for e in escalations]


@app.get("/api/agent/escalations/{escalation_id}")
async def get_escalation_detail_api(
    escalation_id: str,
    storage: StorageDriver = Depends(get_storage_driver),
) -> Dict[str, Any]:
    """Retrieves full details and context for a specific escalation record."""
    from clipping.agent.repository import TaskRepository
    repo = TaskRepository(storage_driver=storage)
    esc = await repo.get_escalation(escalation_id)
    if not esc:
        raise HTTPException(status_code=404, detail="Escalation not found")
    return esc.model_dump(mode="json")


@app.post("/api/agent/escalations/{escalation_id}/resolve")
async def resolve_escalation_api(
    escalation_id: str,
    req: EscalationResolveRequest,
    operator: str = Depends(get_current_operator),
    storage: StorageDriver = Depends(get_storage_driver),
) -> Dict[str, Any]:
    """Resolves or rejects an active operator escalation."""
    from clipping.agent.repository import TaskRepository
    repo = TaskRepository(storage_driver=storage)
    esc = await repo.get_escalation(escalation_id)
    if not esc:
        raise HTTPException(status_code=404, detail="Escalation not found")

    if req.action.lower() == "reject":
        resolved = esc.reject(operator=operator, notes=req.notes)
    else:
        resolved = esc.resolve(operator=operator, action=req.action, notes=req.notes)

    await repo.save_escalation(resolved)
    logger.info("Operator resolved escalation", escalation_id=escalation_id, operator=operator, action=req.action)
    return {
        "status": "success",
        "escalation_id": escalation_id,
        "escalation_status": resolved.status.value,
        "resolved_by": operator,
    }


@app.get("/api/agent/telemetry")
async def list_agent_telemetry_api(
    limit: int = 50,
    storage: StorageDriver = Depends(get_storage_driver),
) -> List[Dict[str, Any]]:
    """Retrieves recent cloud telemetry events."""
    from clipping.agent.cloud.telemetry import CloudTelemetryEngine
    engine = CloudTelemetryEngine(storage_driver=storage)
    events = await engine.list_events(limit=limit)
    return [e.model_dump(mode="json") for e in events]


@app.get("/api/dashboard/overview")
async def get_dashboard_overview_api(
    storage: StorageDriver = Depends(get_storage_driver),
) -> Dict[str, Any]:
    """
    Unified operational state across all 11 AL AMR CLIPPING functional subsystems:
    Control, Agent, Tasks, Workers, Campaigns, Accounts, Clipping Jobs,
    Approvals, Publishing, Escalations, and Telemetry.
    """
    from clipping.agent.cloud.queue import CloudTaskQueue
    from clipping.agent.campaign.repository import CampaignRepository
    from clipping.agent.vault.vault import EncryptedCredentialVault
    from clipping.agent.repository import TaskRepository
    from clipping.agent.cloud.lease import WorkerLeaseEngine
    from clipping.approval.repository import ApprovalRepository
    from clipping.publishing.repository import PublishingRepository
    from clipping.agent.cloud.telemetry import CloudTelemetryEngine

    settings = Settings()
    control_repo = ControlRepository(storage_driver=storage)
    control_state = await control_repo.get_state()

    camp_repo = CampaignRepository(storage_driver=storage)
    campaigns = await camp_repo.list_campaigns()

    vault = EncryptedCredentialVault(storage_driver=storage)
    accounts = await vault.list_accounts()

    task_repo = TaskRepository(storage_driver=storage)
    recent_tasks = await task_repo.list_tasks(limit=50)
    escalations = await task_repo.list_escalations(limit=20)

    queue = CloudTaskQueue(storage_driver=storage)
    pending = await queue.list_pending_items(limit=100)

    lease_engine = WorkerLeaseEngine(storage_driver=storage)
    leases = await lease_engine.list_leases(limit=20)
    now = datetime.now(timezone.utc)
    active_leases = [l for l in leases if l.is_valid_at(now)]

    state_repo = RemoteStorageStateRepository(storage_driver=storage)
    recent_jobs = await state_repo.list_jobs(limit=10)

    app_repo = ApprovalRepository(storage_driver=storage)
    pending_approvals = await app_repo.list_all_pending_requests(limit=50)

    pub_repo = PublishingRepository(storage_driver=storage)
    pub_records = await pub_repo.list_all_records(limit=20)

    telemetry_engine = CloudTelemetryEngine(storage_driver=storage)
    telemetry_events = await telemetry_engine.list_events(limit=10)

    browser_tasks = [t for t in recent_tasks if t.task_type.value == "browser_operation"]
    clipping_tasks = [t for t in recent_tasks if t.task_type.value == "media_clipping"]
    failures = [t for t in recent_tasks if t.status.value == "failed"]
    open_escalations = [e for e in escalations if e.status.value == "open"]

    return {
        "project_name": settings.PRODUCT_NAME,
        "status": "operational" if not control_state.emergency_stopped else "emergency_stopped",
        "operating_mode": control_state.mode.value,
        "emergency_stopped": control_state.emergency_stopped,
        "automation_paused": control_state.automation_paused,
        "publishing_locked": control_state.publishing_locked,
        "can_start_new_jobs": control_state.can_start_new_jobs(),
        "can_publish": control_state.can_publish(),
        "campaigns_count": len(campaigns),
        "accounts_count": len(accounts),
        "counts": {
            "campaigns": len(campaigns),
            "accounts": len(accounts),
            "queue_depth": len(pending),
            "active_workers": len(active_leases),
            "browser_jobs": len(browser_tasks),
            "clipping_jobs": len(clipping_tasks),
            "recent_jobs": len(recent_jobs),
            "pending_approvals": len(pending_approvals),
            "publishing_records": len(pub_records),
            "open_escalations": len(open_escalations),
            "recent_failures": len(failures),
        },
        "recent_failures": [f.model_dump(mode="json") for f in failures[:5]],
        "open_escalations": [e.model_dump(mode="json") for e in open_escalations[:5]],
        "recent_telemetry": [e.model_dump(mode="json") for e in telemetry_events[:5]],
        "timestamp": now.isoformat(),
    }


@app.get("/api/mission-control/overview")
async def get_mission_control_overview_api(
    storage: StorageDriver = Depends(get_storage_driver),
) -> Dict[str, Any]:
    """Backward compatibility alias for AL AMR CLIPPING dashboard overview."""
    return await get_dashboard_overview_api(storage=storage)


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
@app.get("/agent", response_class=HTMLResponse)
@app.get("/campaigns", response_class=HTMLResponse)
@app.get("/accounts", response_class=HTMLResponse)
@app.get("/clipping", response_class=HTMLResponse)
@app.get("/approvals", response_class=HTMLResponse)
@app.get("/publishing", response_class=HTMLResponse)
@app.get("/tasks", response_class=HTMLResponse)
@app.get("/workers", response_class=HTMLResponse)
@app.get("/escalations", response_class=HTMLResponse)
@app.get("/activity", response_class=HTMLResponse)
@app.get("/system", response_class=HTMLResponse)
async def serve_index():
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        return HTMLResponse("<h1>AL AMR Clipping Automation Console</h1><p>Static index.html not found</p>", status_code=200)
    return FileResponse(str(index_file))
