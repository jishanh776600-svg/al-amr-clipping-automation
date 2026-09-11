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
    """Return 'github' or 'local'."""
    return os.environ.get("AUTOCLIP_DISPATCH_MODE", "local").strip().lower()


def is_github_dispatch_enabled() -> bool:
    mode = get_dispatch_mode()
    if mode == "github":
        return True
    if mode == "auto":
        token = (
            os.environ.get("GITHUB_PAT")
            or os.environ.get("GH_TOKEN")
            or os.environ.get("GITHUB_TOKEN")
        )
        return bool(token)
    return False


async def dispatch_job_to_github(
    job: Job,
    source: Source,
    campaign_brief: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Trigger GitHub Actions worker.yml workflow for the given job."""
    token = (
        os.environ.get("GITHUB_PAT")
        or os.environ.get("GH_TOKEN")
        or os.environ.get("GITHUB_TOKEN")
    )
    if not token:
        err = "GitHub token (GITHUB_PAT or GH_TOKEN) is not configured in server environment."
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

    inputs = {
        "job_id": str(job.id),
        "source_url": str(source_url),
        "campaign_brief": json.dumps(brief_data),
        "callback_url": str(callback_url),
        "callback_token": str(callback_token),
        "publish_targets": json.dumps(publish_targets),
    }

    dispatch_url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "AL-AMR-AutoClip-Dispatcher/1.0",
    }

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
            store.update_job(job.id, status="failed", error=err)
            broker.publish(Event(type="failed", job_id=job.id, data={"error": err}))
            raise RuntimeError(err) from exc

    if resp.status_code not in (200, 204):
        err = f"GitHub dispatch rejected with HTTP {resp.status_code}: {resp.text}"
        log.error(err)
        store.update_job(job.id, status="failed", error=err)
        broker.publish(Event(type="failed", job_id=job.id, data={"error": err}))
        raise RuntimeError(err)

    store.update_job(
        job.id,
        status="running",
        current_stage="dispatched_to_github",
        dispatch_mode="github",
        progress=0.05,
    )
    broker.publish(
        Event(
            type="dispatched",
            job_id=job.id,
            data={
                "stage": "dispatched_to_github",
                "repo": repo,
                "workflow": workflow,
                "ref": ref,
            },
        )
    )
    log.info("Successfully dispatched job %s to GitHub Actions.", job.id)
    return {
        "status": "dispatched",
        "job_id": job.id,
        "repo": repo,
        "workflow": workflow,
        "ref": ref,
    }
