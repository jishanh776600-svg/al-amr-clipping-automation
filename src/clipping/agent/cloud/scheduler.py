"""Cloud Task Scheduler coordinating execution queues, dispatchers, and recurring execution hooks."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from clipping.agent.cloud.queue import CloudTaskQueue, QueueItem
from clipping.agent.models import AgentTask, TaskPriority
from clipping.agent.repository import AgentTaskRepository
from clipping.control.github import GitHubWorkflowDispatcher
from clipping.storage.base import StorageDriver
from clipping.logging.logger import get_logger

logger = get_logger("clipping.agent.cloud.scheduler")


class CloudTaskScheduler:
    """
    Coordinates task queueing, priority scheduling, delayed retries,
    and optional dispatch of cloud runners via GitHub Actions.
    """

    def __init__(
        self,
        queue: CloudTaskQueue,
        task_repository: AgentTaskRepository,
        dispatcher: Optional[GitHubWorkflowDispatcher] = None,
    ):
        self.queue = queue
        self.task_repo = task_repository
        self.dispatcher = dispatcher or GitHubWorkflowDispatcher()

    async def schedule(
        self,
        task: AgentTask,
        delay_seconds: float = 0.0,
        dispatch_cloud: bool = False,
    ) -> QueueItem:
        """
        Persists a task into the task repository, pushes it into the durable queue,
        and optionally triggers an ephemeral GitHub Actions cloud worker runner.
        """
        await self.task_repo.save_task(task)

        item = await self.queue.enqueue(
            task_id=task.task_id,
            priority=int(task.priority),
            delay_seconds=delay_seconds,
            metadata={"objective": task.objective, "campaign_id": task.campaign_id},
        )

        if dispatch_cloud and self.dispatcher.is_configured:
            dispatched, msg = await self.dispatcher.dispatch_workflow(
                workflow_name="agent_worker.yml",
                inputs={"task_id": task.task_id},
            )
            logger.info("Dispatched cloud worker for task", task_id=task.task_id, success=dispatched, message=msg)

        return item

    async def schedule_delayed_retry(
        self,
        task_id: str,
        attempt_number: int,
        delay_seconds: float,
    ) -> QueueItem:
        """Enqueues a task for delayed retry with exponential backoff."""
        task = await self.task_repo.get_task(task_id)
        priority = int(task.priority) if task else int(TaskPriority.NORMAL)
        return await self.queue.enqueue(
            task_id=task_id,
            priority=priority,
            delay_seconds=delay_seconds,
            metadata={"retry_attempt": attempt_number},
        )

    async def cancel(self, task_id: str) -> None:
        """Cancels a scheduled or pending task across queue and repository."""
        await self.queue.cancel(task_id)

    async def poll_stale_and_reclaim(self) -> List[str]:
        """Scans for and reclaims tasks abandoned by crashed cloud workers."""
        return await self.queue.reclaim_stale_tasks()

    async def get_status(self) -> Dict[str, Any]:
        """Provides an overview of current scheduling and queue depths."""
        depth = await self.queue.get_queue_depth()
        return {
            "queue_depth": depth,
            "cloud_dispatcher_configured": self.dispatcher.is_configured,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
