"""Data contracts for Master Control, System Operating Modes, and Emergency State."""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict


class SystemOperatingMode(str, Enum):
    """Global operational state of the autonomous clipping machine."""
    OPERATIONAL = "operational"
    AUTOMATION_PAUSED = "automation_paused"
    EMERGENCY_STOPPED = "emergency_stopped"


class SystemControlState(BaseModel):
    """
    Durable global control state persisted in Google Drive (system/control_state.json).
    Survives container redeployments, browser refreshes, and runner terminations.
    """
    model_config = ConfigDict(frozen=True)

    mode: SystemOperatingMode = SystemOperatingMode.OPERATIONAL
    automation_paused: bool = False
    emergency_stopped: bool = False
    publishing_locked: bool = False
    active_job_id: Optional[str] = None
    last_changed_by: str = Field(default="system", max_length=128)
    reason: Optional[str] = Field(default=None, max_length=500)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    version: int = Field(default=1, ge=1)

    def can_start_new_jobs(self) -> bool:
        """Determines if new ingestion or clipping workflows are allowed to commence."""
        return not self.emergency_stopped and not self.automation_paused

    def can_publish(self) -> bool:
        """Determines if YouTube uploads are globally permitted."""
        return not self.emergency_stopped and not self.publishing_locked


class ControlAuditRecord(BaseModel):
    """Immutable audit trail entry for every Master Control operator action."""
    model_config = ConfigDict(frozen=True)

    audit_id: str
    action: str  # EMERGENCY_STOP, PAUSE_AUTOMATION, RESUME_AUTOMATION, LOCK_PUBLISHING, UNLOCK_PUBLISHING, RETRY_JOB, CANCEL_JOB
    previous_mode: SystemOperatingMode
    new_mode: SystemOperatingMode
    operator: str
    reason: Optional[str] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    schema_version: str = "1.0"
