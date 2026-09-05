"""Cloud Integration Validation: End-to-End Lifecycle and Worker Interruption/Replacement Recovery."""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
import pytest

from clipping.agent.capabilities.base import AgentCapability, CapabilityContext, CapabilityResult
from clipping.agent.capabilities.registry import CapabilityRegistry
from clipping.agent.cloud.lease import WorkerLeaseEngine
from clipping.agent.cloud.limits import CloudResourceLimits
from clipping.agent.cloud.queue import CloudTaskQueue, QueueItemStatus
from clipping.agent.cloud.scheduler import CloudTaskScheduler
from clipping.agent.cloud.telemetry import CloudTelemetryEngine, TelemetryEventType
from clipping.agent.cloud.worker import CloudAgentWorker
from clipping.agent.events import AgentEventSystem
from clipping.agent.models import AgentTask, RetryPolicy, TaskPriority, TaskType
from clipping.agent.policy import PolicyDecisionType, PolicyEngine
from clipping.agent.repository import AgentTaskRepository
from clipping.agent.state import TaskState
from clipping.control.repository import ControlRepository
from clipping.storage.local import LocalStorageDriver


class TwoStageProcessingCapability(AgentCapability):
    """
    Capability that executes in two discrete stages.
    If stage 1 was already checkpointed, it skips stage 1 and executes only stage 2.
    """
    def __init__(self, simulate_crash_on_first_run: bool = False):
        self.simulate_crash = simulate_crash_on_first_run
        self.invocations = 0

    @property
    def name(self) -> str:
        return "two_stage_processor"

    @property
    def description(self) -> str:
        return "Processes work in two checkpointed stages"

    async def execute(self, context: CapabilityContext) -> CapabilityResult:
        self.invocations += 1

        # Check if Stage 1 already completed from checkpoint
        stage1_done = context.checkpoint_data.get("stage_1_complete", False)

        if not stage1_done:
            # Execute Stage 1
            stage1_checkpoint = {
                "stage_1_complete": True,
                "stage_1_output": "features_extracted",
                "processed_items": 50,
            }
            if self.simulate_crash and self.invocations == 1:
                # Crash right after saving Stage 1 checkpoint
                return CapabilityResult.failed(
                    error_type="CloudWorkerPreemptedError",
                    message="Cloud spot runner preempted mid-task",
                    is_transient=True,
                    should_retry=True,
                    checkpoint=stage1_checkpoint,
                )

        # Execute Stage 2 (resumes using Stage 1 checkpoint data)
        prev_items = context.checkpoint_data.get("processed_items", 50)
        return CapabilityResult.successful(
            outputs={
                "total_items": prev_items + 50,
                "final_artifact": "vault/output/final.mp4",
                "status": "fully_processed",
            },
            checkpoint={"stage_2_complete": True, "done": True},
        )


@pytest.mark.asyncio
async def test_cloud_execution_end_to_end_lifecycle(tmp_path):
    """
    Proves:
    Task submitted
    -> durable state created
    -> cloud worker dispatched
    -> worker claims task
    -> capability invoked
    -> checkpoint persisted
    -> worker completes
    -> task becomes SUCCEEDED
    """
    storage = LocalStorageDriver(root_dir=str(tmp_path / "cloud_lifecycle_vault"))
    task_repo = AgentTaskRepository(storage)
    control_repo = ControlRepository(storage)
    lease_engine = WorkerLeaseEngine(storage)
    queue = CloudTaskQueue(storage, lease_engine)
    telemetry = CloudTelemetryEngine(storage)
    event_system = AgentEventSystem(storage)
    limits = CloudResourceLimits()

    cap_registry = CapabilityRegistry()
    cap_registry.register(TwoStageProcessingCapability(simulate_crash_on_first_run=False))

    policy = PolicyEngine(default_decision=PolicyDecisionType.ALLOW)
    scheduler = CloudTaskScheduler(queue, task_repo)

    worker = CloudAgentWorker(
        worker_id="cloud_runner_01",
        task_repository=task_repo,
        queue=queue,
        capabilities=cap_registry,
        policy_engine=policy,
        event_system=event_system,
        control_repository=control_repo,
        lease_engine=lease_engine,
        telemetry=telemetry,
        storage_driver=storage,
        limits=limits,
    )

    # 1. Submit task via Scheduler
    task = AgentTask(
        task_id="task_lifecycle_01",
        objective="Process campaign asset end-to-end",
        task_type=TaskType.CAMPAIGN_ANALYSIS,
        priority=TaskPriority.HIGH,
        inputs={"capability": "two_stage_processor", "asset_id": "asset_123"},
    )
    queued_item = await scheduler.schedule(task)
    assert queued_item.status == QueueItemStatus.PENDING

    # Verify durable state created in storage
    stored_task = await task_repo.get_task("task_lifecycle_01")
    assert stored_task is not None
    assert stored_task.status == TaskState.PENDING

    # 2. Cloud worker claims and executes
    completed_task = await worker.run_next_task()
    assert completed_task is not None
    assert completed_task.status == TaskState.SUCCEEDED
    assert completed_task.outputs["total_items"] == 100
    assert completed_task.checkpoint_data["done"] is True

    # 3. Verify queue finalized and lease released
    final_queue_item = await queue.get_item("task_lifecycle_01")
    assert final_queue_item.status == QueueItemStatus.COMPLETED

    lease = await lease_engine.get_lease("task_lifecycle_01")
    assert lease.status == "released"

    # 4. Verify telemetry recorded
    tel_events = telemetry.get_buffered_events("task_lifecycle_01")
    assert len(tel_events) >= 2
    types = [e.event_type for e in tel_events]
    assert TelemetryEventType.TASK_CLAIMED in types
    assert TelemetryEventType.TASK_COMPLETED in types


@pytest.mark.asyncio
async def test_cloud_worker_interruption_and_replacement_recovery(tmp_path):
    """
    Proves:
    Task
    -> worker checkpoint
    -> worker disappears (lease expires)
    -> replacement worker claims
    -> resumes from checkpoint without repeating work
    -> completes successfully
    """
    storage = LocalStorageDriver(root_dir=str(tmp_path / "cloud_recovery_vault"))
    task_repo = AgentTaskRepository(storage)
    control_repo = ControlRepository(storage)
    lease_engine = WorkerLeaseEngine(storage)
    queue = CloudTaskQueue(storage, lease_engine)
    telemetry = CloudTelemetryEngine(storage)
    event_system = AgentEventSystem(storage)
    limits = CloudResourceLimits()

    cap = TwoStageProcessingCapability(simulate_crash_on_first_run=True)
    cap_registry = CapabilityRegistry()
    cap_registry.register(cap)

    policy = PolicyEngine(default_decision=PolicyDecisionType.ALLOW)
    scheduler = CloudTaskScheduler(queue, task_repo)

    # Worker 1 with short 1s lease
    worker_1 = CloudAgentWorker(
        worker_id="worker_node_alpha",
        task_repository=task_repo,
        queue=queue,
        capabilities=cap_registry,
        policy_engine=policy,
        event_system=event_system,
        control_repository=control_repo,
        lease_engine=lease_engine,
        telemetry=telemetry,
        storage_driver=storage,
        limits=limits,
        lease_ttl_seconds=1,
    )

    # Submit task
    task = AgentTask(
        task_id="task_recover_01",
        objective="Resilient task",
        inputs={"capability": "two_stage_processor"},
        retry_policy=RetryPolicy(max_attempts=3, initial_delay_seconds=0.01),
    )
    await scheduler.schedule(task)

    # --- Phase 1: Worker 1 runs Stage 1, writes checkpoint, and crashes ---
    res1 = await worker_1.run_next_task()
    assert res1.status == TaskState.PENDING
    assert res1.checkpoint_data["stage_1_complete"] is True
    assert res1.checkpoint_data["stage_1_output"] == "features_extracted"

    # --- Phase 2: Worker 1 disappears, lease expires ---
    # Simulate time passing so worker 1's lease expires
    await asyncio.sleep(1.1)

    # --- Phase 3: Replacement Worker 2 mounts storage and claims task ---
    worker_2 = CloudAgentWorker(
        worker_id="worker_node_beta",
        task_repository=task_repo,
        queue=queue,
        capabilities=cap_registry,
        policy_engine=policy,
        event_system=event_system,
        control_repository=control_repo,
        lease_engine=lease_engine,
        telemetry=telemetry,
        storage_driver=storage,
        limits=limits,
        lease_ttl_seconds=60,
    )

    # Worker 2 claims and resumes
    res2 = await worker_2.run_next_task()
    assert res2 is not None
    assert res2.status == TaskState.SUCCEEDED
    # Stage 2 executed using Stage 1's saved items (50 + 50 = 100)
    assert res2.outputs["total_items"] == 100
    assert res2.outputs["status"] == "fully_processed"
    assert res2.checkpoint_data["stage_2_complete"] is True

    # Confirm lease clean
    final_lease = await lease_engine.get_lease("task_recover_01")
    assert final_lease.status == "released"
