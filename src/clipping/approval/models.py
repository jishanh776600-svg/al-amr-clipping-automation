"""Data models for Telegram Human Approval Gateway."""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict


class ApprovalStatus(str, Enum):
    """Lifecycle state of an individual clip approval."""
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"


class ApprovalAction(str, Enum):
    """Compact action identifiers for Telegram inline keyboard callback data."""
    APPROVE = "A"
    REJECT = "R"


class TelegramCallbackPayload(BaseModel):
    """
    Compact callback data parser & validator.
    Telegram enforces a strict 64-byte maximum length for callback_data.
    Format: 'v1:<A|R>:<request_id>'
    """
    model_config = ConfigDict(frozen=True)

    action: ApprovalAction
    approval_request_id: str = Field(..., min_length=1, max_length=48)
    protocol_version: str = "v1"

    def serialize(self) -> str:
        serialized = f"{self.protocol_version}:{self.action.value}:{self.approval_request_id}"
        if len(serialized.encode("utf-8")) > 64:
            raise ValueError(f"Callback data exceeds 64 bytes: '{serialized}'")
        return serialized

    @classmethod
    def parse(cls, data: str) -> "TelegramCallbackPayload":
        parts = data.strip().split(":")
        if len(parts) != 3 or parts[0] != "v1":
            raise ValueError(f"Invalid callback payload format: '{data}'. Expected 'v1:<A|R>:<req_id>'")
        action_val = parts[1]
        req_id = parts[2]
        if action_val == "A":
            action = ApprovalAction.APPROVE
        elif action_val == "R":
            action = ApprovalAction.REJECT
        else:
            raise ValueError(f"Unrecognized action in callback: '{action_val}'")
        return cls(action=action, approval_request_id=req_id)


class ApprovalRequest(BaseModel):
    """Durable representation of an approval request for a rendered vertical clip."""
    model_config = ConfigDict(frozen=True)

    approval_request_id: str = Field(..., min_length=1, max_length=64)
    job_id: str = Field(..., min_length=1, max_length=128)
    source_video_id: str = Field(..., min_length=1, max_length=128)
    clip_id: str = Field(..., min_length=1, max_length=128)
    clip_index: int = Field(..., ge=1)
    title: str = Field(..., min_length=1, max_length=200)
    hook_sentence: Optional[str] = Field(default=None, max_length=500)
    start_time: float = Field(..., ge=0.0)
    end_time: float = Field(..., gt=0.0)
    duration: float = Field(..., gt=0.0)
    score: float = Field(..., ge=0.0, le=100.0)
    qa_status: str = Field(default="PASS", max_length=32)
    video_storage_key: str = Field(..., min_length=1)
    status: ApprovalStatus = ApprovalStatus.AWAITING_APPROVAL
    telegram_message_id: Optional[int] = None
    telegram_chat_id: Optional[int] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    decided_at: Optional[datetime] = None
    decided_by: Optional[int] = None  # Telegram user ID
    decision_source: Optional[str] = None
    version: int = Field(default=1, ge=1)


class ApprovalAuditRecord(BaseModel):
    """Immutable audit record logging an approval decision event."""
    model_config = ConfigDict(frozen=True)

    audit_id: str
    approval_request_id: str
    job_id: str
    clip_id: str
    previous_status: ApprovalStatus
    new_status: ApprovalStatus
    telegram_user_id: int
    telegram_chat_id: int
    callback_query_id: Optional[str] = None
    reason: Optional[str] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    decision_source: str = "telegram"
    schema_version: str = "1.0"


class ApprovalSummary(BaseModel):
    """Aggregate approval metrics for a specific job."""
    model_config = ConfigDict(frozen=True)

    job_id: str
    total_clips: int
    approved_count: int
    rejected_count: int
    awaiting_count: int
    all_decided: bool
