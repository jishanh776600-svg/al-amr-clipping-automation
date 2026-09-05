"""CLI Entrypoint for Headless Cloud Agent Worker."""

import argparse
import asyncio
import os
import sys

from clipping.agent.capabilities.clipping_adapter import MediaClippingCapability
from clipping.agent.capabilities.registry import CapabilityRegistry
from clipping.agent.cloud.limits import CloudResourceLimits
from clipping.agent.cloud.worker import CloudAgentWorker
from clipping.agent.policy import PolicyEngine
from clipping.agent.repository import AgentTaskRepository
from clipping.agent.state import TaskState
from clipping.control.repository import ControlRepository
from clipping.logging.logger import get_logger
from clipping.storage.factory import StorageFactory

logger = get_logger("clipping.cli.agent_worker")


async def main_async(args: argparse.Namespace) -> int:
    """Asynchronous execution logic for cloud agent worker."""
    storage_driver = StorageFactory.create()
    worker_id = args.worker_id or os.getenv("GITHUB_RUN_ID", f"runner_{os.getpid()}")

    logger.info("Initializing cloud agent worker", worker_id=worker_id, storage=type(storage_driver).__name__)

    # Register default capabilities including existing 9-stage clipping pipeline
    cap_registry = CapabilityRegistry()
    cap_registry.register(MediaClippingCapability())

    worker = CloudAgentWorker(
        worker_id=worker_id,
        capabilities=cap_registry,
        storage_driver=storage_driver,
    )

    if args.task_id:
        logger.info("Executing targeted task", task_id=args.task_id)
        # Attempt to claim target task directly or execute
        result_task = await worker.execute_claimed_task(args.task_id)
        return 0 if result_task.status in (TaskState.SUCCEEDED, TaskState.ESCALATED, TaskState.DEFERRED) else 1

    elif args.poll_queue:
        logger.info("Polling cloud task queue for pending work", max_tasks=args.max_tasks)
        tasks_executed = 0
        while tasks_executed < args.max_tasks:
            executed_task = await worker.run_next_task()
            if not executed_task:
                logger.info("No pending tasks ready for execution; worker idle")
                break
            tasks_executed += 1
            logger.info("Completed task from queue", task_id=executed_task.task_id, status=executed_task.status.value)
        return 0

    else:
        logger.error("Must specify either --task-id or --poll-queue")
        return 2


def main() -> None:
    parser = argparse.ArgumentParser(description="Headless Cloud Agent Worker")
    parser.add_argument("--task-id", type=str, default=None, help="Target task ID to claim and execute")
    parser.add_argument("--poll-queue", action="store_true", help="Poll queue for highest priority pending tasks")
    parser.add_argument("--max-tasks", type=int, default=1, help="Max tasks to execute before runner exits cleanly")
    parser.add_argument("--worker-id", type=str, default=None, help="Explicit worker identity")
    args = parser.parse_args()

    exit_code = asyncio.run(main_async(args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
