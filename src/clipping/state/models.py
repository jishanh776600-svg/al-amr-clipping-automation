"""Persistent State and Job Models."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import (
    Column,
    String,
    Integer,
    DateTime,
    JSON,
    ForeignKey,
    Enum as SAEnum,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class JobState(str, Enum):
    CREATED = "created"
    PARSING_CAMPAIGN = "parsing_campaign"
    INGESTING_VIDEO = "ingesting_video"
    TRANSCRIBING = "transcribing"
    DIARIZING = "diarizing"
    RESOLVING_SPEAKERS = "resolving_speakers"
    DISCOVERING_CLIPS = "discovering_clips"
    REFRAMING_AND_RENDERING = "reframing_and_rendering"
    RUNNING_QA = "running_qa"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    REGENERATING = "regenerating"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"


class PipelineStage(str, Enum):
    INITIALIZATION = "initialization"
    DOCUMENT_PARSING = "document_parsing"
    INGESTION = "ingestion"
    PERCEPTION = "perception"
    DIRECTOR = "director"
    INTELLIGENCE = "intelligence"
    RENDERING = "rendering"
    QA = "qa"
    APPROVAL = "approval"
    PUBLISHING = "publishing"
    COMPLETED = "completed"


# --- PYDANTIC DTOs ---

class JobRecord(BaseModel):
    """Pydantic representation of a persistent Job."""
    model_config = ConfigDict(from_attributes=True)

    job_id: str
    campaign_id: str
    source_video_id: str
    idempotency_key: str
    current_state: JobState
    current_stage: PipelineStage
    retry_count: int = 0
    error_message: Optional[str] = None
    metadata_json: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class StateTransitionRecord(BaseModel):
    """Pydantic representation of a StateTransition event."""
    model_config = ConfigDict(from_attributes=True)

    id: Optional[int] = None
    job_id: str
    from_state: JobState
    to_state: JobState
    stage: PipelineStage
    reason: Optional[str] = None
    transition_metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


# --- SQLALCHEMY ORM MODELS ---

class JobModel(Base):
    __tablename__ = "jobs"

    job_id = Column(String(128), primary_key=True)
    campaign_id = Column(String(128), nullable=False, index=True)
    source_video_id = Column(String(128), nullable=False, index=True)
    idempotency_key = Column(String(256), nullable=False, unique=True, index=True)
    current_state = Column(SAEnum(JobState), nullable=False, default=JobState.CREATED, index=True)
    current_stage = Column(SAEnum(PipelineStage), nullable=False, default=PipelineStage.INITIALIZATION)
    retry_count = Column(Integer, nullable=False, default=0)
    error_message = Column(String(2048), nullable=True)
    metadata_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class StateTransitionModel(Base):
    __tablename__ = "state_transitions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String(128), ForeignKey("jobs.job_id"), nullable=False, index=True)
    from_state = Column(SAEnum(JobState), nullable=False)
    to_state = Column(SAEnum(JobState), nullable=False)
    stage = Column(SAEnum(PipelineStage), nullable=False)
    reason = Column(String(512), nullable=True)
    transition_metadata = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
