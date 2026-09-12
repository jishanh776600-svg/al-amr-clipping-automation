"""Pydantic request and response models for the REST API.

Kept separate from the dataclasses in ``db.models``: those mirror SQLite rows,
these are the wire contract. Splitting them means a schema change doesn't
silently reshape the API, and the API can expose derived fields (durations,
export URLs) that don't belong in storage.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from ..campaign.models import CampaignBrief as CampaignBriefIn
from ..db import models


class SourceOut(BaseModel):
    id: str
    type: str
    title: str
    url: str | None = None
    filename: str | None = None
    channel: str | None = None
    duration_s: float
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    has_audio: bool
    has_video: bool
    created_at: str

    @classmethod
    def of(cls, source: models.Source) -> SourceOut:
        # Built field by field rather than from vars(): the row carries an
        # absolute filesystem path that has no business leaving the process.
        return cls(
            id=source.id,
            type=source.type,
            title=source.title,
            url=source.url,
            filename=source.filename,
            channel=source.channel,
            duration_s=source.duration_s,
            width=source.width,
            height=source.height,
            fps=source.fps,
            has_audio=source.has_audio,
            has_video=source.has_video,
            created_at=source.created_at,
        )


class YouTubeIngestIn(BaseModel):
    url: str
    cookies_from_browser: str | None = None
    cookies_file: str | None = None


class UrlIngestIn(BaseModel):
    url: str
    cookies_from_browser: str | None = None
    cookies_file: str | None = None


class JobSettingsIn(BaseModel):
    """Per-job overrides. Anything omitted falls back to saved settings."""

    provider: str | None = None
    whisper_model: str | None = None
    language: str | None = None
    diarization: bool | None = None
    min_duration_s: float | None = Field(default=None, gt=0)
    max_duration_s: float | None = Field(default=None, gt=0)
    max_clips: int | None = Field(default=None, ge=1, le=50)
    caption_style: str | None = None
    ratio: Literal["9:16", "1:1", "16:9"] | None = None


class JobCreateIn(BaseModel):
    source_id: str
    settings: JobSettingsIn = Field(default_factory=JobSettingsIn)
    campaign: CampaignBriefIn | None = None


class CampaignEvaluationOut(BaseModel):
    clip_id: str
    campaign_id: str = ""
    approved: bool = True
    final_score: float = 0.0
    hook_score: float = 0.0
    cta_score: float = 0.0
    viral_score: float = 0.0
    density_score: float = 0.0
    hard_failures: list[str] = Field(default_factory=list)
    soft_warnings: list[str] = Field(default_factory=list)
    rule_results: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def of(cls, row: models.CampaignEvaluationRow) -> CampaignEvaluationOut:
        return cls(
            clip_id=row.clip_id,
            campaign_id=row.campaign_id,
            approved=row.approved,
            final_score=row.final_score,
            hook_score=row.hook_score,
            cta_score=row.cta_score,
            viral_score=row.viral_score,
            density_score=row.density_score,
            hard_failures=row.hard_failures,
            soft_warnings=row.soft_warnings,
            rule_results=row.rule_results,
        )


class JobOut(BaseModel):
    id: str
    source_id: str
    status: str
    current_stage: str
    progress: float
    error: str | None = None
    provider: str
    dispatch_mode: str = "local"
    github_run_id: str | None = None
    attempt: int = 1
    max_attempts: int = 3
    last_heartbeat_at: str | None = None
    stale_at: str | None = None
    github_workflow: str | None = None
    github_job_id: str | None = None
    github_run_url: str | None = None
    github_run_status: str | None = None
    github_conclusion: str | None = None
    dispatched_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    failed_at: str | None = None
    cancelled_at: str | None = None
    cancel_requested_at: str | None = None
    finished_at: str | None = None
    created_at: str
    updated_at: str
    source: SourceOut | None = None

    @classmethod
    def of(cls, job: models.Job, source: models.Source | None = None) -> JobOut:
        return cls(
            id=job.id,
            source_id=job.source_id,
            status=job.status,
            current_stage=job.current_stage,
            progress=job.progress,
            error=job.error,
            provider=job.provider,
            dispatch_mode=job.dispatch_mode,
            github_run_id=job.github_run_id,
            attempt=getattr(job, "attempt", 1),
            max_attempts=getattr(job, "max_attempts", 3),
            last_heartbeat_at=getattr(job, "last_heartbeat_at", None),
            stale_at=getattr(job, "stale_at", None),
            github_workflow=getattr(job, "github_workflow", None),
            github_job_id=getattr(job, "github_job_id", None),
            github_run_url=getattr(job, "github_run_url", None),
            github_run_status=getattr(job, "github_run_status", None),
            github_conclusion=getattr(job, "github_conclusion", None),
            dispatched_at=getattr(job, "dispatched_at", None),
            started_at=job.started_at,
            completed_at=getattr(job, "completed_at", None),
            failed_at=getattr(job, "failed_at", None),
            cancelled_at=getattr(job, "cancelled_at", None),
            cancel_requested_at=getattr(job, "cancel_requested_at", None),
            finished_at=job.finished_at,
            created_at=job.created_at,
            updated_at=job.updated_at,
            source=SourceOut.of(source) if source else None,
        )


class WorkerCallbackResponseOut(BaseModel):
    status: str
    job_id: str
    cancel_requested: bool = False
    message: str = "ok"


class JobManifestClipExportOut(BaseModel):
    export_id: str
    ratio: str
    style: str
    size_bytes: int
    drive_file_id: str | None = None
    drive_storage_key: str | None = None
    drive_web_view_link: str | None = None
    download_url: str
    stream_url: str
    publishing_records: list[dict[str, Any]] = Field(default_factory=list)


class JobManifestClipOut(BaseModel):
    clip_id: str
    rank: int
    title: str
    hook: str
    duration_s: float
    start_s: float
    end_s: float
    score: int
    status: str
    campaign_evaluation: dict[str, Any] | None = None
    exports: list[JobManifestClipExportOut] = Field(default_factory=list)


class JobManifestOut(BaseModel):
    job_id: str
    status: str
    current_stage: str
    progress: float
    error: str | None = None
    dispatch_mode: str = "local"
    attempt: int = 1
    max_attempts: int = 3
    github: dict[str, Any] = Field(default_factory=dict)
    timestamps: dict[str, Any] = Field(default_factory=dict)
    source: dict[str, Any] | None = None
    clips: list[JobManifestClipOut] = Field(default_factory=list)


class WordOut(BaseModel):
    text: str
    start: float
    end: float
    speaker: str | None = None


class ExportOut(BaseModel):
    id: str
    clip_id: str
    ratio: str
    style: str
    size_bytes: int
    created_at: str
    download_url: str
    stream_url: str = ""
    drive_file_id: str | None = None
    drive_web_view_link: str | None = None
    drive_storage_key: str | None = None

    @classmethod
    def of(cls, export: models.Export) -> ExportOut:
        return cls(
            id=export.id,
            clip_id=export.clip_id,
            ratio=export.ratio,
            style=export.style,
            size_bytes=export.size_bytes,
            created_at=export.created_at,
            download_url=f"/api/exports/{export.id}/download",
            stream_url=f"/api/exports/{export.id}/stream",
            drive_file_id=export.drive_file_id,
            drive_web_view_link=export.drive_web_view_link,
            drive_storage_key=export.drive_storage_key,
        )


class ClipOut(BaseModel):
    id: str
    job_id: str
    rank: int
    start_s: float
    end_s: float
    duration_s: float
    start_word: int
    end_word: int
    title: str
    hook: str
    score: int
    reason: str
    status: str
    user_trimmed: bool
    caption_style: str = "bold_pop"
    ratio: str = "9:16"
    exports: list[ExportOut] = Field(default_factory=list)
    evaluation: CampaignEvaluationOut | None = None

    @classmethod
    def of(
        cls,
        clip: models.Clip,
        *,
        edit: models.ClipEdit | None = None,
        exports: list[models.Export] | None = None,
        evaluation: models.CampaignEvaluationRow | None = None,
    ) -> ClipOut:
        return cls(
            id=clip.id,
            job_id=clip.job_id,
            rank=clip.rank,
            start_s=clip.start_s,
            end_s=clip.end_s,
            duration_s=clip.duration_s,
            start_word=clip.start_word,
            end_word=clip.end_word,
            title=clip.title,
            hook=clip.hook,
            score=clip.score,
            reason=clip.reason,
            status=clip.status,
            user_trimmed=clip.user_trimmed,
            caption_style=edit.caption_style if edit else "bold_pop",
            ratio=edit.ratio if edit else "9:16",
            exports=[ExportOut.of(e) for e in (exports or [])],
            evaluation=CampaignEvaluationOut.of(evaluation) if evaluation else None,
        )


class ClipPatchIn(BaseModel):
    start_s: float | None = Field(default=None, ge=0)
    end_s: float | None = Field(default=None, gt=0)
    title: str | None = None
    status: Literal["candidate", "kept", "discarded", "exported"] | None = None


class CaptionPatchIn(BaseModel):
    """Word-level caption edits for one clip."""

    words: list[WordOut] | None = None
    caption_style: str | None = None
    ratio: Literal["9:16", "1:1", "16:9"] | None = None


class ExportRequestIn(BaseModel):
    ratio: Literal["9:16", "1:1", "16:9"] = "9:16"
    style: str = "bold_pop"
    write_srt: bool = False


class CaptionStyleOut(BaseModel):
    key: str
    label: str
    description: str
    #: Enough for the UI to approximate the look in HTML/CSS before rendering.
    preview: dict[str, Any]


class ProviderStatusOut(BaseModel):
    name: str
    available: bool
    detail: str = ""
    models: list[str] = Field(default_factory=list)
    requires_key: bool = True
    has_key: bool = False


class SettingsOut(BaseModel):
    active_provider: str
    providers: dict[str, dict[str, Any]]
    whisper: dict[str, Any]
    clips: dict[str, Any]
    ingest: dict[str, Any]
    export: dict[str, Any]
    insecure_secret_storage: bool
    #: Which providers have a key stored. The keys themselves never leave the
    #: keyring, so the UI shows presence, not value.
    keys_present: dict[str, bool] = Field(default_factory=dict)


class SettingsIn(BaseModel):
    active_provider: str | None = None
    providers: dict[str, dict[str, Any]] | None = None
    whisper: dict[str, Any] | None = None
    clips: dict[str, Any] | None = None
    ingest: dict[str, Any] | None = None
    export: dict[str, Any] | None = None


class SecretIn(BaseModel):
    key: str
    value: str


class SystemOut(BaseModel):
    ready: bool
    python_version: str
    platform: str
    ffmpeg_version: str | None
    has_libass: bool
    nvenc_works: bool
    accel: str
    gpu_name: str | None
    compute_type: str
    diarization_available: bool


class WorkerCallbackIn(BaseModel):
    token: str | None = None
    status: str | None = None
    stage: str | None = None
    progress: float | None = None
    error: str | None = None
    github_run_id: str | None = None
    clips: list[dict[str, Any]] | None = None
    evaluations: list[dict[str, Any]] | None = None
    exports: list[dict[str, Any]] | None = None
    publishing_records: list[dict[str, Any]] | None = None


class PublishingRecordOut(BaseModel):
    id: str
    export_id: str
    job_id: str
    platform: str
    status: str
    destination: str = ""
    external_id: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str

    @classmethod
    def of(cls, record: models.PublishingRecord) -> PublishingRecordOut:
        return cls(
            id=record.id,
            export_id=record.export_id,
            job_id=record.job_id,
            platform=record.platform,
            status=record.status,
            destination=record.destination or "",
            external_id=record.external_id,
            error=record.error,
            metadata=record.metadata or {},
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class PublishRequestIn(BaseModel):
    platforms: list[Literal["telegram", "youtube", "instagram"]] = Field(
        default_factory=lambda: ["telegram"]
    )
    title: str | None = None
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    destination: str = ""
    dry_run: bool = False


class PublishingPlatformInfo(BaseModel):
    platform: str
    available: bool
    configured: bool
    details: str = ""


