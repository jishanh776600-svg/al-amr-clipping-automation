"""Abstract Base Capability Model and Result Contracts for Master Agent."""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field, ConfigDict

from clipping.agent.escalation import EscalationContext
from clipping.agent.models import TaskErrorInfo
from clipping.storage.base import StorageDriver


class CapabilityContext(BaseModel):
    """Runtime execution context provided to a capability upon invocation."""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    task_id: str
    campaign_id: Optional[str] = None
    inputs: Dict[str, Any] = Field(default_factory=dict)
    checkpoint_data: Dict[str, Any] = Field(default_factory=dict)
    storage_driver: StorageDriver
    scratch_dir: Optional[str] = None


class CapabilityResult(BaseModel):
    """Outcome produced by capability execution."""
    model_config = ConfigDict(frozen=True)

    success: bool
    outputs: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[TaskErrorInfo] = None
    checkpoint_data: Dict[str, Any] = Field(default_factory=dict)
    should_retry: bool = False
    escalation_required: bool = False
    escalation_context: Optional[EscalationContext] = None

    @classmethod
    def successful(cls, outputs: Optional[Dict[str, Any]] = None, checkpoint: Optional[Dict[str, Any]] = None) -> "CapabilityResult":
        return cls(
            success=True,
            outputs=outputs or {},
            checkpoint_data=checkpoint or {},
        )

    @classmethod
    def failed(
        cls,
        error_type: str,
        message: str,
        is_transient: bool = False,
        details: Optional[Dict[str, Any]] = None,
        should_retry: bool = False,
        checkpoint: Optional[Dict[str, Any]] = None,
    ) -> "CapabilityResult":
        return cls(
            success=False,
            error=TaskErrorInfo(
                error_type=error_type,
                error_message=message,
                is_transient=is_transient,
                details=details or {},
            ),
            should_retry=should_retry,
            checkpoint_data=checkpoint or {},
        )

    @classmethod
    def escalate(cls, escalation_context: EscalationContext, checkpoint: Optional[Dict[str, Any]] = None) -> "CapabilityResult":
        return cls(
            success=False,
            escalation_required=True,
            escalation_context=escalation_context,
            checkpoint_data=checkpoint or {},
        )


class AgentCapability(ABC):
    """
    Abstract extension contract for all Master Agent skills and tools.
    Encapsulates specific domain operations (e.g. Media Clipping, Web Browser, YouTube Ops).
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Globally unique capability identifier."""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable specification of what this capability achieves."""
        pass

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def is_idempotent(self) -> bool:
        """Whether executing this capability multiple times with identical inputs produces identical side-effects."""
        return True

    @property
    def is_reversible(self) -> bool:
        """Whether mutations introduced by this capability can be rolled back."""
        return True

    @abstractmethod
    async def execute(self, context: CapabilityContext) -> CapabilityResult:
        """Executes the capability logic within the provided runtime context."""
        pass
