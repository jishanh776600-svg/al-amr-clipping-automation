"""CRUD operations over the AutoClip database.

Every function opens its own transaction via :func:`autoclip.db.connection`,
so callers don't manage commits. Functions are synchronous; async callers should
wrap them in ``asyncio.to_thread``.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from . import connection
from .models import (
    AppCredentialRecord,
    CampaignGuideline,
    CampaignSpecificationRecord,
    Clip,
    ClipApprovalRecord,
    ClipCandidateRecord,
    ClipEdit,
    ClipSpecificationRecord,
    ClipStatus,
    BGMAssetRecord,
    BGMMixRecord,
    FinalRenderRecord,
    ClipMetadataRecord,
    CaptionOptimizationRecord,
    RetentionOptimizationRecord,
    VisualCompositionRecord,
    CampaignEvaluationRow,
    CampaignPreset,
    Export,
    Job,
    JobStatus,
    PublishingRecord,
    PublicationRecord,
    Source,
    Transcript,
    utcnow,
)

# --------------------------------------------------------------------------
# Sources
# --------------------------------------------------------------------------


def create_source(source: Source) -> Source:
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO sources (id, type, url, filename, path, title, channel,
                                 duration_s, width, height, fps, has_audio,
                                 has_video, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source.id,
                source.type,
                source.url,
                source.filename,
                source.path,
                source.title,
                source.channel,
                source.duration_s,
                source.width,
                source.height,
                source.fps,
                int(source.has_audio),
                int(source.has_video),
                source.created_at,
            ),
        )
    return source


def get_source(source_id: str) -> Source | None:
    with connection() as conn:
        row = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
    return Source.from_row(row) if row else None


def list_sources(limit: int = 50) -> list[Source]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM sources ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [Source.from_row(r) for r in rows]


def update_source(
    source_id: str,
    *,
    path: str | None = None,
    title: str | None = None,
    filename: str | None = None,
    channel: str | None = None,
    duration_s: float | None = None,
    width: int | None = None,
    height: int | None = None,
    fps: float | None = None,
    has_audio: bool | None = None,
    has_video: bool | None = None,
) -> Source | None:
    """Update fields on an existing source record."""
    assignments: list[str] = []
    params: list[Any] = []

    if path is not None:
        assignments.append("path = ?")
        params.append(path)
    if title is not None:
        assignments.append("title = ?")
        params.append(title)
    if filename is not None:
        assignments.append("filename = ?")
        params.append(filename)
    if channel is not None:
        assignments.append("channel = ?")
        params.append(channel)
    if duration_s is not None:
        assignments.append("duration_s = ?")
        params.append(duration_s)
    if width is not None:
        assignments.append("width = ?")
        params.append(width)
    if height is not None:
        assignments.append("height = ?")
        params.append(height)
    if fps is not None:
        assignments.append("fps = ?")
        params.append(fps)
    if has_audio is not None:
        assignments.append("has_audio = ?")
        params.append(int(has_audio))
    if has_video is not None:
        assignments.append("has_video = ?")
        params.append(int(has_video))

    if not assignments:
        return get_source(source_id)

    params.append(source_id)
    with connection() as conn:
        conn.execute(
            f"UPDATE sources SET {', '.join(assignments)} WHERE id = ?",
            tuple(params),
        )
    return get_source(source_id)


# --------------------------------------------------------------------------
# Jobs
# --------------------------------------------------------------------------


def create_job(job: Job) -> Job:
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO jobs (
                id, source_id, status, current_stage, progress, error,
                provider, settings_json, dispatch_mode, github_run_id,
                attempt, max_attempts, last_heartbeat_at, stale_at,
                github_workflow, github_job_id, github_run_url, github_run_status, github_conclusion,
                dispatched_at, started_at, completed_at, failed_at, cancelled_at, cancel_requested_at,
                campaign_spec_id, finished_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job.id,
                job.source_id,
                job.status,
                job.current_stage,
                job.progress,
                job.error,
                job.provider,
                json.dumps(job.settings),
                job.dispatch_mode,
                job.github_run_id,
                job.attempt,
                job.max_attempts,
                job.last_heartbeat_at,
                job.stale_at,
                job.github_workflow,
                job.github_job_id,
                job.github_run_url,
                job.github_run_status,
                job.github_conclusion,
                job.dispatched_at,
                job.started_at,
                job.completed_at,
                job.failed_at,
                job.cancelled_at,
                job.cancel_requested_at,
                getattr(job, "campaign_spec_id", None),
                job.finished_at,
                job.created_at,
                job.updated_at,
            ),
        )
    return job


def get_job(job_id: str) -> Job | None:
    with connection() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return Job.from_row(row) if row else None


def list_jobs(limit: int = 50, status: JobStatus | None = None) -> list[Job]:
    query = "SELECT * FROM jobs"
    params: list[Any] = []
    if status:
        query += " WHERE status = ?"
        params.append(status)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [Job.from_row(r) for r in rows]


class _Unset:
    """Sentinel distinguishing "leave this alone" from "set this to NULL".

    Without it, nullable columns can only ever be written, never cleared — a
    retried job would keep displaying the error message from its failed run.
    """

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "UNSET"


UNSET = _Unset()


def update_job(
    job_id: str,
    *,
    status: JobStatus | None = None,
    current_stage: str | None = None,
    progress: float | None = None,
    dispatch_mode: str | None = None,
    attempt: int | None = None,
    max_attempts: int | None = None,
    github_run_id: str | None | _Unset = UNSET,
    error: str | None | _Unset = UNSET,
    started_at: str | None | _Unset = UNSET,
    finished_at: str | None | _Unset = UNSET,
    last_heartbeat_at: str | None | _Unset = UNSET,
    stale_at: str | None | _Unset = UNSET,
    github_workflow: str | None | _Unset = UNSET,
    github_job_id: str | None | _Unset = UNSET,
    github_run_url: str | None | _Unset = UNSET,
    github_run_status: str | None | _Unset = UNSET,
    github_conclusion: str | None | _Unset = UNSET,
    dispatched_at: str | None | _Unset = UNSET,
    completed_at: str | None | _Unset = UNSET,
    failed_at: str | None | _Unset = UNSET,
    cancelled_at: str | None | _Unset = UNSET,
    cancel_requested_at: str | None | _Unset = UNSET,
    settings: dict[str, Any] | None = None,
) -> None:
    """Patch the supplied fields. Omitted fields are left untouched."""
    fields: dict[str, Any] = {"updated_at": utcnow()}
    if settings is not None:
        fields["settings_json"] = json.dumps(settings)
    for name, value in (
        ("status", status),
        ("current_stage", current_stage),
        ("progress", progress),
        ("dispatch_mode", dispatch_mode),
        ("attempt", attempt),
        ("max_attempts", max_attempts),
    ):
        if value is not None:
            fields[name] = value

    for name, value in (
        ("error", error),
        ("started_at", started_at),
        ("finished_at", finished_at),
        ("github_run_id", github_run_id),
        ("last_heartbeat_at", last_heartbeat_at),
        ("stale_at", stale_at),
        ("github_workflow", github_workflow),
        ("github_job_id", github_job_id),
        ("github_run_url", github_run_url),
        ("github_run_status", github_run_status),
        ("github_conclusion", github_conclusion),
        ("dispatched_at", dispatched_at),
        ("completed_at", completed_at),
        ("failed_at", failed_at),
        ("cancelled_at", cancelled_at),
        ("cancel_requested_at", cancel_requested_at),
    ):
        if not isinstance(value, _Unset):
            fields[name] = value

    assignments = ", ".join(f"{name} = ?" for name in fields)
    with connection() as conn:
        conn.execute(
            f"UPDATE jobs SET {assignments} WHERE id = ?",
            (*fields.values(), job_id),
        )


def list_stale_jobs(heartbeat_timeout_s: float = 300.0) -> list[Job]:
    """Find running/dispatching jobs that haven't sent a heartbeat within timeout."""
    active_statuses = ("running", "dispatching", "processing", "uploading", "publishing")
    placeholders = ", ".join("?" for _ in active_statuses)
    with connection() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM jobs
            WHERE status IN ({placeholders})
              AND (
                (last_heartbeat_at IS NOT NULL AND datetime(last_heartbeat_at) < datetime('now', ?))
                OR
                (last_heartbeat_at IS NULL AND started_at IS NOT NULL AND datetime(started_at) < datetime('now', ?))
                OR
                (last_heartbeat_at IS NULL AND started_at IS NULL AND datetime(created_at) < datetime('now', ?))
              )
            """,
            (*active_statuses, f"-{int(heartbeat_timeout_s)} seconds", f"-{int(heartbeat_timeout_s)} seconds", f"-{int(heartbeat_timeout_s)} seconds"),
        ).fetchall()
    return [Job.from_row(r) for r in rows]


def next_queued_job() -> Job | None:
    """Return the oldest queued local job — the FIFO the worker pulls from."""
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE status = 'queued' AND (dispatch_mode = 'local' OR dispatch_mode IS NULL) ORDER BY created_at ASC LIMIT 1"
        ).fetchone()
    return Job.from_row(row) if row else None


# --------------------------------------------------------------------------
# Transcripts
# --------------------------------------------------------------------------


def upsert_transcript(transcript: Transcript) -> Transcript:
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO transcripts (job_id, json_path, language, model,
                                     has_diarization, word_count, source, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                json_path = excluded.json_path,
                language = excluded.language,
                model = excluded.model,
                has_diarization = excluded.has_diarization,
                word_count = excluded.word_count,
                source = excluded.source
            """,
            (
                transcript.job_id,
                transcript.json_path,
                transcript.language,
                transcript.model,
                int(transcript.has_diarization),
                transcript.word_count,
                transcript.source,
                transcript.created_at,
            ),
        )
    return transcript


def get_transcript(job_id: str) -> Transcript | None:
    with connection() as conn:
        row = conn.execute("SELECT * FROM transcripts WHERE job_id = ?", (job_id,)).fetchone()
    return Transcript.from_row(row) if row else None


# --------------------------------------------------------------------------
# Clips
# --------------------------------------------------------------------------


def create_clip(clip: Clip) -> Clip:
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO clips (id, job_id, rank, start_s, end_s, start_word, end_word,
                               title, hook, score, reason, status, user_trimmed, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                clip.id,
                clip.job_id,
                clip.rank,
                clip.start_s,
                clip.end_s,
                clip.start_word,
                clip.end_word,
                clip.title,
                clip.hook,
                clip.score,
                clip.reason,
                clip.status,
                int(clip.user_trimmed),
                clip.created_at,
            ),
        )
    return clip


def replace_clips(job_id: str, clips: list[Clip]) -> list[Clip]:
    """Swap in a fresh set of clips for a job.

    Used when highlight detection reruns — a retry should not leave the previous
    run's candidates behind.
    """
    with connection() as conn:
        conn.execute("DELETE FROM clips WHERE job_id = ?", (job_id,))
        conn.executemany(
            """
            INSERT INTO clips (id, job_id, rank, start_s, end_s, start_word, end_word,
                               title, hook, score, reason, status, user_trimmed, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    c.id,
                    c.job_id,
                    c.rank,
                    c.start_s,
                    c.end_s,
                    c.start_word,
                    c.end_word,
                    c.title,
                    c.hook,
                    c.score,
                    c.reason,
                    c.status,
                    int(c.user_trimmed),
                    c.created_at,
                )
                for c in clips
            ],
        )
    return clips


def get_clip(clip_id: str) -> Clip | None:
    with connection() as conn:
        row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
    return Clip.from_row(row) if row else None


def list_clips(job_id: str) -> list[Clip]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM clips WHERE job_id = ? ORDER BY rank ASC", (job_id,)
        ).fetchall()
    return [Clip.from_row(r) for r in rows]


list_clips_for_job = list_clips


def update_clip(
    clip_id: str,
    *,
    start_s: float | None = None,
    end_s: float | None = None,
    start_word: int | None = None,
    end_word: int | None = None,
    title: str | None = None,
    status: ClipStatus | None = None,
    user_trimmed: bool | None = None,
) -> None:
    fields: dict[str, Any] = {}
    for name, value in (
        ("start_s", start_s),
        ("end_s", end_s),
        ("start_word", start_word),
        ("end_word", end_word),
        ("title", title),
        ("status", status),
    ):
        if value is not None:
            fields[name] = value
    if user_trimmed is not None:
        fields["user_trimmed"] = int(user_trimmed)

    if not fields:
        return

    assignments = ", ".join(f"{name} = ?" for name in fields)
    with connection() as conn:
        conn.execute(
            f"UPDATE clips SET {assignments} WHERE id = ?",
            (*fields.values(), clip_id),
        )


# --------------------------------------------------------------------------
# Clip edits
# --------------------------------------------------------------------------


def upsert_clip_edit(edit: ClipEdit) -> ClipEdit:
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO clip_edits (clip_id, edited_words_json, caption_style, ratio, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(clip_id) DO UPDATE SET
                edited_words_json = excluded.edited_words_json,
                caption_style = excluded.caption_style,
                ratio = excluded.ratio,
                updated_at = excluded.updated_at
            """,
            (
                edit.clip_id,
                json.dumps(edit.edited_words) if edit.edited_words is not None else None,
                edit.caption_style,
                edit.ratio,
                utcnow(),
            ),
        )
    return edit


def get_clip_edit(clip_id: str) -> ClipEdit | None:
    with connection() as conn:
        row = conn.execute("SELECT * FROM clip_edits WHERE clip_id = ?", (clip_id,)).fetchone()
    return ClipEdit.from_row(row) if row else None


# --------------------------------------------------------------------------
# Exports
# --------------------------------------------------------------------------


def create_export(export: Export) -> Export:
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO exports (id, clip_id, path, ratio, style, size_bytes, created_at,
                                 drive_file_id, drive_web_view_link, drive_storage_key)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                export.id,
                export.clip_id,
                export.path,
                export.ratio,
                export.style,
                export.size_bytes,
                export.created_at,
                export.drive_file_id,
                export.drive_web_view_link,
                export.drive_storage_key,
            ),
        )
    return export


def update_export_drive_info(
    export_id: str,
    *,
    drive_file_id: str | None = None,
    drive_web_view_link: str | None = None,
    drive_storage_key: str | None = None,
) -> None:
    with connection() as conn:
        conn.execute(
            """
            UPDATE exports
            SET drive_file_id = ?, drive_web_view_link = ?, drive_storage_key = ?
            WHERE id = ?
            """,
            (drive_file_id, drive_web_view_link, drive_storage_key, export_id),
        )


def get_export(export_id: str) -> Export | None:
    with connection() as conn:
        row = conn.execute("SELECT * FROM exports WHERE id = ?", (export_id,)).fetchone()
    return Export.from_row(row) if row else None


def list_exports(clip_id: str) -> list[Export]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM exports WHERE clip_id = ? ORDER BY created_at DESC", (clip_id,)
        ).fetchall()
    return [Export.from_row(r) for r in rows]


def list_exports_for_job(job_id: str) -> list[Export]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT e.* FROM exports e
            JOIN clips c ON e.clip_id = c.id
            WHERE c.job_id = ?
            ORDER BY e.created_at DESC
            """,
            (job_id,),
        ).fetchall()
    return [Export.from_row(r) for r in rows]


def delete_export(export_id: str) -> None:
    with connection() as conn:
        conn.execute("DELETE FROM exports WHERE id = ?", (export_id,))


# --------------------------------------------------------------------------
# Campaign Evaluations
# --------------------------------------------------------------------------


def create_campaign_evaluation(evaluation: CampaignEvaluationRow) -> CampaignEvaluationRow:
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO campaign_evaluations (
                clip_id, campaign_id, approved, final_score, hook_score,
                cta_score, viral_score, density_score, hard_failures,
                soft_warnings, rule_results, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(clip_id) DO UPDATE SET
                campaign_id = excluded.campaign_id,
                approved = excluded.approved,
                final_score = excluded.final_score,
                hook_score = excluded.hook_score,
                cta_score = excluded.cta_score,
                viral_score = excluded.viral_score,
                density_score = excluded.density_score,
                hard_failures = excluded.hard_failures,
                soft_warnings = excluded.soft_warnings,
                rule_results = excluded.rule_results,
                created_at = excluded.created_at
            """,
            (
                evaluation.clip_id,
                evaluation.campaign_id,
                int(evaluation.approved),
                evaluation.final_score,
                evaluation.hook_score,
                evaluation.cta_score,
                evaluation.viral_score,
                evaluation.density_score,
                json.dumps(evaluation.hard_failures),
                json.dumps(evaluation.soft_warnings),
                json.dumps(evaluation.rule_results),
                evaluation.created_at,
            ),
        )
    return evaluation


def get_campaign_evaluation(clip_id: str) -> CampaignEvaluationRow | None:
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM campaign_evaluations WHERE clip_id = ?", (clip_id,)
        ).fetchone()
    return CampaignEvaluationRow.from_row(row) if row else None


def list_campaign_evaluations_for_job(job_id: str) -> list[CampaignEvaluationRow]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT e.* FROM campaign_evaluations e
            JOIN clips c ON c.id = e.clip_id
            WHERE c.job_id = ?
            ORDER BY e.final_score DESC
            """,
            (job_id,),
        ).fetchall()
    return [CampaignEvaluationRow.from_row(r) for r in rows]


def list_recent_clips(limit: int = 50, status: str | None = None) -> list[Clip]:
    with connection() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM clips WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM clips ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
    return [Clip.from_row(r) for r in rows]


# --------------------------------------------------------------------------
# Campaigns
# --------------------------------------------------------------------------


def create_or_update_campaign(campaign: CampaignPreset) -> CampaignPreset:
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO campaigns (id, name, brief_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                brief_json = excluded.brief_json,
                updated_at = excluded.updated_at
            """,
            (
                campaign.id,
                campaign.name,
                json.dumps(campaign.brief),
                campaign.created_at,
                campaign.updated_at,
            ),
        )
    return campaign


def get_campaign(campaign_id: str) -> CampaignPreset | None:
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM campaigns WHERE id = ?", (campaign_id,)
        ).fetchone()
    return CampaignPreset.from_row(row) if row else None


def list_campaigns() -> list[CampaignPreset]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM campaigns ORDER BY updated_at DESC"
        ).fetchall()
    return [CampaignPreset.from_row(r) for r in rows]


def delete_campaign(campaign_id: str) -> bool:
    with connection() as conn:
        cursor = conn.execute("DELETE FROM campaigns WHERE id = ?", (campaign_id,))
    return cursor.rowcount > 0


# --------------------------------------------------------------------------
# Publishing Records
# --------------------------------------------------------------------------


def create_or_update_publishing_record(record: PublishingRecord) -> PublishingRecord:
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO publishing_records (
                id, export_id, job_id, platform, status, external_id,
                destination, metadata_json, error, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(export_id, platform, destination) DO UPDATE SET
                status = excluded.status,
                external_id = COALESCE(excluded.external_id, publishing_records.external_id),
                metadata_json = excluded.metadata_json,
                error = excluded.error,
                updated_at = excluded.updated_at
            """,
            (
                record.id,
                record.export_id,
                record.job_id,
                record.platform,
                record.status,
                record.external_id,
                record.destination or "",
                json.dumps(record.metadata),
                record.error,
                record.created_at,
                record.updated_at,
            ),
        )
    return get_publishing_record_by_target(record.export_id, record.platform, record.destination) or record


def get_publishing_record(record_id: str) -> PublishingRecord | None:
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM publishing_records WHERE id = ?", (record_id,)
        ).fetchone()
    return PublishingRecord.from_row(row) if row else None


def get_publishing_record_by_target(
    export_id: str, platform: str, destination: str | None = None
) -> PublishingRecord | None:
    dest = destination or ""
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM publishing_records WHERE export_id = ? AND platform = ? AND destination = ?",
            (export_id, platform, dest),
        ).fetchone()
    return PublishingRecord.from_row(row) if row else None


def update_publishing_record(
    record_id: str,
    *,
    status: str | None = None,
    external_id: str | None = None,
    error: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> PublishingRecord | None:
    clauses: list[str] = ["updated_at = ?"]
    params: list[Any] = [utcnow()]

    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    if external_id is not None:
        clauses.append("external_id = ?")
        params.append(external_id)
    if error is not None:
        clauses.append("error = ?")
        params.append(error)
    if metadata is not None:
        clauses.append("metadata_json = ?")
        params.append(json.dumps(metadata))

    params.append(record_id)
    sql = f"UPDATE publishing_records SET {', '.join(clauses)} WHERE id = ?"

    with connection() as conn:
        conn.execute(sql, params)
    return get_publishing_record(record_id)


def list_publishing_records(
    *,
    job_id: str | None = None,
    export_id: str | None = None,
    platform: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[PublishingRecord]:
    clauses: list[str] = []
    params: list[Any] = []

    if job_id:
        clauses.append("job_id = ?")
        params.append(job_id)
    if export_id:
        clauses.append("export_id = ?")
        params.append(export_id)
    if platform:
        clauses.append("platform = ?")
        params.append(platform)
    if status:
        clauses.append("status = ?")
        params.append(status)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"SELECT * FROM publishing_records {where} ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)

    with connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [PublishingRecord.from_row(r) for r in rows]


# --------------------------------------------------------------------------
# Campaign Guidelines
# --------------------------------------------------------------------------


def create_guideline(guideline: CampaignGuideline) -> CampaignGuideline:
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO campaign_guidelines (
                id, job_id, filename, mime_type, size_bytes, storage_path,
                extracted_text, parsed_brief, status, error, created_at,
                source_type, drive_file_id, sha256, word_count, char_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guideline.id,
                guideline.job_id,
                guideline.filename,
                guideline.mime_type,
                guideline.size_bytes,
                guideline.storage_path,
                guideline.extracted_text,
                json.dumps(guideline.parsed_brief),
                guideline.status,
                guideline.error,
                guideline.created_at,
                guideline.source_type,
                guideline.drive_file_id,
                guideline.sha256,
                guideline.word_count,
                guideline.char_count,
            ),
        )
    return guideline


def get_guideline(guideline_id: str) -> CampaignGuideline | None:
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM campaign_guidelines WHERE id = ?", (guideline_id,)
        ).fetchone()
    return CampaignGuideline.from_row(row) if row else None


def get_guideline_for_job(job_id: str) -> CampaignGuideline | None:
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM campaign_guidelines WHERE job_id = ? ORDER BY created_at DESC LIMIT 1",
            (job_id,),
        ).fetchone()
    return CampaignGuideline.from_row(row) if row else None


def update_guideline(guideline_id: str, **updates: Any) -> CampaignGuideline | None:
    if not updates:
        return get_guideline(guideline_id)

    clauses: list[str] = []
    params: list[Any] = []
    for key, value in updates.items():
        if key == "parsed_brief" and isinstance(value, dict):
            value = json.dumps(value)
        clauses.append(f"{key} = ?")
        params.append(value)
    params.append(guideline_id)

    sql = f"UPDATE campaign_guidelines SET {', '.join(clauses)} WHERE id = ?"
    with connection() as conn:
        conn.execute(sql, params)
    return get_guideline(guideline_id)


def list_guidelines_for_job(job_id: str) -> list[CampaignGuideline]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM campaign_guidelines WHERE job_id = ? ORDER BY created_at ASC",
            (job_id,),
        ).fetchall()
    return [CampaignGuideline.from_row(row) for row in rows]


# --------------------------------------------------------------------------
# Campaign Specifications (Multi-Document Normalized Intelligence)
# --------------------------------------------------------------------------


def create_campaign_spec(record: CampaignSpecificationRecord) -> CampaignSpecificationRecord:
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO campaign_specifications (
                id, job_id, title, spec_json, has_conflicts, conflict_count,
                document_count, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.id,
                record.job_id,
                record.title,
                json.dumps(record.spec),
                1 if record.has_conflicts else 0,
                record.conflict_count,
                record.document_count,
                record.created_at,
                record.updated_at,
            ),
        )
    return record


def get_campaign_spec(spec_id: str) -> CampaignSpecificationRecord | None:
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM campaign_specifications WHERE id = ?", (spec_id,)
        ).fetchone()
    return CampaignSpecificationRecord.from_row(row) if row else None


def get_campaign_spec_for_job(job_id: str) -> CampaignSpecificationRecord | None:
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM campaign_specifications WHERE job_id = ? ORDER BY created_at DESC LIMIT 1",
            (job_id,),
        ).fetchone()
    return CampaignSpecificationRecord.from_row(row) if row else None


def update_campaign_spec(spec_id: str, **updates: Any) -> CampaignSpecificationRecord | None:
    if not updates:
        return get_campaign_spec(spec_id)

    clauses: list[str] = []
    params: list[Any] = []
    for key, value in updates.items():
        if key == "spec" and isinstance(value, dict):
            key = "spec_json"
            value = json.dumps(value)
        elif key == "has_conflicts":
            value = 1 if value else 0
        clauses.append(f"{key} = ?")
        params.append(value)
    params.append(spec_id)

    sql = f"UPDATE campaign_specifications SET {', '.join(clauses)} WHERE id = ?"
    with connection() as conn:
        conn.execute(sql, params)
    return get_campaign_spec(spec_id)


# --------------------------------------------------------------------------
# Clip Candidates (Step 15)
# --------------------------------------------------------------------------


def replace_clip_candidates(
    job_id: str, candidates: list[ClipCandidateRecord]
) -> list[ClipCandidateRecord]:
    """Atomically swap in a fresh set of candidate discovery records for a job."""
    with connection() as conn:
        conn.execute("DELETE FROM clip_candidates WHERE job_id = ?", (job_id,))
        conn.executemany(
            """
            INSERT INTO clip_candidates (
                id, job_id, rank, selected, status, start_s, end_s, duration_s,
                start_word, end_word, title, hook_text, reason, transcript_slice,
                score, score_breakdown, hook_signals, climax_signals, cta_signals,
                requirement_matches, rejection_reasons, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    c.id,
                    c.job_id,
                    c.rank,
                    1 if c.selected else 0,
                    c.status,
                    c.start_s,
                    c.end_s,
                    c.duration_s,
                    c.start_word,
                    c.end_word,
                    c.title,
                    c.hook_text,
                    c.reason,
                    c.transcript_slice,
                    c.score,
                    json.dumps(c.score_breakdown),
                    json.dumps(c.hook_signals),
                    json.dumps(c.climax_signals),
                    json.dumps(c.cta_signals),
                    json.dumps(c.requirement_matches),
                    json.dumps(c.rejection_reasons),
                    c.created_at,
                    c.updated_at,
                )
                for c in candidates
            ],
        )
    return candidates


def list_clip_candidates(
    job_id: str, selected_only: bool = False
) -> list[ClipCandidateRecord]:
    with connection() as conn:
        if selected_only:
            rows = conn.execute(
                "SELECT * FROM clip_candidates WHERE job_id = ? AND selected = 1 ORDER BY rank ASC, score DESC",
                (job_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM clip_candidates WHERE job_id = ? ORDER BY rank ASC, score DESC",
                (job_id,),
            ).fetchall()
    return [ClipCandidateRecord.from_row(r) for r in rows]


def get_clip_candidate(candidate_id: str) -> ClipCandidateRecord | None:
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM clip_candidates WHERE id = ?", (candidate_id,)
        ).fetchone()
    return ClipCandidateRecord.from_row(row) if row else None


# --------------------------------------------------------------------------
# Clip Specifications (Step 16)
# --------------------------------------------------------------------------


def replace_clip_specifications(
    job_id: str, specs: list[ClipSpecificationRecord]
) -> list[ClipSpecificationRecord]:
    """Atomically swap in a fresh set of clip specifications for a job."""
    with connection() as conn:
        conn.execute("DELETE FROM clip_specifications WHERE job_id = ?", (job_id,))
        conn.executemany(
            """
            INSERT INTO clip_specifications (
                id, job_id, candidate_id, source_id, start_time, end_time,
                duration, start_word, end_word, hook_start, hook_end, hook_type,
                climax_start, climax_end, cta_start, cta_end,
                boundary_adjustments, requirement_matches, quality_score,
                quality_status, rejection_reasons, warnings, final_rank,
                version, telemetry, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    s.id,
                    s.job_id,
                    s.candidate_id,
                    s.source_id,
                    s.start_time,
                    s.end_time,
                    s.duration,
                    s.start_word,
                    s.end_word,
                    s.hook_start,
                    s.hook_end,
                    s.hook_type,
                    s.climax_start,
                    s.climax_end,
                    s.cta_start,
                    s.cta_end,
                    json.dumps(s.boundary_adjustments),
                    json.dumps(s.requirement_matches),
                    s.quality_score,
                    s.quality_status,
                    json.dumps(s.rejection_reasons),
                    json.dumps(s.warnings),
                    s.final_rank,
                    s.version,
                    json.dumps(s.telemetry),
                    s.created_at,
                    s.updated_at,
                )
                for s in specs
            ],
        )
    return specs


def list_clip_specifications(
    job_id: str, approved_only: bool = False, status: str | None = None
) -> list[ClipSpecificationRecord]:
    with connection() as conn:
        if approved_only:
            rows = conn.execute(
                """
                SELECT * FROM clip_specifications
                WHERE job_id = ? AND quality_status IN ('QUALITY_PASS', 'QUALITY_WARN')
                ORDER BY final_rank ASC, quality_score DESC
                """,
                (job_id,),
            ).fetchall()
        elif status:
            rows = conn.execute(
                """
                SELECT * FROM clip_specifications
                WHERE job_id = ? AND quality_status = ?
                ORDER BY final_rank ASC, quality_score DESC
                """,
                (job_id, status),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM clip_specifications
                WHERE job_id = ?
                ORDER BY final_rank ASC, quality_score DESC
                """,
                (job_id,),
            ).fetchall()
    return [ClipSpecificationRecord.from_row(r) for r in rows]


def get_clip_specification(spec_id: str) -> ClipSpecificationRecord | None:
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM clip_specifications WHERE id = ?", (spec_id,)
        ).fetchone()
    return ClipSpecificationRecord.from_row(row) if row else None


# --------------------------------------------------------------------------
# Visual Compositions (Step 17)
# --------------------------------------------------------------------------


def replace_visual_compositions(
    job_id: str, records: list[VisualCompositionRecord]
) -> list[VisualCompositionRecord]:
    """Atomically swap in visual composition records for a job."""
    with connection() as conn:
        conn.execute("DELETE FROM visual_compositions WHERE job_id = ?", (job_id,))
        conn.executemany(
            """
            INSERT INTO visual_compositions (
                id, clip_id, job_id, source_width, source_height,
                output_width, output_height, crop_strategy, tracking_strategy,
                tracking_confidence, camera_movement_score, smoothing_parameters,
                fallback_used, fallback_reason, quality_score, quality_status,
                warnings, rejection_reasons, version, telemetry,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    r.id,
                    r.clip_id,
                    r.job_id,
                    r.source_width,
                    r.source_height,
                    r.output_width,
                    r.output_height,
                    r.crop_strategy,
                    r.tracking_strategy,
                    r.tracking_confidence,
                    r.camera_movement_score,
                    json.dumps(r.smoothing_parameters),
                    1 if r.fallback_used else 0,
                    r.fallback_reason,
                    r.quality_score,
                    r.quality_status,
                    json.dumps(r.warnings),
                    json.dumps(r.rejection_reasons),
                    r.version,
                    json.dumps(r.telemetry),
                    r.created_at,
                    r.updated_at,
                )
                for r in records
            ],
        )
    return records


def list_visual_compositions(
    job_id: str, approved_only: bool = False, status: str | None = None
) -> list[VisualCompositionRecord]:
    with connection() as conn:
        if approved_only:
            rows = conn.execute(
                """
                SELECT * FROM visual_compositions
                WHERE job_id = ? AND quality_status IN ('VISUAL_PASS', 'VISUAL_WARN')
                ORDER BY created_at ASC
                """,
                (job_id,),
            ).fetchall()
        elif status:
            rows = conn.execute(
                """
                SELECT * FROM visual_compositions
                WHERE job_id = ? AND quality_status = ?
                ORDER BY created_at ASC
                """,
                (job_id, status),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM visual_compositions
                WHERE job_id = ?
                ORDER BY created_at ASC
                """,
                (job_id,),
            ).fetchall()
    return [VisualCompositionRecord.from_row(r) for r in rows]


def get_visual_composition(clip_id: str) -> VisualCompositionRecord | None:
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM visual_compositions WHERE clip_id = ?", (clip_id,)
        ).fetchone()
    return VisualCompositionRecord.from_row(row) if row else None


# --------------------------------------------------------------------------
# Retention Optimizations (Step 18)
# --------------------------------------------------------------------------


def replace_retention_optimizations(
    job_id: str, records: list[RetentionOptimizationRecord]
) -> list[RetentionOptimizationRecord]:
    """Atomically swap in retention optimization records for a job."""
    with connection() as conn:
        conn.execute("DELETE FROM retention_optimizations WHERE job_id = ?", (job_id,))
        conn.executemany(
            """
            INSERT INTO retention_optimizations (
                id, clip_id, job_id, retention_score, final_score, quality_status,
                hook_strength, speech_density_wps, dead_air_percentage, pacing_score,
                narrative_score, editing_decisions, visual_emphasis, scoring_breakdown,
                rejection_reasons, warnings, processing_time_s, version, telemetry,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    r.id,
                    r.clip_id,
                    r.job_id,
                    r.retention_score,
                    r.final_score,
                    r.quality_status,
                    r.hook_strength,
                    r.speech_density_wps,
                    r.dead_air_percentage,
                    r.pacing_score,
                    r.narrative_score,
                    json.dumps(r.editing_decisions),
                    json.dumps(r.visual_emphasis),
                    json.dumps(r.scoring_breakdown),
                    json.dumps(r.rejection_reasons),
                    json.dumps(r.warnings),
                    r.processing_time_s,
                    r.version,
                    json.dumps(r.telemetry),
                    r.created_at,
                    r.updated_at,
                )
                for r in records
            ],
        )
    return records


def list_retention_optimizations(
    job_id: str, approved_only: bool = False, status: str | None = None
) -> list[RetentionOptimizationRecord]:
    with connection() as conn:
        if approved_only:
            rows = conn.execute(
                """
                SELECT * FROM retention_optimizations
                WHERE job_id = ? AND quality_status IN ('FINAL_PASS', 'FINAL_WARN')
                ORDER BY final_score DESC
                """,
                (job_id,),
            ).fetchall()
        elif status:
            rows = conn.execute(
                """
                SELECT * FROM retention_optimizations
                WHERE job_id = ? AND quality_status = ?
                ORDER BY final_score DESC
                """,
                (job_id, status),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM retention_optimizations
                WHERE job_id = ?
                ORDER BY final_score DESC
                """,
                (job_id,),
            ).fetchall()
    return [RetentionOptimizationRecord.from_row(r) for r in rows]


def get_retention_optimization(clip_id: str) -> RetentionOptimizationRecord | None:
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM retention_optimizations WHERE clip_id = ?", (clip_id,)
        ).fetchone()
    return RetentionOptimizationRecord.from_row(row) if row else None


# --------------------------------------------------------------------------
# Caption Optimizations (Step 19)
# --------------------------------------------------------------------------


def replace_caption_optimizations(
    job_id: str, records: list[CaptionOptimizationRecord]
) -> list[CaptionOptimizationRecord]:
    with connection() as conn:
        conn.execute(
            "DELETE FROM caption_optimizations WHERE job_id = ?",
            (job_id,),
        )
        conn.executemany(
            """
            INSERT INTO caption_optimizations (
                id, clip_id, job_id, style_key, style_label, caption_segments,
                emphasis_metadata, hook_treatment, climax_treatment, cta_treatment,
                quality_score, quality_status, rejection_reasons, warnings,
                fallback_used, fallback_reason, render_time_s, version,
                telemetry, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    r.id,
                    r.clip_id,
                    r.job_id,
                    r.style_key,
                    r.style_label,
                    json.dumps(r.caption_segments),
                    json.dumps(r.emphasis_metadata),
                    json.dumps(r.hook_treatment),
                    json.dumps(r.climax_treatment),
                    json.dumps(r.cta_treatment),
                    r.quality_score,
                    r.quality_status,
                    json.dumps(r.rejection_reasons),
                    json.dumps(r.warnings),
                    1 if r.fallback_used else 0,
                    r.fallback_reason,
                    r.render_time_s,
                    r.version,
                    json.dumps(r.telemetry),
                    r.created_at,
                    r.updated_at,
                )
                for r in records
            ],
        )
    return records


def list_caption_optimizations(
    job_id: str, approved_only: bool = False, status: str | None = None
) -> list[CaptionOptimizationRecord]:
    with connection() as conn:
        if approved_only:
            rows = conn.execute(
                """
                SELECT * FROM caption_optimizations
                WHERE job_id = ? AND quality_status IN ('CAPTION_PASS', 'CAPTION_WARN')
                ORDER BY created_at ASC
                """,
                (job_id,),
            ).fetchall()
        elif status:
            rows = conn.execute(
                """
                SELECT * FROM caption_optimizations
                WHERE job_id = ? AND quality_status = ?
                ORDER BY created_at ASC
                """,
                (job_id, status),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM caption_optimizations
                WHERE job_id = ?
                ORDER BY created_at ASC
                """,
                (job_id,),
            ).fetchall()
    return [CaptionOptimizationRecord.from_row(r) for r in rows]


def get_caption_optimization(clip_id: str) -> CaptionOptimizationRecord | None:
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM caption_optimizations WHERE clip_id = ?", (clip_id,)
        ).fetchone()
    return CaptionOptimizationRecord.from_row(row) if row else None


# --------------------------------------------------------------------------
# BGM Assets (Step 20 BGM Vault)
# --------------------------------------------------------------------------


def create_bgm_asset(asset: BGMAssetRecord) -> BGMAssetRecord:
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO bgm_assets (
                id, name, file_path, genre, mood, tags, mime_type,
                duration_s, file_size_bytes, enabled, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                asset.id,
                asset.name,
                asset.file_path,
                asset.genre,
                asset.mood,
                json.dumps(asset.tags),
                asset.mime_type,
                asset.duration_s,
                asset.file_size_bytes,
                1 if asset.enabled else 0,
                asset.created_at,
                asset.updated_at,
            ),
        )
    return asset


def get_bgm_asset(asset_id: str) -> BGMAssetRecord | None:
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM bgm_assets WHERE id = ?", (asset_id,)
        ).fetchone()
    return BGMAssetRecord.from_row(row) if row else None


def list_bgm_assets(enabled_only: bool = False, genre: str | None = None) -> list[BGMAssetRecord]:
    with connection() as conn:
        query = "SELECT * FROM bgm_assets WHERE 1=1"
        params: list[Any] = []
        if enabled_only:
            query += " AND enabled = 1"
        if genre:
            query += " AND LOWER(genre) = LOWER(?)"
            params.append(genre.strip())
        query += " ORDER BY created_at DESC"
        rows = conn.execute(query, params).fetchall()
    return [BGMAssetRecord.from_row(r) for r in rows]


def update_bgm_asset(
    asset_id: str,
    name: str | None = None,
    genre: str | None = None,
    mood: str | None = None,
    tags: list[str] | None = None,
    enabled: bool | None = None,
) -> BGMAssetRecord | None:
    updates: list[str] = []
    params: list[Any] = []
    now = utcnow()

    if name is not None:
        updates.append("name = ?")
        params.append(name.strip())
    if genre is not None:
        updates.append("genre = ?")
        params.append(genre.strip())
    if mood is not None:
        updates.append("mood = ?")
        params.append(mood.strip())
    if tags is not None:
        updates.append("tags = ?")
        params.append(json.dumps(tags))
    if enabled is not None:
        updates.append("enabled = ?")
        params.append(1 if enabled else 0)

    if not updates:
        return get_bgm_asset(asset_id)

    updates.append("updated_at = ?")
    params.append(now)
    params.append(asset_id)

    with connection() as conn:
        cursor = conn.execute(
            f"UPDATE bgm_assets SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        if cursor.rowcount == 0:
            return None
    return get_bgm_asset(asset_id)


def delete_bgm_asset(asset_id: str) -> bool:
    with connection() as conn:
        cursor = conn.execute("DELETE FROM bgm_assets WHERE id = ?", (asset_id,))
        return cursor.rowcount > 0


# --------------------------------------------------------------------------
# BGM Mixes (Step 21 Audio Mixing)
# --------------------------------------------------------------------------


def replace_bgm_mixes(
    job_id: str, records: list[BGMMixRecord]
) -> list[BGMMixRecord]:
    with connection() as conn:
        conn.execute(
            "DELETE FROM bgm_mixes WHERE job_id = ?",
            (job_id,),
        )
        conn.executemany(
            """
            INSERT INTO bgm_mixes (
                id, clip_id, job_id, bgm_asset_id, bgm_asset_name, bgm_applied,
                clip_duration_s, bgm_duration_s, loop_trim_decision,
                ducking_applied, ducking_parameters, normalization_applied,
                integrated_lufs, true_peak_db, quality_score, quality_status,
                warnings, rejection_reasons, processing_time_s,
                mixed_audio_path, telemetry, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    r.id,
                    r.clip_id,
                    r.job_id,
                    r.bgm_asset_id,
                    r.bgm_asset_name,
                    1 if r.bgm_applied else 0,
                    r.clip_duration_s,
                    r.bgm_duration_s,
                    r.loop_trim_decision,
                    1 if r.ducking_applied else 0,
                    json.dumps(r.ducking_parameters),
                    1 if r.normalization_applied else 0,
                    r.integrated_lufs,
                    r.true_peak_db,
                    r.quality_score,
                    r.quality_status,
                    json.dumps(r.warnings),
                    json.dumps(r.rejection_reasons),
                    r.processing_time_s,
                    r.mixed_audio_path,
                    json.dumps(r.telemetry),
                    r.created_at,
                    r.updated_at,
                )
                for r in records
            ],
        )
    return records


def list_bgm_mixes(
    job_id: str, approved_only: bool = False, status: str | None = None
) -> list[BGMMixRecord]:
    with connection() as conn:
        if approved_only:
            rows = conn.execute(
                """
                SELECT * FROM bgm_mixes
                WHERE job_id = ? AND quality_status IN ('MIX_PASS', 'MIX_WARN')
                ORDER BY created_at ASC
                """,
                (job_id,),
            ).fetchall()
        elif status:
            rows = conn.execute(
                """
                SELECT * FROM bgm_mixes
                WHERE job_id = ? AND quality_status = ?
                ORDER BY created_at ASC
                """,
                (job_id, status),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM bgm_mixes
                WHERE job_id = ?
                ORDER BY created_at ASC
                """,
                (job_id,),
            ).fetchall()
    return [BGMMixRecord.from_row(r) for r in rows]


def get_bgm_mix(clip_id: str) -> BGMMixRecord | None:
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM bgm_mixes WHERE clip_id = ?", (clip_id,)
        ).fetchone()
    return BGMMixRecord.from_row(row) if row else None


# --------------------------------------------------------------------------
# Final Renders (Step 22 Final Render & Quality Gate)
# --------------------------------------------------------------------------


def replace_final_renders(
    job_id: str, records: list[FinalRenderRecord]
) -> list[FinalRenderRecord]:
    with connection() as conn:
        conn.execute(
            "DELETE FROM final_renders WHERE job_id = ?",
            (job_id,),
        )
        conn.executemany(
            """
            INSERT INTO final_renders (
                id, job_id, clip_id, output_path, package_dir, duration,
                width, height, fps, video_codec, audio_codec, caption_style,
                bgm_asset_id, quality_score, quality_status, render_status,
                render_attempt, error_details, telemetry, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    r.id,
                    r.job_id,
                    r.clip_id,
                    r.output_path,
                    r.package_dir,
                    r.duration,
                    r.width,
                    r.height,
                    r.fps,
                    r.video_codec,
                    r.audio_codec,
                    r.caption_style,
                    r.bgm_asset_id,
                    r.quality_score,
                    r.quality_status,
                    r.render_status,
                    r.render_attempt,
                    json.dumps(r.error_details),
                    json.dumps(r.telemetry),
                    r.created_at,
                    r.updated_at,
                )
                for r in records
            ],
        )
    return records


def create_final_render(record: FinalRenderRecord) -> FinalRenderRecord:
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO final_renders (
                id, job_id, clip_id, output_path, package_dir, duration,
                width, height, fps, video_codec, audio_codec, caption_style,
                bgm_asset_id, quality_score, quality_status, render_status,
                render_attempt, error_details, telemetry, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                job_id = excluded.job_id,
                clip_id = excluded.clip_id,
                output_path = excluded.output_path,
                package_dir = excluded.package_dir,
                duration = excluded.duration,
                width = excluded.width,
                height = excluded.height,
                fps = excluded.fps,
                video_codec = excluded.video_codec,
                audio_codec = excluded.audio_codec,
                caption_style = excluded.caption_style,
                bgm_asset_id = excluded.bgm_asset_id,
                quality_score = excluded.quality_score,
                quality_status = excluded.quality_status,
                render_status = excluded.render_status,
                render_attempt = excluded.render_attempt,
                error_details = excluded.error_details,
                telemetry = excluded.telemetry,
                updated_at = excluded.updated_at
            """,
            (
                record.id,
                record.job_id,
                record.clip_id,
                record.output_path,
                record.package_dir,
                record.duration,
                record.width,
                record.height,
                record.fps,
                record.video_codec,
                record.audio_codec,
                record.caption_style,
                record.bgm_asset_id,
                record.quality_score,
                record.quality_status,
                record.render_status,
                record.render_attempt,
                json.dumps(record.error_details) if isinstance(record.error_details, (dict, list)) else record.error_details,
                json.dumps(record.telemetry) if isinstance(record.telemetry, (dict, list)) else record.telemetry,
                record.created_at,
                record.updated_at,
            ),
        )
    return record


def list_final_renders(
    job_id: str, approved_only: bool = False, status: str | None = None
) -> list[FinalRenderRecord]:
    with connection() as conn:
        if approved_only:
            rows = conn.execute(
                """
                SELECT * FROM final_renders
                WHERE job_id = ? AND render_status = 'completed' AND quality_status IN ('RENDER_PASS', 'RENDER_WARN')
                ORDER BY created_at ASC
                """,
                (job_id,),
            ).fetchall()
        elif status:
            rows = conn.execute(
                """
                SELECT * FROM final_renders
                WHERE job_id = ? AND quality_status = ?
                ORDER BY created_at ASC
                """,
                (job_id, status),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM final_renders
                WHERE job_id = ?
                ORDER BY created_at ASC
                """,
                (job_id,),
            ).fetchall()
    return [FinalRenderRecord.from_row(r) for r in rows]


def get_final_render(clip_id: str) -> FinalRenderRecord | None:
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM final_renders WHERE clip_id = ?", (clip_id,)
        ).fetchone()
    return FinalRenderRecord.from_row(row) if row else None


# --------------------------------------------------------------------------
# Step 23: Clip SEO & Metadata
# --------------------------------------------------------------------------


def create_clip_metadata(record: ClipMetadataRecord) -> ClipMetadataRecord:
    with connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO clip_metadata (
                id, job_id, clip_id, generated_title, final_title,
                generated_description, final_description, generated_hashtags,
                final_hashtags, generated_mentions, final_mentions, generated_cta,
                final_cta, campaign_requirements_matched, compliance_status,
                compliance_score, validation_errors, validation_warnings, version,
                telemetry, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.id,
                record.job_id,
                record.clip_id,
                record.generated_title,
                record.final_title,
                record.generated_description,
                record.final_description,
                json.dumps(record.generated_hashtags),
                json.dumps(record.final_hashtags),
                json.dumps(record.generated_mentions),
                json.dumps(record.final_mentions),
                record.generated_cta,
                record.final_cta,
                json.dumps(record.campaign_requirements_matched),
                record.compliance_status,
                record.compliance_score,
                json.dumps(record.validation_errors),
                json.dumps(record.validation_warnings),
                record.version,
                json.dumps(record.telemetry),
                record.created_at,
                record.updated_at,
            ),
        )
    return record


def get_clip_metadata(clip_id: str) -> ClipMetadataRecord | None:
    with connection() as conn:
        row = conn.execute("SELECT * FROM clip_metadata WHERE clip_id = ?", (clip_id,)).fetchone()
    return ClipMetadataRecord.from_row(row) if row else None


def list_clip_metadata_for_job(job_id: str) -> list[ClipMetadataRecord]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM clip_metadata WHERE job_id = ? ORDER BY created_at ASC",
            (job_id,),
        ).fetchall()
    return [ClipMetadataRecord.from_row(r) for r in rows]


def update_clip_metadata(clip_id: str, **updates: Any) -> ClipMetadataRecord | None:
    existing = get_clip_metadata(clip_id)
    if not existing:
        return None

    # Handle JSON serializations
    for list_key in ("final_hashtags", "final_mentions", "validation_errors", "validation_warnings", "generated_hashtags", "generated_mentions"):
        if list_key in updates and isinstance(updates[list_key], (list, tuple)):
            updates[list_key] = json.dumps(updates[list_key])

    for dict_key in ("campaign_requirements_matched", "telemetry"):
        if dict_key in updates and isinstance(updates[dict_key], dict):
            updates[dict_key] = json.dumps(updates[dict_key])

    if "updated_at" not in updates:
        updates["updated_at"] = utcnow()

    fields = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [clip_id]

    with connection() as conn:
        conn.execute(f"UPDATE clip_metadata SET {fields} WHERE clip_id = ?", values)

    return get_clip_metadata(clip_id)


def replace_clip_metadata(job_id: str, records: list[ClipMetadataRecord]) -> None:
    with connection() as conn:
        for r in records:
            conn.execute(
                """
                INSERT OR REPLACE INTO clip_metadata (
                    id, job_id, clip_id, generated_title, final_title,
                    generated_description, final_description, generated_hashtags,
                    final_hashtags, generated_mentions, final_mentions, generated_cta,
                    final_cta, campaign_requirements_matched, compliance_status,
                    compliance_score, validation_errors, validation_warnings, version,
                    telemetry, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    r.id,
                    r.job_id,
                    r.clip_id,
                    r.generated_title,
                    r.final_title,
                    r.generated_description,
                    r.final_description,
                    json.dumps(r.generated_hashtags),
                    json.dumps(r.final_hashtags),
                    json.dumps(r.generated_mentions),
                    json.dumps(r.final_mentions),
                    r.generated_cta,
                    r.final_cta,
                    json.dumps(r.campaign_requirements_matched),
                    r.compliance_status,
                    r.compliance_score,
                    json.dumps(r.validation_errors),
                    json.dumps(r.validation_warnings),
                    r.version,
                    json.dumps(r.telemetry),
                    r.created_at,
                    r.updated_at,
                ),
            )






# --------------------------------------------------------------------------
# Step 24: Clip Approval
# --------------------------------------------------------------------------


def create_clip_approval(record: ClipApprovalRecord) -> ClipApprovalRecord:
    """Persist a new clip approval record (upsert on clip_id UNIQUE)."""
    with connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO clip_approvals (
                id, job_id, clip_id, current_status, operator_action,
                operator_note, version, previous_status, publish_eligible,
                blocking_reasons, history, telemetry, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.id,
                record.job_id,
                record.clip_id,
                record.current_status,
                record.operator_action,
                record.operator_note,
                record.version,
                record.previous_status,
                int(record.publish_eligible),
                json.dumps(record.blocking_reasons),
                json.dumps(record.history),
                json.dumps(record.telemetry),
                record.created_at,
                record.updated_at,
            ),
        )
    return record


def get_clip_approval(clip_id: str) -> ClipApprovalRecord | None:
    """Retrieve the approval record for a single clip."""
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM clip_approvals WHERE clip_id = ?", (clip_id,)
        ).fetchone()
    return ClipApprovalRecord.from_row(row) if row else None


def get_clip_approval_by_id(approval_id: str) -> ClipApprovalRecord | None:
    """Retrieve approval record by its primary key."""
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM clip_approvals WHERE id = ?", (approval_id,)
        ).fetchone()
    return ClipApprovalRecord.from_row(row) if row else None


def list_clip_approvals_for_job(job_id: str) -> list[ClipApprovalRecord]:
    """List all approval records for clips belonging to a job."""
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM clip_approvals WHERE job_id = ? ORDER BY created_at ASC",
            (job_id,),
        ).fetchall()
    return [ClipApprovalRecord.from_row(r) for r in rows]


def update_clip_approval(record: ClipApprovalRecord) -> ClipApprovalRecord:
    """Overwrite the approval record (use after ClipApprovalRecord.apply_action)."""
    with connection() as conn:
        conn.execute(
            """
            UPDATE clip_approvals
            SET current_status  = ?,
                operator_action = ?,
                operator_note   = ?,
                version         = ?,
                previous_status = ?,
                publish_eligible= ?,
                blocking_reasons= ?,
                history         = ?,
                telemetry       = ?,
                updated_at      = ?
            WHERE clip_id = ?
            """,
            (
                record.current_status,
                record.operator_action,
                record.operator_note,
                record.version,
                record.previous_status,
                int(record.publish_eligible),
                json.dumps(record.blocking_reasons),
                json.dumps(record.history),
                json.dumps(record.telemetry),
                record.updated_at,
                record.clip_id,
            ),
        )
    return record


def reset_clip_approval(clip_id: str) -> ClipApprovalRecord | None:
    """Reset a clip's approval back to PENDING_REVIEW, preserving history.

    Designed for the API's reset endpoint. Records the reset action in history.
    """
    existing = get_clip_approval(clip_id)
    if not existing:
        return None

    old_status = existing.current_status
    existing.previous_status = old_status
    existing.current_status = "PENDING_REVIEW"
    existing.operator_action = "reset"
    existing.operator_note = "Reset to PENDING_REVIEW by operator"
    existing.version += 1
    existing.updated_at = utcnow()
    existing.history.append({
        "from_status": old_status,
        "to_status": "PENDING_REVIEW",
        "operator_action": "reset",
        "operator_note": "Reset to PENDING_REVIEW by operator",
        "actor": "system",
        "version": existing.version,
        "timestamp": existing.updated_at,
    })

    return update_clip_approval(existing)


def is_clip_approved_for_publishing(clip_id: str) -> tuple[bool, list[str]]:
    """Publishing guard for Step 25.

    Returns ``(True, [])`` if the clip is APPROVED and publish-eligible.
    Returns ``(False, reasons)`` otherwise with human-readable blocking reasons.
    """
    record = get_clip_approval(clip_id)
    if record is None:
        return False, ["Clip has not been reviewed. Approval required before publishing."]
    if record.current_status != "APPROVED":
        return False, [f"Clip approval status is '{record.current_status}' — must be 'APPROVED'."]
    if not record.publish_eligible:
        reasons = record.blocking_reasons or ["Clip failed publish-readiness gate."]
        return False, reasons
    return True, []


def build_clip_approval_review_package(
    clip_id: str,
    job_id: str,
) -> dict[str, Any]:
    """Assemble a review package for Telegram notification.

    Reuses already-computed Step 17–23 artifacts from DB. Does NOT recompute
    rendering, BGM mixing, captions, or SEO.
    """
    from .models import new_id  # local import to avoid circular at module level

    clip_row = None
    with connection() as conn:
        row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
        if row:
            from .models import Clip
            clip_row = Clip.from_row(row)

    final_render = get_final_render(clip_id)
    clip_meta = get_clip_metadata(clip_id)
    approval = get_clip_approval(clip_id)

    pkg: dict[str, Any] = {
        "clip_id": clip_id,
        "job_id": job_id,
        "rank": clip_row.rank if clip_row else 0,
        "title": clip_row.title if clip_row else "",
        "score": clip_row.score if clip_row else 0,
        "duration_s": clip_row.duration_s if clip_row else 0.0,
        "output_path": final_render.output_path if final_render else None,
        "quality_score": final_render.quality_score if final_render else None,
        "quality_status": final_render.quality_status if final_render else None,
        "seo_title": clip_meta.final_title if clip_meta else "",
        "seo_description": clip_meta.final_description if clip_meta else "",
        "seo_hashtags": clip_meta.final_hashtags if clip_meta else [],
        "seo_compliance_status": clip_meta.compliance_status if clip_meta else None,
        "approval_status": approval.current_status if approval else "PENDING_REVIEW",
        "approval_version": approval.version if approval else 1,
        "publish_eligible": approval.publish_eligible if approval else False,
        "blocking_reasons": approval.blocking_reasons if approval else [],
    }
    return pkg


# --------------------------------------------------------------------------
# Step 25: Remote Publications
# --------------------------------------------------------------------------


def create_publication(record: PublicationRecord) -> PublicationRecord:
    """Insert a new remote publication attempt record (or replace by idempotency key)."""
    with connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO publications (
                id, job_id, clip_id, final_render_id, platform, account_id,
                destination_id, status, attempt_number, idempotency_key,
                remote_media_id, remote_post_id, permalink, upload_started_at,
                upload_completed_at, published_at, error_code, error_message,
                response_metadata, retry_count, version, telemetry,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.id,
                record.job_id,
                record.clip_id,
                record.final_render_id,
                record.platform,
                record.account_id,
                record.destination_id,
                record.status,
                record.attempt_number,
                record.idempotency_key,
                record.remote_media_id,
                record.remote_post_id,
                record.permalink,
                record.upload_started_at,
                record.upload_completed_at,
                record.published_at,
                record.error_code,
                record.error_message,
                json.dumps(record.response_metadata),
                record.retry_count,
                record.version,
                json.dumps(record.telemetry),
                record.created_at,
                record.updated_at,
            ),
        )
    return record


def get_publication(publication_id: str) -> PublicationRecord | None:
    """Retrieve a publication record by its primary key ID."""
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM publications WHERE id = ?", (publication_id,)
        ).fetchone()
    return PublicationRecord.from_row(row) if row else None


def get_publication_by_idempotency_key(idempotency_key: str) -> PublicationRecord | None:
    """Retrieve publication record by its unique idempotency key."""
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM publications WHERE idempotency_key = ?", (idempotency_key,)
        ).fetchone()
    return PublicationRecord.from_row(row) if row else None


def list_publications_for_job(job_id: str, limit: int = 100) -> list[PublicationRecord]:
    """List publication records for a job, ordered newest first."""
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM publications WHERE job_id = ? ORDER BY created_at DESC LIMIT ?",
            (job_id, limit),
        ).fetchall()
    return [PublicationRecord.from_row(r) for r in rows]


def list_publications_for_clip(clip_id: str) -> list[PublicationRecord]:
    """List publication records for a specific clip."""
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM publications WHERE clip_id = ? ORDER BY created_at DESC",
            (clip_id,),
        ).fetchall()
    return [PublicationRecord.from_row(r) for r in rows]


def update_publication(record: PublicationRecord) -> PublicationRecord:
    """Update mutable fields of an existing publication record."""
    with connection() as conn:
        conn.execute(
            """
            UPDATE publications
            SET status              = ?,
                attempt_number      = ?,
                remote_media_id     = ?,
                remote_post_id      = ?,
                permalink           = ?,
                upload_started_at   = ?,
                upload_completed_at = ?,
                published_at        = ?,
                error_code          = ?,
                error_message       = ?,
                response_metadata   = ?,
                retry_count         = ?,
                version             = ?,
                telemetry           = ?,
                updated_at          = ?
            WHERE id = ?
            """,
            (
                record.status,
                record.attempt_number,
                record.remote_media_id,
                record.remote_post_id,
                record.permalink,
                record.upload_started_at,
                record.upload_completed_at,
                record.published_at,
                record.error_code,
                record.error_message,
                json.dumps(record.response_metadata),
                record.retry_count,
                record.version,
                json.dumps(record.telemetry),
                record.updated_at,
                record.id,
            ),
        )
    return record


# --------------------------------------------------------------------------
# Step 26: Publishing Destinations (Multi-Account)
# --------------------------------------------------------------------------


def create_destination(dest: models.DestinationRecord) -> models.DestinationRecord:
    """Insert or update a multi-account publishing destination."""
    with connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO publishing_destinations (
                id, platform, display_name, account_identifier,
                enabled, priority, config_metadata, daily_limit,
                spacing_seconds, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                dest.id,
                dest.platform,
                dest.display_name,
                dest.account_identifier,
                1 if dest.enabled else 0,
                dest.priority,
                json.dumps(dest.config_metadata),
                dest.daily_limit,
                dest.spacing_seconds,
                dest.created_at,
                dest.updated_at,
            ),
        )
    return dest


def get_destination(destination_id: str) -> models.DestinationRecord | None:
    """Retrieve destination by ID."""
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM publishing_destinations WHERE id = ?", (destination_id,)
        ).fetchone()
    return models.DestinationRecord.from_row(row) if row else None


def list_destinations(
    platform: str | None = None, enabled_only: bool = False
) -> list[models.DestinationRecord]:
    """List publishing destinations with optional filters."""
    query = "SELECT * FROM publishing_destinations WHERE 1=1"
    params: list[Any] = []
    if platform:
        query += " AND platform = ?"
        params.append(platform.strip().lower())
    if enabled_only:
        query += " AND enabled = 1"
    query += " ORDER BY priority DESC, created_at ASC"

    with connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [models.DestinationRecord.from_row(r) for r in rows]


def update_destination(
    destination_id: str,
    *,
    display_name: str | None = None,
    account_identifier: str | None = None,
    enabled: bool | None = None,
    priority: int | None = None,
    config_metadata: dict[str, Any] | None = None,
    daily_limit: int | None = None,
    spacing_seconds: int | None = None,
) -> models.DestinationRecord | None:
    """Update fields on a publishing destination."""
    updates: list[str] = []
    params: list[Any] = []
    now = models.utcnow()

    if display_name is not None:
        updates.append("display_name = ?")
        params.append(display_name.strip())
    if account_identifier is not None:
        updates.append("account_identifier = ?")
        params.append(account_identifier.strip())
    if enabled is not None:
        updates.append("enabled = ?")
        params.append(1 if enabled else 0)
    if priority is not None:
        updates.append("priority = ?")
        params.append(priority)
    if config_metadata is not None:
        updates.append("config_metadata = ?")
        params.append(json.dumps(config_metadata))
    if daily_limit is not None:
        updates.append("daily_limit = ?")
        params.append(daily_limit)
    if spacing_seconds is not None:
        updates.append("spacing_seconds = ?")
        params.append(spacing_seconds)

    if not updates:
        return get_destination(destination_id)

    updates.append("updated_at = ?")
    params.append(now)
    params.append(destination_id)

    with connection() as conn:
        cursor = conn.execute(
            f"UPDATE publishing_destinations SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        if cursor.rowcount == 0:
            return None
    return get_destination(destination_id)


def delete_destination(destination_id: str) -> bool:
    """Delete a publishing destination."""
    with connection() as conn:
        cursor = conn.execute(
            "DELETE FROM publishing_destinations WHERE id = ?", (destination_id,)
        )
        return cursor.rowcount > 0


# --------------------------------------------------------------------------
# Step 26: Publishing Queue & Scheduling Operations
# --------------------------------------------------------------------------


def create_queue_item(item: models.PublishingQueueRecord) -> models.PublishingQueueRecord:
    """Insert or replace a publishing queue item."""
    with connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO publishing_queue (
                id, job_id, clip_id, destination_id, publication_id,
                platform, scheduled_at, priority, status, attempt_count,
                claimed_by, claimed_at, lease_expires_at, error_message,
                idempotency_key, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.id,
                item.job_id,
                item.clip_id,
                item.destination_id,
                item.publication_id,
                item.platform,
                item.scheduled_at,
                item.priority,
                item.status,
                item.attempt_count,
                item.claimed_by,
                item.claimed_at,
                item.lease_expires_at,
                item.error_message,
                item.idempotency_key,
                item.created_at,
                item.updated_at,
            ),
        )
    return item


def get_queue_item(queue_id: str) -> models.PublishingQueueRecord | None:
    """Retrieve queue item by ID."""
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM publishing_queue WHERE id = ?", (queue_id,)
        ).fetchone()
    return models.PublishingQueueRecord.from_row(row) if row else None


def get_queue_item_by_idempotency_key(key: str) -> models.PublishingQueueRecord | None:
    """Retrieve queue item by its unique idempotency key."""
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM publishing_queue WHERE idempotency_key = ?", (key,)
        ).fetchone()
    return models.PublishingQueueRecord.from_row(row) if row else None


def list_queue_items(
    job_id: str | None = None,
    clip_id: str | None = None,
    destination_id: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[models.PublishingQueueRecord]:
    """List queue items matching optional filters, ordered by scheduled time."""
    query = "SELECT * FROM publishing_queue WHERE 1=1"
    params: list[Any] = []

    if job_id:
        query += " AND job_id = ?"
        params.append(job_id)
    if clip_id:
        query += " AND clip_id = ?"
        params.append(clip_id)
    if destination_id:
        query += " AND destination_id = ?"
        params.append(destination_id)
    if status:
        query += " AND status = ?"
        params.append(status)

    query += " ORDER BY priority DESC, scheduled_at ASC LIMIT ?"
    params.append(limit)

    with connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [models.PublishingQueueRecord.from_row(r) for r in rows]


def count_destination_publications_today(destination_id: str) -> int:
    """Count how many successful publications have been made to a destination today (UTC)."""
    today_prefix = models.utcnow()[:10]  # "YYYY-MM-DD"
    with connection() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) FROM publishing_queue
            WHERE destination_id = ? AND status = 'PUBLISHED' AND substr(updated_at, 1, 10) = ?
            """,
            (destination_id, today_prefix),
        ).fetchone()
    return int(row[0]) if row else 0


def get_latest_scheduled_time_for_destination(destination_id: str) -> str | None:
    """Get the latest scheduled_at timestamp for pending/active items for this destination."""
    with connection() as conn:
        row = conn.execute(
            """
            SELECT MAX(scheduled_at) FROM publishing_queue
            WHERE destination_id = ? AND status IN ('QUEUED', 'SCHEDULED', 'CLAIMED', 'PUBLISHING', 'PUBLISHED')
            """,
            (destination_id,),
        ).fetchone()
    return str(row[0]) if (row and row[0]) else None


def recover_stale_queue_claims(lease_seconds: int = 300) -> int:
    """Recover items stuck in CLAIMED or PUBLISHING whose lease has expired."""
    now = models.utcnow()
    with connection() as conn:
        cursor = conn.execute(
            """
            UPDATE publishing_queue
            SET status = 'QUEUED',
                claimed_by = NULL,
                claimed_at = NULL,
                lease_expires_at = NULL,
                updated_at = ?
            WHERE status IN ('CLAIMED', 'PUBLISHING')
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at < ?
            """,
            (now, now),
        )
        return cursor.rowcount


def claim_next_queue_item(
    worker_id: str, lease_seconds: int = 300
) -> models.PublishingQueueRecord | None:
    """Safely and atomically claim the highest-priority, due item from the publishing queue."""
    from datetime import datetime, timedelta, timezone

    now_dt = datetime.now(timezone.utc)
    now_str = now_dt.isoformat()
    lease_expires_str = (now_dt + timedelta(seconds=lease_seconds)).isoformat()

    with connection() as conn:
        # 1. Recover expired leases first
        conn.execute(
            """
            UPDATE publishing_queue
            SET status = 'QUEUED',
                claimed_by = NULL,
                claimed_at = NULL,
                lease_expires_at = NULL,
                updated_at = ?
            WHERE status IN ('CLAIMED', 'PUBLISHING')
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at < ?
            """,
            (now_str, now_str),
        )

        # 2. Find next eligible candidate
        row = conn.execute(
            """
            SELECT id FROM publishing_queue
            WHERE status IN ('QUEUED', 'SCHEDULED')
              AND scheduled_at <= ?
            ORDER BY priority DESC, scheduled_at ASC, created_at ASC
            LIMIT 1
            """,
            (now_str,),
        ).fetchone()

        if not row:
            return None

        queue_id = row[0]

        # 3. Atomically update and claim
        conn.execute(
            """
            UPDATE publishing_queue
            SET status = 'CLAIMED',
                claimed_by = ?,
                claimed_at = ?,
                lease_expires_at = ?,
                attempt_count = attempt_count + 1,
                updated_at = ?
            WHERE id = ? AND status IN ('QUEUED', 'SCHEDULED')
            """,
            (worker_id, now_str, lease_expires_str, now_str, queue_id),
        )

        claimed_row = conn.execute(
            "SELECT * FROM publishing_queue WHERE id = ?", (queue_id,)
        ).fetchone()

    return models.PublishingQueueRecord.from_row(claimed_row) if claimed_row else None


def update_queue_item_status(
    queue_id: str,
    status: models.QueueStatus,
    *,
    error_message: str | None = None,
    publication_id: str | None = None,
) -> models.PublishingQueueRecord | None:
    """Transition queue item to a new status with optional error or publication linkage."""
    now = models.utcnow()
    updates = ["status = ?", "updated_at = ?"]
    params: list[Any] = [status, now]

    if error_message is not None:
        updates.append("error_message = ?")
        params.append(error_message)

    if publication_id is not None:
        updates.append("publication_id = ?")
        params.append(publication_id)

    if status in ("PUBLISHED", "FAILED_PERMANENT", "CANCELLED", "SKIPPED"):
        updates.append("lease_expires_at = NULL")

    params.append(queue_id)

    with connection() as conn:
        cursor = conn.execute(
            f"UPDATE publishing_queue SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        if cursor.rowcount == 0:
            return None
    return get_queue_item(queue_id)


def reschedule_queue_item(
    queue_id: str, new_scheduled_at: str
) -> models.PublishingQueueRecord | None:
    """Reschedule a queue item to a new scheduled time, resetting claims."""
    now = models.utcnow()
    with connection() as conn:
        cursor = conn.execute(
            """
            UPDATE publishing_queue
            SET scheduled_at = ?,
                status = 'SCHEDULED',
                claimed_by = NULL,
                claimed_at = NULL,
                lease_expires_at = NULL,
                error_message = NULL,
                updated_at = ?
            WHERE id = ? AND status NOT IN ('PUBLISHED', 'CANCELLED')
            """,
            (new_scheduled_at, now, queue_id),
        )
        if cursor.rowcount == 0:
            return None
    return get_queue_item(queue_id)


def cancel_queue_item(
    queue_id: str, reason: str = "Cancelled by operator"
) -> models.PublishingQueueRecord | None:
    """Cancel a queue item."""
    now = models.utcnow()
    with connection() as conn:
        cursor = conn.execute(
            """
            UPDATE publishing_queue
            SET status = 'CANCELLED',
                claimed_by = NULL,
                lease_expires_at = NULL,
                error_message = ?,
                updated_at = ?
            WHERE id = ? AND status != 'PUBLISHED'
            """,
            (reason, now, queue_id),
        )
        if cursor.rowcount == 0:
            return None
    return get_queue_item(queue_id)


# --------------------------------------------------------------------------
# Step 28: Analytics, Learning & Production Reserve
# --------------------------------------------------------------------------


def create_or_update_publication_metric(
    metric: models.PublicationMetricRecord,
) -> models.PublicationMetricRecord:
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO publication_metrics (
                id, publication_id, clip_id, job_id, platform, destination_id,
                views, likes, comments, shares, watch_time_s, avg_view_duration_s,
                completion_rate, raw_payload, published_at, collected_at, is_mature,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                views = excluded.views,
                likes = excluded.likes,
                comments = excluded.comments,
                shares = excluded.shares,
                watch_time_s = excluded.watch_time_s,
                avg_view_duration_s = excluded.avg_view_duration_s,
                completion_rate = excluded.completion_rate,
                raw_payload = excluded.raw_payload,
                collected_at = excluded.collected_at,
                is_mature = excluded.is_mature,
                updated_at = excluded.updated_at
            """,
            (
                metric.id,
                metric.publication_id,
                metric.clip_id,
                metric.job_id,
                metric.platform,
                metric.destination_id,
                metric.views,
                metric.likes,
                metric.comments,
                metric.shares,
                metric.watch_time_s,
                metric.avg_view_duration_s,
                metric.completion_rate,
                json.dumps(metric.raw_payload) if isinstance(metric.raw_payload, (dict, list)) else metric.raw_payload,
                metric.published_at,
                metric.collected_at,
                1 if metric.is_mature else 0,
                metric.created_at,
                metric.updated_at,
            ),
        )
    return metric


def list_publication_metrics(
    clip_id: str | None = None,
    job_id: str | None = None,
    platform: str | None = None,
    mature_only: bool = False,
    limit: int = 100,
) -> list[models.PublicationMetricRecord]:
    query = "SELECT * FROM publication_metrics WHERE 1=1"
    params: list[Any] = []
    if clip_id:
        query += " AND clip_id = ?"
        params.append(clip_id)
    if job_id:
        query += " AND job_id = ?"
        params.append(job_id)
    if platform:
        query += " AND platform = ?"
        params.append(platform)
    if mature_only:
        query += " AND is_mature = 1"
    query += " ORDER BY collected_at DESC LIMIT ?"
    params.append(limit)
    with connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [models.PublicationMetricRecord.from_row(r) for r in rows]


def get_mature_metrics(min_age_hours: int = 24) -> list[models.PublicationMetricRecord]:
    """Retrieve metrics strictly satisfying the 24-hour maturation rule."""
    from datetime import datetime, timedelta, timezone

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=min_age_hours)).isoformat()
    with connection() as conn:
        conn.execute(
            """
            UPDATE publication_metrics
            SET is_mature = 1
            WHERE is_mature = 0 AND published_at <= ?
            """,
            (cutoff,),
        )
        rows = conn.execute(
            """
            SELECT * FROM publication_metrics
            WHERE published_at <= ?
            ORDER BY views DESC
            """,
            (cutoff,),
        ).fetchall()
    return [models.PublicationMetricRecord.from_row(r) for r in rows]


def record_learning_audit(audit: models.LearningAuditRecord) -> models.LearningAuditRecord:
    with connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO learning_audits (
                id, category, metric_observed, evidence_sample_size,
                recommendation, action_taken, rationale,
                parameters_before, parameters_after, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit.id,
                audit.category,
                audit.metric_observed,
                audit.evidence_sample_size,
                audit.recommendation,
                audit.action_taken,
                audit.rationale,
                json.dumps(audit.parameters_before) if isinstance(audit.parameters_before, (dict, list)) else audit.parameters_before,
                json.dumps(audit.parameters_after) if isinstance(audit.parameters_after, (dict, list)) else audit.parameters_after,
                audit.created_at,
            ),
        )
    return audit


def list_learning_audits(category: str | None = None, limit: int = 100) -> list[models.LearningAuditRecord]:
    query = "SELECT * FROM learning_audits WHERE 1=1"
    params: list[Any] = []
    if category:
        query += " AND category = ?"
        params.append(category)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [models.LearningAuditRecord.from_row(r) for r in rows]


def count_ready_reserve(job_id: str | None = None) -> int:
    """Count finished clips that are ready for publishing (reserve pool).

    A clip belongs to the ready reserve if:
    1. Step 22 Final Render is passed (RENDER_PASS or RENDER_WARN)
    2. Step 24 Operator Approval is APPROVED (or PENDING_REVIEW awaiting action)
    3. It has NOT yet been published
    """
    query = """
        SELECT COUNT(DISTINCT c.id)
        FROM clips c
        JOIN final_renders fr ON fr.clip_id = c.id
        LEFT JOIN clip_approvals ca ON ca.clip_id = c.id
        WHERE fr.render_status = 'completed'
          AND fr.quality_status IN ('RENDER_PASS', 'RENDER_WARN')
          AND (ca.current_status IS NULL OR ca.current_status IN ('APPROVED', 'PENDING_REVIEW'))
          AND c.id NOT IN (
              SELECT DISTINCT clip_id FROM publications WHERE status = 'PUBLISHED'
          )
    """
    params: list[Any] = []
    if job_id:
        query += " AND c.job_id = ?"
        params.append(job_id)
    with connection() as conn:
        row = conn.execute(query, params).fetchone()
    return int(row[0]) if row else 0


# --------------------------------------------------------------------------
# App Credentials (Encrypted Secret Vault)
# --------------------------------------------------------------------------


def save_credential(key: str, ciphertext: str, fingerprint: str = "") -> models.AppCredentialRecord:
    now = utcnow()
    try:
        with connection() as conn:
            conn.execute(
                """
                INSERT INTO app_credentials (key, ciphertext, fingerprint, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    ciphertext = excluded.ciphertext,
                    fingerprint = excluded.fingerprint,
                    updated_at = excluded.updated_at
                """,
                (key, ciphertext, fingerprint, now, now),
            )
    except sqlite3.OperationalError:
        from . import init
        init()
        with connection() as conn:
            conn.execute(
                """
                INSERT INTO app_credentials (key, ciphertext, fingerprint, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    ciphertext = excluded.ciphertext,
                    fingerprint = excluded.fingerprint,
                    updated_at = excluded.updated_at
                """,
                (key, ciphertext, fingerprint, now, now),
            )
    record = get_credential(key)
    if record is None:
        raise RuntimeError(f"Failed to retrieve credential {key} after write")
    return record


def get_credential(key: str) -> AppCredentialRecord | None:
    try:
        with connection() as conn:
            row = conn.execute("SELECT * FROM app_credentials WHERE key = ?", (key,)).fetchone()
    except sqlite3.OperationalError:
        from . import init
        init()
        with connection() as conn:
            row = conn.execute("SELECT * FROM app_credentials WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    return AppCredentialRecord.from_row(row)


def delete_credential(key: str) -> bool:
    try:
        with connection() as conn:
            cur = conn.execute("DELETE FROM app_credentials WHERE key = ?", (key,))
        return cur.rowcount > 0
    except sqlite3.OperationalError:
        return False


def list_credentials() -> list[AppCredentialRecord]:
    try:
        with connection() as conn:
            rows = conn.execute("SELECT * FROM app_credentials ORDER BY key ASC").fetchall()
        return [AppCredentialRecord.from_row(r) for r in rows]
    except sqlite3.OperationalError:
        return []



