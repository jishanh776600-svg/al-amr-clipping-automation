"""Row models for the tables in :mod:`autoclip.db.schema`.

Plain dataclasses rather than Pydantic models: these mirror SQLite rows and are
constructed from trusted database output, so validation would be dead weight.
The API layer converts them to Pydantic response models at the boundary.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

SourceType = Literal["youtube", "upload"]
JobStatus = Literal[
    "queued",
    "dispatching",
    "running",
    "processing",
    "uploading",
    "publishing",
    "done",
    "failed",
    "cancel_requested",
    "cancelled",
]
ClipStatus = Literal["candidate", "kept", "discarded", "exported"]


def new_id() -> str:
    """Generate a short, filesystem-safe, collision-resistant identifier."""
    return uuid.uuid4().hex[:16]


def utcnow() -> str:
    """Current UTC time as an ISO-8601 string — the storage format for all timestamps."""
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Source:
    id: str
    type: SourceType
    path: str
    title: str = ""
    url: str | None = None
    filename: str | None = None
    channel: str | None = None
    duration_s: float = 0.0
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    has_audio: bool = True
    has_video: bool = True
    created_at: str = field(default_factory=utcnow)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Source:
        return cls(
            id=row["id"],
            type=row["type"],
            path=row["path"],
            title=row["title"],
            url=row["url"],
            filename=row["filename"],
            channel=row["channel"],
            duration_s=row["duration_s"],
            width=row["width"],
            height=row["height"],
            fps=row["fps"],
            has_audio=bool(row["has_audio"]),
            has_video=bool(row["has_video"]),
            created_at=row["created_at"],
        )


@dataclass
class Job:
    id: str
    source_id: str
    status: JobStatus = "queued"
    current_stage: str = ""
    progress: float = 0.0
    error: str | None = None
    provider: str = ""
    settings: dict[str, Any] = field(default_factory=dict)
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
    campaign_spec_id: str | None = None
    finished_at: str | None = None
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Job:
        keys = row.keys()
        return cls(
            id=row["id"],
            source_id=row["source_id"],
            status=row["status"],
            current_stage=row["current_stage"],
            progress=row["progress"],
            error=row["error"],
            provider=row["provider"],
            settings=json.loads(row["settings_json"] or "{}"),
            dispatch_mode=row["dispatch_mode"] if "dispatch_mode" in keys else "local",
            github_run_id=row["github_run_id"] if "github_run_id" in keys else None,
            attempt=row["attempt"] if "attempt" in keys else 1,
            max_attempts=row["max_attempts"] if "max_attempts" in keys else 3,
            last_heartbeat_at=row["last_heartbeat_at"] if "last_heartbeat_at" in keys else None,
            stale_at=row["stale_at"] if "stale_at" in keys else None,
            github_workflow=row["github_workflow"] if "github_workflow" in keys else None,
            github_job_id=row["github_job_id"] if "github_job_id" in keys else None,
            github_run_url=row["github_run_url"] if "github_run_url" in keys else None,
            github_run_status=row["github_run_status"] if "github_run_status" in keys else None,
            github_conclusion=row["github_conclusion"] if "github_conclusion" in keys else None,
            dispatched_at=row["dispatched_at"] if "dispatched_at" in keys else None,
            started_at=row["started_at"] if "started_at" in keys else None,
            completed_at=row["completed_at"] if "completed_at" in keys else None,
            failed_at=row["failed_at"] if "failed_at" in keys else None,
            cancelled_at=row["cancelled_at"] if "cancelled_at" in keys else None,
            cancel_requested_at=row["cancel_requested_at"] if "cancel_requested_at" in keys else None,
            campaign_spec_id=row["campaign_spec_id"] if "campaign_spec_id" in keys else None,
            finished_at=row["finished_at"] if "finished_at" in keys else None,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass
class Transcript:
    job_id: str
    json_path: str
    language: str = ""
    model: str = ""
    has_diarization: bool = False
    word_count: int = 0
    #: "whisper" or "youtube" — which path produced this transcript.
    source: str = "whisper"
    created_at: str = field(default_factory=utcnow)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Transcript:
        return cls(
            job_id=row["job_id"],
            json_path=row["json_path"],
            language=row["language"],
            model=row["model"],
            has_diarization=bool(row["has_diarization"]),
            word_count=row["word_count"],
            source=row["source"],
            created_at=row["created_at"],
        )


@dataclass
class Clip:
    id: str
    job_id: str
    start_s: float
    end_s: float
    rank: int = 0
    start_word: int = 0
    end_word: int = 0
    title: str = ""
    hook: str = ""
    score: int = 0
    reason: str = ""
    status: ClipStatus = "candidate"
    user_trimmed: bool = False
    created_at: str = field(default_factory=utcnow)

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Clip:
        return cls(
            id=row["id"],
            job_id=row["job_id"],
            start_s=row["start_s"],
            end_s=row["end_s"],
            rank=row["rank"],
            start_word=row["start_word"],
            end_word=row["end_word"],
            title=row["title"],
            hook=row["hook"],
            score=row["score"],
            reason=row["reason"],
            status=row["status"],
            user_trimmed=bool(row["user_trimmed"]),
            created_at=row["created_at"],
        )


@dataclass
class ClipEdit:
    clip_id: str
    edited_words: list[dict[str, Any]] | None = None
    caption_style: str = "bold_pop"
    ratio: str = "9:16"
    updated_at: str = field(default_factory=utcnow)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> ClipEdit:
        raw = row["edited_words_json"]
        return cls(
            clip_id=row["clip_id"],
            edited_words=json.loads(raw) if raw else None,
            caption_style=row["caption_style"],
            ratio=row["ratio"],
            updated_at=row["updated_at"],
        )


@dataclass
class Export:
    id: str
    clip_id: str
    path: str
    ratio: str = "9:16"
    style: str = "bold_pop"
    size_bytes: int = 0
    drive_file_id: str | None = None
    drive_web_view_link: str | None = None
    drive_storage_key: str | None = None
    created_at: str = field(default_factory=utcnow)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Export:
        keys = row.keys()
        return cls(
            id=row["id"],
            clip_id=row["clip_id"],
            path=row["path"],
            ratio=row["ratio"],
            style=row["style"],
            size_bytes=row["size_bytes"],
            drive_file_id=row["drive_file_id"] if "drive_file_id" in keys else None,
            drive_web_view_link=row["drive_web_view_link"] if "drive_web_view_link" in keys else None,
            drive_storage_key=row["drive_storage_key"] if "drive_storage_key" in keys else None,
            created_at=row["created_at"],
        )


@dataclass
class CampaignEvaluationRow:
    clip_id: str
    campaign_id: str = ""
    approved: bool = True
    final_score: float = 0.0
    hook_score: float = 0.0
    cta_score: float = 0.0
    viral_score: float = 0.0
    density_score: float = 0.0
    hard_failures: list[str] = field(default_factory=list)
    soft_warnings: list[str] = field(default_factory=list)
    rule_results: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utcnow)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> CampaignEvaluationRow:
        return cls(
            clip_id=row["clip_id"],
            campaign_id=row["campaign_id"],
            approved=bool(row["approved"]),
            final_score=row["final_score"],
            hook_score=row["hook_score"],
            cta_score=row["cta_score"],
            viral_score=row["viral_score"],
            density_score=row["density_score"],
            hard_failures=json.loads(row["hard_failures"] or "[]"),
            soft_warnings=json.loads(row["soft_warnings"] or "[]"),
            rule_results=json.loads(row["rule_results"] or "{}"),
            created_at=row["created_at"],
        )


@dataclass
class CampaignPreset:
    id: str
    name: str
    brief: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> CampaignPreset:
        return cls(
            id=row["id"],
            name=row["name"],
            brief=json.loads(row["brief_json"] or "{}"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass
class PublishingRecord:
    id: str
    export_id: str
    job_id: str
    platform: str
    status: str  # "pending", "publishing", "published", "failed"
    external_id: str | None = None
    destination: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> PublishingRecord:
        return cls(
            id=row["id"],
            export_id=row["export_id"],
            job_id=row["job_id"],
            platform=row["platform"],
            status=row["status"],
            external_id=row["external_id"],
            destination=row["destination"] or "",
            metadata=json.loads(row["metadata_json"] or "{}"),
            error=row["error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass
class CampaignGuideline:
    id: str
    job_id: str | None
    filename: str
    mime_type: str
    size_bytes: int
    storage_path: str
    extracted_text: str = ""
    parsed_brief: dict[str, Any] = field(default_factory=dict)
    status: str = "extracted"  # "extracted", "failed"
    error: str | None = None
    created_at: str = field(default_factory=utcnow)
    source_type: str = "upload_pdf"  # "upload_pdf", "upload_docx", "google_drive"
    drive_file_id: str | None = None
    sha256: str | None = None
    word_count: int = 0
    char_count: int = 0

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> CampaignGuideline:
        keys = row.keys()
        return cls(
            id=row["id"],
            job_id=row["job_id"],
            filename=row["filename"],
            mime_type=row["mime_type"],
            size_bytes=row["size_bytes"],
            storage_path=row["storage_path"],
            extracted_text=row["extracted_text"],
            parsed_brief=json.loads(row["parsed_brief"] or "{}"),
            status=row["status"],
            error=row["error"],
            created_at=row["created_at"],
            source_type=row["source_type"] if "source_type" in keys else "upload_pdf",
            drive_file_id=row["drive_file_id"] if "drive_file_id" in keys else None,
            sha256=row["sha256"] if "sha256" in keys else None,
            word_count=row["word_count"] if "word_count" in keys else 0,
            char_count=row["char_count"] if "char_count" in keys else 0,
        )


@dataclass
class CampaignSpecificationRecord:
    id: str
    job_id: str | None
    title: str
    spec: dict[str, Any]
    has_conflicts: bool = False
    conflict_count: int = 0
    document_count: int = 0
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> CampaignSpecificationRecord:
        keys = row.keys()
        return cls(
            id=row["id"],
            job_id=row["job_id"],
            title=row["title"],
            spec=json.loads(row["spec_json"] or "{}"),
            has_conflicts=bool(row["has_conflicts"]),
            conflict_count=row["conflict_count"] if "conflict_count" in keys else 0,
            document_count=row["document_count"] if "document_count" in keys else 0,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass
class ClipCandidateRecord:
    id: str
    job_id: str
    rank: int = 0
    selected: bool = False
    status: str = "discovered"  # discovered, scored, selected, rejected
    start_s: float = 0.0
    end_s: float = 0.0
    duration_s: float = 0.0
    start_word: int = 0
    end_word: int = 0
    title: str = ""
    hook_text: str = ""
    reason: str = ""
    transcript_slice: str = ""
    score: float = 0.0
    score_breakdown: dict[str, Any] = field(default_factory=dict)
    hook_signals: dict[str, Any] = field(default_factory=dict)
    climax_signals: dict[str, Any] = field(default_factory=dict)
    cta_signals: dict[str, Any] = field(default_factory=dict)
    requirement_matches: list[dict[str, Any]] = field(default_factory=list)
    rejection_reasons: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> ClipCandidateRecord:
        keys = row.keys()
        return cls(
            id=row["id"],
            job_id=row["job_id"],
            rank=row["rank"] if "rank" in keys else 0,
            selected=bool(row["selected"]) if "selected" in keys else False,
            status=row["status"] if "status" in keys else "discovered",
            start_s=float(row["start_s"]),
            end_s=float(row["end_s"]),
            duration_s=float(row["duration_s"]),
            start_word=row["start_word"] if "start_word" in keys else 0,
            end_word=row["end_word"] if "end_word" in keys else 0,
            title=row["title"] or "",
            hook_text=row["hook_text"] or "",
            reason=row["reason"] or "",
            transcript_slice=row["transcript_slice"] or "",
            score=float(row["score"] or 0.0),
            score_breakdown=json.loads(row["score_breakdown"] or "{}") if "score_breakdown" in keys else {},
            hook_signals=json.loads(row["hook_signals"] or "{}") if "hook_signals" in keys else {},
            climax_signals=json.loads(row["climax_signals"] or "{}") if "climax_signals" in keys else {},
            cta_signals=json.loads(row["cta_signals"] or "{}") if "cta_signals" in keys else {},
            requirement_matches=json.loads(row["requirement_matches"] or "[]") if "requirement_matches" in keys else [],
            rejection_reasons=json.loads(row["rejection_reasons"] or "[]") if "rejection_reasons" in keys else [],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass
class ClipSpecificationRecord:
    id: str
    job_id: str
    candidate_id: str
    source_id: str
    start_time: float
    end_time: float
    duration: float
    start_word: int = 0
    end_word: int = 0
    hook_start: float | None = None
    hook_end: float | None = None
    hook_type: str = ""
    climax_start: float | None = None
    climax_end: float | None = None
    cta_start: float | None = None
    cta_end: float | None = None
    boundary_adjustments: dict[str, Any] = field(default_factory=dict)
    requirement_matches: list[dict[str, Any]] = field(default_factory=list)
    quality_score: float = 0.0
    quality_status: str = "QUALITY_PASS"  # QUALITY_PASS, QUALITY_WARN, QUALITY_REJECT
    rejection_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    final_rank: int = 0
    version: int = 1
    telemetry: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)

    @property
    def is_approved(self) -> bool:
        return self.quality_status in ("QUALITY_PASS", "QUALITY_WARN")

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> ClipSpecificationRecord:
        keys = row.keys()
        return cls(
            id=row["id"],
            job_id=row["job_id"],
            candidate_id=row["candidate_id"],
            source_id=row["source_id"],
            start_time=float(row["start_time"]),
            end_time=float(row["end_time"]),
            duration=float(row["duration"]),
            start_word=row["start_word"] if "start_word" in keys else 0,
            end_word=row["end_word"] if "end_word" in keys else 0,
            hook_start=float(row["hook_start"]) if ("hook_start" in keys and row["hook_start"] is not None) else None,
            hook_end=float(row["hook_end"]) if ("hook_end" in keys and row["hook_end"] is not None) else None,
            hook_type=row["hook_type"] if "hook_type" in keys and row["hook_type"] else "",
            climax_start=float(row["climax_start"]) if ("climax_start" in keys and row["climax_start"] is not None) else None,
            climax_end=float(row["climax_end"]) if ("climax_end" in keys and row["climax_end"] is not None) else None,
            cta_start=float(row["cta_start"]) if ("cta_start" in keys and row["cta_start"] is not None) else None,
            cta_end=float(row["cta_end"]) if ("cta_end" in keys and row["cta_end"] is not None) else None,
            boundary_adjustments=json.loads(row["boundary_adjustments"] or "{}") if "boundary_adjustments" in keys else {},
            requirement_matches=json.loads(row["requirement_matches"] or "[]") if "requirement_matches" in keys else [],
            quality_score=float(row["quality_score"] or 0.0),
            quality_status=row["quality_status"] or "QUALITY_PASS",
            rejection_reasons=json.loads(row["rejection_reasons"] or "[]") if "rejection_reasons" in keys else [],
            warnings=json.loads(row["warnings"] or "[]") if "warnings" in keys else [],
            final_rank=row["final_rank"] if "final_rank" in keys else 0,
            version=row["version"] if "version" in keys else 1,
            telemetry=json.loads(row["telemetry"] or "{}") if "telemetry" in keys else {},
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass
class VisualCompositionRecord:
    id: str
    clip_id: str
    job_id: str
    source_width: int
    source_height: int
    output_width: int
    output_height: int
    crop_strategy: str = "track"
    tracking_strategy: str = "mediapipe"
    tracking_confidence: float = 0.0
    camera_movement_score: float = 0.0
    smoothing_parameters: dict[str, Any] = field(default_factory=dict)
    fallback_used: bool = False
    fallback_reason: str = ""
    quality_score: float = 0.0
    quality_status: str = "VISUAL_PASS"  # VISUAL_PASS, VISUAL_WARN, VISUAL_REJECT
    warnings: list[str] = field(default_factory=list)
    rejection_reasons: list[str] = field(default_factory=list)
    version: int = 1
    telemetry: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)

    @property
    def is_approved(self) -> bool:
        return self.quality_status in ("VISUAL_PASS", "VISUAL_WARN")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "clip_id": self.clip_id,
            "job_id": self.job_id,
            "source_width": self.source_width,
            "source_height": self.source_height,
            "output_width": self.output_width,
            "output_height": self.output_height,
            "crop_strategy": self.crop_strategy,
            "tracking_strategy": self.tracking_strategy,
            "tracking_confidence": self.tracking_confidence,
            "camera_movement_score": self.camera_movement_score,
            "smoothing_parameters": self.smoothing_parameters,
            "fallback_used": self.fallback_used,
            "fallback_reason": self.fallback_reason,
            "quality_score": self.quality_score,
            "quality_status": self.quality_status,
            "warnings": self.warnings,
            "rejection_reasons": self.rejection_reasons,
            "version": self.version,
            "telemetry": self.telemetry,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> VisualCompositionRecord:
        keys = row.keys()
        return cls(
            id=row["id"],
            clip_id=row["clip_id"],
            job_id=row["job_id"],
            source_width=int(row["source_width"]),
            source_height=int(row["source_height"]),
            output_width=int(row["output_width"]),
            output_height=int(row["output_height"]),
            crop_strategy=row["crop_strategy"] if "crop_strategy" in keys else "track",
            tracking_strategy=row["tracking_strategy"] if "tracking_strategy" in keys else "mediapipe",
            tracking_confidence=float(row["tracking_confidence"] or 0.0),
            camera_movement_score=float(row["camera_movement_score"] or 0.0),
            smoothing_parameters=json.loads(row["smoothing_parameters"] or "{}") if "smoothing_parameters" in keys else {},
            fallback_used=bool(row["fallback_used"]) if "fallback_used" in keys else False,
            fallback_reason=row["fallback_reason"] if "fallback_reason" in keys and row["fallback_reason"] else "",
            quality_score=float(row["quality_score"] or 0.0),
            quality_status=row["quality_status"] or "VISUAL_PASS",
            warnings=json.loads(row["warnings"] or "[]") if "warnings" in keys else [],
            rejection_reasons=json.loads(row["rejection_reasons"] or "[]") if "rejection_reasons" in keys else [],
            version=row["version"] if "version" in keys else 1,
            telemetry=json.loads(row["telemetry"] or "{}") if "telemetry" in keys else {},
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass
class RetentionOptimizationRecord:
    id: str
    clip_id: str
    job_id: str
    retention_score: float = 0.0
    final_score: float = 0.0
    quality_status: str = "FINAL_PASS"  # FINAL_PASS, FINAL_WARN, FINAL_REJECT
    hook_strength: float = 0.0
    speech_density_wps: float = 0.0
    dead_air_percentage: float = 0.0
    pacing_score: float = 0.0
    narrative_score: float = 0.0
    editing_decisions: dict[str, Any] = field(default_factory=dict)
    visual_emphasis: list[dict[str, Any]] = field(default_factory=list)
    scoring_breakdown: dict[str, Any] = field(default_factory=dict)
    rejection_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    processing_time_s: float = 0.0
    version: int = 1
    telemetry: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)

    @property
    def is_approved(self) -> bool:
        return self.quality_status in ("FINAL_PASS", "FINAL_WARN")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "clip_id": self.clip_id,
            "job_id": self.job_id,
            "retention_score": self.retention_score,
            "final_score": self.final_score,
            "quality_status": self.quality_status,
            "hook_strength": self.hook_strength,
            "speech_density_wps": self.speech_density_wps,
            "dead_air_percentage": self.dead_air_percentage,
            "pacing_score": self.pacing_score,
            "narrative_score": self.narrative_score,
            "editing_decisions": self.editing_decisions,
            "visual_emphasis": self.visual_emphasis,
            "scoring_breakdown": self.scoring_breakdown,
            "rejection_reasons": self.rejection_reasons,
            "warnings": self.warnings,
            "processing_time_s": self.processing_time_s,
            "version": self.version,
            "telemetry": self.telemetry,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> RetentionOptimizationRecord:
        keys = row.keys()
        return cls(
            id=row["id"],
            clip_id=row["clip_id"],
            job_id=row["job_id"],
            retention_score=float(row["retention_score"] or 0.0),
            final_score=float(row["final_score"] or 0.0),
            quality_status=row["quality_status"] or "FINAL_PASS",
            hook_strength=float(row["hook_strength"] or 0.0) if "hook_strength" in keys else 0.0,
            speech_density_wps=float(row["speech_density_wps"] or 0.0) if "speech_density_wps" in keys else 0.0,
            dead_air_percentage=float(row["dead_air_percentage"] or 0.0) if "dead_air_percentage" in keys else 0.0,
            pacing_score=float(row["pacing_score"] or 0.0) if "pacing_score" in keys else 0.0,
            narrative_score=float(row["narrative_score"] or 0.0) if "narrative_score" in keys else 0.0,
            editing_decisions=json.loads(row["editing_decisions"] or "{}") if "editing_decisions" in keys else {},
            visual_emphasis=json.loads(row["visual_emphasis"] or "[]") if "visual_emphasis" in keys else [],
            scoring_breakdown=json.loads(row["scoring_breakdown"] or "{}") if "scoring_breakdown" in keys else {},
            rejection_reasons=json.loads(row["rejection_reasons"] or "[]") if "rejection_reasons" in keys else [],
            warnings=json.loads(row["warnings"] or "[]") if "warnings" in keys else [],
            processing_time_s=float(row["processing_time_s"] or 0.0) if "processing_time_s" in keys else 0.0,
            version=int(row["version"]) if "version" in keys else 1,
            telemetry=json.loads(row["telemetry"] or "{}") if "telemetry" in keys else {},
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass
class CaptionOptimizationRecord:
    id: str
    clip_id: str
    job_id: str
    style_key: str = "classic_professional"
    style_label: str = "Classic Professional"
    caption_segments: list[dict[str, Any]] = field(default_factory=list)
    emphasis_metadata: dict[str, Any] = field(default_factory=dict)
    hook_treatment: dict[str, Any] = field(default_factory=dict)
    climax_treatment: dict[str, Any] = field(default_factory=dict)
    cta_treatment: dict[str, Any] = field(default_factory=dict)
    quality_score: float = 0.0
    quality_status: str = "CAPTION_PASS"  # CAPTION_PASS, CAPTION_WARN, CAPTION_REJECT
    rejection_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    fallback_used: bool = False
    fallback_reason: str = ""
    render_time_s: float = 0.0
    version: int = 1
    telemetry: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)

    @property
    def is_approved(self) -> bool:
        return self.quality_status in ("CAPTION_PASS", "CAPTION_WARN")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "clip_id": self.clip_id,
            "job_id": self.job_id,
            "style_key": self.style_key,
            "style_label": self.style_label,
            "caption_segments": self.caption_segments,
            "emphasis_metadata": self.emphasis_metadata,
            "hook_treatment": self.hook_treatment,
            "climax_treatment": self.climax_treatment,
            "cta_treatment": self.cta_treatment,
            "quality_score": self.quality_score,
            "quality_status": self.quality_status,
            "rejection_reasons": self.rejection_reasons,
            "warnings": self.warnings,
            "fallback_used": self.fallback_used,
            "fallback_reason": self.fallback_reason,
            "render_time_s": self.render_time_s,
            "version": self.version,
            "telemetry": self.telemetry,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> CaptionOptimizationRecord:
        keys = row.keys()
        return cls(
            id=row["id"],
            clip_id=row["clip_id"],
            job_id=row["job_id"],
            style_key=row["style_key"] if "style_key" in keys else "classic_professional",
            style_label=row["style_label"] if "style_label" in keys else "Classic Professional",
            caption_segments=json.loads(row["caption_segments"] or "[]") if "caption_segments" in keys else [],
            emphasis_metadata=json.loads(row["emphasis_metadata"] or "{}") if "emphasis_metadata" in keys else {},
            hook_treatment=json.loads(row["hook_treatment"] or "{}") if "hook_treatment" in keys else {},
            climax_treatment=json.loads(row["climax_treatment"] or "{}") if "climax_treatment" in keys else {},
            cta_treatment=json.loads(row["cta_treatment"] or "{}") if "cta_treatment" in keys else {},
            quality_score=float(row["quality_score"] or 0.0) if "quality_score" in keys else 0.0,
            quality_status=row["quality_status"] if "quality_status" in keys else "CAPTION_PASS",
            rejection_reasons=json.loads(row["rejection_reasons"] or "[]") if "rejection_reasons" in keys else [],
            warnings=json.loads(row["warnings"] or "[]") if "warnings" in keys else [],
            fallback_used=bool(row["fallback_used"]) if "fallback_used" in keys else False,
            fallback_reason=row["fallback_reason"] if "fallback_reason" in keys else "",
            render_time_s=float(row["render_time_s"] or 0.0) if "render_time_s" in keys else 0.0,
            version=int(row["version"]) if "version" in keys else 1,
            telemetry=json.loads(row["telemetry"] or "{}") if "telemetry" in keys else {},
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
