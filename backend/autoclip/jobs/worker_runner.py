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
from ..publishing.base import PublishingMetadata
from ..publishing.service import PublishingService
from ..storage.drive import GoogleDriveStorage

log = logging.getLogger("alamr.worker_runner")


def _float_or_none(val: Any) -> float | None:
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


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
    parser.add_argument("--min-duration", type=_float_or_none, default=None, help="Minimum clip duration in seconds")
    parser.add_argument("--max-duration", type=_float_or_none, default=None, help="Maximum clip duration in seconds")
    parser.add_argument("--job-settings", default="{}", help="Job settings JSON string")
    parser.add_argument("--visual-filter", default="", help="Visual filter preset (e.g. black_and_white, cinematic_warm)")
    parser.add_argument("--caption-style", default="", help="Caption style preset (e.g. kinetic, bold_pop)")
    parser.add_argument("--bgm-asset-id", default="", help="BGM asset ID or name (e.g. motivation)")
    return parser.parse_args()


class WorkerCancelledError(Exception):
    """Raised when worker detects that job cancellation was requested by operator."""


def send_callback(
    callback_url: str,
    token: str,
    *,
    status: str | None = None,
    stage: str | None = None,
    progress: float | None = None,
    message: str | None = None,
    error: str | None = None,
    clips: list[dict[str, Any]] | None = None,
    evaluations: list[dict[str, Any]] | None = None,
    exports: list[dict[str, Any]] | None = None,
    final_renders: list[dict[str, Any]] | None = None,
    clip_metadata: list[dict[str, Any]] | None = None,
    publishing_records: list[dict[str, Any]] | None = None,
    approvals: list[dict[str, Any]] | None = None,
    acquisition_event: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not callback_url:
        return None

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
    if message is not None:
        payload["message"] = message
    if error is not None:
        payload["error"] = error
    if clips is not None:
        payload["clips"] = clips
    if evaluations is not None:
        payload["evaluations"] = evaluations
    if exports is not None:
        payload["exports"] = exports
    if final_renders is not None:
        payload["final_renders"] = final_renders
    if clip_metadata is not None:
        payload["clip_metadata"] = clip_metadata
    if publishing_records is not None:
        payload["publishing_records"] = publishing_records
    if approvals is not None:
        payload["approvals"] = approvals
    if acquisition_event is not None:
        payload["acquisition_event"] = acquisition_event

    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-API-Key"] = token

    try:
        resp = httpx.post(callback_url, json=payload, headers=headers, timeout=20.0)
        log.info("Callback to %s reported (HTTP %s): stage=%s, progress=%s", callback_url, resp.status_code, stage, progress)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") in ("cancel_requested", "cancelled"):
                log.warning("Received cancellation signal from control plane! Terminating worker...")
                raise WorkerCancelledError("Job cancelled by operator")
            return data
    except WorkerCancelledError:
        raise
    except Exception as exc:
        log.warning("Callback to %s failed: %s", callback_url, exc)
    return None


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

    # Listen for granular acquisition events and relay to control plane
    from ..jobs.events import broker

    def on_acquisition_event(event):
        if event.type == "acquisition" and event.job_id == args.job_id:
            report(
                stage="acquiring_source",
                acquisition_event=event.data,
            )

    broker.add_listener(on_acquisition_event)

    report(status="running", stage="worker_initialized", progress=0.05)

    # 2. Parse briefs and targets
    try:
        brief_data = json.loads(args.campaign_brief) if args.campaign_brief else {}
    except Exception:
        brief_data = {}

    publish_targets: list[str] = []
    if args.publish_targets:
        raw_pt = str(args.publish_targets).strip()
        try:
            parsed = json.loads(raw_pt)
            if isinstance(parsed, list):
                publish_targets = [str(x).strip() for x in parsed if str(x).strip()]
            elif isinstance(parsed, str):
                publish_targets = [parsed.strip()]
        except Exception:
            cleaned = raw_pt.strip("[]()\"'").replace("\\", "").replace('"', '').replace("'", "")
            publish_targets = [p.strip() for p in cleaned.split(",") if p.strip()]
    log.info("Resolved publish targets: %s (raw: %r)", publish_targets, args.publish_targets)

    # 3. Ingest source & configure settings
    report(stage="acquiring_source", progress=0.05)
    settings = load_settings()
    settings.whisper.model = args.whisper_model
    settings.clips.max_clips = args.max_clips

    # Parse and layer incoming job settings and duration arguments
    incoming_settings: dict[str, Any] = {}
    env_settings = os.environ.get("WORKER_JOB_SETTINGS") or os.environ.get("AUTOCLIP_JOB_SETTINGS")
    if env_settings and str(env_settings).strip() not in ("", "{}"):
        try:
            incoming_settings = json.loads(env_settings)
        except Exception as exc:
            log.warning("Failed parsing env WORKER_JOB_SETTINGS: %s", exc)

    if not incoming_settings:
        raw_job_settings = args.job_settings if (args.job_settings and str(args.job_settings).strip() != "{}") else "{}"
        if raw_job_settings and raw_job_settings.strip() != "{}":
            try:
                incoming_settings = json.loads(raw_job_settings) if isinstance(raw_job_settings, str) else dict(raw_job_settings)
            except Exception as exc:
                log.warning("Failed parsing args.job_settings: %s", exc)

    # Layer explicit CLI arguments over incoming_settings
    cli_filter = getattr(args, "visual_filter", None)
    if cli_filter and str(cli_filter).strip():
        incoming_settings["visual_filter"] = str(cli_filter).strip()

    cli_caption = getattr(args, "caption_style", None)
    if cli_caption and str(cli_caption).strip():
        incoming_settings["caption_style"] = str(cli_caption).strip()

    cli_bgm = getattr(args, "bgm_asset_id", None)
    if cli_bgm and str(cli_bgm).strip():
        val = str(cli_bgm).strip()
        incoming_settings["bgm_asset_id"] = val
        if val.lower() in ("none", "null", "false", "no", "disabled", "__none__"):
            incoming_settings["bgm_enabled"] = False
        else:
            incoming_settings["bgm_enabled"] = True

    from autoclip.campaign.duration import resolve_duration_limits, resolve_max_clips

    eff_min_dur, eff_max_dur = resolve_duration_limits(
        job_settings=incoming_settings,
        campaign_brief=brief_data if brief_data else None,
        default_min=20.0,
        default_max=30.0,
    )

    if args.min_duration is not None and args.min_duration > 0:
        eff_min_dur = float(args.min_duration)
    if args.max_duration is not None and args.max_duration > 0:
        eff_max_dur = float(args.max_duration)

    if eff_min_dur >= eff_max_dur:
        eff_max_dur = eff_min_dur + 10.0

    eff_max_clips = resolve_max_clips(
        job_settings=incoming_settings,
        campaign_brief=brief_data if brief_data else None,
        default_max_clips=5,
    )
    if args.max_clips is not None and args.max_clips > 0:
        eff_max_clips = int(args.max_clips)

    settings.clips.min_duration_s = eff_min_dur
    settings.clips.max_duration_s = eff_max_dur
    settings.clips.max_clips = eff_max_clips
    incoming_settings["max_clips"] = eff_max_clips
    log.info(
        "AL AMR Worker configured duration constraints: min=%.1fs, max=%.1fs, max_clips=%d",
        settings.clips.min_duration_s,
        settings.clips.max_duration_s,
        settings.clips.max_clips,
    )

    source: Source
    source_url = args.source_url.strip()

    def on_progress(p: float) -> None:
        report(stage="acquiring_source", progress=round(0.05 + 0.04 * max(0.0, min(1.0, p)), 3))

    if Path(source_url).exists():
        log.info("Source is local file: %s", source_url)
        report(stage="validating_source", progress=0.10)
        source = ingest.ingest_file(Path(source_url), move=False, title=Path(source_url).stem)
    elif ingest.is_youtube_url(source_url):
        log.info("Source is YouTube URL: %s", source_url)
        source = ingest.ingest_youtube(source_url, settings.ingest, job_id=args.job_id, on_progress=on_progress)
        report(stage="validating_source", progress=0.10)
    else:
        log.info("Source is generic URL: %s", source_url)
        # Check if direct file download
        try:
            source = ingest.ingest_url(source_url, settings.ingest, job_id=args.job_id, on_progress=on_progress)
            report(stage="validating_source", progress=0.10)
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
            report(stage="validating_source", progress=0.10)
            source = ingest.ingest_file(tmp_path, move=True, title="remote_source")

    store.create_source(source)
    report(stage="source_acquired", progress=0.12)
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
    if incoming_settings:
        job_settings.update(incoming_settings)
    job_settings["min_duration_s"] = eff_min_dur
    job_settings["max_duration_s"] = eff_max_dur
    job_settings["max_clips"] = eff_max_clips
    if "clips" not in job_settings or not isinstance(job_settings["clips"], dict):
        job_settings["clips"] = {}
    job_settings["clips"]["min_duration_s"] = eff_min_dur
    job_settings["clips"]["max_duration_s"] = eff_max_dur
    job_settings["clips"]["max_clips"] = eff_max_clips
    if brief_data:
        job_settings["campaign"] = brief_data
    if publish_targets:
        job_settings["publish_targets"] = publish_targets
    if hasattr(source, "source_acquisition") and source.source_acquisition:
        job_settings["source_acquisition"] = source.source_acquisition

    # 4b. Propagate visual_filter and caption_style settings
    eff_visual_filter = (
        job_settings.get("visual_filter")
        or (job_settings.get("export") or {}).get("visual_filter")
        or "original"
    )
    eff_caption_style = (
        job_settings.get("caption_style")
        or (job_settings.get("export") or {}).get("caption_style")
        or settings.export.caption_style
        or "classic_professional"
    )
    settings.export.visual_filter = eff_visual_filter
    settings.export.caption_style = eff_caption_style
    job_settings["visual_filter"] = eff_visual_filter
    job_settings["caption_style"] = eff_caption_style

    # 4c. Ensure BGM assets are available and properly referenced on worker filesystem
    bgm_setting = job_settings.get("bgm_enabled")
    bgm_asset_id = job_settings.get("bgm_asset_id") or (job_settings.get("export") or {}).get("bgm_asset_id")
    if str(bgm_asset_id).lower() in ("none", "null", "false", "no", "disabled", "__none__"):
        bgm_enabled = False
    elif bgm_setting is not None:
        bgm_enabled = bool(bgm_setting)
    else:
        # Default enabled if BGM assets exist
        bgm_enabled = True

    from ..bgm.vault import BGMVault
    vault = BGMVault()
    vault.reconcile_vault()

    if bgm_enabled:
        local_asset = vault.get_asset(bgm_asset_id) if bgm_asset_id else None
        local_path = Path(local_asset.file_path) if local_asset and Path(local_asset.file_path).is_file() else None

        # If missing locally and callback_url available, attempt download from control plane stream
        if not local_path and bgm_asset_id and args.callback_url:
            base_url = args.callback_url.split("/api/jobs")[0] if "/api/jobs" in args.callback_url else ""
            if base_url:
                stream_url = f"{base_url}/api/bgm/{bgm_asset_id}/stream"
                log.info("Downloading BGM asset %s from control plane stream %s...", bgm_asset_id, stream_url)
                dl_dest = vault.base_dir / f"{bgm_asset_id}.wav"
                try:
                    dl_headers: dict[str, str] = {}
                    if args.callback_token:
                        dl_headers["Authorization"] = f"Bearer {args.callback_token}"
                        dl_headers["X-API-Key"] = args.callback_token
                    with httpx.stream("GET", stream_url, headers=dl_headers, timeout=60.0) as r:
                        if r.status_code == 200:
                            with open(dl_dest, "wb") as f:
                                for chunk in r.iter_bytes(1024 * 64):
                                    f.write(chunk)
                            vault.reconcile_vault()
                            local_asset = vault.get_asset(bgm_asset_id)
                            if dl_dest.is_file():
                                local_path = dl_dest
                except Exception as exc:
                    log.warning("Could not download BGM asset %s: %s", bgm_asset_id, exc)

        # If still missing, check if any local vault asset can serve as fallback
        if not local_path or not local_path.is_file():
            assets = vault.list_assets(enabled_only=True)
            for a in assets:
                if Path(a.file_path).is_file():
                    local_asset = a
                    local_path = Path(a.file_path)
                    log.info("Worker fallback to local BGM asset %s (%s)", a.name, a.id)
                    break

        if local_path and local_path.is_file():
            job_settings["bgm_enabled"] = True
            job_settings["bgm_asset_id"] = local_asset.id if local_asset else bgm_asset_id
            job_settings["bgm_asset_name"] = local_asset.name if local_asset else "BGM"
            job_settings["bgm_asset_path"] = str(local_path.resolve())
            log.info("Resolved worker BGM asset path: %s", job_settings["bgm_asset_path"])
        else:
            log.warning("No BGM asset available on worker; proceeding with bgm_enabled=False")
            job_settings["bgm_enabled"] = False
            job_settings["bgm_asset_path"] = None

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
        report(stage=stage_name, progress=round(event.overall, 3), message=getattr(event, "message", None))

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
    publishing_payload: list[dict[str, Any]] = []
    publishing_service = PublishingService()

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

            # Validate media output including strict duration bounds check
            val_min_dur = float(job_settings["min_duration_s"])
            val_max_dur = float(job_settings["max_duration_s"])
            validation = validate_media_output(exp_path, min_duration_s=val_min_dur, max_duration_s=val_max_dur)
            actual_mp4_dur = validation.duration_s
            selected_clip_dur = clip.end_s - clip.start_s

            if not validation.is_valid:
                log.error(
                    "CRITICAL: Export %s (clip %s) failed duration or media validation: %s. Rejecting upload and publish.",
                    exp.id,
                    clip.id,
                    validation.errors,
                )
                log.info(
                    "DURATION_VALIDATION: configured_min_duration=%.1f configured_max_duration=%.1f selected_clip_duration=%.2f final_mp4_duration=%.2f duration_validation = FAIL",
                    val_min_dur,
                    val_max_dur,
                    selected_clip_dur,
                    actual_mp4_dur,
                )
                continue

            log.info(
                "DURATION_VALIDATION: configured_min_duration=%.1f configured_max_duration=%.1f selected_clip_duration=%.2f final_mp4_duration=%.2f duration_validation = PASS",
                val_min_dur,
                val_max_dur,
                selected_clip_dur,
                actual_mp4_dur,
            )

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
                    log.info(
                        "Successfully uploaded export %s to Google Drive: file_id=%s, key=%s, link=%s",
                        exp.id,
                        drive_file_id,
                        drive_storage_key,
                        drive_web_view_link,
                    )
                except Exception as exc:
                    log.error("Failed to upload export %s to Google Drive: %s", exp.id, exc)

            # Publish if requested
            if publish_targets:
                report(stage="publishing", progress=0.95)
                log.info("Publishing export %s (clip %s) to %s...", exp.id, clip.id, publish_targets)
                cta = ""
                if eval_row and eval_row.campaign_id:
                    camp = store.get_campaign(eval_row.campaign_id)
                    if camp and camp.brief:
                        cta = camp.brief.get("cta_text", "")
                desc = clip.hook or ""
                if cta and cta not in desc:
                    desc = f"{desc}\n\n{cta}".strip()

                meta = PublishingMetadata(
                    title=clip.title or "AL AMR Highlight",
                    description=desc,
                    tags=["ALAMR", "Shorts"],
                    destination="",
                    extra={"export_id": exp.id},
                )
                for target in publish_targets:
                    try:
                        record = await publishing_service.publish_export(
                            export_id=exp.id,
                            platform=target,
                            metadata=meta,
                            dry_run=False,
                        )
                        publishing_payload.append({
                            "id": record.id,
                            "export_id": record.export_id,
                            "clip_id": clip.id,
                            "platform": record.platform,
                            "status": record.status,
                            "external_id": record.external_id,
                            "destination": record.destination,
                            "metadata": record.metadata,
                            "error": record.error,
                            "created_at": record.created_at,
                            "updated_at": record.updated_at,
                        })
                    except Exception as exc:
                        log.error("Failed publishing export %s to %s: %s", exp.id, target, exc)

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

    # Collect Step 22 Final Renders
    final_renders_payload = []
    final_renders = store.list_final_renders(job.id)
    for fr in final_renders:
        final_renders_payload.append({
            "id": fr.id,
            "job_id": fr.job_id,
            "clip_id": fr.clip_id,
            "output_path": fr.output_path,
            "package_dir": fr.package_dir,
            "duration": fr.duration,
            "width": fr.width,
            "height": fr.height,
            "fps": fr.fps,
            "video_codec": fr.video_codec,
            "audio_codec": fr.audio_codec,
            "caption_style": fr.caption_style,
            "bgm_asset_id": fr.bgm_asset_id,
            "quality_score": fr.quality_score,
            "quality_status": fr.quality_status,
            "render_status": fr.render_status,
            "render_attempt": fr.render_attempt,
            "error_details": fr.error_details,
            "telemetry": fr.telemetry,
            "created_at": fr.created_at,
            "updated_at": fr.updated_at,
        })

    # Collect Step 23 SEO Clip Metadata
    clip_metadata_payload = []
    for c in clips:
        meta = store.get_clip_metadata(c.id)
        if meta:
            clip_metadata_payload.append(meta.to_dict())

    # 7. Trigger Telegram Review delivery from worker if configured (only for successfully rendered clips)
    rendered_clip_ids = {fr["clip_id"] for fr in final_renders_payload if fr.get("quality_status") == "RENDER_PASS"}
    try:
        from ..telegram.review_bot import is_telegram_configured, send_clip_review
        if is_telegram_configured(job_settings) and rendered_clip_ids:
            log.info("Telegram configured: worker delivering review cards for %d clips...", len(rendered_clip_ids))
            for c in clips:
                if c.id not in rendered_clip_ids:
                    continue
                try:
                    await send_clip_review(args.job_id, c.id, job_settings=job_settings)
                except Exception as exc:
                    log.warning("Worker Telegram review delivery failed for clip %s: %s", c.id, exc)
    except Exception as exc:
        log.warning("Could not initialize Telegram review delivery on worker: %s", exc)

    # Collect approvals so control plane knows review status
    approvals_payload = []
    for c in clips:
        app = store.get_clip_approval(c.id)
        if app:
            approvals_payload.append({
                "id": app.id,
                "clip_id": app.clip_id,
                "job_id": app.job_id,
                "current_status": app.current_status,
                "operator_action": app.operator_action,
                "operator_note": app.operator_note,
                "version": app.version,
                "previous_status": app.previous_status,
                "publish_eligible": app.publish_eligible,
                "blocking_reasons": app.blocking_reasons,
                "history": app.history,
                "telemetry": app.telemetry,
                "created_at": app.created_at,
                "updated_at": app.updated_at,
            })

    # 8. Final completion callback
    report(
        status="done",
        stage="completed",
        progress=1.0,
        clips=clips_payload,
        evaluations=evaluations_payload,
        exports=exports_payload,
        final_renders=final_renders_payload,
        clip_metadata=clip_metadata_payload,
        publishing_records=publishing_payload,
        approvals=approvals_payload,
    )
    log.info("AL AMR Worker completed job %s successfully with %d clips.", args.job_id, len(clips))


def main() -> None:
    args = parse_args()
    try:
        asyncio.run(async_main())
    except WorkerCancelledError:
        log.warning("Worker execution cleanly terminated due to job cancellation.")
        send_callback(
            args.callback_url,
            args.callback_token,
            status="cancelled",
            stage="cancelled",
            error="Job cancelled by operator",
        )
        sys.exit(0)
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

