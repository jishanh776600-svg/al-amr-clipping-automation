"""Autonomous Production Orchestration & Job Lifecycle Engine for AL AMR AutoClip.

Maintains SQLite / Render Control Plane as the authoritative source of truth.
Manages state transitions, idempotent worker dispatch, stale detection,
automatic retry with exponential backoff, cancellation, and job output manifests.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

from ..db import models, store
from ..db.models import Job, JobStatus, utcnow
from .events import Event, broker

log = logging.getLogger("alamr.orchestrator")

# Configuration with safe production defaults
DEFAULT_MAX_ATTEMPTS = int(os.environ.get("AUTOCLIP_MAX_ATTEMPTS", "3"))
DEFAULT_HEARTBEAT_TIMEOUT_S = float(os.environ.get("AUTOCLIP_WORKER_HEARTBEAT_TIMEOUT_SECONDS", "300.0"))
DEFAULT_RETRY_BASE_DELAY_S = float(os.environ.get("AUTOCLIP_RETRY_BASE_DELAY_SECONDS", "5.0"))
DEFAULT_RETRY_MAX_DELAY_S = float(os.environ.get("AUTOCLIP_RETRY_MAX_DELAY_SECONDS", "60.0"))


# --------------------------------------------------------------------------
# State Machine & Valid Transitions
# --------------------------------------------------------------------------

VALID_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    "queued": {"dispatching", "running", "processing", "uploading", "publishing", "done", "cancelled", "failed"},
    "dispatching": {"running", "processing", "uploading", "publishing", "done", "failed", "cancel_requested", "cancelled"},
    "running": {"processing", "uploading", "publishing", "done", "failed", "cancel_requested", "cancelled"},
    "processing": {"uploading", "publishing", "done", "failed", "cancel_requested"},
    "uploading": {"publishing", "done", "failed", "cancel_requested"},
    "publishing": {"done", "failed", "cancel_requested"},
    "cancel_requested": {"cancelled", "failed", "done"},
    "cancelled": {"queued", "dispatching"},  # Allowed via explicit operator retry
    "failed": {"queued", "dispatching"},     # Allowed via explicit or auto retry
    "done": set(),                           # Terminal authoritative state
}


class InvalidStateTransitionError(Exception):
    """Raised when an illegal state machine transition is attempted."""


def can_transition(current: JobStatus, target: JobStatus) -> bool:
    """Check if transitioning from current to target status is permissible."""
    if current == target:
        return True
    allowed = VALID_TRANSITIONS.get(current, set())
    return target in allowed


def validate_transition(current: JobStatus, target: JobStatus) -> None:
    """Validate transition or raise InvalidStateTransitionError."""
    if not can_transition(current, target):
        raise InvalidStateTransitionError(
            f"Invalid job state transition: cannot transition from '{current}' to '{target}'."
        )


# --------------------------------------------------------------------------
# Retry Policy & Error Classification
# --------------------------------------------------------------------------

NON_RETRYABLE_PATTERNS = [
    "invalid source",
    "source not found",
    "unsupported media",
    "unsupported format",
    "corrupt video",
    "cannot decode",
    "campaign not found",
    "permanently invalid",
    "authentication permanently invalid",
    "credentials permanently revoked",
    "operator cancellation",
    "cancelled by operator",
    "job cancelled",
    "malformed request",
]


def is_retryable_error(error: str | None) -> bool:
    """Determine whether an error is transient/retryable or permanently fatal."""
    if not error:
        return True
    lowered = error.lower()
    for pattern in NON_RETRYABLE_PATTERNS:
        if pattern in lowered:
            return False
    return True


def compute_backoff_delay(attempt: int, base_s: float = DEFAULT_RETRY_BASE_DELAY_S, max_s: float = DEFAULT_RETRY_MAX_DELAY_S) -> float:
    """Calculate exponential backoff delay in seconds for an attempt (1-indexed)."""
    delay = base_s * (2 ** max(0, attempt - 1))
    return min(delay, max_s)


# --------------------------------------------------------------------------
# GitHub Actions Run Operations
# --------------------------------------------------------------------------

async def cancel_github_workflow_run(run_id: str) -> bool:
    """Request cancellation of an active GitHub Actions run."""
    token = (
        os.environ.get("GITHUB_PAT")
        or os.environ.get("GH_TOKEN")
        or os.environ.get("GITHUB_TOKEN")
    )
    if not token or not run_id:
        return False

    repo = os.environ.get("GITHUB_REPOSITORY", "jishanh776600-svg/al-amr-clipping-automation")
    url = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/cancel"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "AL-AMR-AutoClip-Orchestrator/1.0",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=headers)
            if resp.status_code in (202, 200):
                log.info("Successfully requested GitHub run cancellation for run %s", run_id)
                return True
            log.warning("GitHub run %s cancellation returned HTTP %s: %s", run_id, resp.status_code, resp.text)
            return False
    except Exception as exc:
        log.warning("Failed to reach GitHub API to cancel run %s: %s", run_id, exc)
        return False


# --------------------------------------------------------------------------
# Job Cancellation Manager
# --------------------------------------------------------------------------

async def request_job_cancellation(job_id: str) -> Job:
    """Initiate graceful job cancellation."""
    from .queue import queue

    job = store.get_job(job_id)
    if job is None:
        raise ValueError(f"Job {job_id} not found.")

    if job.status in ("done", "cancelled", "failed"):
        return job

    now = utcnow()
    if job.status == "queued":
        # Immediate cancellation for queued jobs
        store.update_job(
            job_id,
            status="cancelled",
            cancelled_at=now,
            finished_at=now,
        )
        queue.cancel(job_id)
        broker.publish(Event(type="cancelled", job_id=job_id))
        updated = store.get_job(job_id)
        return updated or job

    # Running / dispatching / processing
    store.update_job(
        job_id,
        status="cancel_requested",
        cancel_requested_at=now,
    )
    broker.publish(Event(type="cancel_requested", job_id=job_id))

    # Signal in-process local runner
    queue.cancel(job_id)

    # Cancel remote GitHub Actions workflow if run ID exists
    if job.github_run_id:
        asyncio.create_task(cancel_github_workflow_run(job.github_run_id))

    updated = store.get_job(job_id)
    return updated or job


# --------------------------------------------------------------------------
# Stale Job Detection & Recovery
# --------------------------------------------------------------------------

def sweep_stale_jobs(heartbeat_timeout_s: float = DEFAULT_HEARTBEAT_TIMEOUT_S) -> list[Job]:
    """Detect jobs that haven't reported heartbeats within the threshold."""
    stale_candidates = store.list_stale_jobs(heartbeat_timeout_s=heartbeat_timeout_s)
    stale_jobs: list[Job] = []

    for job in stale_candidates:
        # Avoid overriding cancel requests or terminal states
        if job.status in ("done", "failed", "cancelled", "cancel_requested"):
            continue

        now = utcnow()
        err_msg = f"Worker heartbeat timed out after {int(heartbeat_timeout_s)}s without progress."
        log.warning("Job %s identified as STALE: %s", job.id, err_msg)

        store.update_job(
            job.id,
            stale_at=now,
            error=err_msg,
        )

        # Check if automatic retry is permissible
        if job.attempt < job.max_attempts and is_retryable_error(err_msg):
            log.info("Scheduling automated retry for stale job %s (attempt %d/%d)...", job.id, job.attempt + 1, job.max_attempts)
            next_attempt = job.attempt + 1
            store.update_job(
                job.id,
                status="queued",
                attempt=next_attempt,
                progress=0.0,
                last_heartbeat_at=None,
            )
            broker.publish(Event(type="retried", job_id=job.id, data={"attempt": next_attempt, "reason": "stale_recovery"}))
        else:
            store.update_job(
                job.id,
                status="failed",
                failed_at=now,
                finished_at=now,
            )
            broker.publish(Event(type="failed", job_id=job.id, data={"error": err_msg}))

        updated = store.get_job(job.id)
        if updated:
            stale_jobs.append(updated)

    return stale_jobs


# --------------------------------------------------------------------------
# Crash Recovery on Startup
# --------------------------------------------------------------------------

def reconcile_on_startup() -> None:
    """Authoritative reconciliation of jobs on control plane reboot."""
    from .queue import queue

    log.info("Reconciling jobs on control plane startup...")
    # 1. Check in-flight local jobs
    queue._requeue_interrupted()

    # 2. Reconcile GitHub-dispatched jobs left running
    running_jobs = store.list_jobs(limit=100, status="running")
    for job in running_jobs:
        if job.dispatch_mode == "github":
            last_activity = job.last_heartbeat_at or job.started_at or job.created_at
            log.info("Discovered active GitHub job %s on startup (run_id: %s, last activity: %s)", job.id, job.github_run_id, last_activity)

    # 3. Perform stale detection pass
    sweep_stale_jobs()


# --------------------------------------------------------------------------
# Authoritative Job Manifest
# --------------------------------------------------------------------------

def get_job_manifest(job_id: str) -> dict[str, Any] | None:
    """Generate forensic manifest answering 'What happened to this clip?' without raw logs."""
    job = store.get_job(job_id)
    if job is None:
        return None

    source = store.get_source(job.source_id)
    clips = store.list_clips(job_id)
    evaluations = store.list_campaign_evaluations_for_job(job_id) if hasattr(store, "list_campaign_evaluations_for_job") else []
    eval_by_clip = {ev.clip_id: ev for ev in evaluations}

    manifest_clips: list[dict[str, Any]] = []
    for c in clips:
        exports = store.list_exports(c.id)
        clip_exports: list[dict[str, Any]] = []
        for exp in exports:
            pub_records = store.list_publishing_records(export_id=exp.id)
            clip_exports.append({
                "export_id": exp.id,
                "ratio": exp.ratio,
                "style": exp.style,
                "size_bytes": exp.size_bytes,
                "drive_file_id": exp.drive_file_id,
                "drive_storage_key": exp.drive_storage_key,
                "drive_web_view_link": exp.drive_web_view_link,
                "download_url": f"/api/exports/{exp.id}/download",
                "stream_url": f"/api/exports/{exp.id}/stream",
                "publishing_records": [
                    {
                        "id": p.id,
                        "platform": p.platform,
                        "status": p.status,
                        "external_id": p.external_id,
                        "destination": p.destination,
                        "error": p.error,
                        "metadata": p.metadata,
                        "created_at": p.created_at,
                        "updated_at": p.updated_at,
                    }
                    for p in pub_records
                ],
            })

        ev = eval_by_clip.get(c.id)
        manifest_clips.append({
            "clip_id": c.id,
            "rank": c.rank,
            "title": c.title,
            "hook": c.hook,
            "duration_s": round(c.end_s - c.start_s, 2),
            "start_s": c.start_s,
            "end_s": c.end_s,
            "score": c.score,
            "status": c.status,
            "campaign_evaluation": {
                "approved": ev.approved if ev else None,
                "final_score": ev.final_score if ev else None,
                "hard_failures": ev.hard_failures if ev else [],
                "soft_warnings": ev.soft_warnings if ev else [],
            } if ev else None,
            "exports": clip_exports,
        })

    return {
        "job_id": job.id,
        "status": job.status,
        "current_stage": job.current_stage,
        "progress": job.progress,
        "error": job.error,
        "dispatch_mode": job.dispatch_mode,
        "attempt": job.attempt,
        "max_attempts": job.max_attempts,
        "github": {
            "run_id": job.github_run_id,
            "workflow": job.github_workflow,
            "job_id": job.github_job_id,
            "run_url": job.github_run_url,
            "run_status": job.github_run_status,
            "conclusion": job.github_conclusion,
        },
        "timestamps": {
            "created_at": job.created_at,
            "dispatched_at": job.dispatched_at,
            "started_at": job.started_at,
            "last_heartbeat_at": job.last_heartbeat_at,
            "stale_at": job.stale_at,
            "completed_at": job.completed_at,
            "failed_at": job.failed_at,
            "cancelled_at": job.cancelled_at,
            "cancel_requested_at": job.cancel_requested_at,
            "finished_at": job.finished_at,
            "updated_at": job.updated_at,
        },
        "source": {
            "id": source.id,
            "title": source.title,
            "duration_s": source.duration_s,
            "type": source.type,
        } if source else None,
        "clips": manifest_clips,
    }
