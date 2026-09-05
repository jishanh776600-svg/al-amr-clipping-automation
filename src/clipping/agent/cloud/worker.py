"""Stateless Cloud Agent Worker managing task execution, heartbeats, and failure classification."""

import asyncio
from datetime import datetime, timedelta, timezone
import os
import time
import uuid
from typing import Any, Dict, Optional

from clipping.agent.capabilities.base import CapabilityContext, CapabilityResult
from clipping.agent.capabilities.registry import CapabilityRegistry
from clipping.agent.cloud.lease import WorkerLeaseEngine
from clipping.agent.cloud.limits import CloudResourceLimits
from clipping.agent.cloud.queue import CloudTaskQueue, QueueItem
from clipping.agent.cloud.telemetry import CloudTelemetryEngine, TelemetryEventType
from clipping.agent.escalation import (
    EscalationContext,
    EscalationReason,
    EscalationRecord,
    EscalationSeverity,
    EscalationStatus,
)
from clipping.agent.events import AgentEventSystem, AgentEventType
from clipping.agent.exceptions import (
    AuthenticationRequiredError,
    CapabilityNotFoundError,
    ExternalPlatformBlockedError,
    HumanInterventionRequiredError,
    PermanentTaskError,
    PolicyViolationError,
    ResourceLimitExceededError,
    TransientTaskError,
)
from clipping.agent.models import AgentTask, TaskAttempt, TaskErrorInfo
from clipping.agent.policy import ActionRiskTier, ActionScope, PolicyDecisionType, PolicyEngine
from clipping.agent.repository import AgentTaskRepository
from clipping.agent.state import TaskState
from clipping.control.repository import ControlRepository
from clipping.core.workspace import WorkerScratchWorkspace
from clipping.logging.logger import get_logger
from clipping.storage.base import StorageDriver

logger = get_logger("clipping.agent.cloud.worker")


class FailureClassification:
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    AUTHENTICATION = "authentication"
    POLICY = "policy"
    PLATFORM_BLOCK = "platform_block"
    RESOURCE_LIMIT = "resource_limit"
    HUMAN_INTERVENTION = "human_intervention"
    UNKNOWN = "unknown"


def classify_exception(exc: Exception) -> str:
    """Classifies an exception into the canonical failure categories."""
    if isinstance(exc, (TransientTaskError, TimeoutError, ConnectionError)):
        return FailureClassification.TRANSIENT
    if isinstance(exc, PermanentTaskError):
        return FailureClassification.PERMANENT
    if isinstance(exc, AuthenticationRequiredError):
        return FailureClassification.AUTHENTICATION
    if isinstance(exc, PolicyViolationError):
        return FailureClassification.POLICY
    if isinstance(exc, ExternalPlatformBlockedError):
        return FailureClassification.PLATFORM_BLOCK
    if isinstance(exc, ResourceLimitExceededError):
        return FailureClassification.RESOURCE_LIMIT
    if isinstance(exc, HumanInterventionRequiredError):
        return FailureClassification.HUMAN_INTERVENTION
    return FailureClassification.UNKNOWN


class CloudAgentWorker:
    """
    Stateless, disposable cloud compute worker.
    Runs on ephemeral compute (GitHub Actions Ubuntu runner or Render background service).
    Executes tasks safely with lease heartbeat renewals and checkpoint persistence.
    """

    def __init__(
        self,
        worker_id: Optional[str] = None,
        task_repository: Optional[AgentTaskRepository] = None,
        queue: Optional[CloudTaskQueue] = None,
        capabilities: Optional[CapabilityRegistry] = None,
        policy_engine: Optional[PolicyEngine] = None,
        event_system: Optional[AgentEventSystem] = None,
        control_repository: Optional[ControlRepository] = None,
        lease_engine: Optional[WorkerLeaseEngine] = None,
        telemetry: Optional[CloudTelemetryEngine] = None,
        storage_driver: Optional[StorageDriver] = None,
        limits: Optional[CloudResourceLimits] = None,
        heartbeat_interval_seconds: float = 30.0,
        lease_ttl_seconds: int = 300,
    ):
        self.worker_id = worker_id or os.getenv("GITHUB_RUN_ID", f"worker_{uuid.uuid4().hex[:8]}")
        self.storage = storage_driver
        self.task_repo = task_repository or AgentTaskRepository(storage_driver)
        self.lease_engine = lease_engine or WorkerLeaseEngine(storage_driver)
        self.queue = queue or CloudTaskQueue(storage_driver, self.lease_engine)
        self.capabilities = capabilities or CapabilityRegistry()
        self.policy = policy_engine or PolicyEngine()
        self.events = event_system or AgentEventSystem(storage_driver)
        self.control_repo = control_repository or ControlRepository(storage_driver)
        self.telemetry = telemetry or CloudTelemetryEngine(storage_driver)
        self.limits = limits or CloudResourceLimits()

        self.heartbeat_interval = heartbeat_interval_seconds
        self.lease_ttl = lease_ttl_seconds
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._is_running = False

    async def _heartbeat_loop(self, task_id: str) -> None:
        """Background heartbeat loop renewing worker lease while task is in flight."""
        try:
            while self._is_running:
                await asyncio.sleep(self.heartbeat_interval)
                if not self._is_running:
                    break
                renewed = await self.queue.heartbeat(
                    task_id=task_id,
                    worker_id=self.worker_id,
                    extend_seconds=self.lease_ttl,
                )
                if not renewed:
                    logger.warning("Heartbeat renewal failed; stopping worker loop", task_id=task_id)
                    break
                await self.telemetry.record(
                    event_type=TelemetryEventType.HEARTBEAT_RENEWED,
                    task_id=task_id,
                    worker_id=self.worker_id,
                )
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Error in heartbeat loop", task_id=task_id, error=str(e))

    def _start_heartbeat(self, task_id: str) -> None:
        self._is_running = True
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(task_id))

    async def _stop_heartbeat(self) -> None:
        self._is_running = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None

    async def run_next_task(self) -> Optional[AgentTask]:
        """Claims and executes the highest-priority pending task from the cloud queue."""
        # Check Master Control before claiming
        if await self.control_repo.is_emergency_stopped():
            logger.error("Master Control Emergency Stop is active; worker will not claim tasks", worker_id=self.worker_id)
            return None
        if await self.control_repo.is_automation_paused():
            logger.warning("Master Control Automation is paused; worker idling", worker_id=self.worker_id)
            return None

        # Reclaim any stale tasks before claiming
        await self.queue.reclaim_stale_tasks()

        claimed_item = await self.queue.claim(worker_id=self.worker_id, lease_duration_seconds=self.lease_ttl)
        if not claimed_item:
            return None

        return await self.execute_claimed_task(claimed_item.task_id)

    async def execute_claimed_task(self, task_id: str) -> AgentTask:
        """Executes a claimed task with end-to-end cloud safety, heartbeats, and checkpointing."""
        start_time = time.monotonic()
        await self.telemetry.record(
            event_type=TelemetryEventType.TASK_CLAIMED,
            task_id=task_id,
            worker_id=self.worker_id,
        )

        task = await self.task_repo.get_task(task_id)
        if not task:
            await self.queue.fail(task_id, self.worker_id, f"Task {task_id} not found in repository")
            raise ValueError(f"Task {task_id} not found")

        # Idempotency check: if already succeeded, mark complete and return
        if task.status == TaskState.SUCCEEDED:
            await self.queue.complete(task_id, self.worker_id)
            return task

        # Master Control checks
        if await self.control_repo.is_emergency_stopped():
            logger.error("Halting task due to Master Control Emergency Stop", task_id=task_id)
            task = await self.task_repo.update_task_state(
                task_id=task_id,
                new_state=TaskState.FAILED,
                reason="Aborted: Global Emergency Stop",
            )
            await self.queue.fail(task_id, self.worker_id, "Aborted: Emergency Stop", should_retry=False)
            return task

        if await self.control_repo.is_automation_paused():
            logger.warning("Deferring task due to Master Control Automation Pause", task_id=task_id)
            task = await self.task_repo.update_task_state(
                task_id=task_id,
                new_state=TaskState.DEFERRED,
                reason="Deferred: Global Automation Paused",
            )
            await self.queue.defer(task_id, self.worker_id, datetime.now(timezone.utc) + timedelta(seconds=60))
            return task

        # Resource limits checks
        current_attempt = task.attempt_count + 1
        try:
            self.limits.verify_attempts(current_attempt)
        except ResourceLimitExceededError as e:
            logger.error("Task exceeded attempt limits", task_id=task_id, error=str(e))
            task = await self.task_repo.update_task_state(
                task_id=task_id,
                new_state=TaskState.FAILED,
                reason=str(e),
            )
            await self.queue.fail(task_id, self.worker_id, str(e), should_retry=False)
            return task

        # Verify Dependencies
        for dep_id in task.dependencies:
            dep_task = await self.task_repo.get_task(dep_id)
            if not dep_task or dep_task.status != TaskState.SUCCEEDED:
                logger.warning("Prerequisite dependency not satisfied", task_id=task_id, dependency=dep_id)
                task = await self.task_repo.update_task_state(
                    task_id=task_id,
                    new_state=TaskState.BLOCKED,
                    reason=f"Prerequisite dependency '{dep_id}' is not SUCCEEDED",
                )
                await self.queue.fail(task_id, self.worker_id, f"Dependency {dep_id} unfulfilled", should_retry=False)
                return task

        # Policy Engine Evaluation
        capability_name = str(task.inputs.get("capability") or task.task_type.value)
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
            },
        )

        if policy_result.decision == PolicyDecisionType.DENY:
            logger.error("Task denied by policy", task_id=task_id, reason=policy_result.reason)
            task = await self.task_repo.update_task_state(
                task_id=task_id,
                new_state=TaskState.FAILED,
                reason=f"Policy denial: {policy_result.reason}",
            )
            await self.queue.fail(task_id, self.worker_id, policy_result.reason, should_retry=False)
            return task

        if policy_result.decision in (PolicyDecisionType.REQUIRE_CONFIRMATION, PolicyDecisionType.ESCALATE):
            escalation_id = f"esc_{uuid.uuid4().hex[:10]}"
            record = EscalationRecord(
                escalation_id=escalation_id,
                task_id=task.task_id,
                campaign_id=task.campaign_id,
                reason=EscalationReason.IRREVERSIBLE_ACTION if policy_result.decision == PolicyDecisionType.REQUIRE_CONFIRMATION else EscalationReason.POLICY_VIOLATION,
                severity=EscalationSeverity.HIGH if not action_scope.is_reversible else EscalationSeverity.MEDIUM,
                status=EscalationStatus.OPEN,
                context=EscalationContext(
                    what_happened=f"Task action requires human confirmation: {action_scope.action_name}",
                    why_it_happened=policy_result.reason,
                    what_was_attempted=["Policy evaluation"],
                    decision_required="Authorize or reject this operation",
                    available_options=["APPROVE", "REJECT"],
                    metadata={"action_scope": action_scope.model_dump()},
                ),
            )
            await self.task_repo.save_escalation(record)
            updated_task = task.model_copy(update={"escalation_id": escalation_id})
            await self.task_repo.save_task(updated_task)
            task = await self.task_repo.update_task_state(
                task_id=task_id,
                new_state=TaskState.ESCALATED,
                reason=f"Escalated to human operator: {policy_result.reason}",
            )
            await self.queue.defer(task_id, self.worker_id, datetime.now(timezone.utc) + timedelta(seconds=3600))
            return task

        # Resolve capability
        try:
            capability = self.capabilities.get(capability_name)
        except CapabilityNotFoundError as e:
            logger.error("Capability lookup failed", task_id=task_id, error=str(e))
            task = await self.task_repo.update_task_state(task_id=task_id, new_state=TaskState.FAILED, reason=str(e))
            await self.queue.fail(task_id, self.worker_id, str(e), should_retry=False)
            return task

        # Transition task to RUNNING and start heartbeat
        task = await self.task_repo.update_task_state(
            task_id=task_id,
            new_state=TaskState.RUNNING,
            reason=f"Cloud worker {self.worker_id} executing capability {capability.name}",
        )
        self._start_heartbeat(task_id)

        try:
            with WorkerScratchWorkspace(job_id=task_id) as workspace:
                context = CapabilityContext(
                    task_id=task_id,
                    campaign_id=task.campaign_id,
                    inputs=task.inputs,
                    checkpoint_data=task.checkpoint_data,
                    storage_driver=self.storage,
                    scratch_dir=str(workspace.workspace_dir),
                )

                await self.telemetry.record(
                    event_type=TelemetryEventType.CAPABILITY_STARTED,
                    task_id=task_id,
                    worker_id=self.worker_id,
                    capability_name=capability.name,
                )

                result = await capability.execute(context)

            duration = time.monotonic() - start_time

            # Update attempt record
            attempt_record = TaskAttempt(
                attempt_number=current_attempt,
                status=TaskState.SUCCEEDED if result.success else TaskState.FAILED,
                error=result.error,
                execution_time_seconds=round(duration, 3),
                capability_name=capability.name,
            )
            checkpoint = {**task.checkpoint_data, **result.checkpoint_data}

            if result.success:
                task = task.model_copy(update={
                    "attempt_count": current_attempt,
                    "attempts": [*task.attempts, attempt_record],
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
                await self.queue.complete(task_id, self.worker_id)
                await self.telemetry.record(
                    event_type=TelemetryEventType.TASK_COMPLETED,
                    task_id=task_id,
                    worker_id=self.worker_id,
                    capability_name=capability.name,
                    duration_seconds=round(duration, 3),
                    checkpoint_summary=checkpoint,
                )
                return task

            elif result.escalation_required and result.escalation_context:
                # Escalation required
                record = EscalationRecord(
                    escalation_id=f"esc_{uuid.uuid4().hex[:10]}",
                    task_id=task_id,
                    campaign_id=task.campaign_id,
                    reason=EscalationReason.UNCLASSIFIED_FAILURE,
                    severity=EscalationSeverity.MEDIUM,
                    status=EscalationStatus.OPEN,
                    context=result.escalation_context,
                )
                await self.task_repo.save_escalation(record)
                task = task.model_copy(update={
                    "attempt_count": current_attempt,
                    "attempts": [*task.attempts, attempt_record],
                    "checkpoint_data": checkpoint,
                    "escalation_id": record.escalation_id,
                })
                await self.task_repo.save_task(task)
                task = await self.task_repo.update_task_state(
                    task_id=task_id,
                    new_state=TaskState.ESCALATED,
                    reason=result.escalation_context.what_happened,
                )
                await self.queue.defer(task_id, self.worker_id, datetime.now(timezone.utc) + timedelta(seconds=3600))
                return task

            else:
                # Failed execution - evaluate retry policy
                task = task.model_copy(update={
                    "attempt_count": current_attempt,
                    "attempts": [*task.attempts, attempt_record],
                    "checkpoint_data": checkpoint,
                    "error_info": result.error,
                })
                await self.task_repo.save_task(task)

                should_retry = task.can_retry() and result.should_retry
                if should_retry:
                    delay = task.retry_policy.get_delay_for_attempt(current_attempt + 1)
                    task = await self.task_repo.update_task_state(
                        task_id=task_id,
                        new_state=TaskState.PENDING,
                        reason=f"Scheduled retry attempt {current_attempt + 1}",
                    )
                    await self.queue.fail(
                        task_id=task_id,
                        worker_id=self.worker_id,
                        error_message=result.error.error_message if result.error else "Execution failed",
                        should_retry=True,
                        retry_delay_seconds=delay,
                    )
                    await self.telemetry.record(
                        event_type=TelemetryEventType.RETRY_SCHEDULED,
                        task_id=task_id,
                        worker_id=self.worker_id,
                        attempt_number=current_attempt,
                        failure_classification=FailureClassification.TRANSIENT,
                    )
                else:
                    task = await self.task_repo.update_task_state(
                        task_id=task_id,
                        new_state=TaskState.FAILED,
                        reason=result.error.error_message if result.error else "Execution failed",
                    )
                    await self.queue.fail(
                        task_id=task_id,
                        worker_id=self.worker_id,
                        error_message=result.error.error_message if result.error else "Execution failed",
                        should_retry=False,
                    )
                    await self.telemetry.record(
                        event_type=TelemetryEventType.TASK_FAILED,
                        task_id=task_id,
                        worker_id=self.worker_id,
                        attempt_number=current_attempt,
                        failure_classification=FailureClassification.PERMANENT,
                    )
                return task

        except Exception as e:
            duration = time.monotonic() - start_time
            failure_type = classify_exception(e)
            logger.error("Cloud worker caught unhandled exception", task_id=task_id, classification=failure_type, error=str(e))

            if failure_type == FailureClassification.AUTHENTICATION:
                # Escalate authentication failure
                record = EscalationRecord(
                    escalation_id=f"esc_auth_{uuid.uuid4().hex[:8]}",
                    task_id=task_id,
                    campaign_id=task.campaign_id,
                    reason=EscalationReason.IDENTITY_VERIFICATION,
                    severity=EscalationSeverity.CRITICAL,
                    status=EscalationStatus.OPEN,
                    context=EscalationContext(
                        what_happened="Authentication credential expired or MFA required",
                        why_it_happened=str(e),
                        what_was_attempted=["OAuth token refresh"],
                        decision_required="Re-authenticate or provide updated secret credentials",
                        available_options=["PROVIDE_CREDENTIALS", "ABORT"],
                    ),
                )
                await self.task_repo.save_escalation(record)
                task = task.model_copy(update={"escalation_id": record.escalation_id})
                await self.task_repo.save_task(task)
                task = await self.task_repo.update_task_state(
                    task_id=task_id,
                    new_state=TaskState.ESCALATED,
                    reason=f"Authentication escalation: {e}",
                )
                await self.queue.defer(task_id, self.worker_id, datetime.now(timezone.utc) + timedelta(seconds=7200))
                return task

            is_transient = failure_type == FailureClassification.TRANSIENT
            error_info = TaskErrorInfo(
                error_type=type(e).__name__,
                error_message=str(e),
                is_transient=is_transient,
            )
            attempt_rec = TaskAttempt(
                attempt_number=current_attempt,
                status=TaskState.FAILED,
                error=error_info,
                execution_time_seconds=round(duration, 3),
            )
            task = task.model_copy(update={
                "attempt_count": current_attempt,
                "attempts": [*task.attempts, attempt_rec],
                "error_info": error_info,
            })
            await self.task_repo.save_task(task)

            should_retry = is_transient and task.can_retry()
            if should_retry:
                delay = task.retry_policy.get_delay_for_attempt(current_attempt + 1)
                task = await self.task_repo.update_task_state(
                    task_id=task_id,
                    new_state=TaskState.PENDING,
                    reason=f"Retry scheduled after transient error: {e}",
                )
                await self.queue.fail(task_id, self.worker_id, str(e), should_retry=True, retry_delay_seconds=delay)
            else:
                task = await self.task_repo.update_task_state(
                    task_id=task_id,
                    new_state=TaskState.FAILED,
                    reason=str(e),
                )
                await self.queue.fail(task_id, self.worker_id, str(e), should_retry=False)

            await self.telemetry.record(
                event_type=TelemetryEventType.TASK_FAILED,
                task_id=task_id,
                worker_id=self.worker_id,
                attempt_number=current_attempt,
                failure_classification=failure_type,
            )
            return task

        finally:
            await self._stop_heartbeat()
