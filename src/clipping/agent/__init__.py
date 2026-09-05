"""Master Agent Architecture Package for AL AMR Clipping Automation."""

from clipping.agent.models import (
    AgentTask,
    TaskPriority,
    TaskType,
    RetryPolicy,
    TaskErrorInfo,
    TaskAttempt,
    TaskTransitionAudit,
)
from clipping.agent.state import TaskState, validate_task_transition
from clipping.agent.orchestrator import MasterAgentOrchestrator
from clipping.agent.repository import AgentTaskRepository
from clipping.agent.memory import AgentMemoryStore, MemoryScope
from clipping.agent.planner import TaskPlanner, TaskGraph
from clipping.agent.policy import (
    PolicyEngine,
    PolicyRule,
    PolicyDecisionType,
    ActionScope,
    ActionRiskTier,
    PolicyEvaluationResult,
)
from clipping.agent.events import (
    AgentEventSystem,
    AgentEvent,
    AgentEventType,
    mask_sensitive_data,
)
from clipping.agent.escalation import (
    EscalationRecord,
    EscalationContext,
    EscalationReason,
    EscalationSeverity,
    EscalationStatus,
)
from clipping.agent.capabilities.base import (
    AgentCapability,
    CapabilityContext,
    CapabilityResult,
)
from clipping.agent.capabilities.registry import CapabilityRegistry
from clipping.agent.capabilities.clipping_adapter import MediaClippingCapability
from clipping.agent.exceptions import (
    AgentError,
    InvalidStateTransitionError,
    TaskDependencyError,
    CapabilityNotFoundError,
    CapabilityExecutionError,
    TransientTaskError,
    PermanentTaskError,
    PolicyViolationError,
    ExternalPlatformBlockedError,
    AuthenticationRequiredError,
    ResourceLimitExceededError,
    HumanInterventionRequiredError,
)

__all__ = [
    "AgentTask",
    "TaskState",
    "TaskPriority",
    "TaskType",
    "RetryPolicy",
    "TaskErrorInfo",
    "TaskAttempt",
    "TaskTransitionAudit",
    "validate_task_transition",
    "MasterAgentOrchestrator",
    "AgentTaskRepository",
    "AgentMemoryStore",
    "MemoryScope",
    "TaskPlanner",
    "TaskGraph",
    "PolicyEngine",
    "PolicyRule",
    "PolicyDecisionType",
    "ActionScope",
    "ActionRiskTier",
    "PolicyEvaluationResult",
    "AgentEventSystem",
    "AgentEvent",
    "AgentEventType",
    "mask_sensitive_data",
    "EscalationRecord",
    "EscalationContext",
    "EscalationReason",
    "EscalationSeverity",
    "EscalationStatus",
    "AgentCapability",
    "CapabilityContext",
    "CapabilityResult",
    "CapabilityRegistry",
    "MediaClippingCapability",
    "AgentError",
    "InvalidStateTransitionError",
    "TaskDependencyError",
    "CapabilityNotFoundError",
    "CapabilityExecutionError",
    "TransientTaskError",
    "PermanentTaskError",
    "PolicyViolationError",
    "ExternalPlatformBlockedError",
    "AuthenticationRequiredError",
    "ResourceLimitExceededError",
    "HumanInterventionRequiredError",
]
