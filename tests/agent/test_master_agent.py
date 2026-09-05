"""Comprehensive Master Agent Test Suite verifying all 21 architectural requirements."""

import pytest
from typing import Any, Dict, List, Optional

from clipping.agent.models import (
    AgentTask,
    TaskPriority,
    TaskType,
    RetryPolicy,
    TaskErrorInfo,
)
from clipping.agent.state import TaskState, validate_task_transition
from clipping.agent.exceptions import (
    InvalidStateTransitionError,
    TaskDependencyError,
    CapabilityNotFoundError,
    PolicyViolationError,
)
from clipping.agent.capabilities.base import (
    AgentCapability,
    CapabilityContext,
    CapabilityResult,
)
from clipping.agent.capabilities.registry import CapabilityRegistry
from clipping.agent.capabilities.clipping_adapter import MediaClippingCapability
from clipping.agent.escalation import (
    EscalationReason,
    EscalationSeverity,
    EscalationStatus,
    EscalationContext,
)
from clipping.agent.policy import (
    PolicyEngine,
    PolicyRule,
    PolicyDecisionType,
    ActionScope,
    ActionRiskTier,
)
from clipping.agent.events import AgentEventSystem, AgentEventType
from clipping.agent.memory import AgentMemoryStore, MemoryScope
from clipping.agent.planner import TaskPlanner, TaskGraph
from clipping.agent.repository import AgentTaskRepository
from clipping.agent.orchestrator import MasterAgentOrchestrator
from clipping.control.models import SystemControlState, SystemOperatingMode
from clipping.control.repository import ControlRepository
from clipping.state.lease import JobLeaseRepository
from clipping.storage.local import LocalStorageDriver


# --- Test Capabilities ---

class EchoCapability(AgentCapability):
    @property
    def name(self) -> str:
        return "echo_capability"

    @property
    def description(self) -> str:
        return "Echoes inputs back as outputs"

    async def execute(self, context: CapabilityContext) -> CapabilityResult:
        return CapabilityResult.successful(
            outputs={"echo": context.inputs.get("msg", "default")},
            checkpoint={"step": 1},
        )


class FlakyTransientCapability(AgentCapability):
    def __init__(self, succeed_on_attempt: int = 2):
        self.call_count = 0
        self.succeed_on_attempt = succeed_on_attempt

    @property
    def name(self) -> str:
        return "flaky_capability"

    @property
    def description(self) -> str:
        return "Fails with transient error until reaching target attempt"

    async def execute(self, context: CapabilityContext) -> CapabilityResult:
        self.call_count += 1
        if self.call_count >= self.succeed_on_attempt:
            return CapabilityResult.successful(outputs={"status": "recovered"})
        return CapabilityResult.failed(
            error_type="NetworkTimeoutError",
            message="Transient connection dropped",
            is_transient=True,
            should_retry=True,
        )


class PermanentFailingCapability(AgentCapability):
    @property
    def name(self) -> str:
        return "permanent_failure_capability"

    @property
    def description(self) -> str:
        return "Always fails with permanent error"

    async def execute(self, context: CapabilityContext) -> CapabilityResult:
        return CapabilityResult.failed(
            error_type="MalformedDataError",
            message="Fatal malformed payload",
            is_transient=False,
            should_retry=False,
        )


class EscalatingCapability(AgentCapability):
    @property
    def name(self) -> str:
        return "escalating_capability"

    @property
    def description(self) -> str:
        return "Demands human intervention"

    async def execute(self, context: CapabilityContext) -> CapabilityResult:
        return CapabilityResult.escalate(
            escalation_context=EscalationContext(
                what_happened="Encountered 2FA challenge on platform",
                why_it_happened="Session expired",
                what_was_attempted=["Session token refresh"],
                decision_required="Provide 6-digit OTP code",
                available_options=["SUBMIT_OTP", "CANCEL"],
            )
        )


# --- Test Fixtures ---

@pytest.fixture
def agent_env(tmp_path):
    storage = LocalStorageDriver(root_dir=str(tmp_path / "vault"))
    task_repo = AgentTaskRepository(storage_driver=storage)
    lease_repo = JobLeaseRepository(storage_driver=storage)
    control_repo = ControlRepository(storage_driver=storage)
    event_system = AgentEventSystem(storage_driver=storage)
    memory_store = AgentMemoryStore(storage_driver=storage)
    policy_engine = PolicyEngine(default_decision=PolicyDecisionType.ALLOW)

    cap_registry = CapabilityRegistry()
    cap_registry.register(EchoCapability())
    flaky_cap = FlakyTransientCapability(succeed_on_attempt=2)
    cap_registry.register(flaky_cap)
    cap_registry.register(PermanentFailingCapability())
    cap_registry.register(EscalatingCapability())

    orchestrator = MasterAgentOrchestrator(
        task_repository=task_repo,
        capability_registry=cap_registry,
        policy_engine=policy_engine,
        event_system=event_system,
        memory_store=memory_store,
        control_repository=control_repo,
        lease_repository=lease_repo,
        storage_driver=storage,
    )

    return {
        "storage": storage,
        "task_repo": task_repo,
        "lease_repo": lease_repo,
        "control_repo": control_repo,
        "event_system": event_system,
        "memory_store": memory_store,
        "policy_engine": policy_engine,
        "capabilities": cap_registry,
        "orchestrator": orchestrator,
        "flaky_cap": flaky_cap,
    }


# --- 21 Specific Requirement Tests ---

def test_01_task_creation():
    """1. Task creation with defaults and metadata."""
    task = AgentTask(
        task_id="task_001",
        objective="Inspect campaign requirements",
        task_type=TaskType.CAMPAIGN_ANALYSIS,
        inputs={"doc_url": "https://example.com/brief.pdf"},
        priority=TaskPriority.HIGH,
    )
    assert task.task_id == "task_001"
    assert task.status == TaskState.PENDING
    assert task.priority == TaskPriority.HIGH
    assert task.attempt_count == 0
    assert task.can_retry() is True


def test_02_valid_state_transitions():
    """2. Valid state transitions execute and update audit history."""
    task = AgentTask(
        task_id="task_002",
        objective="Test transitions",
    )
    t1 = task.transition_to(TaskState.PLANNED, reason="Planned in DAG")
    assert t1.status == TaskState.PLANNED
    assert len(t1.transitions) == 1

    t2 = t1.transition_to(TaskState.RUNNING, reason="Started worker")
    assert t2.status == TaskState.RUNNING
    assert t2.started_at is not None

    t3 = t2.transition_to(TaskState.SUCCEEDED, reason="Finished successfully")
    assert t3.status == TaskState.SUCCEEDED
    assert t3.completed_at is not None
    assert len(t3.transitions) == 3


def test_03_invalid_state_transitions():
    """3. Invalid state transitions raise InvalidStateTransitionError."""
    task = AgentTask(
        task_id="task_003",
        objective="Test invalid transition",
    )
    # PENDING -> SUCCEEDED directly is disallowed
    with pytest.raises(InvalidStateTransitionError):
        task.transition_to(TaskState.SUCCEEDED)

    # Terminal state SUCCEEDED cannot transition anywhere
    t_succ = task.transition_to(TaskState.RUNNING).transition_to(TaskState.SUCCEEDED)
    with pytest.raises(InvalidStateTransitionError):
        t_succ.transition_to(TaskState.RUNNING)


def test_04_task_dependency_handling():
    """4. Task dependency graph, topological order, and cycle detection."""
    t1 = AgentTask(task_id="t1", objective="Base task", status=TaskState.SUCCEEDED)
    t2 = AgentTask(task_id="t2", objective="Dependent task", dependencies=["t1"], status=TaskState.PENDING)
    t3 = AgentTask(task_id="t3", objective="Final task", dependencies=["t2"], status=TaskState.PENDING)

    graph = TaskGraph([t3, t2, t1])
    order = graph.get_topological_order()
    assert [t.task_id for t in order] == ["t1", "t2", "t3"]

    # Ready tasks when t1 is completed
    ready = graph.resolve_ready_tasks(completed_task_ids={"t1"})
    assert len(ready) == 1
    assert ready[0].task_id == "t2"

    # Cyclic graph detection
    c1 = AgentTask(task_id="c1", objective="A", dependencies=["c2"])
    c2 = AgentTask(task_id="c2", objective="B", dependencies=["c1"])
    cyclic_graph = TaskGraph([c1, c2])
    with pytest.raises(TaskDependencyError):
        cyclic_graph.validate_acyclic()


def test_05_capability_registration(agent_env):
    """5. Capability registration in CapabilityRegistry."""
    registry = agent_env["capabilities"]
    assert registry.has("echo_capability") is True
    assert registry.has("flaky_capability") is True

    # Duplicate without override raises error
    with pytest.raises(ValueError):
        registry.register(EchoCapability(), override=False)


def test_06_capability_lookup(agent_env):
    """6. Capability lookup and error handling for missing capability."""
    registry = agent_env["capabilities"]
    cap = registry.get("echo_capability")
    assert cap.name == "echo_capability"

    with pytest.raises(CapabilityNotFoundError):
        registry.get("nonexistent_unknown_tool")


@pytest.mark.asyncio
async def test_07_successful_capability_execution(agent_env):
    """7. Successful capability execution by Orchestrator."""
    orch = agent_env["orchestrator"]
    task = AgentTask(
        task_id="task_echo_01",
        objective="Run echo capability",
        inputs={"capability": "echo_capability", "msg": "hello master agent"},
    )
    await orch.submit_task(task)
    finished = await orch.execute_task("task_echo_01")

    assert finished.status == TaskState.SUCCEEDED
    assert finished.outputs["echo"] == "hello master agent"
    assert finished.attempt_count == 1
    assert len(finished.attempts) == 1
    assert finished.attempts[0].status == TaskState.SUCCEEDED


@pytest.mark.asyncio
async def test_08_capability_failure(agent_env):
    """8. Capability failure handling when capability crashes or errors."""
    orch = agent_env["orchestrator"]
    task = AgentTask(
        task_id="task_fail_01",
        objective="Execute crashing tool",
        inputs={"capability": "permanent_failure_capability"},
        retry_policy=RetryPolicy(max_attempts=1),
    )
    await orch.submit_task(task)
    finished = await orch.execute_task("task_fail_01")

    assert finished.status == TaskState.FAILED
    assert finished.error_info is not None
    assert finished.error_info.error_type == "MalformedDataError"


@pytest.mark.asyncio
async def test_09_bounded_retry_behavior(agent_env):
    """9. Bounded retry behavior on transient errors."""
    orch = agent_env["orchestrator"]
    task = AgentTask(
        task_id="task_flaky_01",
        objective="Recover from transient fault",
        inputs={"capability": "flaky_capability"},
        retry_policy=RetryPolicy(max_attempts=3, initial_delay_seconds=0.01),
    )
    await orch.submit_task(task)

    # Attempt 1: fails, transitions to PENDING for retry
    t1 = await orch.execute_task("task_flaky_01")
    assert t1.status == TaskState.PENDING
    assert t1.attempt_count == 1

    # Attempt 2: recovers, transitions to SUCCEEDED
    t2 = await orch.execute_task("task_flaky_01")
    assert t2.status == TaskState.SUCCEEDED
    assert t2.attempt_count == 2
    assert t2.outputs["status"] == "recovered"


@pytest.mark.asyncio
async def test_10_retry_exhaustion(agent_env):
    """10. Retry exhaustion marks task FAILED after max_attempts."""
    orch = agent_env["orchestrator"]
    # Flaky cap configured to only succeed on attempt 5, but max_attempts = 2
    agent_env["flaky_cap"].succeed_on_attempt = 5

    task = AgentTask(
        task_id="task_exhaust_01",
        objective="Exhaust retries",
        inputs={"capability": "flaky_capability"},
        retry_policy=RetryPolicy(max_attempts=2, initial_delay_seconds=0.01),
    )
    await orch.submit_task(task)

    # Attempt 1 -> PENDING
    await orch.execute_task("task_exhaust_01")
    # Attempt 2 -> FAILED (max attempts reached)
    t2 = await orch.execute_task("task_exhaust_01")

    assert t2.status == TaskState.FAILED
    assert t2.attempt_count == 2
    assert t2.can_retry() is False


def test_11_policy_allow():
    """11. Policy Engine allows authorized actions."""
    policy = PolicyEngine()
    scope = ActionScope(
        capability_name="media_clipping",
        action_name="render_video",
        target_resource="video_123",
        risk_tier=ActionRiskTier.ROUTINE_COMPUTE,
        is_reversible=True,
    )
    res = policy.evaluate(scope)
    assert res.allowed is True
    assert res.decision == PolicyDecisionType.ALLOW


def test_12_policy_deny():
    """12. Policy Engine denies unauthorized or dangerous operations."""
    deny_rule = PolicyRule(
        rule_id="BLOCK_CRYPTO",
        description="Block cryptocurrency activity",
        capability_pattern="*crypto*",
        action_pattern="*",
        decision=PolicyDecisionType.DENY,
        reason="Cryptocurrency operations are forbidden",
    )
    policy = PolicyEngine(rules=[deny_rule], default_decision=PolicyDecisionType.DENY)
    scope = ActionScope(
        capability_name="crypto_wallet",
        action_name="transfer",
        target_resource="wallet_abc",
        risk_tier=ActionRiskTier.MUTATING_IRREVERSIBLE,
        is_reversible=False,
    )
    res = policy.evaluate(scope)
    assert res.allowed is False
    assert res.decision == PolicyDecisionType.DENY


@pytest.mark.asyncio
async def test_13_escalation_creation(agent_env):
    """13. Capability requiring human intervention creates an Escalation."""
    orch = agent_env["orchestrator"]
    task_repo = agent_env["task_repo"]

    task = AgentTask(
        task_id="task_esc_01",
        objective="Perform 2FA flow",
        inputs={"capability": "escalating_capability"},
    )
    await orch.submit_task(task)
    finished = await orch.execute_task("task_esc_01")

    assert finished.status == TaskState.ESCALATED
    assert finished.escalation_id is not None

    esc = await task_repo.get_escalation(finished.escalation_id)
    assert esc is not None
    assert esc.status == EscalationStatus.OPEN
    assert "Provide 6-digit OTP code" in esc.context.decision_required

    # Operator resolves escalation
    resolved = await orch.resolve_escalation(
        escalation_id=esc.escalation_id,
        operator="Lead Director",
        action="APPROVE",
        notes="OTP confirmed out of band",
    )
    assert resolved.status == EscalationStatus.RESOLVED


@pytest.mark.asyncio
async def test_14_task_cancellation(agent_env):
    """14. Cooperative task cancellation transitions to CANCELLED and emits event."""
    orch = agent_env["orchestrator"]
    task = AgentTask(
        task_id="task_cancel_01",
        objective="Task to cancel",
        inputs={"capability": "echo_capability"},
    )
    await orch.submit_task(task)
    cancelled = await orch.cancel_task("task_cancel_01", reason="User requested abort")

    assert cancelled.status == TaskState.CANCELLED
    events = agent_env["event_system"].get_in_memory_events("task_cancel_01")
    assert any(e.event_type == AgentEventType.TASK_CANCELLED for e in events)


@pytest.mark.asyncio
async def test_15_resumability(agent_env):
    """15. Task resumption preserves checkpoints and continues execution."""
    orch = agent_env["orchestrator"]
    task_repo = agent_env["task_repo"]

    task = AgentTask(
        task_id="task_resume_01",
        objective="Resumable task",
        inputs={"capability": "echo_capability", "msg": "resumed message"},
        checkpoint_data={"bytes_downloaded": 1048576, "last_stage": "ingestion"},
    )
    await orch.submit_task(task)

    resumed = await orch.resume_task("task_resume_01")
    assert resumed.status == TaskState.SUCCEEDED
    assert resumed.checkpoint_data["bytes_downloaded"] == 1048576
    assert resumed.checkpoint_data["step"] == 1


@pytest.mark.asyncio
async def test_16_idempotency(agent_env):
    """16. Executing an already SUCCEEDED task is idempotent and does not re-run."""
    orch = agent_env["orchestrator"]
    task = AgentTask(
        task_id="task_idemp_01",
        objective="Idempotent task",
        inputs={"capability": "echo_capability"},
    )
    await orch.submit_task(task)
    first_run = await orch.execute_task("task_idemp_01")
    assert first_run.status == TaskState.SUCCEEDED
    assert first_run.attempt_count == 1

    second_run = await orch.execute_task("task_idemp_01")
    assert second_run.status == TaskState.SUCCEEDED
    assert second_run.attempt_count == 1  # Did NOT re-execute capability


@pytest.mark.asyncio
async def test_17_emergency_stop_compatibility(agent_env):
    """17. Global Emergency Stop halts task execution immediately."""
    orch = agent_env["orchestrator"]
    control_repo = agent_env["control_repo"]

    # Trigger emergency stop in Master Control
    await control_repo.save_state(SystemControlState(
        mode=SystemOperatingMode.EMERGENCY_STOPPED,
        emergency_stopped=True,
    ))

    task = AgentTask(
        task_id="task_estop_01",
        objective="Run during emergency stop",
        inputs={"capability": "echo_capability"},
    )
    await orch.submit_task(task)
    result = await orch.execute_task("task_estop_01")

    assert result.status == TaskState.FAILED
    assert "Emergency Stop" in result.transitions[-1].reason


@pytest.mark.asyncio
async def test_18_pause_compatibility(agent_env):
    """18. Global Automation Pause defers task execution."""
    orch = agent_env["orchestrator"]
    control_repo = agent_env["control_repo"]

    # Pause automation in Master Control
    await control_repo.save_state(SystemControlState(
        mode=SystemOperatingMode.AUTOMATION_PAUSED,
        automation_paused=True,
    ))

    task = AgentTask(
        task_id="task_pause_01",
        objective="Run during pause",
        inputs={"capability": "echo_capability"},
    )
    await orch.submit_task(task)
    result = await orch.execute_task("task_pause_01")

    assert result.status == TaskState.DEFERRED
    assert "Paused" in result.transitions[-1].reason


@pytest.mark.asyncio
async def test_19_audit_event_generation_and_secret_masking(agent_env):
    """19. Structured events are emitted and secrets are strictly masked."""
    event_sys = agent_env["event_system"]
    captured_events = []
    event_sys.add_handler(lambda e: captured_events.append(e))

    evt = await event_sys.emit(
        event_type=AgentEventType.POLICY_DECISION,
        task_id="task_secret_01",
        details={
            "api_key": "super_secret_api_key_12345",
            "access_token": "secret_oauth_token",
            "nested_log": "Calling https://api.telegram.org/bot123456:ABC-DEF/sendMessage with Bearer eyJhbGciOi...",
            "safe_metric": 42,
        },
    )

    assert evt.details["api_key"] == "<MASKED_SECRET>"
    assert evt.details["access_token"] == "<MASKED_SECRET>"
    assert "<MASKED_TOKEN>" in evt.details["nested_log"]
    assert "Bearer <MASKED_TOKEN>" in evt.details["nested_log"]
    assert evt.details["safe_metric"] == 42
    assert len(captured_events) >= 1


@pytest.mark.asyncio
async def test_20_persistence_and_reload_of_task_state(agent_env):
    """20. Persistent reload of task state from StorageDriver."""
    task_repo = agent_env["task_repo"]
    storage = agent_env["storage"]

    task = AgentTask(
        task_id="task_persist_01",
        objective="Durable reload test",
        inputs={"param": "value"},
        checkpoint_data={"step": 42},
    )
    await task_repo.save_task(task)

    # Re-initialize a fresh repository pointing to the same storage
    new_repo = AgentTaskRepository(storage_driver=storage)
    reloaded = await new_repo.get_task("task_persist_01")

    assert reloaded is not None
    assert reloaded.task_id == "task_persist_01"
    assert reloaded.checkpoint_data["step"] == 42
    assert reloaded.status == TaskState.PENDING


@pytest.mark.asyncio
async def test_21_clipping_pipeline_adapter_compatibility(agent_env):
    """21. Existing 9-stage clipping pipeline is callable via MediaClippingCapability."""
    orch = agent_env["orchestrator"]
    registry = agent_env["capabilities"]

    # Fast mock of the pipeline runner returning exit code 0
    async def mock_runner(source_uri: str, campaign_id: str, job_id: str, storage: Any):
        assert source_uri == "https://youtube.com/watch?v=mock_video"
        assert campaign_id == "campaign_alpha"
        return 0

    adapter = MediaClippingCapability(runner_fn=mock_runner)
    registry.register(adapter, override=True)

    task = AgentTask(
        task_id="task_clip_compat_01",
        objective="Run clipping via agent adapter",
        task_type=TaskType.MEDIA_CLIPPING,
        inputs={
            "capability": "media_clipping",
            "source_uri": "https://youtube.com/watch?v=mock_video",
            "campaign_id": "campaign_alpha",
        },
    )
    await orch.submit_task(task)
    finished = await orch.execute_task("task_clip_compat_01")

    assert finished.status == TaskState.SUCCEEDED
    assert finished.outputs["pipeline_status"] == "awaiting_approval"
    assert finished.outputs["campaign_id"] == "campaign_alpha"
