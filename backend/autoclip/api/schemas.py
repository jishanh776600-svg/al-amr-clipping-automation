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
    bgm_asset_id: str | None = None
    ratio: Literal["9:16", "1:1", "16:9"] | None = None


class CampaignGuidelineOut(BaseModel):
    id: str
    job_id: str | None = None
    filename: str
    mime_type: str
    size_bytes: int
    source_type: str = "upload_pdf"
    drive_file_id: str | None = None
    sha256: str | None = None
    word_count: int = 0
    char_count: int = 0
    extracted_text: str = ""
    parsed_brief: dict[str, Any] = Field(default_factory=dict)
    status: str = "extracted"
    error: str | None = None
    created_at: str

    @classmethod
    def of(cls, guideline: models.CampaignGuideline) -> CampaignGuidelineOut:
        return cls(
            id=guideline.id,
            job_id=guideline.job_id,
            filename=guideline.filename,
            mime_type=guideline.mime_type,
            size_bytes=guideline.size_bytes,
            source_type=getattr(guideline, "source_type", "upload_pdf"),
            drive_file_id=getattr(guideline, "drive_file_id", None),
            sha256=getattr(guideline, "sha256", None),
            word_count=getattr(guideline, "word_count", 0),
            char_count=getattr(guideline, "char_count", 0),
            extracted_text=guideline.extracted_text,
            parsed_brief=guideline.parsed_brief,
            status=guideline.status,
            error=guideline.error,
            created_at=guideline.created_at,
        )


class DriveGuidelineIn(BaseModel):
    drive_url: str = Field(..., description="Google Drive share link, doc link, or file ID")


class RequirementItemOut(BaseModel):
    value: Any = None
    confidence: str = "explicit"
    source_doc_id: str = ""
    source_filename: str = ""
    source_type: str = ""
    snippet: str = ""
    weight: float = 1.0


class IngestedDocumentOut(BaseModel):
    doc_id: str
    source_type: str
    filename: str
    source_url: str | None = None
    sha256: str | None = None
    size_bytes: int = 0
    word_count: int = 0
    char_count: int = 0
    status: str = "extracted"
    error: str | None = None
    warning: str | None = None
    extracted_at: str


class CampaignConflictOut(BaseModel):
    id: str
    rule_category: str
    severity: str
    document_a: dict[str, Any] = Field(default_factory=dict)
    document_b: dict[str, Any] = Field(default_factory=dict)
    description: str
    resolution_status: str
    resolution_notes: str | None = None


class CampaignSpecificationOut(BaseModel):
    campaign_id: str
    title: str
    description: str = ""
    objective: str = ""
    created_at: str
    campaign_url: str | None = None
    documents: list[IngestedDocumentOut] = Field(default_factory=list)
    desired_topics: list[RequirementItemOut] = Field(default_factory=list)
    preferred_speakers: list[RequirementItemOut] = Field(default_factory=list)
    required_themes: list[RequirementItemOut] = Field(default_factory=list)
    banned_topics: list[RequirementItemOut] = Field(default_factory=list)
    banned_words: list[RequirementItemOut] = Field(default_factory=list)
    duration_min_s: RequirementItemOut
    duration_max_s: RequirementItemOut
    duration_preferred_s: RequirementItemOut
    output_count: RequirementItemOut
    aspect_ratio: RequirementItemOut
    hook_required: RequirementItemOut
    hook_window_s: RequirementItemOut
    hook_min_score: RequirementItemOut
    hook_types: list[RequirementItemOut] = Field(default_factory=list)
    hook_instructions: list[RequirementItemOut] = Field(default_factory=list)
    cta_required: RequirementItemOut
    cta_types: list[RequirementItemOut] = Field(default_factory=list)
    cta_window_s: RequirementItemOut
    cta_instructions: list[RequirementItemOut] = Field(default_factory=list)
    tone: RequirementItemOut
    pacing: RequirementItemOut
    caption_preset: RequirementItemOut
    caption_instructions: list[RequirementItemOut] = Field(default_factory=list)
    visual_instructions: list[RequirementItemOut] = Field(default_factory=list)
    branding_rules: list[RequirementItemOut] = Field(default_factory=list)
    title_patterns: list[RequirementItemOut] = Field(default_factory=list)
    description_guidelines: list[RequirementItemOut] = Field(default_factory=list)
    hashtags: list[RequirementItemOut] = Field(default_factory=list)
    keywords: list[RequirementItemOut] = Field(default_factory=list)
    required_mentions: list[RequirementItemOut] = Field(default_factory=list)
    platforms: list[str] = Field(default_factory=list)
    platform_rules: dict[str, list[RequirementItemOut]] = Field(default_factory=dict)
    max_silence_s: RequirementItemOut
    min_speech_density: RequirementItemOut
    conflicts: list[CampaignConflictOut] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    has_critical_conflicts: bool = False


class CampaignUrlExtractIn(BaseModel):
    url: str = Field(..., description="Campaign URL to extract")


class CampaignUrlExtractOut(BaseModel):
    url: str
    title: str = ""
    description: str = ""
    headings: list[str] = Field(default_factory=list)
    raw_text: str = ""
    status: str = "extracted"
    error: str | None = None
    word_count: int = 0
    char_count: int = 0


class JobCreateIn(BaseModel):
    source_id: str
    settings: JobSettingsIn = Field(default_factory=JobSettingsIn)
    campaign: CampaignBriefIn | None = None
    guideline_id: str | None = None


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
    guideline: CampaignGuidelineOut | None = None
    campaign_spec_id: str | None = None
    campaign_spec: dict[str, Any] | None = None

    @classmethod
    def of(
        cls,
        job: models.Job,
        source: models.Source | None = None,
        guideline: models.CampaignGuideline | None = None,
        campaign_spec: dict[str, Any] | None = None,
    ) -> JobOut:
        guideline_out = None
        if guideline:
            guideline_out = CampaignGuidelineOut.of(guideline)
        elif "guideline" in job.settings and isinstance(job.settings["guideline"], dict):
            g_dict = job.settings["guideline"]
            guideline_out = CampaignGuidelineOut(
                id=g_dict.get("id", ""),
                job_id=job.id,
                filename=g_dict.get("filename", ""),
                mime_type=g_dict.get("mime_type", ""),
                size_bytes=g_dict.get("size_bytes", 0),
                extracted_text=g_dict.get("extracted_text", ""),
                parsed_brief=g_dict.get("parsed_brief", {}),
                status=g_dict.get("status", "extracted"),
                error=g_dict.get("error"),
                created_at=g_dict.get("created_at", job.created_at),
            )

        spec_dict = campaign_spec or job.settings.get("campaign_spec")

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
            campaign_spec_id=getattr(job, "campaign_spec_id", None),
            campaign_spec=spec_dict,
            finished_at=job.finished_at,
            created_at=job.created_at,
            updated_at=job.updated_at,
            source=SourceOut.of(source) if source else None,
            guideline=guideline_out,
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
    source_acquisition: dict[str, Any] | None = None
    guideline: dict[str, Any] | None = None
    campaign: dict[str, Any] | None = None
    destinations: list[str] = Field(default_factory=lambda: ["telegram", "youtube", "drive"])
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
    acquisition_event: dict[str, Any] | None = None


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


class ClipCandidateOut(BaseModel):
    id: str
    job_id: str
    rank: int
    selected: bool
    status: str
    start_s: float
    end_s: float
    duration_s: float
    start_word: int
    end_word: int
    title: str
    hook_text: str
    reason: str
    transcript_slice: str
    score: float
    score_breakdown: dict[str, Any] = Field(default_factory=dict)
    hook_signals: dict[str, Any] = Field(default_factory=dict)
    climax_signals: dict[str, Any] = Field(default_factory=dict)
    cta_signals: dict[str, Any] = Field(default_factory=dict)
    requirement_matches: list[dict[str, Any]] = Field(default_factory=list)
    rejection_reasons: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str

    @classmethod
    def of(cls, record: models.ClipCandidateRecord) -> ClipCandidateOut:
        return cls(
            id=record.id,
            job_id=record.job_id,
            rank=record.rank,
            selected=record.selected,
            status=record.status,
            start_s=record.start_s,
            end_s=record.end_s,
            duration_s=record.duration_s,
            start_word=record.start_word,
            end_word=record.end_word,
            title=record.title,
            hook_text=record.hook_text,
            reason=record.reason,
            transcript_slice=record.transcript_slice,
            score=record.score,
            score_breakdown=record.score_breakdown or {},
            hook_signals=record.hook_signals or {},
            climax_signals=record.climax_signals or {},
            cta_signals=record.cta_signals or {},
            requirement_matches=record.requirement_matches or [],
            rejection_reasons=record.rejection_reasons or [],
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class ClipSpecificationOut(BaseModel):
    id: str
    job_id: str
    candidate_id: str
    source_id: str
    start_time: float
    end_time: float
    duration: float
    start_word: int
    end_word: int
    hook_start: float | None = None
    hook_end: float | None = None
    hook_type: str = ""
    climax_start: float | None = None
    climax_end: float | None = None
    cta_start: float | None = None
    cta_end: float | None = None
    boundary_adjustments: dict[str, Any] = Field(default_factory=dict)
    requirement_matches: list[dict[str, Any]] = Field(default_factory=list)
    quality_score: float
    quality_status: str
    rejection_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    final_rank: int = 0
    version: int = 1
    telemetry: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str

    @classmethod
    def of(cls, record: models.ClipSpecificationRecord) -> ClipSpecificationOut:
        return cls(
            id=record.id,
            job_id=record.job_id,
            candidate_id=record.candidate_id,
            source_id=record.source_id,
            start_time=record.start_time,
            end_time=record.end_time,
            duration=record.duration,
            start_word=record.start_word,
            end_word=record.end_word,
            hook_start=record.hook_start,
            hook_end=record.hook_end,
            hook_type=record.hook_type,
            climax_start=record.climax_start,
            climax_end=record.climax_end,
            cta_start=record.cta_start,
            cta_end=record.cta_end,
            boundary_adjustments=record.boundary_adjustments or {},
            requirement_matches=record.requirement_matches or [],
            quality_score=record.quality_score,
            quality_status=record.quality_status,
            rejection_reasons=record.rejection_reasons or [],
            warnings=record.warnings or [],
            final_rank=record.final_rank,
            version=record.version,
            telemetry=record.telemetry or {},
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class VisualCompositionOut(BaseModel):
    id: str
    clip_id: str
    job_id: str
    source_width: int
    source_height: int
    output_width: int
    output_height: int
    crop_strategy: str
    tracking_strategy: str
    tracking_confidence: float
    camera_movement_score: float
    smoothing_parameters: dict[str, Any] = Field(default_factory=dict)
    fallback_used: bool = False
    fallback_reason: str = ""
    quality_score: float
    quality_status: str
    warnings: list[str] = Field(default_factory=list)
    rejection_reasons: list[str] = Field(default_factory=list)
    version: int = 1
    telemetry: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str

    @classmethod
    def of(cls, record: models.VisualCompositionRecord) -> VisualCompositionOut:
        return cls(
            id=record.id,
            clip_id=record.clip_id,
            job_id=record.job_id,
            source_width=record.source_width,
            source_height=record.source_height,
            output_width=record.output_width,
            output_height=record.output_height,
            crop_strategy=record.crop_strategy,
            tracking_strategy=record.tracking_strategy,
            tracking_confidence=record.tracking_confidence,
            camera_movement_score=record.camera_movement_score,
            smoothing_parameters=record.smoothing_parameters or {},
            fallback_used=record.fallback_used,
            fallback_reason=record.fallback_reason,
            quality_score=record.quality_score,
            quality_status=record.quality_status,
            warnings=record.warnings or [],
            rejection_reasons=record.rejection_reasons or [],
            version=record.version,
            telemetry=record.telemetry or {},
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class RetentionOptimizationOut(BaseModel):
    id: str
    clip_id: str
    job_id: str
    retention_score: float
    final_score: float
    quality_status: str
    hook_strength: float
    speech_density_wps: float
    dead_air_percentage: float
    pacing_score: float
    narrative_score: float
    editing_decisions: dict[str, Any] = Field(default_factory=dict)
    visual_emphasis: list[dict[str, Any]] = Field(default_factory=list)
    scoring_breakdown: dict[str, Any] = Field(default_factory=dict)
    rejection_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    processing_time_s: float = 0.0
    version: int = 1
    telemetry: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str

    @classmethod
    def of(cls, record: models.RetentionOptimizationRecord) -> RetentionOptimizationOut:
        return cls(
            id=record.id,
            clip_id=record.clip_id,
            job_id=record.job_id,
            retention_score=record.retention_score,
            final_score=record.final_score,
            quality_status=record.quality_status,
            hook_strength=record.hook_strength,
            speech_density_wps=record.speech_density_wps,
            dead_air_percentage=record.dead_air_percentage,
            pacing_score=record.pacing_score,
            narrative_score=record.narrative_score,
            editing_decisions=record.editing_decisions or {},
            visual_emphasis=record.visual_emphasis or [],
            scoring_breakdown=record.scoring_breakdown or {},
            rejection_reasons=record.rejection_reasons or [],
            warnings=record.warnings or [],
            processing_time_s=record.processing_time_s,
            version=record.version,
            telemetry=record.telemetry or {},
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class CaptionOptimizationOut(BaseModel):
    id: str
    clip_id: str
    job_id: str
    style_key: str = "classic_professional"
    style_label: str = "Classic Professional"
    caption_segments: list[dict[str, Any]] = Field(default_factory=list)
    emphasis_metadata: dict[str, Any] = Field(default_factory=dict)
    hook_treatment: dict[str, Any] = Field(default_factory=dict)
    climax_treatment: dict[str, Any] = Field(default_factory=dict)
    cta_treatment: dict[str, Any] = Field(default_factory=dict)
    quality_score: float = 0.0
    quality_status: str = "CAPTION_PASS"
    rejection_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    fallback_used: bool = False
    fallback_reason: str = ""
    render_time_s: float = 0.0
    version: int = 1
    telemetry: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str

    @classmethod
    def of(cls, record: models.CaptionOptimizationRecord) -> CaptionOptimizationOut:
        return cls(
            id=record.id,
            clip_id=record.clip_id,
            job_id=record.job_id,
            style_key=record.style_key,
            style_label=record.style_label,
            caption_segments=record.caption_segments or [],
            emphasis_metadata=record.emphasis_metadata or {},
            hook_treatment=record.hook_treatment or {},
            climax_treatment=record.climax_treatment or {},
            cta_treatment=record.cta_treatment or {},
            quality_score=record.quality_score,
            quality_status=record.quality_status,
            rejection_reasons=record.rejection_reasons or [],
            warnings=record.warnings or [],
            fallback_used=record.fallback_used,
            fallback_reason=record.fallback_reason or "",
            render_time_s=record.render_time_s,
            version=record.version,
            telemetry=record.telemetry or {},
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class BGMAssetOut(BaseModel):
    id: str
    name: str
    genre: str = ""
    mood: str = ""
    tags: list[str] = Field(default_factory=list)
    mime_type: str = "audio/mpeg"
    duration_s: float = 0.0
    file_size_bytes: int = 0
    enabled: bool = True
    created_at: str
    updated_at: str

    @classmethod
    def of(cls, record: models.BGMAssetRecord) -> BGMAssetOut:
        return cls(
            id=record.id,
            name=record.name,
            genre=record.genre,
            mood=record.mood,
            tags=record.tags or [],
            mime_type=record.mime_type,
            duration_s=record.duration_s,
            file_size_bytes=record.file_size_bytes,
            enabled=record.enabled,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class BGMAssetUpdateIn(BaseModel):
    name: str | None = None
    genre: str | None = None
    mood: str | None = None
    tags: list[str] | str | None = None
    enabled: bool | None = None


class BGMMixOut(BaseModel):
    id: str
    clip_id: str
    job_id: str
    bgm_asset_id: str | None = None
    bgm_asset_name: str = ""
    bgm_applied: bool = False
    clip_duration_s: float = 0.0
    bgm_duration_s: float = 0.0
    loop_trim_decision: str = "none"
    ducking_applied: bool = False
    ducking_parameters: dict[str, Any] = Field(default_factory=dict)
    normalization_applied: bool = False
    integrated_lufs: float = 0.0
    true_peak_db: float = 0.0
    quality_score: float = 0.0
    quality_status: str = "MIX_PASS"
    warnings: list[str] = Field(default_factory=list)
    rejection_reasons: list[str] = Field(default_factory=list)
    processing_time_s: float = 0.0
    mixed_audio_path: str = ""
    telemetry: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str

    @classmethod
    def of(cls, record: models.BGMMixRecord) -> BGMMixOut:
        return cls(
            id=record.id,
            clip_id=record.clip_id,
            job_id=record.job_id,
            bgm_asset_id=record.bgm_asset_id,
            bgm_asset_name=record.bgm_asset_name,
            bgm_applied=record.bgm_applied,
            clip_duration_s=record.clip_duration_s,
            bgm_duration_s=record.bgm_duration_s,
            loop_trim_decision=record.loop_trim_decision,
            ducking_applied=record.ducking_applied,
            ducking_parameters=record.ducking_parameters or {},
            normalization_applied=record.normalization_applied,
            integrated_lufs=record.integrated_lufs,
            true_peak_db=record.true_peak_db,
            quality_score=record.quality_score,
            quality_status=record.quality_status,
            warnings=record.warnings or [],
            rejection_reasons=record.rejection_reasons or [],
            processing_time_s=record.processing_time_s,
            mixed_audio_path=record.mixed_audio_path,
            telemetry=record.telemetry or {},
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class FinalRenderOut(BaseModel):
    id: str
    job_id: str
    clip_id: str
    output_path: str
    package_dir: str = ""
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    video_codec: str = ""
    audio_codec: str = ""
    caption_style: str = ""
    bgm_asset_id: str | None = None
    quality_score: float = 0.0
    quality_status: str = "RENDER_PASS"
    render_status: str = "completed"
    render_attempt: int = 1
    error_details: list[str] = Field(default_factory=list)
    telemetry: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str

    @classmethod
    def of(cls, record: models.FinalRenderRecord) -> FinalRenderOut:
        return cls(
            id=record.id,
            job_id=record.job_id,
            clip_id=record.clip_id,
            output_path=record.output_path,
            package_dir=record.package_dir,
            duration=record.duration,
            width=record.width,
            height=record.height,
            fps=record.fps,
            video_codec=record.video_codec,
            audio_codec=record.audio_codec,
            caption_style=record.caption_style,
            bgm_asset_id=record.bgm_asset_id,
            quality_score=record.quality_score,
            quality_status=record.quality_status,
            render_status=record.render_status,
            render_attempt=record.render_attempt,
            error_details=record.error_details or [],
            telemetry=record.telemetry or {},
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class ClipMetadataOut(BaseModel):
    id: str
    job_id: str
    clip_id: str
    generated_title: str
    final_title: str
    generated_description: str
    final_description: str
    generated_hashtags: list[str] = Field(default_factory=list)
    final_hashtags: list[str] = Field(default_factory=list)
    generated_mentions: list[str] = Field(default_factory=list)
    final_mentions: list[str] = Field(default_factory=list)
    generated_cta: str = ""
    final_cta: str = ""
    campaign_requirements_matched: dict[str, Any] = Field(default_factory=dict)
    compliance_status: str = "SEO_PASS"
    compliance_score: float = 100.0
    validation_errors: list[str] = Field(default_factory=list)
    validation_warnings: list[str] = Field(default_factory=list)
    version: int = 1
    is_publish_ready: bool = True
    telemetry: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str

    @classmethod
    def of(cls, record: models.ClipMetadataRecord) -> ClipMetadataOut:
        return cls(
            id=record.id,
            job_id=record.job_id,
            clip_id=record.clip_id,
            generated_title=record.generated_title,
            final_title=record.final_title,
            generated_description=record.generated_description,
            final_description=record.final_description,
            generated_hashtags=record.generated_hashtags,
            final_hashtags=record.final_hashtags,
            generated_mentions=record.generated_mentions,
            final_mentions=record.final_mentions,
            generated_cta=record.generated_cta,
            final_cta=record.final_cta,
            campaign_requirements_matched=record.campaign_requirements_matched,
            compliance_status=record.compliance_status,
            compliance_score=record.compliance_score,
            validation_errors=record.validation_errors,
            validation_warnings=record.validation_warnings,
            version=record.version,
            is_publish_ready=record.is_publish_ready,
            telemetry=record.telemetry or {},
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class ClipMetadataUpdateIn(BaseModel):
    final_title: str | None = None
    final_description: str | None = None
    final_hashtags: list[str] | None = None
    final_mentions: list[str] | None = None
    final_cta: str | None = None
    action: str | None = None  # e.g., "reset_to_generated"


# ---------------------------------------------------------------------------
# Step 24: Clip Approval
# ---------------------------------------------------------------------------


class ClipApprovalHistoryEntry(BaseModel):
    from_status: str
    to_status: str
    operator_action: str
    operator_note: str = ""
    actor: str = "operator"
    version: int
    timestamp: str


class ClipApprovalOut(BaseModel):
    id: str
    job_id: str
    clip_id: str
    current_status: str
    operator_action: str | None = None
    operator_note: str = ""
    version: int = 1
    previous_status: str | None = None
    publish_eligible: bool = False
    blocking_reasons: list[str] = Field(default_factory=list)
    is_approved_for_publishing: bool = False
    history: list[dict[str, Any]] = Field(default_factory=list)
    telemetry: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str

    @classmethod
    def of(cls, record: models.ClipApprovalRecord) -> "ClipApprovalOut":
        return cls(
            id=record.id,
            job_id=record.job_id,
            clip_id=record.clip_id,
            current_status=record.current_status,
            operator_action=record.operator_action,
            operator_note=record.operator_note,
            version=record.version,
            previous_status=record.previous_status,
            publish_eligible=record.publish_eligible,
            blocking_reasons=record.blocking_reasons or [],
            is_approved_for_publishing=record.is_approved_for_publishing,
            history=record.history or [],
            telemetry=record.telemetry or {},
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class ClipApprovalActionIn(BaseModel):
    """Operator approval action payload.

    ``action`` must be one of: APPROVE, REJECT, REQUEST_CHANGES.
    ``operator_note`` is required for REJECT and REQUEST_CHANGES.
    ``expected_version`` enables optimistic concurrency — submit the current
    version you saw; the server rejects if a newer version exists.
    """
    action: str  # APPROVE | REJECT | REQUEST_CHANGES
    operator_note: str = ""
    expected_version: int | None = None


class ClipApprovalTelemetryOut(BaseModel):
    """Summary telemetry for Step 24 progress card."""
    total_eligible: int = 0
    pending: int = 0
    approved: int = 0
    rejected: int = 0
    changes_requested: int = 0
    publishing_locked: int = 0
    publish_ready: int = 0







