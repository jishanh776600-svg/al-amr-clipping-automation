"""Durable Remote Storage Repository for Master Agent Tasks and Escalations."""

import json
from typing import Any, Dict, List, Optional
from clipping.agent.models import AgentTask
from clipping.agent.state import TaskState
from clipping.agent.escalation import EscalationContext, EscalationReason, EscalationRecord, EscalationSeverity, EscalationStatus
from clipping.storage.base import StorageDriver
from clipping.logging.logger import get_logger

logger = get_logger("clipping.agent.repository")


class AgentTaskRepository:
    """
    Persists tasks, escalations, and state histories into StorageDriver.
    Ensures zero data loss across ephemeral GitHub Actions cloud runners.
    """

    def __init__(self, storage_driver: StorageDriver, escalation_notifier: Optional[Any] = None):
        self.storage = storage_driver
        self.escalation_notifier = escalation_notifier

    def _task_key(self, task_id: str) -> str:
        return f"tasks/{task_id}/task.json"

    def _escalation_key(self, escalation_id: str) -> str:
        return f"escalations/{escalation_id}/escalation.json"

    async def save_task(self, task: AgentTask) -> None:
        """Saves or updates an agent task document and status pointer."""
        task_key = self._task_key(task.task_id)
        payload = task.model_dump_json(indent=2).encode("utf-8")
        await self.storage.upload_bytes(payload, task_key, content_type="application/json")

        # Index by status pointer
        status_key = f"tasks/by_status/{task.status.value}/{task.task_id}.json"
        ptr = json.dumps({"task_id": task.task_id, "updated_at": task.updated_at.isoformat()}).encode("utf-8")
        await self.storage.upload_bytes(ptr, status_key, content_type="application/json")

        if task.campaign_id:
            campaign_key = f"tasks/by_campaign/{task.campaign_id}/{task.task_id}.json"
            await self.storage.upload_bytes(ptr, campaign_key, content_type="application/json")

        logger.info("Saved agent task to storage", task_id=task.task_id, status=task.status.value)

    async def get_task(self, task_id: str) -> Optional[AgentTask]:
        """Retrieves a task by task_id."""
        task_key = self._task_key(task_id)
        if not await self.storage.exists(task_key):
            return None
        try:
            raw = await self.storage.download_bytes(task_key)
            return AgentTask.model_validate_json(raw.decode("utf-8"))
        except Exception as e:
            logger.error("Failed to read agent task", task_id=task_id, error=str(e))
            return None

    async def update_task_state(
        self,
        task_id: str,
        new_state: TaskState,
        reason: Optional[str] = None,
        actor: str = "agent",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AgentTask:
        """Transitions a task to a new state and persists the change atomically."""
        task = await self.get_task(task_id)
        if not task:
            raise ValueError(f"Task not found: {task_id}")

        updated_task = task.transition_to(new_state=new_state, reason=reason, actor=actor, metadata=metadata)
        await self.save_task(updated_task)
        return updated_task

    async def list_tasks(
        self,
        status: Optional[TaskState] = None,
        campaign_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[AgentTask]:
        """Lists tasks filtered optionally by status or campaign."""
        try:
            if status:
                prefix = f"tasks/by_status/{status.value}/"
                files = await self.storage.list_files(prefix)
                task_ids = [f.storage_key.split("/")[-1].replace(".json", "") for f in files if f.storage_key.endswith(".json")]
            elif campaign_id:
                prefix = f"tasks/by_campaign/{campaign_id}/"
                files = await self.storage.list_files(prefix)
                task_ids = [f.storage_key.split("/")[-1].replace(".json", "") for f in files if f.storage_key.endswith(".json")]
            else:
                files = await self.storage.list_files("tasks/")
                task_ids = [
                    f.storage_key.split("/")[1]
                    for f in files
                    if f.storage_key.endswith("/task.json")
                ]

            tasks: List[AgentTask] = []
            for tid in task_ids[:limit]:
                t = await self.get_task(tid)
                if t:
                    tasks.append(t)
            tasks.sort(key=lambda x: x.created_at, reverse=True)
            return tasks
        except Exception as e:
            logger.error("Failed to list agent tasks", error=str(e))
            return []

    async def save_escalation(self, escalation: EscalationRecord) -> None:
        """Persists an escalation record to storage."""
        esc_key = self._escalation_key(escalation.escalation_id)
        payload = escalation.model_dump_json(indent=2).encode("utf-8")
        await self.storage.upload_bytes(payload, esc_key, content_type="application/json")

        status_key = f"escalations/by_status/{escalation.status.value}/{escalation.escalation_id}.json"
        ptr = json.dumps({"escalation_id": escalation.escalation_id, "task_id": escalation.task_id}).encode("utf-8")
        await self.storage.upload_bytes(ptr, status_key, content_type="application/json")
        logger.info("Saved escalation record", escalation_id=escalation.escalation_id, status=escalation.status.value)

    async def create_escalation(
        self,
        context: EscalationContext,
        task_id: Optional[str] = None,
        campaign_id: Optional[str] = None,
        reason: Optional[EscalationReason] = None,
        severity: Optional[EscalationSeverity] = None,
    ) -> EscalationRecord:
        """Helper to create and persist an EscalationRecord directly from an EscalationContext."""
        import uuid
        from clipping.agent.escalation import EscalationReason, EscalationSeverity

        effective_task_id = task_id or context.metadata.get("task_id", f"task_{uuid.uuid4().hex[:8]}")
        effective_reason = (
            reason
            or context.reason
            or (EscalationReason(context.metadata["reason"]) if "reason" in context.metadata else EscalationReason.UNCLASSIFIED_FAILURE)
        )
        effective_severity = (
            severity
            or context.severity
            or (EscalationSeverity(context.metadata["severity"]) if "severity" in context.metadata else EscalationSeverity.MEDIUM)
        )

        record = EscalationRecord(
            escalation_id=f"esc_{uuid.uuid4().hex[:12]}",
            task_id=effective_task_id,
            campaign_id=campaign_id or context.metadata.get("campaign_id"),
            reason=effective_reason,
            severity=effective_severity,
            context=context,
        )
        await self.save_escalation(record)

        # Dispatch real-time operator alert via Telegram if configured
        notifier = self.escalation_notifier
        if notifier is None:
            try:
                from clipping.approval.escalation_notifier import TelegramEscalationNotifier
                notifier = TelegramEscalationNotifier()
            except Exception:
                notifier = None

        if notifier and getattr(notifier, "is_configured", False):
            try:
                await notifier.notify(record)
            except Exception as ex:
                logger.error("Failed to notify escalation via Telegram", error=str(ex))

        return record

    async def get_escalation(self, escalation_id: str) -> Optional[EscalationRecord]:
        """Retrieves an escalation record by id."""
        esc_key = self._escalation_key(escalation_id)
        if not await self.storage.exists(esc_key):
            return None
        try:
            raw = await self.storage.download_bytes(esc_key)
            return EscalationRecord.model_validate_json(raw.decode("utf-8"))
        except Exception as e:
            logger.error("Failed to read escalation record", escalation_id=escalation_id, error=str(e))
            return None

    async def list_escalations(
        self,
        status: Optional[EscalationStatus] = None,
        limit: int = 50,
    ) -> List[EscalationRecord]:
        """Lists escalations filtered optionally by status up to limit."""
        try:
            if status:
                prefix = f"escalations/by_status/{status.value}/"
                files = await self.storage.list_files(prefix)
                ids = [f.storage_key.split("/")[-1].replace(".json", "") for f in files if f.storage_key.endswith(".json")]
            else:
                files = await self.storage.list_files("escalations/")
                ids = [
                    f.storage_key.split("/")[1]
                    for f in files
                    if f.storage_key.endswith("/escalation.json")
                ]

            records: List[EscalationRecord] = []
            for eid in ids[:limit]:
                rec = await self.get_escalation(eid)
                if rec:
                    records.append(rec)
            records.sort(key=lambda x: x.created_at, reverse=True)
            return records
        except Exception as e:
            logger.error("Failed to list escalations", error=str(e))
            return []


# Convenient alias
TaskRepository = AgentTaskRepository
