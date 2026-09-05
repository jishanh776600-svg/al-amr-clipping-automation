"""Master Agent Orchestrator coordinating task execution, policy enforcement, recovery, and safety."""

import os
import time
import uuid
from typing import Any, Dict, Optional

from clipping.agent.capabilities.base import CapabilityContext, CapabilityResult
from clipping.agent.capabilities.registry import CapabilityRegistry
from clipping.agent.escalation import (
    EscalationContext,
    EscalationReason,
    EscalationRecord,
    EscalationSeverity,
    EscalationStatus,
)
from clipping.agent.events import AgentEventSystem, AgentEventType
from clipping.agent.exceptions import (
    CapabilityNotFoundError,
    InvalidStateTransitionError,
    PolicyViolationError,
    TaskDependencyError,
)
from clipping.agent.memory import AgentMemoryStore
from clipping.agent.models import (
    AgentTask,
    TaskAttempt,
    TaskErrorInfo,
)
from clipping.agent.policy import ActionRiskTier, ActionScope, PolicyDecisionType, PolicyEngine
from clipping.agent.repository import AgentTaskRepository
from clipping.agent.state import TaskState
from clipping.control.repository import ControlRepository
from clipping.core.workspace import WorkerScratchWorkspace
from clipping.logging.logger import get_logger
from clipping.state.lease import JobLeaseRepository
from clipping.storage.base import StorageDriver

logger = get_logger("clipping.agent.orchestrator")


class MasterAgentOrchestrator:
    """
    Central orchestration authority for autonomous campaign and content operations.
    Enforces policy boundaries, safe recovery, bounded retries, and human escalations.
    """

    def __init__(
        self,
        task_repository: AgentTaskRepository,
        capability_registry: CapabilityRegistry,
        policy_engine: PolicyEngine,
        event_system: AgentEventSystem,
        memory_store: AgentMemoryStore,
        control_repository: ControlRepository,
        lease_repository: JobLeaseRepository,
        storage_driver: StorageDriver,
    ):
        self.task_repo = task_repository
        self.capabilities = capability_registry
        self.policy = policy_engine
        self.events = event_system
        self.memory = memory_store
        self.control_repo = control_repository
        self.lease_repo = lease_repository
        self.storage = storage_driver

    async def submit_task(self, task: AgentTask) -> AgentTask:
        """Saves a new task and emits a TASK_CREATED audit event."""
        await self.task_repo.save_task(task)
        await self.events.emit(
            event_type=AgentEventType.TASK_CREATED,
            task_id=task.task_id,
            campaign_id=task.campaign_id,
            details={"objective": task.objective, "priority": task.priority.value, "task_type": task.task_type.value},
        )
        return task

    async def cancel_task(self, task_id: str, reason: str = "Operator cancelled", actor: str = "operator") -> AgentTask:
        """Cooperatively cancels a task."""
        task = await self.task_repo.get_task(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")

        if task.status.is_terminal:
            return task

        cancelled_task = await self.task_repo.update_task_state(
            task_id=task_id,
            new_state=TaskState.CANCELLED,
            reason=reason,
            actor=actor,
        )
        await self.events.emit(
            event_type=AgentEventType.TASK_CANCELLED,
            task_id=task_id,
            campaign_id=task.campaign_id,
            actor=actor,
            details={"reason": reason},
        )
        return cancelled_task

    async def execute_task(
        self,
        task_id: str,
        worker_id: Optional[str] = None,
    ) -> AgentTask:
        """
        Executes a Master Agent Task with comprehensive safety, policy gating,
        lease locking, bounded retries, and escalation handling.
        """
        active_worker_id = worker_id or os.getenv("GITHUB_RUN_ID", f"agent_worker_{uuid.uuid4().hex[:8]}")

        # 1. Global Control State Pre-flight Check
        if await self.control_repo.is_emergency_stopped():
            logger.error("Emergency stop is active; aborting task execution", task_id=task_id)
            task = await self.task_repo.get_task(task_id)
            if task and task.status != TaskState.FAILED:
                task = await self.task_repo.update_task_state(
                    task_id=task_id,
                    new_state=TaskState.FAILED,
                    reason="Aborted: Global Master Control Emergency Stop",
                )
            return task or await self._create_emergency_stub(task_id)

        if await self.control_repo.is_automation_paused():
            logger.warning("Automation is paused; deferring task execution", task_id=task_id)
            task = await self.task_repo.get_task(task_id)
            if task and task.status in (TaskState.PENDING, TaskState.PLANNED):
                task = await self.task_repo.update_task_state(
                    task_id=task_id,
                    new_state=TaskState.DEFERRED,
                    reason="Deferred: Global Automation is Paused",
                )
            return task or await self._create_emergency_stub(task_id)

        # 2. Acquire Distributed Lease (Duplicate Execution Guard)
        acquired, collision = await self.lease_repo.acquire_lease(
            job_id=task_id,
            worker_id=active_worker_id,
            ttl_seconds=3600,
        )
        if not acquired:
            logger.warning("Cannot acquire task lease; another worker active", task_id=task_id, reason=collision)
            task = await self.task_repo.get_task(task_id)
            return task or await self._create_emergency_stub(task_id)

        try:
            task = await self.task_repo.get_task(task_id)
            if not task:
                raise ValueError(f"Task not found: {task_id}")

            # Idempotency check: if already completed, return immediately
            if task.status == TaskState.SUCCEEDED:
                logger.info("Task already completed successfully; skipping re-execution", task_id=task_id)
                return task

            # 3. Verify Prerequisites / Dependencies
            for dep_id in task.dependencies:
                dep_task = await self.task_repo.get_task(dep_id)
                if not dep_task or dep_task.status != TaskState.SUCCEEDED:
                    logger.warning("Prerequisite dependency not satisfied", task_id=task_id, dependency=dep_id)
                    return await self.task_repo.update_task_state(
                        task_id=task_id,
                        new_state=TaskState.BLOCKED,
                        reason=f"Prerequisite dependency '{dep_id}' is not SUCCEEDED",
                    )

            # 4. Resolve Target Capability Name
            capability_name = str(task.inputs.get("capability") or task.task_type.value)

            # 5. Policy Engine Evaluation
            action_scope = ActionScope(
                capability_name=capability_name,
                action_name=task.inputs.get("action", "execute"),
                target_resource=task.inputs.get("source_uri", task.task_id),
                is_reversible=task.inputs.get("is_reversible", True),
                risk_tier=ActionRiskTier(task.inputs.get("risk_tier", ActionRiskTier.ROUTINE_COMPUTE.value)),
                parameters=task.inputs,
            )
            policy_result = self.policy.evaluate(action_scope)

            await self.events.emit(
                event_type=AgentEventType.POLICY_DECISION,
                task_id=task_id,
                campaign_id=task.campaign_id,
                details={
                    "decision": policy_result.decision.value,
                    "allowed": policy_result.allowed,
                    "reason": policy_result.reason,
                    "rule_id": policy_result.matched_rule_id,
                },
            )

            # Handle Policy Denials
            if policy_result.decision == PolicyDecisionType.DENY:
                logger.error("Action denied by Policy Engine", task_id=task_id, reason=policy_result.reason)
                error_info = TaskErrorInfo(
                    error_type="PolicyViolationError",
                    error_message=policy_result.reason,
                    is_transient=False,
                )
                task = task.model_copy(update={"error_info": error_info})
                await self.task_repo.save_task(task)
                return await self.task_repo.update_task_state(
                    task_id=task_id,
                    new_state=TaskState.FAILED,
                    reason=f"Policy denial: {policy_result.reason}",
                )

            # Handle Escalations / Human Confirmation requirements
            if policy_result.decision in (PolicyDecisionType.REQUIRE_CONFIRMATION, PolicyDecisionType.ESCALATE):
                return await self._create_and_apply_escalation(
                    task=task,
                    reason=EscalationReason.POLICY_VIOLATION if policy_result.decision == PolicyDecisionType.ESCALATE else EscalationReason.IRREVERSIBLE_ACTION,
                    context=EscalationContext(
                        what_happened=f"Task action requires human confirmation: {action_scope.action_name}",
                        why_it_happened=policy_result.reason,
                        what_was_attempted=["Policy evaluation"],
                        decision_required="Authorize or reject this operation",
                        available_options=["APPROVE", "REJECT"],
                        metadata={"action_scope": action_scope.model_dump()},
                    ),
                    severity=EscalationSeverity.HIGH if not action_scope.is_reversible else EscalationSeverity.MEDIUM,
                )

            # 6. Resolve Capability
            try:
                capability = self.capabilities.get(capability_name)
            except CapabilityNotFoundError as e:
                logger.error("Capability lookup failed", task_id=task_id, error=str(e))
                error_info = TaskErrorInfo(
                    error_type="CapabilityNotFoundError",
                    error_message=str(e),
                    is_transient=False,
                )
                task = task.model_copy(update={"error_info": error_info})
                await self.task_repo.save_task(task)
                return await self.task_repo.update_task_state(
                    task_id=task_id,
                    new_state=TaskState.FAILED,
                    reason=str(e),
                )

            # 7. Transition Task to RUNNING
            task = await self.task_repo.update_task_state(
                task_id=task_id,
                new_state=TaskState.RUNNING,
                reason=f"Executing capability: {capability.name}",
            )
            await self.events.emit(
                event_type=AgentEventType.TASK_STARTED,
                task_id=task_id,
                campaign_id=task.campaign_id,
                details={"capability": capability.name, "attempt": task.attempt_count + 1},
            )

            # 8. Execute within Scratch Workspace
            start_time = time.monotonic()
            attempt_number = task.attempt_count + 1

            with WorkerScratchWorkspace(job_id=task_id) as workspace:
                context = CapabilityContext(
                    task_id=task_id,
                    campaign_id=task.campaign_id,
                    inputs=task.inputs,
                    checkpoint_data=task.checkpoint_data,
                    storage_driver=self.storage,
                    scratch_dir=str(workspace.workspace_dir),
                )

                await self.events.emit(
                    event_type=AgentEventType.CAPABILITY_INVOKED,
                    task_id=task_id,
                    campaign_id=task.campaign_id,
                    details={"capability": capability.name},
                )

                try:
                    result = await capability.execute(context)
                except Exception as e:
                    logger.error("Unhandled capability crash", task_id=task_id, error=str(e))
                    result = CapabilityResult.failed(
                        error_type=type(e).__name__,
                        message=str(e),
                        is_transient=True,
                        should_retry=True,
                    )

            execution_duration = time.monotonic() - start_time

            # 9. Handle Result
            attempt_record = TaskAttempt(
                attempt_number=attempt_number,
                status=TaskState.SUCCEEDED if result.success else TaskState.FAILED,
                error=result.error,
                execution_time_seconds=round(execution_duration, 3),
                capability_name=capability.name,
            )

            updated_attempts = [*task.attempts, attempt_record]
            checkpoint = {**task.checkpoint_data, **result.checkpoint_data}

            if result.success:
                task = task.model_copy(update={
                    "attempt_count": attempt_number,
                    "attempts": updated_attempts,
                    "outputs": result.outputs,
                    "checkpoint_data": checkpoint,
                    "error_info": None,
                })
                await self.task_repo.save_task(task)

                task = await self.task_repo.update_task_state(
                    task_id=task_id,
                    new_state=TaskState.SUCCEEDED,
                    reason="Capability executed successfully",
                )
                await self.events.emit(
                    event_type=AgentEventType.TASK_SUCCEEDED,
                    task_id=task_id,
                    campaign_id=task.campaign_id,
                    details={"duration_seconds": round(execution_duration, 3)},
                )
                return task

            elif result.escalation_required and result.escalation_context:
                task = task.model_copy(update={
                    "attempt_count": attempt_number,
                    "attempts": updated_attempts,
                    "checkpoint_data": checkpoint,
                })
                await self.task_repo.save_task(task)
                return await self._create_and_apply_escalation(
                    task=task,
                    reason=EscalationReason.UNCLASSIFIED_FAILURE,
                    context=result.escalation_context,
                )

            else:
                # Failed result - check bounded retry policy
                task = task.model_copy(update={
                    "attempt_count": attempt_number,
                    "attempts": updated_attempts,
                    "error_info": result.error,
                    "checkpoint_data": checkpoint,
                })
                await self.task_repo.save_task(task)

                if task.can_retry() and result.should_retry:
                    delay = task.retry_policy.get_delay_for_attempt(attempt_number + 1)
                    logger.warning(
                        "Scheduling bounded retry for task",
                        task_id=task_id,
                        attempt=attempt_number,
                        max_attempts=task.retry_policy.max_attempts,
                        delay_seconds=delay,
                    )
                    await self.events.emit(
                        event_type=AgentEventType.RETRY_SCHEDULED,
                        task_id=task_id,
                        campaign_id=task.campaign_id,
                        details={
                            "attempt": attempt_number,
                            "max_attempts": task.retry_policy.max_attempts,
                            "delay_seconds": delay,
                        },
                    )
                    return await self.task_repo.update_task_state(
                        task_id=task_id,
                        new_state=TaskState.PENDING,
                        reason=f"Scheduled retry attempt {attempt_number + 1} after error: {result.error.error_message if result.error else 'unknown'}",
                    )
                else:
                    logger.error(
                        "Task failed and retry budget exhausted or error is permanent",
                        task_id=task_id,
                        attempt=attempt_number,
                    )
                    task = await self.task_repo.update_task_state(
                        task_id=task_id,
                        new_state=TaskState.FAILED,
                        reason=result.error.error_message if result.error else "Task execution failed",
                    )
                    await self.events.emit(
                        event_type=AgentEventType.TASK_FAILED,
                        task_id=task_id,
                        campaign_id=task.campaign_id,
                        details={"error": result.error.model_dump() if result.error else None},
                    )
                    return task

        finally:
            await self.lease_repo.release_lease(job_id=task_id, worker_id=active_worker_id)

    async def resume_task(self, task_id: str, worker_id: Optional[str] = None) -> AgentTask:
        """Resumes an interrupted or pending task from its last recorded checkpoint."""
        task = await self.task_repo.get_task(task_id)
        if not task:
            raise ValueError(f"Task not found: {task_id}")

        await self.events.emit(
            event_type=AgentEventType.TASK_RESUMED,
            task_id=task_id,
            campaign_id=task.campaign_id,
            details={"resuming_from_state": task.status.value, "checkpoint": task.checkpoint_data},
        )
        return await self.execute_task(task_id=task_id, worker_id=worker_id)

    async def resolve_escalation(
        self,
        escalation_id: str,
        operator: str,
        action: str,
        notes: Optional[str] = None,
    ) -> EscalationRecord:
        """Applies human operator decision to an escalation."""
        esc = await self.task_repo.get_escalation(escalation_id)
        if not esc:
            raise ValueError(f"Escalation {escalation_id} not found")

        resolved_esc = esc.resolve(operator=operator, action=action, notes=notes)
        await self.task_repo.save_escalation(resolved_esc)

        task = await self.task_repo.get_task(esc.task_id)
        if task:
            await self.events.emit(
                event_type=AgentEventType.HUMAN_DECISION,
                task_id=task.task_id,
                campaign_id=task.campaign_id,
                actor=operator,
                details={"action": action, "notes": notes, "escalation_id": escalation_id},
            )
            # Transition task according to decision
            if action.upper() in ("APPROVE", "PROCEED", "RETRY"):
                await self.task_repo.update_task_state(
                    task_id=task.task_id,
                    new_state=TaskState.RUNNING,
                    reason=f"Operator '{operator}' resolved escalation with: {action}",
                    actor=operator,
                )
            elif action.upper() in ("REJECT", "ABORT", "CANCEL"):
                await self.task_repo.update_task_state(
                    task_id=task.task_id,
                    new_state=TaskState.CANCELLED,
                    reason=f"Operator '{operator}' cancelled task via escalation",
                    actor=operator,
                )

        return resolved_esc

    async def _create_and_apply_escalation(
        self,
        task: AgentTask,
        reason: EscalationReason,
        context: EscalationContext,
        severity: EscalationSeverity = EscalationSeverity.MEDIUM,
    ) -> AgentTask:
        escalation_id = f"esc_{uuid.uuid4().hex[:10]}"
        record = EscalationRecord(
            escalation_id=escalation_id,
            task_id=task.task_id,
            campaign_id=task.campaign_id,
            reason=reason,
            severity=severity,
            status=EscalationStatus.OPEN,
            context=context,
        )
        await self.task_repo.save_escalation(record)

        updated_task = task.model_copy(update={"escalation_id": escalation_id})
        await self.task_repo.save_task(updated_task)

        escalated_task = await self.task_repo.update_task_state(
            task_id=task.task_id,
            new_state=TaskState.ESCALATED,
            reason=f"Escalated to human operator: {context.what_happened}",
        )
        await self.events.emit(
            event_type=AgentEventType.ESCALATION_CREATED,
            task_id=task.task_id,
            campaign_id=task.campaign_id,
            details={
                "escalation_id": escalation_id,
                "reason": reason.value,
                "severity": severity.value,
                "decision_required": context.decision_required,
            },
        )
        return escalated_task

    async def _create_emergency_stub(self, task_id: str) -> AgentTask:
        from clipping.agent.models import TaskPriority, TaskType
        return AgentTask(
            task_id=task_id,
            objective="Emergency stub",
            task_type=TaskType.CUSTOM,
            status=TaskState.FAILED,
        )
