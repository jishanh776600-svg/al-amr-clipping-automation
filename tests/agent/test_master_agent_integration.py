"""End-to-End Integration Validation for Master Agent Architecture."""

import pytest
from typing import Any, Dict, Optional

from clipping.agent.models import AgentTask, TaskPriority, TaskType, RetryPolicy
from clipping.agent.state import TaskState
from clipping.agent.capabilities.base import AgentCapability, CapabilityContext, CapabilityResult
from clipping.agent.capabilities.registry import CapabilityRegistry
from clipping.agent.escalation import EscalationReason, EscalationStatus, EscalationContext
from clipping.agent.policy import PolicyEngine, PolicyRule, PolicyDecisionType, ActionScope, ActionRiskTier
from clipping.agent.events import AgentEventSystem, AgentEventType
from clipping.agent.memory import AgentMemoryStore, MemoryScope
from clipping.agent.planner import TaskPlanner, TaskGraph
from clipping.agent.repository import AgentTaskRepository
from clipping.agent.orchestrator import MasterAgentOrchestrator
from clipping.control.models import SystemControlState, SystemOperatingMode
from clipping.control.repository import ControlRepository
from clipping.state.lease import JobLeaseRepository
from clipping.storage.local import LocalStorageDriver


class Step1DiscoveryCapability(AgentCapability):
    @property
    def name(self) -> str:
        return "campaign_discovery"

    @property
    def description(self) -> str:
        return "Discovers target content and extracts metadata"

    async def execute(self, context: CapabilityContext) -> CapabilityResult:
        source_url = context.inputs.get("source_url")
        return CapabilityResult.successful(
            outputs={"discovered_sources": [source_url], "candidate_count": 3},
            checkpoint={"discovery_completed": True, "source_id": "src_999"},
        )


class Step2IrreversiblePublishCapability(AgentCapability):
    @property
    def name(self) -> str:
        return "content_publishing"

    @property
    def description(self) -> str:
        return "Publishes content to external platform"

    @property
    def is_reversible(self) -> bool:
        return False

    async def execute(self, context: CapabilityContext) -> CapabilityResult:
        return CapabilityResult.successful(outputs={"publish_url": "https://platform.com/post_123"})


@pytest.mark.asyncio
async def test_master_agent_end_to_end_integration_lifecycle(tmp_path):
    """
    Validates complete Master Agent operational lifecycle:
    1. Plan & submit task DAG
    2. Durable storage persistence
    3. Capability resolution and execution
    4. Worker interruption and recovery
    5. Policy gating of irreversible action -> Escalation creation
    6. Human-in-the-loop escalation resolution
    7. Clean lease release
    8. Master Control emergency stop enforcement
    """
    storage = LocalStorageDriver(root_dir=str(tmp_path / "integration_vault"))
    task_repo = AgentTaskRepository(storage_driver=storage)
    lease_repo = JobLeaseRepository(storage_driver=storage)
    control_repo = ControlRepository(storage_driver=storage)
    event_system = AgentEventSystem(storage_driver=storage)
    memory_store = AgentMemoryStore(storage_driver=storage)

    # Policy requiring human confirmation for irreversible actions
    policy_engine = PolicyEngine(
        rules=[
            PolicyRule(
                rule_id="RULE_ALLOW_DISCOVERY",
                description="Allow discovery",
                capability_pattern="campaign_discovery",
                decision=PolicyDecisionType.ALLOW,
            ),
            PolicyRule(
                rule_id="RULE_CONFIRM_PUBLISH",
                description="Require confirmation before publish",
                capability_pattern="content_publishing",
                decision=PolicyDecisionType.REQUIRE_CONFIRMATION,
                requires_human_confirmation=True,
            ),
        ]
    )

    cap_registry = CapabilityRegistry()
    cap_registry.register(Step1DiscoveryCapability())
    cap_registry.register(Step2IrreversiblePublishCapability())

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

    # --- Phase A: Plan & Submit Tasks ---
    task1 = AgentTask(
        task_id="int_task_01",
        objective="Discover candidates for campaign",
        task_type=TaskType.CAMPAIGN_DISCOVERY,
        inputs={"capability": "campaign_discovery", "source_url": "https://youtube.com/watch?v=sample"},
    )
    task2 = AgentTask(
        task_id="int_task_02",
        objective="Publish approved content",
        task_type=TaskType.CONTENT_PUBLISHING,
        inputs={
            "capability": "content_publishing",
            "action": "publish_short",
            "is_reversible": False,
            "risk_tier": ActionRiskTier.MUTATING_IRREVERSIBLE.value,
        },
        dependencies=["int_task_01"],
    )

    await orchestrator.submit_task(task1)
    await orchestrator.submit_task(task2)

    # --- Phase B: Execute Task 1 ---
    t1_exec = await orchestrator.execute_task("int_task_01")
    assert t1_exec.status == TaskState.SUCCEEDED
    assert t1_exec.outputs["candidate_count"] == 3
    assert t1_exec.checkpoint_data["discovery_completed"] is True

    # Verify task 1 persisted in storage
    reloaded_t1 = await task_repo.get_task("int_task_01")
    assert reloaded_t1 is not None
    assert reloaded_t1.status == TaskState.SUCCEEDED

    # --- Phase C: Worker Crash & Task Resumption ---
    # Simulate a fresh worker node mounting the storage
    fresh_task_repo = AgentTaskRepository(storage_driver=storage)
    fresh_orchestrator = MasterAgentOrchestrator(
        task_repository=fresh_task_repo,
        capability_registry=cap_registry,
        policy_engine=policy_engine,
        event_system=event_system,
        memory_store=memory_store,
        control_repository=control_repo,
        lease_repository=lease_repo,
        storage_driver=storage,
    )

    # Resume/Execute Task 2 (prerequisite Task 1 is already SUCCEEDED)
    t2_exec = await fresh_orchestrator.execute_task("int_task_02")

    # Policy Engine intercepted irreversible action -> ESCALATED
    assert t2_exec.status == TaskState.ESCALATED
    assert t2_exec.escalation_id is not None

    # Verify escalation persisted
    esc = await fresh_task_repo.get_escalation(t2_exec.escalation_id)
    assert esc is not None
    assert esc.status == EscalationStatus.OPEN
    assert "Authorize or reject this operation" in esc.context.decision_required

    # --- Phase D: Human-in-the-Loop Operator Decision ---
    resolved_esc = await fresh_orchestrator.resolve_escalation(
        escalation_id=esc.escalation_id,
        operator="Supervisory Director",
        action="APPROVE",
        notes="Approved via Telegram/Console",
    )
    assert resolved_esc.status == EscalationStatus.RESOLVED

    # Task transitions to RUNNING under operator approval
    t2_after_approval = await fresh_task_repo.get_task("int_task_02")
    assert t2_after_approval.status == TaskState.RUNNING

    # --- Phase E: Master Control Emergency Stop Safety ---
    # Trigger global emergency stop
    await control_repo.save_state(SystemControlState(
        mode=SystemOperatingMode.EMERGENCY_STOPPED,
        emergency_stopped=True,
    ))

    # Submit task 3 during emergency stop
    task3 = AgentTask(task_id="int_task_03", objective="Forbidden task", inputs={"capability": "campaign_discovery"})
    await fresh_orchestrator.submit_task(task3)
    t3_exec = await fresh_orchestrator.execute_task("int_task_03")

    # Execution is blocked and marked FAILED
    assert t3_exec.status == TaskState.FAILED
    assert "Emergency Stop" in t3_exec.transitions[-1].reason

    # Verify leases are cleanly released
    lease_t1 = await lease_repo.get_lease("int_task_01")
    lease_t2 = await lease_repo.get_lease("int_task_02")
    assert lease_t1.status == "released"
    assert lease_t2.status == "released"
