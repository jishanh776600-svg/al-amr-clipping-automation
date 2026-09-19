"""GitHub Actions Dispatcher for on-demand worker execution.

Triggers the worker.yml GitHub Actions workflow via workflow_dispatch,
handing off Whisper, MediaPipe, AI evaluation, and FFmpeg processing to GitHub Actions compute.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

from ..db import store
from ..db.models import Job, Source
from ..jobs.events import Event, broker

log = logging.getLogger(__name__)

DEFAULT_GITHUB_REPO = "jishanh776600-svg/al-amr-clipping-automation"
DEFAULT_WORKFLOW = "worker.yml"
DEFAULT_REF = "main"


def get_dispatch_mode() -> str:
    """Return 'github', 'auto', or 'local'."""
    return os.environ.get("AUTOCLIP_DISPATCH_MODE", "auto").strip().lower()


def get_github_token() -> str | None:
    """Resolve GitHub token from environment variables or durable encrypted vault."""
    from .. import config

    try:
        secret = config.get_secret(config.GITHUB_PAT_KEY)
        if secret and secret.strip() and not config.is_masked_secret(secret):
            return secret.strip()
    except Exception as exc:
        log.debug("Failed to read GITHUB_PAT secret: %s", exc)
    return None


def is_cloud_environment() -> bool:
    """True when running in a cloud/container hosting environment like Render."""
    return bool(
        os.environ.get("RENDER")
        or os.environ.get("RENDER_EXTERNAL_URL")
        or os.environ.get("KUBERNETES_SERVICE_HOST")
        or os.environ.get("AUTOCLIP_ENV") == "production"
    )


def is_github_dispatch_enabled() -> bool:
    mode = get_dispatch_mode()
    if mode == "local":
        return False
    return bool(get_github_token())


def check_dispatch_capability() -> dict[str, Any]:
    """Inspect and return current dispatch readiness for preflight checks."""
    from .. import config
    from ..security.vault import get_vault

    token = get_github_token()
    is_cloud = is_cloud_environment()
    mode = get_dispatch_mode()
    vault = get_vault()
    pat_status = vault.get_secret_status(config.GITHUB_PAT_KEY)

    token_available = bool(token)
    if is_cloud:
        ready = token_available
        capability = "AVAILABLE" if ready else "UNAVAILABLE"
        reason = (
            "Ready for remote GitHub Actions worker execution."
            if ready
            else "GitHub Personal Access Token (GITHUB_PAT) is required for remote worker execution on cloud control plane. Please configure in Settings."
        )
    else:
        ready = True
        capability = "AVAILABLE"
        reason = (
            "Ready for remote GitHub Actions worker execution."
            if token_available
            else "Ready for local execution (GitHub PAT optional for local mode)."
        )

    return {
        "ready": ready,
        "capability": capability,
        "dispatch_mode": mode,
        "is_cloud": is_cloud,
        "token_available": token_available,
        "vault_configured": pat_status.get("configured", False),
        "reason": reason,
    }


async def dispatch_job_to_github(
    job: Job,
    source: Source,
    campaign_brief: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Trigger GitHub Actions worker.yml workflow for the given job."""
    token = get_github_token()
    if not token:
        err = "GitHub token (GITHUB_PAT or GH_TOKEN) is not configured in server environment or Settings."
        log.error(err)
        store.update_job(job.id, status="failed", error=err)
        broker.publish(Event(type="failed", job_id=job.id, data={"error": err}))
        raise RuntimeError(err)

    repo = os.environ.get("GITHUB_REPOSITORY") or DEFAULT_GITHUB_REPO
    workflow = os.environ.get("GITHUB_WORKFLOW") or DEFAULT_WORKFLOW
    ref = os.environ.get("GITHUB_REF") or DEFAULT_REF

    public_api_url = (
        os.environ.get("RENDER_EXTERNAL_URL")
        or os.environ.get("AUTOCLIP_API_URL")
        or "http://localhost:8000"
    ).rstrip("/")

    callback_url = f"{public_api_url}/api/jobs/{job.id}/worker-callback"
    callback_token = (
        os.environ.get("AL_AMR_MASTER_KEY")
        or os.environ.get("WORKER_CALLBACK_SECRET")
        or os.environ.get("AUTOCLIP_API_KEY")
        or os.environ.get("OPERATOR_TOKEN")
        or ""
    )

    source_url = source.url or ""
    if not source_url:
        source_url = f"{public_api_url}/api/sources/{source.id}/file"
        if callback_token:
            source_url += f"?token={callback_token}"

    brief_data = campaign_brief if campaign_brief is not None else job.settings.get("campaign", {})
    publish_targets = job.settings.get("publish_targets", [])

    from ..campaign.duration import resolve_duration_limits, resolve_max_clips

    min_dur, max_dur = resolve_duration_limits(job.settings, default_min=20.0, default_max=30.0)
    max_clips = resolve_max_clips(job.settings, default_max_clips=5)

    inputs = {
        "job_id": str(job.id),
        "source_url": str(source_url),
        "campaign_brief": json.dumps(brief_data),
        "callback_url": str(callback_url),
        "callback_token": str(callback_token),
        "publish_targets": json.dumps(publish_targets),
        "max_clips": str(max_clips),
        "min_duration_s": str(min_dur),
        "max_duration_s": str(max_dur),
        "visual_filter": str(
            job.settings.get("visual_filter")
            or (job.settings.get("export") or {}).get("visual_filter")
            or "original"
        ),
        "caption_style": str(
            job.settings.get("caption_style")
            or (job.settings.get("export") or {}).get("caption_style")
            or "classic_professional"
        ),
        "bgm_asset_id": str(
            job.settings.get("bgm_asset_id")
            or (job.settings.get("export") or {}).get("bgm_asset_id")
            or ""
        ),
        "job_settings": json.dumps(job.settings),
    }

    dispatch_url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "AL-AMR-AutoClip-Dispatcher/1.0",
    }

    # Idempotency check: prevent duplicate active dispatches
    current_job = store.get_job(job.id)
    if current_job and current_job.status in ("dispatching", "running", "processing", "uploading", "publishing") and current_job.github_run_id:
        log.warning("Job %s is already actively running on GitHub (run_id: %s); duplicate dispatch skipped.", job.id, current_job.github_run_id)
        return {
            "status": "already_active",
            "job_id": job.id,
            "github_run_id": current_job.github_run_id,
        }

    now = store.utcnow()
    store.update_job(
        job.id,
        status="dispatching",
        dispatched_at=now,
        github_workflow=workflow,
        dispatch_mode="github",
    )

    log.info("Dispatching job %s to GitHub Actions (%s / %s)...", job.id, repo, workflow)

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(
                dispatch_url,
                headers=headers,
                json={"ref": ref, "inputs": inputs},
            )
        except Exception as exc:
            err = f"Failed to reach GitHub API: {exc}"
            log.error(err)
            store.update_job(job.id, status="failed", failed_at=store.utcnow(), finished_at=store.utcnow(), error=err)
            broker.publish(Event(type="failed", job_id=job.id, data={"error": err}))
            raise RuntimeError(err) from exc

    if resp.status_code not in (200, 204):
        err = f"GitHub dispatch rejected with HTTP {resp.status_code}: {resp.text}"
        log.error(err)
        store.update_job(job.id, status="failed", failed_at=store.utcnow(), finished_at=store.utcnow(), error=err)
        broker.publish(Event(type="failed", job_id=job.id, data={"error": err}))
        raise RuntimeError(err)

    run_id = None
    run_url = None
    try:
        runs_url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/runs?event=workflow_dispatch&per_page=3"
        runs_resp = await client.get(runs_url, headers=headers)
        if runs_resp.status_code == 200:
            runs_data = runs_resp.json().get("workflow_runs", [])
            if runs_data:
                latest_run = runs_data[0]
                run_id = str(latest_run.get("id"))
                run_url = latest_run.get("html_url")
    except Exception as exc:
        log.debug("Could not immediately fetch GitHub run ID: %s", exc)

    update_payload: dict[str, Any] = {
        "status": "running",
        "current_stage": "dispatched_to_github",
        "dispatch_mode": "github",
        "progress": 0.05,
        "started_at": now,
        "last_heartbeat_at": now,
    }
    if run_id:
        update_payload["github_run_id"] = run_id
        update_payload["github_run_url"] = run_url

    store.update_job(job.id, **update_payload)
    broker.publish(
        Event(
            type="dispatched",
            job_id=job.id,
            data={
                "stage": "dispatched_to_github",
                "repo": repo,
                "workflow": workflow,
                "ref": ref,
                "github_run_id": run_id,
                "github_run_url": run_url,
            },
        )
    )
    log.info("Successfully dispatched job %s to GitHub Actions (run_id=%s).", job.id, run_id)
    return {
        "status": "dispatched",
        "job_id": job.id,
        "repo": repo,
        "workflow": workflow,
        "ref": ref,
        "github_run_id": run_id,
        "github_run_url": run_url,
    }
