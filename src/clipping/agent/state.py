"""Deterministic Task Lifecycle States and Validated State Transitions."""

from enum import Enum
from typing import Dict, Set
from clipping.agent.exceptions import InvalidStateTransitionError


class TaskState(str, Enum):
    """
    Deterministic lifecycle states of a Master Agent Task.
    Explicit transitions are strictly validated.
    """
    PENDING = "pending"
    PLANNED = "planned"
    RUNNING = "running"
    WAITING = "waiting"
    BLOCKED = "blocked"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DEFERRED = "deferred"
    ESCALATED = "escalated"

    @property
    def is_terminal(self) -> bool:
        """Determines if the state is final under normal execution."""
        return self in (TaskState.SUCCEEDED, TaskState.CANCELLED)

    @property
    def is_active(self) -> bool:
        """Determines if the task is currently active or in-flight."""
        return self in (TaskState.RUNNING, TaskState.WAITING, TaskState.ESCALATED)


VALID_TASK_TRANSITIONS: Dict[TaskState, Set[TaskState]] = {
    TaskState.PENDING: {
        TaskState.PLANNED,
        TaskState.RUNNING,
        TaskState.BLOCKED,
        TaskState.CANCELLED,
        TaskState.FAILED,
        TaskState.DEFERRED,
        TaskState.ESCALATED,
    },
    TaskState.PLANNED: {
        TaskState.RUNNING,
        TaskState.BLOCKED,
        TaskState.CANCELLED,
        TaskState.DEFERRED,
        TaskState.ESCALATED,
    },
    TaskState.BLOCKED: {
        TaskState.PENDING,
        TaskState.PLANNED,
        TaskState.RUNNING,
        TaskState.CANCELLED,
    },
    TaskState.RUNNING: {
        TaskState.SUCCEEDED,
        TaskState.FAILED,
        TaskState.WAITING,
        TaskState.DEFERRED,
        TaskState.ESCALATED,
        TaskState.CANCELLED,
        TaskState.PENDING,  # Permitted for rescheduling retries
    },
    TaskState.WAITING: {
        TaskState.RUNNING,
        TaskState.CANCELLED,
        TaskState.ESCALATED,
    },
    TaskState.DEFERRED: {
        TaskState.PENDING,
        TaskState.PLANNED,
        TaskState.RUNNING,
        TaskState.CANCELLED,
    },
    TaskState.ESCALATED: {
        TaskState.RUNNING,   # Operator provides decision/input to continue
        TaskState.CANCELLED, # Operator aborts task
        TaskState.FAILED,    # Operator rejects or resolution fails
    },
    TaskState.FAILED: {
        TaskState.PENDING,   # Permitted ONLY via explicit retry/requeue action
    },
    TaskState.SUCCEEDED: set(),  # Pure terminal state
    TaskState.CANCELLED: set(),  # Pure terminal state
}


def validate_task_transition(from_state: TaskState, to_state: TaskState) -> None:
    """
    Validates whether transitioning from from_state to to_state is legal.
    Raises InvalidStateTransitionError if the transition is disallowed.
    """
    if from_state == to_state:
        return  # Idempotent self-transition allowed

    allowed = VALID_TASK_TRANSITIONS.get(from_state, set())
    if to_state not in allowed:
        raise InvalidStateTransitionError(
            f"Invalid task state transition from {from_state.value} to {to_state.value}. "
            f"Allowed target states: {[s.value for s in allowed]}"
        )
