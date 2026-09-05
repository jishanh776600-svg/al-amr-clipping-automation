"""Durable Task Data Models, Priorities, Retry Policies, and Audit Contracts."""

from datetime import datetime, timezone
from enum import Enum, IntEnum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ConfigDict

from clipping.agent.state import TaskState, validate_task_transition


class TaskPriority(IntEnum):
    """Execution priority levels for Master Agent tasks."""
    LOW = 10
    NORMAL = 20
    HIGH = 30
    CRITICAL = 40


class TaskType(str, Enum):
    """Categorical classification of tasks executed by the Master Agent."""
    CAMPAIGN_DISCOVERY = "campaign_discovery"
    CAMPAIGN_ANALYSIS = "campaign_analysis"
    BROWSER_OPERATION = "browser_operation"
    MEDIA_CLIPPING = "media_clipping"
    CONTENT_PUBLISHING = "content_publishing"
    TELEGRAM_INTERACTION = "telegram_interaction"
    CREDENTIAL_OPERATION = "credential_operation"
    ACCOUNT_MANAGEMENT = "account_management"
    SYSTEM_MAINTENANCE = "system_maintenance"
    CUSTOM = "custom"


class RetryPolicy(BaseModel):
    """
    Deterministic, bounded retry configuration for tasks.
    Prevents unbounded / infinite retry loops.
    """
    model_config = ConfigDict(frozen=True)

    max_attempts: int = Field(default=3, ge=1, le=10, description="Maximum execution attempts including initial attempt")
    initial_delay_seconds: float = Field(default=2.0, ge=0.0, le=300.0)
    backoff_multiplier: float = Field(default=2.0, ge=1.0, le=10.0)
    max_delay_seconds: float = Field(default=60.0, ge=1.0, le=3600.0)
    retry_transient_only: bool = Field(default=True, description="When True, permanent errors fail immediately without retry")

    def get_delay_for_attempt(self, attempt: int) -> float:
        """Calculates bounded exponential backoff delay."""
        if attempt <= 1:
            return 0.0
        delay = self.initial_delay_seconds * (self.backoff_multiplier ** (attempt - 2))
        return min(delay, self.max_delay_seconds)


class TaskErrorInfo(BaseModel):
    """Structured capture of execution failure details."""
    model_config = ConfigDict(frozen=True)

    error_type: str = Field(..., max_length=128)
    error_message: str = Field(..., max_length=2048)
    is_transient: bool = False
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    details: Dict[str, Any] = Field(default_factory=dict)


class TaskTransitionAudit(BaseModel):
    """Immutable log entry recording an explicit task state transition."""
    model_config = ConfigDict(frozen=True)

    from_state: TaskState
    to_state: TaskState
    reason: Optional[str] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    actor: str = "agent"
    transition_metadata: Dict[str, Any] = Field(default_factory=dict)


class TaskAttempt(BaseModel):
    """Record of an individual capability execution attempt."""
    model_config = ConfigDict(frozen=True)

    attempt_number: int
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    status: TaskState
    error: Optional[TaskErrorInfo] = None
    execution_time_seconds: Optional[float] = None
    capability_name: Optional[str] = None


class AgentTask(BaseModel):
    """
    Durable, serializable representation of a Master Agent Task.
    Persisted directly to StorageDriver (Google Drive / Local Vault).
    """
    model_config = ConfigDict(frozen=True)

    task_id: str = Field(..., min_length=1, max_length=128)
    objective: str = Field(..., min_length=1, max_length=512)
    task_type: TaskType = TaskType.CUSTOM
    parent_task_id: Optional[str] = None
    campaign_id: Optional[str] = None

    status: TaskState = TaskState.PENDING
    priority: TaskPriority = TaskPriority.NORMAL

    inputs: Dict[str, Any] = Field(default_factory=dict)
    outputs: Dict[str, Any] = Field(default_factory=dict)
    dependencies: List[str] = Field(default_factory=list, description="IDs of prerequisite tasks that must SUCCEED first")

    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    attempt_count: int = Field(default=0, ge=0)
    attempts: List[TaskAttempt] = Field(default_factory=list)

    error_info: Optional[TaskErrorInfo] = None
    checkpoint_data: Dict[str, Any] = Field(default_factory=dict)
    escalation_id: Optional[str] = None

    transitions: List[TaskTransitionAudit] = Field(default_factory=list)

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    version: int = Field(default=1, ge=1)

    def transition_to(
        self,
        new_state: TaskState,
        reason: Optional[str] = None,
        actor: str = "agent",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "AgentTask":
        """
        Validates and transitions the task to a new lifecycle state.
        Returns a new immutable AgentTask instance with updated audit record.
        """
        validate_task_transition(self.status, new_state)

        now = datetime.now(timezone.utc)
        transition_record = TaskTransitionAudit(
            from_state=self.status,
            to_state=new_state,
            reason=reason or f"Transition to {new_state.value}",
            timestamp=now,
            actor=actor,
            transition_metadata=metadata or {},
        )

        updates: Dict[str, Any] = {
            "status": new_state,
            "updated_at": now,
            "version": self.version + 1,
            "transitions": [*self.transitions, transition_record],
        }

        if new_state == TaskState.RUNNING and not self.started_at:
            updates["started_at"] = now
        elif new_state in (TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELLED):
            updates["completed_at"] = now

        return self.model_copy(update=updates)

    def can_retry(self) -> bool:
        """Determines if this task is eligible for another attempt based on policy."""
        if self.attempt_count >= self.retry_policy.max_attempts:
            return False
        if self.error_info and not self.error_info.is_transient and self.retry_policy.retry_transient_only:
            return False
        return True
