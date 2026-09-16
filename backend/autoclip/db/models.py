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


@dataclass
class BGMAssetRecord:
    id: str
    name: str
    file_path: str
    genre: str = ""
    mood: str = ""
    tags: list[str] = field(default_factory=list)
    mime_type: str = "audio/mpeg"
    duration_s: float = 0.0
    file_size_bytes: int = 0
    enabled: bool = True
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "file_path": self.file_path,
            "genre": self.genre,
            "mood": self.mood,
            "tags": self.tags,
            "mime_type": self.mime_type,
            "duration_s": self.duration_s,
            "file_size_bytes": self.file_size_bytes,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> BGMAssetRecord:
        keys = row.keys()
        return cls(
            id=row["id"],
            name=row["name"],
            file_path=row["file_path"],
            genre=row["genre"] if "genre" in keys else "",
            mood=row["mood"] if "mood" in keys else "",
            tags=json.loads(row["tags"] or "[]") if "tags" in keys else [],
            mime_type=row["mime_type"] if "mime_type" in keys else "audio/mpeg",
            duration_s=float(row["duration_s"] or 0.0) if "duration_s" in keys else 0.0,
            file_size_bytes=int(row["file_size_bytes"] or 0) if "file_size_bytes" in keys else 0,
            enabled=bool(row["enabled"]) if "enabled" in keys else True,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass
class BGMMixRecord:
    id: str
    clip_id: str
    job_id: str
    bgm_asset_id: str | None = None
    bgm_asset_name: str = ""
    bgm_applied: bool = False
    clip_duration_s: float = 0.0
    bgm_duration_s: float = 0.0
    loop_trim_decision: str = "none"  # "trim", "loop", "none"
    ducking_applied: bool = False
    ducking_parameters: dict[str, Any] = field(default_factory=dict)
    normalization_applied: bool = False
    integrated_lufs: float = 0.0
    true_peak_db: float = 0.0
    quality_score: float = 0.0
    quality_status: str = "MIX_PASS"  # "MIX_PASS", "MIX_WARN", "MIX_REJECT"
    warnings: list[str] = field(default_factory=list)
    rejection_reasons: list[str] = field(default_factory=list)
    processing_time_s: float = 0.0
    mixed_audio_path: str = ""
    telemetry: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)

    @property
    def is_approved(self) -> bool:
        return self.quality_status in ("MIX_PASS", "MIX_WARN")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "clip_id": self.clip_id,
            "job_id": self.job_id,
            "bgm_asset_id": self.bgm_asset_id,
            "bgm_asset_name": self.bgm_asset_name,
            "bgm_applied": self.bgm_applied,
            "clip_duration_s": self.clip_duration_s,
            "bgm_duration_s": self.bgm_duration_s,
            "loop_trim_decision": self.loop_trim_decision,
            "ducking_applied": self.ducking_applied,
            "ducking_parameters": self.ducking_parameters,
            "normalization_applied": self.normalization_applied,
            "integrated_lufs": self.integrated_lufs,
            "true_peak_db": self.true_peak_db,
            "quality_score": self.quality_score,
            "quality_status": self.quality_status,
            "warnings": self.warnings,
            "rejection_reasons": self.rejection_reasons,
            "processing_time_s": self.processing_time_s,
            "mixed_audio_path": self.mixed_audio_path,
            "telemetry": self.telemetry,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> BGMMixRecord:
        keys = row.keys()
        return cls(
            id=row["id"],
            clip_id=row["clip_id"],
            job_id=row["job_id"],
            bgm_asset_id=row["bgm_asset_id"] if "bgm_asset_id" in keys else None,
            bgm_asset_name=row["bgm_asset_name"] if "bgm_asset_name" in keys else "",
            bgm_applied=bool(row["bgm_applied"]) if "bgm_applied" in keys else False,
            clip_duration_s=float(row["clip_duration_s"] or 0.0) if "clip_duration_s" in keys else 0.0,
            bgm_duration_s=float(row["bgm_duration_s"] or 0.0) if "bgm_duration_s" in keys else 0.0,
            loop_trim_decision=row["loop_trim_decision"] if "loop_trim_decision" in keys else "none",
            ducking_applied=bool(row["ducking_applied"]) if "ducking_applied" in keys else False,
            ducking_parameters=json.loads(row["ducking_parameters"] or "{}") if "ducking_parameters" in keys else {},
            normalization_applied=bool(row["normalization_applied"]) if "normalization_applied" in keys else False,
            integrated_lufs=float(row["integrated_lufs"] or 0.0) if "integrated_lufs" in keys else 0.0,
            true_peak_db=float(row["true_peak_db"] or 0.0) if "true_peak_db" in keys else 0.0,
            quality_score=float(row["quality_score"] or 0.0) if "quality_score" in keys else 0.0,
            quality_status=row["quality_status"] if "quality_status" in keys else "MIX_PASS",
            warnings=json.loads(row["warnings"] or "[]") if "warnings" in keys else [],
            rejection_reasons=json.loads(row["rejection_reasons"] or "[]") if "rejection_reasons" in keys else [],
            processing_time_s=float(row["processing_time_s"] or 0.0) if "processing_time_s" in keys else 0.0,
            mixed_audio_path=row["mixed_audio_path"] if "mixed_audio_path" in keys else "",
            telemetry=json.loads(row["telemetry"] or "{}") if "telemetry" in keys else {},
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass
class FinalRenderRecord:
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
    quality_status: str = "RENDER_PASS"  # "RENDER_PASS", "RENDER_WARN", "RENDER_REJECT"
    render_status: str = "completed"  # "completed", "failed"
    render_attempt: int = 1
    error_details: list[str] = field(default_factory=list)
    telemetry: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)

    @property
    def is_approved(self) -> bool:
        return self.render_status == "completed" and self.quality_status in ("RENDER_PASS", "RENDER_WARN")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "job_id": self.job_id,
            "clip_id": self.clip_id,
            "output_path": self.output_path,
            "package_dir": self.package_dir,
            "duration": self.duration,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "video_codec": self.video_codec,
            "audio_codec": self.audio_codec,
            "caption_style": self.caption_style,
            "bgm_asset_id": self.bgm_asset_id,
            "quality_score": self.quality_score,
            "quality_status": self.quality_status,
            "render_status": self.render_status,
            "render_attempt": self.render_attempt,
            "error_details": self.error_details,
            "telemetry": self.telemetry,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> FinalRenderRecord:
        keys = row.keys()
        return cls(
            id=row["id"],
            job_id=row["job_id"],
            clip_id=row["clip_id"],
            output_path=row["output_path"],
            package_dir=row["package_dir"] if "package_dir" in keys else "",
            duration=float(row["duration"] or 0.0) if "duration" in keys else 0.0,
            width=int(row["width"] or 0) if "width" in keys else 0,
            height=int(row["height"] or 0) if "height" in keys else 0,
            fps=float(row["fps"] or 0.0) if "fps" in keys else 0.0,
            video_codec=row["video_codec"] if "video_codec" in keys else "",
            audio_codec=row["audio_codec"] if "audio_codec" in keys else "",
            caption_style=row["caption_style"] if "caption_style" in keys else "",
            bgm_asset_id=row["bgm_asset_id"] if "bgm_asset_id" in keys else None,
            quality_score=float(row["quality_score"] or 0.0) if "quality_score" in keys else 0.0,
            quality_status=row["quality_status"] if "quality_status" in keys else "RENDER_PASS",
            render_status=row["render_status"] if "render_status" in keys else "completed",
            render_attempt=int(row["render_attempt"] or 1) if "render_attempt" in keys else 1,
            error_details=json.loads(row["error_details"] or "[]") if "error_details" in keys else [],
            telemetry=json.loads(row["telemetry"] or "{}") if "telemetry" in keys else {},
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass
class ClipMetadataRecord:
    id: str
    job_id: str
    clip_id: str
    generated_title: str
    final_title: str
    generated_description: str
    final_description: str
    generated_hashtags: list[str] = field(default_factory=list)
    final_hashtags: list[str] = field(default_factory=list)
    generated_mentions: list[str] = field(default_factory=list)
    final_mentions: list[str] = field(default_factory=list)
    generated_cta: str = ""
    final_cta: str = ""
    campaign_requirements_matched: dict[str, Any] = field(default_factory=dict)
    compliance_status: str = "SEO_PASS"
    compliance_score: float = 100.0
    validation_errors: list[str] = field(default_factory=list)
    validation_warnings: list[str] = field(default_factory=list)
    version: int = 1
    telemetry: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)

    @property
    def is_publish_ready(self) -> bool:
        return self.compliance_status in ("SEO_PASS", "SEO_WARN") and len(self.validation_errors) == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "job_id": self.job_id,
            "clip_id": self.clip_id,
            "generated_title": self.generated_title,
            "final_title": self.final_title,
            "generated_description": self.generated_description,
            "final_description": self.final_description,
            "generated_hashtags": self.generated_hashtags,
            "final_hashtags": self.final_hashtags,
            "generated_mentions": self.generated_mentions,
            "final_mentions": self.final_mentions,
            "generated_cta": self.generated_cta,
            "final_cta": self.final_cta,
            "campaign_requirements_matched": self.campaign_requirements_matched,
            "compliance_status": self.compliance_status,
            "compliance_score": self.compliance_score,
            "validation_errors": self.validation_errors,
            "validation_warnings": self.validation_warnings,
            "version": self.version,
            "is_publish_ready": self.is_publish_ready,
            "telemetry": self.telemetry,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> ClipMetadataRecord:
        keys = row.keys()
        return cls(
            id=row["id"],
            job_id=row["job_id"],
            clip_id=row["clip_id"],
            generated_title=row["generated_title"] or "",
            final_title=row["final_title"] or "",
            generated_description=row["generated_description"] or "",
            final_description=row["final_description"] or "",
            generated_hashtags=json.loads(row["generated_hashtags"] or "[]") if "generated_hashtags" in keys else [],
            final_hashtags=json.loads(row["final_hashtags"] or "[]") if "final_hashtags" in keys else [],
            generated_mentions=json.loads(row["generated_mentions"] or "[]") if "generated_mentions" in keys else [],
            final_mentions=json.loads(row["final_mentions"] or "[]") if "final_mentions" in keys else [],
            generated_cta=row["generated_cta"] or "" if "generated_cta" in keys else "",
            final_cta=row["final_cta"] or "" if "final_cta" in keys else "",
            campaign_requirements_matched=json.loads(row["campaign_requirements_matched"] or "{}") if "campaign_requirements_matched" in keys else {},
            compliance_status=row["compliance_status"] if "compliance_status" in keys else "SEO_PASS",
            compliance_score=float(row["compliance_score"] or 100.0) if "compliance_score" in keys else 100.0,
            validation_errors=json.loads(row["validation_errors"] or "[]") if "validation_errors" in keys else [],
            validation_warnings=json.loads(row["validation_warnings"] or "[]") if "validation_warnings" in keys else [],
            version=int(row["version"] or 1) if "version" in keys else 1,
            telemetry=json.loads(row["telemetry"] or "{}") if "telemetry" in keys else {},
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


# ---------------------------------------------------------------------------
# Step 24: Clip Approval Record
# ---------------------------------------------------------------------------

ApprovalStatus = Literal[
    "PENDING_REVIEW",
    "APPROVED",
    "REJECTED",
    "CHANGES_REQUESTED",
    "PUBLISHING_LOCKED",
]

# Valid deterministic state transitions
_VALID_TRANSITIONS: dict[str, set[str]] = {
    "PENDING_REVIEW": {"APPROVED", "REJECTED", "CHANGES_REQUESTED", "PUBLISHING_LOCKED"},
    "APPROVED": {"REJECTED", "CHANGES_REQUESTED", "PUBLISHING_LOCKED"},
    "REJECTED": {"PENDING_REVIEW", "APPROVED", "CHANGES_REQUESTED"},
    "CHANGES_REQUESTED": {"PENDING_REVIEW", "APPROVED", "REJECTED"},
    "PUBLISHING_LOCKED": {"APPROVED"},  # can only unlock to approved
}


@dataclass
class ClipApprovalRecord:
    """Operator approval state for a single clip.

    Persists full immutable audit trail in ``history``. Optimistic concurrency
    is enforced via ``version`` — a stale update (incoming version < current)
    is rejected deterministically.
    """

    id: str
    job_id: str
    clip_id: str
    current_status: ApprovalStatus = "PENDING_REVIEW"
    operator_action: str | None = None
    operator_note: str = ""
    version: int = 1
    previous_status: str | None = None
    publish_eligible: bool = False
    blocking_reasons: list[str] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)
    telemetry: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)

    # ------------------------------------------------------------------
    # Business logic
    # ------------------------------------------------------------------

    @property
    def is_approved_for_publishing(self) -> bool:
        """True only when the operator explicitly set APPROVED and there are
        no blocking quality-gate reasons preventing publication."""
        return self.current_status == "APPROVED" and self.publish_eligible

    def can_transition_to(self, new_status: str) -> bool:
        """Check if transition from current_status → new_status is valid."""
        allowed = _VALID_TRANSITIONS.get(self.current_status, set())
        return new_status in allowed

    def apply_action(
        self,
        new_status: ApprovalStatus,
        operator_action: str,
        operator_note: str = "",
        actor: str = "operator",
    ) -> None:
        """Mutate approval state deterministically, recording audit entry."""
        if not self.can_transition_to(new_status):
            raise ValueError(
                f"Invalid transition: {self.current_status} → {new_status}"
            )
        old_status = self.current_status
        self.previous_status = old_status
        self.current_status = new_status
        self.operator_action = operator_action
        self.operator_note = operator_note
        self.version += 1
        self.updated_at = utcnow()

        self.history.append({
            "from_status": old_status,
            "to_status": new_status,
            "operator_action": operator_action,
            "operator_note": operator_note,
            "actor": actor,
            "version": self.version,
            "timestamp": self.updated_at,
        })

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "job_id": self.job_id,
            "clip_id": self.clip_id,
            "current_status": self.current_status,
            "operator_action": self.operator_action,
            "operator_note": self.operator_note,
            "version": self.version,
            "previous_status": self.previous_status,
            "publish_eligible": self.publish_eligible,
            "blocking_reasons": self.blocking_reasons,
            "history": self.history,
            "telemetry": self.telemetry,
            "is_approved_for_publishing": self.is_approved_for_publishing,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "ClipApprovalRecord":
        keys = row.keys()
        return cls(
            id=row["id"],
            job_id=row["job_id"],
            clip_id=row["clip_id"],
            current_status=row["current_status"] if "current_status" in keys else "PENDING_REVIEW",
            operator_action=row["operator_action"] if "operator_action" in keys else None,
            operator_note=row["operator_note"] or "" if "operator_note" in keys else "",
            version=int(row["version"] or 1) if "version" in keys else 1,
            previous_status=row["previous_status"] if "previous_status" in keys else None,
            publish_eligible=bool(row["publish_eligible"]) if "publish_eligible" in keys else False,
            blocking_reasons=json.loads(row["blocking_reasons"] or "[]") if "blocking_reasons" in keys else [],
            history=json.loads(row["history"] or "[]") if "history" in keys else [],
            telemetry=json.loads(row["telemetry"] or "{}") if "telemetry" in keys else {},
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


# ---------------------------------------------------------------------------
# Step 25: Remote Publication Record
# ---------------------------------------------------------------------------

PublicationStatus = Literal[
    "PENDING",
    "UPLOADING",
    "PUBLISHED",
    "FAILED_RETRYABLE",
    "FAILED_PERMANENT",
    "SKIPPED",
    "CANCELLED",
]


@dataclass
class PublicationRecord:
    """Represents a discrete publication attempt of a clip to a specific platform/destination.

    Includes full idempotency tracking, retry metrics, remote identifiers,
    and sanitized response metadata.
    """

    id: str
    job_id: str
    clip_id: str
    platform: str
    idempotency_key: str
    final_render_id: str | None = None
    account_id: str = ""
    destination_id: str = ""
    status: PublicationStatus = "PENDING"
    attempt_number: int = 1
    remote_media_id: str | None = None
    remote_post_id: str | None = None
    permalink: str | None = None
    upload_started_at: str | None = None
    upload_completed_at: str | None = None
    published_at: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    response_metadata: dict[str, Any] = field(default_factory=dict)
    retry_count: int = 0
    version: int = 1
    telemetry: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)

    @property
    def is_published(self) -> bool:
        return self.status == "PUBLISHED"

    @property
    def is_retryable(self) -> bool:
        return self.status == "FAILED_RETRYABLE"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "job_id": self.job_id,
            "clip_id": self.clip_id,
            "final_render_id": self.final_render_id,
            "platform": self.platform,
            "account_id": self.account_id,
            "destination_id": self.destination_id,
            "status": self.status,
            "attempt_number": self.attempt_number,
            "idempotency_key": self.idempotency_key,
            "remote_media_id": self.remote_media_id,
            "remote_post_id": self.remote_post_id,
            "permalink": self.permalink,
            "upload_started_at": self.upload_started_at,
            "upload_completed_at": self.upload_completed_at,
            "published_at": self.published_at,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "response_metadata": self.response_metadata,
            "retry_count": self.retry_count,
            "version": self.version,
            "telemetry": self.telemetry,
            "is_published": self.is_published,
            "is_retryable": self.is_retryable,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "PublicationRecord":
        keys = row.keys()
        return cls(
            id=row["id"],
            job_id=row["job_id"],
            clip_id=row["clip_id"],
            final_render_id=row["final_render_id"] if "final_render_id" in keys else None,
            platform=row["platform"],
            account_id=row["account_id"] or "" if "account_id" in keys else "",
            destination_id=row["destination_id"] or "" if "destination_id" in keys else "",
            status=row["status"] if "status" in keys else "PENDING",
            attempt_number=int(row["attempt_number"] or 1) if "attempt_number" in keys else 1,
            idempotency_key=row["idempotency_key"],
            remote_media_id=row["remote_media_id"] if "remote_media_id" in keys else None,
            remote_post_id=row["remote_post_id"] if "remote_post_id" in keys else None,
            permalink=row["permalink"] if "permalink" in keys else None,
            upload_started_at=row["upload_started_at"] if "upload_started_at" in keys else None,
            upload_completed_at=row["upload_completed_at"] if "upload_completed_at" in keys else None,
            published_at=row["published_at"] if "published_at" in keys else None,
            error_code=row["error_code"] if "error_code" in keys else None,
            error_message=row["error_message"] if "error_message" in keys else None,
            response_metadata=json.loads(row["response_metadata"] or "{}") if "response_metadata" in keys else {},
            retry_count=int(row["retry_count"] or 0) if "retry_count" in keys else 0,
            version=int(row["version"] or 1) if "version" in keys else 1,
            telemetry=json.loads(row["telemetry"] or "{}") if "telemetry" in keys else {},
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


# ---------------------------------------------------------------------------
# Step 26: Publishing Destinations & Queue Records
# ---------------------------------------------------------------------------


@dataclass
class DestinationRecord:
    """Normalized multi-account publishing destination (YouTube, Instagram, Telegram).

    Contains account identifier, routing rules, rate limits, and non-secret config.
    NEVER contains tokens, secrets, or passwords.
    """

    id: str
    platform: str  # youtube | instagram | telegram
    display_name: str
    account_identifier: str = ""  # channel handle, page username, or chat id
    enabled: bool = True
    priority: int = 0
    config_metadata: dict[str, Any] = field(default_factory=dict)
    daily_limit: int = 10
    spacing_seconds: int = 3600
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "platform": self.platform,
            "display_name": self.display_name,
            "account_identifier": self.account_identifier,
            "enabled": self.enabled,
            "priority": self.priority,
            "config_metadata": self.config_metadata,
            "daily_limit": self.daily_limit,
            "spacing_seconds": self.spacing_seconds,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "DestinationRecord":
        keys = row.keys()
        return cls(
            id=row["id"],
            platform=row["platform"],
            display_name=row["display_name"],
            account_identifier=row["account_identifier"] if "account_identifier" in keys else "",
            enabled=bool(row["enabled"]) if "enabled" in keys else True,
            priority=int(row["priority"] or 0) if "priority" in keys else 0,
            config_metadata=json.loads(row["config_metadata"] or "{}") if "config_metadata" in keys else {},
            daily_limit=int(row["daily_limit"] or 10) if "daily_limit" in keys else 10,
            spacing_seconds=int(row["spacing_seconds"] or 3600) if "spacing_seconds" in keys else 3600,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


QueueStatus = Literal[
    "QUEUED",
    "SCHEDULED",
    "CLAIMED",
    "PUBLISHING",
    "PUBLISHED",
    "FAILED_RETRYABLE",
    "FAILED_PERMANENT",
    "CANCELLED",
    "SKIPPED",
]


@dataclass
class PublishingQueueRecord:
    """Persistent queue item for scheduled publication across multi-account destinations.

    Includes transactional claiming, worker lease expiration, and idempotent keys.
    """

    id: str
    job_id: str
    clip_id: str
    destination_id: str
    platform: str
    scheduled_at: str
    idempotency_key: str
    publication_id: str | None = None
    priority: int = 0
    status: QueueStatus = "QUEUED"
    attempt_count: int = 0
    claimed_by: str | None = None
    claimed_at: str | None = None
    lease_expires_at: str | None = None
    error_message: str | None = None
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)

    @property
    def is_claimable(self) -> bool:
        return self.status in ("QUEUED", "SCHEDULED")

    @property
    def is_active(self) -> bool:
        return self.status in ("CLAIMED", "PUBLISHING")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "job_id": self.job_id,
            "clip_id": self.clip_id,
            "destination_id": self.destination_id,
            "publication_id": self.publication_id,
            "platform": self.platform,
            "scheduled_at": self.scheduled_at,
            "priority": self.priority,
            "status": self.status,
            "attempt_count": self.attempt_count,
            "claimed_by": self.claimed_by,
            "claimed_at": self.claimed_at,
            "lease_expires_at": self.lease_expires_at,
            "error_message": self.error_message,
            "idempotency_key": self.idempotency_key,
            "is_claimable": self.is_claimable,
            "is_active": self.is_active,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "PublishingQueueRecord":
        keys = row.keys()
        return cls(
            id=row["id"],
            job_id=row["job_id"],
            clip_id=row["clip_id"],
            destination_id=row["destination_id"],
            publication_id=row["publication_id"] if "publication_id" in keys else None,
            platform=row["platform"],
            scheduled_at=row["scheduled_at"],
            priority=int(row["priority"] or 0) if "priority" in keys else 0,
            status=row["status"] if "status" in keys else "QUEUED",
            attempt_count=int(row["attempt_count"] or 0) if "attempt_count" in keys else 0,
            claimed_by=row["claimed_by"] if "claimed_by" in keys else None,
            claimed_at=row["claimed_at"] if "claimed_at" in keys else None,
            lease_expires_at=row["lease_expires_at"] if "lease_expires_at" in keys else None,
            error_message=row["error_message"] if "error_message" in keys else None,
            idempotency_key=row["idempotency_key"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


# ---------------------------------------------------------------------------
# Step 28: Analytics & Learning Models
# ---------------------------------------------------------------------------


@dataclass
class PublicationMetricRecord:
    """Performance telemetry for a published short, supporting the 24h maturation rule."""

    id: str
    publication_id: str
    clip_id: str
    job_id: str
    platform: str
    destination_id: str = ""
    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    watch_time_s: float = 0.0
    avg_view_duration_s: float = 0.0
    completion_rate: float = 0.0
    raw_payload: dict[str, Any] = field(default_factory=dict)
    published_at: str = field(default_factory=utcnow)
    collected_at: str = field(default_factory=utcnow)
    is_mature: bool = False
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "publication_id": self.publication_id,
            "clip_id": self.clip_id,
            "job_id": self.job_id,
            "platform": self.platform,
            "destination_id": self.destination_id,
            "views": self.views,
            "likes": self.likes,
            "comments": self.comments,
            "shares": self.shares,
            "watch_time_s": self.watch_time_s,
            "avg_view_duration_s": self.avg_view_duration_s,
            "completion_rate": self.completion_rate,
            "raw_payload": self.raw_payload,
            "published_at": self.published_at,
            "collected_at": self.collected_at,
            "is_mature": self.is_mature,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "PublicationMetricRecord":
        keys = row.keys()
        return cls(
            id=row["id"],
            publication_id=row["publication_id"],
            clip_id=row["clip_id"],
            job_id=row["job_id"],
            platform=row["platform"],
            destination_id=row["destination_id"] if "destination_id" in keys else "",
            views=int(row["views"] or 0) if "views" in keys else 0,
            likes=int(row["likes"] or 0) if "likes" in keys else 0,
            comments=int(row["comments"] or 0) if "comments" in keys else 0,
            shares=int(row["shares"] or 0) if "shares" in keys else 0,
            watch_time_s=float(row["watch_time_s"] or 0.0) if "watch_time_s" in keys else 0.0,
            avg_view_duration_s=float(row["avg_view_duration_s"] or 0.0) if "avg_view_duration_s" in keys else 0.0,
            completion_rate=float(row["completion_rate"] or 0.0) if "completion_rate" in keys else 0.0,
            raw_payload=json.loads(row["raw_payload"] or "{}") if "raw_payload" in keys else {},
            published_at=row["published_at"],
            collected_at=row["collected_at"],
            is_mature=bool(row["is_mature"]) if "is_mature" in keys else False,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass
class LearningAuditRecord:
    """Immutable audit trail of evidence-based production feedback adjustments."""

    id: str
    category: str  # "hook", "duration", "caption_style", "scheduling", "destination"
    metric_observed: str
    evidence_sample_size: int
    recommendation: str
    action_taken: str
    rationale: str
    parameters_before: dict[str, Any] = field(default_factory=dict)
    parameters_after: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "metric_observed": self.metric_observed,
            "evidence_sample_size": self.evidence_sample_size,
            "recommendation": self.recommendation,
            "action_taken": self.action_taken,
            "rationale": self.rationale,
            "parameters_before": self.parameters_before,
            "parameters_after": self.parameters_after,
            "created_at": self.created_at,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "LearningAuditRecord":
        keys = row.keys()
        return cls(
            id=row["id"],
            category=row["category"],
            metric_observed=row["metric_observed"],
            evidence_sample_size=int(row["evidence_sample_size"] or 0) if "evidence_sample_size" in keys else 0,
            recommendation=row["recommendation"],
            action_taken=row["action_taken"],
            rationale=row["rationale"],
            parameters_before=json.loads(row["parameters_before"] or "{}") if "parameters_before" in keys else {},
            parameters_after=json.loads(row["parameters_after"] or "{}") if "parameters_after" in keys else {},
            created_at=row["created_at"],
        )

