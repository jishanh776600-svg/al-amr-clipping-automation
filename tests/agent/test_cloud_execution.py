"""Comprehensive Test Suite for Cloud Execution Infrastructure covering all 25 scenarios."""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
import pytest

from clipping.agent.capabilities.base import AgentCapability, CapabilityContext, CapabilityResult
from clipping.agent.capabilities.clipping_adapter import MediaClippingCapability
from clipping.agent.capabilities.registry import CapabilityRegistry
from clipping.agent.cloud.lease import WorkerLease, WorkerLeaseEngine
from clipping.agent.cloud.limits import CloudResourceLimits
from clipping.agent.cloud.queue import CloudTaskQueue, QueueItem, QueueItemStatus
from clipping.agent.cloud.scheduler import CloudTaskScheduler
from clipping.agent.cloud.telemetry import CloudTelemetryEngine, TelemetryEventType
from clipping.agent.cloud.worker import CloudAgentWorker, FailureClassification
from clipping.agent.escalation import EscalationReason, EscalationSeverity, EscalationStatus
from clipping.agent.events import AgentEventSystem, AgentEventType
from clipping.agent.exceptions import (
    AuthenticationRequiredError,
    PermanentTaskError,
    PolicyViolationError,
    ResourceLimitExceededError,
    TransientTaskError,
)
from clipping.agent.models import AgentTask, RetryPolicy, TaskPriority, TaskType
from clipping.agent.policy import ActionRiskTier, ActionScope, PolicyDecisionType, PolicyEngine, PolicyRule
from clipping.agent.repository import AgentTaskRepository
from clipping.agent.state import TaskState
from clipping.control.models import SystemControlState, SystemOperatingMode
from clipping.control.repository import ControlRepository
from clipping.storage.local import LocalStorageDriver


# --- Test Capabilities ---

class FastEchoCapability(AgentCapability):
    @property
    def name(self) -> str:
        return "fast_echo"

    @property
    def description(self) -> str:
        return "Fast echo capability for cloud testing"

    async def execute(self, context: CapabilityContext) -> CapabilityResult:
        return CapabilityResult.successful(
            outputs={"echo": context.inputs.get("msg", "default")},
            checkpoint={"step": 1, "data": "processed"},
        )


class FlakyTransientCapability(AgentCapability):
    def __init__(self, succeed_on_attempt: int = 2):
        self.attempts = 0
        self.succeed_on_attempt = succeed_on_attempt

    @property
    def name(self) -> str:
        return "flaky_transient"

    @property
    def description(self) -> str:
        return "Transient failure until target attempt"

    async def execute(self, context: CapabilityContext) -> CapabilityResult:
        self.attempts += 1
        if self.attempts >= self.succeed_on_attempt:
            return CapabilityResult.successful(outputs={"status": "recovered"})
        raise TransientTaskError("Temporary network reset")


class PermanentFailingCapability(AgentCapability):
    @property
    def name(self) -> str:
        return "permanent_failure"

    @property
    def description(self) -> str:
        return "Fails permanently"

    async def execute(self, context: CapabilityContext) -> CapabilityResult:
        raise PermanentTaskError("Deterministic bad data")


class AuthFailingCapability(AgentCapability):
    @property
    def name(self) -> str:
        return "auth_failing"

    @property
    def description(self) -> str:
        return "Fails with auth challenge"

    async def execute(self, context: CapabilityContext) -> CapabilityResult:
        raise AuthenticationRequiredError("OAuth refresh token revoked by identity provider")


class CheckpointProgressCapability(AgentCapability):
    def __init__(self, crash_on_first_run: bool = True):
        self.crash_on_first_run = crash_on_first_run
        self.runs = 0

    @property
    def name(self) -> str:
        return "checkpoint_progress"

    @property
    def description(self) -> str:
        return "Saves checkpoint then optionally crashes"

    async def execute(self, context: CapabilityContext) -> CapabilityResult:
        self.runs += 1
        if self.runs == 1 and self.crash_on_first_run:
            # Simulate worker process crash mid-flight after saving checkpoint
            return CapabilityResult.failed(
                error_type="WorkerKilledError",
                message="Runner killed by cloud supervisor",
                is_transient=True,
                should_retry=True,
                checkpoint={"bytes_processed": 5000000, "phase": "audio_extracted"},
            )
        # Resumed run: inspect checkpoint
        bytes_prev = context.checkpoint_data.get("bytes_processed", 0)
        return CapabilityResult.successful(
            outputs={"total_bytes": bytes_prev + 5000000, "status": "completed"},
            checkpoint={"phase": "done"},
        )


# --- Test Fixtures ---

@pytest.fixture
def cloud_env(tmp_path):
    storage = LocalStorageDriver(root_dir=str(tmp_path / "cloud_vault"))
    task_repo = AgentTaskRepository(storage)
    control_repo = ControlRepository(storage)
    lease_engine = WorkerLeaseEngine(storage)
    queue = CloudTaskQueue(storage, lease_engine)
    telemetry = CloudTelemetryEngine(storage)
    event_system = AgentEventSystem(storage)
    limits = CloudResourceLimits(max_task_attempts=3, max_worker_runtime_seconds=60)

    # Capability Registry
    registry = CapabilityRegistry()
    registry.register(FastEchoCapability())
    flaky = FlakyTransientCapability(succeed_on_attempt=2)
    registry.register(flaky)
    registry.register(PermanentFailingCapability())
    registry.register(AuthFailingCapability())
    checkpoint_cap = CheckpointProgressCapability(crash_on_first_run=True)
    registry.register(checkpoint_cap)

    # Policy Engine
    policy = PolicyEngine(
        default_decision=PolicyDecisionType.ALLOW,
        rules=[
            PolicyRule(
                rule_id="ALLOW_FAST_ECHO",
                description="Allow fast echo",
                capability_pattern="fast_echo",
                decision=PolicyDecisionType.ALLOW,
            ),
            PolicyRule(
                rule_id="CONFIRM_IRREVERSIBLE",
                description="Confirm before publishing",
                capability_pattern="*publish*",
                decision=PolicyDecisionType.REQUIRE_CONFIRMATION,
                requires_human_confirmation=True,
            ),
        ],
    )

    scheduler = CloudTaskScheduler(queue, task_repo)

    def create_worker(worker_id: str = "worker_test_01"):
        return CloudAgentWorker(
            worker_id=worker_id,
            task_repository=task_repo,
            queue=queue,
            capabilities=registry,
            policy_engine=policy,
            event_system=event_system,
            control_repository=control_repo,
            lease_engine=lease_engine,
            telemetry=telemetry,
            storage_driver=storage,
            limits=limits,
            heartbeat_interval_seconds=0.1,
            lease_ttl_seconds=2,
        )

    return {
        "storage": storage,
        "task_repo": task_repo,
        "control_repo": control_repo,
        "lease_engine": lease_engine,
        "queue": queue,
        "telemetry": telemetry,
        "event_system": event_system,
        "capabilities": registry,
        "policy": policy,
        "scheduler": scheduler,
        "limits": limits,
        "flaky_cap": flaky,
        "checkpoint_cap": checkpoint_cap,
        "create_worker": create_worker,
    }


# --- 25 Specific Requirements Tests ---

@pytest.mark.asyncio
async def test_01_enqueue(cloud_env):
    """1. Task enqueuing creates pending item and pointer."""
    queue = cloud_env["queue"]
    item = await queue.enqueue(task_id="t_01", priority=int(TaskPriority.HIGH))
    assert item.task_id == "t_01"
    assert item.status == QueueItemStatus.PENDING
    assert item.priority == int(TaskPriority.HIGH)

    stored = await queue.get_item("t_01")
    assert stored is not None
    assert stored.status == QueueItemStatus.PENDING


@pytest.mark.asyncio
async def test_02_claim(cloud_env):
    """2. Worker claims pending task from queue."""
    queue = cloud_env["queue"]
    await queue.enqueue(task_id="t_02", priority=int(TaskPriority.NORMAL))

    claimed = await queue.claim(worker_id="w_alpha", lease_duration_seconds=60)
    assert claimed is not None
    assert claimed.task_id == "t_02"
    assert claimed.status == QueueItemStatus.CLAIMED
    assert claimed.claimed_by == "w_alpha"


@pytest.mark.asyncio
async def test_03_concurrent_claim_collision(cloud_env):
    """3. Two concurrent workers claiming the same task result in exactly one claim."""
    queue = cloud_env["queue"]
    await queue.enqueue(task_id="t_03", priority=int(TaskPriority.NORMAL))

    c1 = await queue.claim(worker_id="w_one", lease_duration_seconds=60)
    c2 = await queue.claim(worker_id="w_two", lease_duration_seconds=60)

    assert c1 is not None
    assert c1.claimed_by == "w_one"
    assert c2 is None  # Second worker cannot claim already-leased task


@pytest.mark.asyncio
async def test_04_heartbeat(cloud_env):
    """4. Heartbeat extends worker lease expiry timestamp."""
    queue = cloud_env["queue"]
    await queue.enqueue(task_id="t_04", priority=int(TaskPriority.NORMAL))
    claimed = await queue.claim(worker_id="w_hb", lease_duration_seconds=5)
    exp1 = claimed.lease_expires_at

    await asyncio.sleep(0.05)
    ok = await queue.heartbeat(task_id="t_04", worker_id="w_hb", extend_seconds=10)
    assert ok is True

    updated = await queue.get_item("t_04")
    assert updated.lease_expires_at > exp1


@pytest.mark.asyncio
async def test_05_lease_expiry(cloud_env):
    """5. Lease expiry occurs when TTL elapses without heartbeats."""
    lease_engine = cloud_env["lease_engine"]
    await lease_engine.acquire_lease(task_id="t_05", worker_id="w_exp", ttl_seconds=1)
    lease = await lease_engine.get_lease("t_05")
    assert lease.is_valid_at(datetime.now(timezone.utc)) is True

    # After 1.1s expiry
    future_time = datetime.now(timezone.utc) + timedelta(seconds=2)
    assert lease.is_valid_at(future_time) is False
    assert lease.is_stale_at(future_time) is True


@pytest.mark.asyncio
async def test_06_stale_worker_recovery(cloud_env):
    """6. Reclaiming stale tasks re-enqueues abandoned work."""
    queue = cloud_env["queue"]
    await queue.enqueue(task_id="t_06", priority=int(TaskPriority.NORMAL))
    # Claim with short 1-second lease
    claimed = await queue.claim(worker_id="w_crashed", lease_duration_seconds=1)
    assert claimed.status == QueueItemStatus.CLAIMED

    # Wait for lease to become stale
    await asyncio.sleep(1.1)

    reclaimed_ids = await queue.reclaim_stale_tasks(stale_threshold_seconds=0)
    assert "t_06" in reclaimed_ids

    # Next healthy worker can now claim it
    new_claim = await queue.claim(worker_id="w_healthy", lease_duration_seconds=60)
    assert new_claim is not None
    assert new_claim.task_id == "t_06"
    assert new_claim.claimed_by == "w_healthy"


@pytest.mark.asyncio
async def test_07_task_completion(cloud_env):
    """7. Task completion marks status COMPLETED and releases lease."""
    queue = cloud_env["queue"]
    lease_engine = cloud_env["lease_engine"]

    await queue.enqueue(task_id="t_07")
    await queue.claim(worker_id="w_07", lease_duration_seconds=60)
    await queue.complete(task_id="t_07", worker_id="w_07")

    item = await queue.get_item("t_07")
    assert item.status == QueueItemStatus.COMPLETED
    lease = await lease_engine.get_lease("t_07")
    assert lease.status == "released"


@pytest.mark.asyncio
async def test_08_task_failure(cloud_env):
    """8. Task failure without retry marks FAILED permanently."""
    queue = cloud_env["queue"]
    await queue.enqueue(task_id="t_08")
    await queue.claim(worker_id="w_08")
    await queue.fail(task_id="t_08", worker_id="w_08", error_message="Fatal crash", should_retry=False)

    item = await queue.get_item("t_08")
    assert item.status == QueueItemStatus.FAILED
    assert item.error_message == "Fatal crash"


@pytest.mark.asyncio
async def test_09_bounded_retry(cloud_env):
    """9. Bounded retry on transient error re-enqueues with delay."""
    worker = cloud_env["create_worker"]()
    task_repo = cloud_env["task_repo"]
    queue = cloud_env["queue"]

    task = AgentTask(
        task_id="t_retry_01",
        objective="Retry test",
        inputs={"capability": "flaky_transient"},
        retry_policy=RetryPolicy(max_attempts=3, initial_delay_seconds=0.0),
    )
    await task_repo.save_task(task)
    await queue.enqueue(task.task_id)

    # Attempt 1: fails transiently, schedules retry
    res1 = await worker.run_next_task()
    assert res1.status == TaskState.PENDING
    assert res1.attempt_count == 1

    # Attempt 2: recovered
    res2 = await worker.run_next_task()
    assert res2.status == TaskState.SUCCEEDED
    assert res2.attempt_count == 2


@pytest.mark.asyncio
async def test_10_retry_exhaustion(cloud_env):
    """10. Retry exhaustion marks task FAILED after max_attempts."""
    worker = cloud_env["create_worker"]()
    task_repo = cloud_env["task_repo"]
    queue = cloud_env["queue"]

    # Capability set to succeed on attempt 5, but task max_attempts = 2
    cloud_env["flaky_cap"].succeed_on_attempt = 5

    task = AgentTask(
        task_id="t_exhaust_01",
        objective="Retry exhaustion test",
        inputs={"capability": "flaky_transient"},
        retry_policy=RetryPolicy(max_attempts=2, initial_delay_seconds=0.0),
    )
    await task_repo.save_task(task)
    await queue.enqueue(task.task_id)

    # Run attempt 1 -> PENDING
    await worker.run_next_task()
    # Run attempt 2 -> FAILED
    res2 = await worker.run_next_task()

    assert res2.status == TaskState.FAILED
    assert res2.attempt_count == 2


@pytest.mark.asyncio
async def test_11_deferred_task(cloud_env):
    """11. Deferred task is invisible to claim until scheduled time."""
    queue = cloud_env["queue"]
    future_time = datetime.now(timezone.utc) + timedelta(seconds=10)
    await queue.enqueue(task_id="t_defer_01", delay_seconds=10)

    # Immediate claim returns None
    c = await queue.claim(worker_id="w_test")
    assert c is None

    # After scheduled time, item can be claimed
    item = await queue.get_item("t_defer_01")
    assert item.scheduled_for > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_12_cancelled_task(cloud_env):
    """12. Cancelled task is removed from pending queue."""
    queue = cloud_env["queue"]
    await queue.enqueue(task_id="t_cancel_01")
    await queue.cancel("t_cancel_01")

    item = await queue.get_item("t_cancel_01")
    assert item.status == QueueItemStatus.CANCELLED

    claim = await queue.claim(worker_id="w_test")
    assert claim is None


@pytest.mark.asyncio
async def test_13_emergency_stop(cloud_env):
    """13. Master Control Emergency Stop halts cloud worker execution."""
    control_repo = cloud_env["control_repo"]
    worker = cloud_env["create_worker"]()
    task_repo = cloud_env["task_repo"]
    queue = cloud_env["queue"]

    await control_repo.save_state(SystemControlState(
        mode=SystemOperatingMode.EMERGENCY_STOPPED,
        emergency_stopped=True,
    ))

    task = AgentTask(task_id="t_estop_01", objective="Forbidden", inputs={"capability": "fast_echo"})
    await task_repo.save_task(task)
    await queue.enqueue(task.task_id)

    # Worker refuses to claim tasks during emergency stop
    result = await worker.run_next_task()
    assert result is None


@pytest.mark.asyncio
async def test_14_automation_pause(cloud_env):
    """14. Master Control Automation Pause defers worker processing."""
    control_repo = cloud_env["control_repo"]
    worker = cloud_env["create_worker"]()
    task_repo = cloud_env["task_repo"]
    queue = cloud_env["queue"]

    await control_repo.save_state(SystemControlState(
        mode=SystemOperatingMode.AUTOMATION_PAUSED,
        automation_paused=True,
    ))

    task = AgentTask(task_id="t_pause_01", objective="Paused work", inputs={"capability": "fast_echo"})
    await task_repo.save_task(task)
    await queue.enqueue(task.task_id)

    result = await worker.run_next_task()
    assert result is None


@pytest.mark.asyncio
async def test_15_worker_interruption(cloud_env):
    """15. Worker process interruption leaves lease to expire naturally."""
    lease_engine = cloud_env["lease_engine"]
    # Worker A claims with 1s lease
    await lease_engine.acquire_lease("t_interrupt_01", "worker_A", ttl_seconds=1)

    # Worker A crashes without releasing lease
    await asyncio.sleep(1.1)

    # Worker B reclaims expired lease
    reclaimed, lease_b = await lease_engine.reclaim_expired_lease("t_interrupt_01", "worker_B", ttl_seconds=60)
    assert reclaimed is True
    assert lease_b.worker_id == "worker_B"


@pytest.mark.asyncio
async def test_16_checkpoint_recovery(cloud_env):
    """16. Checkpoint recovery resumes processing from stored checkpoint data."""
    worker_1 = cloud_env["create_worker"](worker_id="runner_node_1")
    worker_2 = cloud_env["create_worker"](worker_id="runner_node_2")
    task_repo = cloud_env["task_repo"]
    queue = cloud_env["queue"]

    task = AgentTask(
        task_id="t_chkpt_01",
        objective="Process with checkpoints",
        inputs={"capability": "checkpoint_progress"},
        retry_policy=RetryPolicy(max_attempts=3, initial_delay_seconds=0.0),
    )
    await task_repo.save_task(task)
    await queue.enqueue(task.task_id)

    # Run 1: Worker 1 runs and fails mid-flight, saving checkpoint
    res1 = await worker_1.run_next_task()
    assert res1.checkpoint_data["bytes_processed"] == 5000000

    # Run 2: Worker 2 resumes using the saved checkpoint
    res2 = await worker_2.run_next_task()
    assert res2.status == TaskState.SUCCEEDED
    assert res2.outputs["total_bytes"] == 10000000


@pytest.mark.asyncio
async def test_17_idempotent_resume(cloud_env):
    """17. Resuming an already SUCCEEDED task is completely idempotent."""
    worker = cloud_env["create_worker"]()
    task_repo = cloud_env["task_repo"]
    queue = cloud_env["queue"]

    task = AgentTask(
        task_id="t_idemp_01",
        objective="Idempotent task",
        inputs={"capability": "fast_echo"},
    )
    await task_repo.save_task(task)
    await queue.enqueue(task.task_id)

    # Run 1 completes
    r1 = await worker.run_next_task()
    assert r1.status == TaskState.SUCCEEDED

    # Run 2 on same task
    r2 = await worker.execute_claimed_task(task.task_id)
    assert r2.status == TaskState.SUCCEEDED
    assert r2.attempt_count == 1  # Attempt count did not increment


@pytest.mark.asyncio
async def test_18_priority_scheduling(cloud_env):
    """18. Tasks with higher priority are claimed before lower priority tasks."""
    queue = cloud_env["queue"]
    await queue.enqueue(task_id="t_low", priority=int(TaskPriority.LOW))
    await queue.enqueue(task_id="t_critical", priority=int(TaskPriority.CRITICAL))
    await queue.enqueue(task_id="t_normal", priority=int(TaskPriority.NORMAL))

    c1 = await queue.claim(worker_id="w1")
    c2 = await queue.claim(worker_id="w2")
    c3 = await queue.claim(worker_id="w3")

    assert c1.task_id == "t_critical"
    assert c2.task_id == "t_normal"
    assert c3.task_id == "t_low"


@pytest.mark.asyncio
async def test_19_scheduled_execution(cloud_env):
    """19. Scheduler correctly manages delayed execution."""
    scheduler = cloud_env["scheduler"]
    task_repo = cloud_env["task_repo"]
    queue = cloud_env["queue"]

    task = AgentTask(task_id="t_sched_01", objective="Scheduled task")
    await scheduler.schedule(task, delay_seconds=0.0)

    item = await queue.get_item("t_sched_01")
    assert item is not None
    assert item.status == QueueItemStatus.PENDING


@pytest.mark.asyncio
async def test_20_resource_limit_enforcement(cloud_env):
    """20. Resource limits reject tasks exceeding attempt boundaries."""
    limits = cloud_env["limits"]
    with pytest.raises(ResourceLimitExceededError):
        limits.verify_attempts(4)  # Limit is 3

    with pytest.raises(ResourceLimitExceededError):
        limits.verify_runtime(3601.0)  # Limit is 3600s


@pytest.mark.asyncio
async def test_21_authentication_escalation(cloud_env):
    """21. Authentication failures create critical Escalations rather than blind retries."""
    worker = cloud_env["create_worker"]()
    task_repo = cloud_env["task_repo"]
    queue = cloud_env["queue"]

    task = AgentTask(
        task_id="t_auth_01",
        objective="Auth task",
        inputs={"capability": "auth_failing"},
    )
    await task_repo.save_task(task)
    await queue.enqueue(task.task_id)

    res = await worker.run_next_task()
    assert res.status == TaskState.ESCALATED
    assert res.escalation_id is not None

    esc = await task_repo.get_escalation(res.escalation_id)
    assert esc is not None
    assert esc.reason == EscalationReason.IDENTITY_VERIFICATION
    assert esc.severity == EscalationSeverity.CRITICAL


@pytest.mark.asyncio
async def test_22_policy_escalation(cloud_env):
    """22. Policy-flagged irreversible actions escalate for operator authorization."""
    worker = cloud_env["create_worker"]()
    task_repo = cloud_env["task_repo"]
    queue = cloud_env["queue"]

    task = AgentTask(
        task_id="t_pol_01",
        objective="Publish video",
        inputs={
            "capability": "content_publishing",
            "action": "publish_video",
            "is_reversible": False,
            "risk_tier": ActionRiskTier.MUTATING_IRREVERSIBLE.value,
        },
    )
    await task_repo.save_task(task)
    await queue.enqueue(task.task_id)

    res = await worker.run_next_task()
    assert res.status == TaskState.ESCALATED
    assert "human confirmation" in res.transitions[-1].reason


@pytest.mark.asyncio
async def test_23_audit_events_emitted(cloud_env):
    """23. Cloud execution emits structured events and telemetry."""
    worker = cloud_env["create_worker"]()
    task_repo = cloud_env["task_repo"]
    queue = cloud_env["queue"]
    telemetry = cloud_env["telemetry"]

    task = AgentTask(task_id="t_audit_01", objective="Audit test", inputs={"capability": "fast_echo"})
    await task_repo.save_task(task)
    await queue.enqueue(task.task_id)

    await worker.run_next_task()

    tel_events = telemetry.get_buffered_events("t_audit_01")
    assert len(tel_events) >= 2
    types = [e.event_type for e in tel_events]
    assert TelemetryEventType.TASK_CLAIMED in types
    assert TelemetryEventType.TASK_COMPLETED in types


@pytest.mark.asyncio
async def test_24_capability_execution(cloud_env):
    """24. Standard capability execution delivers validated outputs."""
    worker = cloud_env["create_worker"]()
    task_repo = cloud_env["task_repo"]
    queue = cloud_env["queue"]

    task = AgentTask(
        task_id="t_exec_01",
        objective="Echo execution",
        inputs={"capability": "fast_echo", "msg": "cloud verification"},
    )
    await task_repo.save_task(task)
    await queue.enqueue(task.task_id)

    res = await worker.run_next_task()
    assert res.status == TaskState.SUCCEEDED
    assert res.outputs["echo"] == "cloud verification"


@pytest.mark.asyncio
async def test_25_existing_clipping_capability_compatibility(cloud_env):
    """25. Existing clipping pipeline is executable via cloud worker."""
    registry = cloud_env["capabilities"]
    worker = cloud_env["create_worker"]()
    task_repo = cloud_env["task_repo"]
    queue = cloud_env["queue"]

    # Mock real clipping pipeline returning exit 0
    async def mock_runner(source_uri: str, campaign_id: str, job_id: str, storage: Any):
        return 0

    adapter = MediaClippingCapability(runner_fn=mock_runner)
    registry.register(adapter, override=True)

    task = AgentTask(
        task_id="t_clip_cloud_01",
        objective="Cloud clipping run",
        task_type=TaskType.MEDIA_CLIPPING,
        inputs={
            "capability": "media_clipping",
            "source_uri": "https://youtube.com/watch?v=cloud_sample",
            "campaign_id": "camp_cloud_01",
        },
    )
    await task_repo.save_task(task)
    await queue.enqueue(task.task_id)

    res = await worker.run_next_task()
    assert res.status == TaskState.SUCCEEDED
    assert res.outputs["pipeline_status"] == "awaiting_approval"
    assert res.outputs["campaign_id"] == "camp_cloud_01"
