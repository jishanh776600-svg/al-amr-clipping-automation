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

