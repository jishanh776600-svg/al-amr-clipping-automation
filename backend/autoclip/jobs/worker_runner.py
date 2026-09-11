"""Standalone worker runner for GitHub Actions on-demand compute.

Executes the full AutoClip pipeline (Whisper transcription, campaign evaluation,
MediaPipe active-speaker reframing, animated kinetic captions, FFmpeg rendering,
media validation), archives artifacts and clips to Google Drive persistent storage,
executes publishing destinations, and reports progress to the Render control plane.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx

from .. import db, paths
from ..config import load as load_settings
from ..db import store
from ..db.models import Job, Source, new_id
from ..pipeline import ingest
from ..pipeline.runner import PipelineRunner
from ..pipeline.validator import validate_media_output
from ..publishing.publisher import publish_clip
from ..storage.drive import GoogleDriveStorage

log = logging.getLogger("alamr.worker_runner")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AL AMR On-Demand Worker Runner")
    parser.add_argument("--job-id", required=True, help="Job ID to process")
    parser.add_argument("--source-url", required=True, help="URL or path of source video")
    parser.add_argument("--campaign-brief", default="{}", help="Campaign brief JSON string")
    parser.add_argument("--callback-url", default="", help="Control plane callback endpoint")
    parser.add_argument("--callback-token", default="", help="Auth token for callback")
    parser.add_argument("--publish-targets", default="[]", help="Publish destinations JSON array")
    parser.add_argument("--whisper-model", default="base", help="Faster-Whisper model")
    parser.add_argument("--max-clips", type=int, default=3, help="Maximum clips to generate")
    return parser.parse_args()


def send_callback(
    callback_url: str,
    token: str,
    *,
    status: str | None = None,
    stage: str | None = None,
    progress: float | None = None,
    error: str | None = None,
    clips: list[dict[str, Any]] | None = None,
    evaluations: list[dict[str, Any]] | None = None,
    exports: list[dict[str, Any]] | None = None,
) -> None:
    if not callback_url:
        return

    payload: dict[str, Any] = {
        "token": token,
        "github_run_id": os.environ.get("GITHUB_RUN_ID"),
    }
    if status is not None:
        payload["status"] = status
    if stage is not None:
        payload["stage"] = stage
    if progress is not None:
        payload["progress"] = progress
    if error is not None:
        payload["error"] = error
    if clips is not None:
        payload["clips"] = clips
    if evaluations is not None:
        payload["evaluations"] = evaluations
    if exports is not None:
        payload["exports"] = exports

    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-API-Key"] = token

    try:
        resp = httpx.post(callback_url, json=payload, headers=headers, timeout=20.0)
        log.info("Callback to %s reported (HTTP %s): stage=%s, progress=%s", callback_url, resp.status_code, stage, progress)
    except Exception as exc:
        log.warning("Callback to %s failed: %s", callback_url, exc)


async def async_main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    log.info("AL AMR Worker Runner started for job %s", args.job_id)

    # 1. Initialize layout and DB schema
    paths.ensure_layout()
    db.init()

    def report(**kw):
        send_callback(args.callback_url, args.callback_token, **kw)

    report(status="running", stage="worker_initialized", progress=0.05)

    # 2. Parse briefs and targets
    try:
        brief_data = json.loads(args.campaign_brief) if args.campaign_brief else {}
    except Exception:
        brief_data = {}

    try:
        publish_targets = json.loads(args.publish_targets) if args.publish_targets else []
    except Exception:
        publish_targets = []

    # 3. Ingest source
    report(stage="ingesting_source", progress=0.10)
    settings = load_settings()
    settings.whisper.model = args.whisper_model
    settings.clips.max_clips = args.max_clips

    source: Source
    source_url = args.source_url.strip()

    if Path(source_url).exists():
        log.info("Source is local file: %s", source_url)
        source = ingest.ingest_file(Path(source_url), move=False, title=Path(source_url).stem)
    elif ingest.is_youtube_url(source_url):
        log.info("Source is YouTube URL: %s", source_url)
        source = ingest.ingest_youtube(source_url, settings.ingest)
    else:
        log.info("Source is generic URL: %s", source_url)
        # Check if direct file download
        try:
            source = ingest.ingest_url(source_url, settings.ingest)
        except Exception:
            # Fallback direct download
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp:
                tmp_path = Path(tmp.name)
            log.info("Downloading direct stream to %s...", tmp_path)
            dl_headers: dict[str, str] = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Accept": "*/*",
            }
            if args.callback_token:
                dl_headers["Authorization"] = f"Bearer {args.callback_token}"
                dl_headers["X-API-Key"] = args.callback_token
            with httpx.stream("GET", source_url, headers=dl_headers, timeout=120.0) as r:
                r.raise_for_status()
                with open(tmp_path, "wb") as f:
                    for chunk in r.iter_bytes(chunk_size=1024 * 1024):
                        f.write(chunk)
            source = ingest.ingest_file(tmp_path, move=True, title="remote_source")

    store.create_source(source)
    log.info("Source ingested successfully: ID=%s, Title=%s, Duration=%.1fs", source.id, source.title, source.duration_s)

    # Optional: Archive source to Google Drive
    drive_storage = GoogleDriveStorage()
    if drive_storage.is_configured:
        try:
            log.info("Archiving source to Google Drive AL-AMR/sources/...")
            drive_storage.upload_file(
                source.path,
                f"{args.job_id}_{Path(source.path).name}",
                folder_type="sources",
                subfolder=args.job_id,
            )
        except Exception as exc:
            log.warning("Source backup to Google Drive failed: %s", exc)

    # 4. Create Job in local store
    job_settings = settings.model_dump(mode="json")
    if brief_data:
        job_settings["campaign"] = brief_data
    if publish_targets:
        job_settings["publish_targets"] = publish_targets

    job = Job(
        id=args.job_id,
        source_id=source.id,
        status="running",
        current_stage="pipeline_starting",
        progress=0.15,
        dispatch_mode="github",
        github_run_id=os.environ.get("GITHUB_RUN_ID"),
        settings=job_settings,
    )
    store.create_job(job)

    # 5. Execute PipelineRunner
    def on_progress(event):
        stage_name = event.stage.value if hasattr(event.stage, "value") else str(event.stage)
        report(stage=stage_name, progress=round(event.overall, 3))

    runner = PipelineRunner(
        job,
        source,
        settings=settings,
        on_progress=on_progress,
    )

    log.info("Running pipeline stages for job %s...", job.id)
    await runner.run()
    log.info("Pipeline processing completed for job %s.", job.id)

    # 6. Collect results
    clips = store.list_clips_for_job(job.id)
    clips_payload: list[dict[str, Any]] = []
    evaluations_payload: list[dict[str, Any]] = []
    exports_payload: list[dict[str, Any]] = []

    for clip in clips:
        clips_payload.append({
            "id": clip.id,
            "job_id": clip.job_id,
            "start_s": clip.start_s,
            "end_s": clip.end_s,
            "rank": clip.rank,
            "start_word": clip.start_word,
            "end_word": clip.end_word,
            "title": clip.title,
            "hook": clip.hook,
            "score": clip.score,
            "reason": clip.reason,
            "status": clip.status,
        })
        eval_row = store.get_campaign_evaluation(clip.id)
        if eval_row:
            evaluations_payload.append({
                "clip_id": eval_row.clip_id,
                "campaign_id": eval_row.campaign_id,
                "approved": eval_row.approved,
                "final_score": eval_row.final_score,
                "hook_score": eval_row.hook_score,
                "cta_score": eval_row.cta_score,
                "viral_score": eval_row.viral_score,
                "density_score": eval_row.density_score,
                "hard_failures": eval_row.hard_failures,
                "soft_warnings": eval_row.soft_warnings,
                "rule_results": eval_row.rule_results,
            })

        clip_exports = store.list_exports(clip.id)
        for exp in clip_exports:
            exp_path = Path(exp.path)
            if not exp_path.exists():
                continue

            # Validate media
            validation = validate_media_output(exp_path)
            if not validation.is_valid:
                log.warning("Export %s failed validation: %s", exp.id, validation.errors)

            drive_file_id = None
            drive_web_view_link = None
            drive_storage_key = None

            # Upload export to Google Drive
            if drive_storage.is_configured:
                report(stage="uploading_to_drive", progress=0.90)
                try:
                    meta = drive_storage.upload_file(
                        exp_path,
                        f"clip_{clip.id}_{exp.ratio.replace(':', 'x')}.mp4",
                        folder_type="clips",
                        subfolder=args.job_id,
                    )
                    drive_file_id = meta.file_id
                    drive_web_view_link = meta.web_view_link
                    drive_storage_key = meta.storage_key
                    store.update_export_drive_info(
                        exp.id,
                        drive_file_id=drive_file_id,
                        drive_web_view_link=drive_web_view_link,
                        drive_storage_key=drive_storage_key,
                    )
                except Exception as exc:
                    log.error("Failed to upload export %s to Google Drive: %s", exp.id, exc)

            # Publish if requested
            if publish_targets:
                report(stage="publishing", progress=0.95)
                log.info("Publishing clip %s to %s...", clip.id, publish_targets)
                publish_clip(
                    exp_path,
                    clip.title,
                    publish_targets,
                    drive_link=drive_web_view_link,
                )

            exports_payload.append({
                "id": exp.id,
                "clip_id": exp.clip_id,
                "path": exp.path,
                "ratio": exp.ratio,
                "style": exp.style,
                "size_bytes": exp_path.stat().st_size if exp_path.exists() else exp.size_bytes,
                "drive_file_id": drive_file_id,
                "drive_web_view_link": drive_web_view_link,
                "drive_storage_key": drive_storage_key,
            })

    # 7. Final completion callback
    report(
        status="done",
        stage="completed",
        progress=1.0,
        clips=clips_payload,
        evaluations=evaluations_payload,
        exports=exports_payload,
    )
    log.info("AL AMR Worker completed job %s successfully with %d clips.", args.job_id, len(clips))


def main() -> None:
    args = parse_args()
    try:
        asyncio.run(async_main())
    except Exception as exc:
        log.exception("Worker run failed with unhandled exception: %s", exc)
        send_callback(
            args.callback_url,
            args.callback_token,
            status="failed",
            stage="failed",
            error=str(exc),
        )
        sys.exit(1)


if __name__ == "__main__":
    main()

