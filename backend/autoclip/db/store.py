"""CRUD operations over the AutoClip database.

Every function opens its own transaction via :func:`autoclip.db.connection`,
so callers don't manage commits. Functions are synchronous; async callers should
wrap them in ``asyncio.to_thread``.
"""

from __future__ import annotations

import json
from typing import Any

from . import connection
from .models import (
    CampaignGuideline,
    CampaignSpecificationRecord,
    Clip,
    ClipCandidateRecord,
    ClipEdit,
    ClipSpecificationRecord,
    ClipStatus,
    CaptionOptimizationRecord,
    RetentionOptimizationRecord,
    VisualCompositionRecord,
    CampaignEvaluationRow,
    CampaignPreset,
    Export,
    Job,
    JobStatus,
    PublishingRecord,
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






