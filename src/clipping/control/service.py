"""Master Control Domain Service managing Global Operating Modes and Job Control."""

import uuid
from datetime import datetime, timezone
from typing import Optional
from clipping.control.models import (
    SystemControlState,
    SystemOperatingMode,
    ControlAuditRecord,
)
from clipping.control.repository import ControlRepository
from clipping.state.models import JobState, PipelineStage
from clipping.state.remote import RemoteStorageStateRepository
from clipping.storage.base import StorageDriver
from clipping.logging.logger import get_logger

logger = get_logger("clipping.control.service")


class MasterControlService:
    """
    Executes authoritative Master Control state transitions:
    - Emergency Stop (cooperative cancellation + global execution blocking)
    - Automation Pause and Resume
    - Global YouTube Publishing Lock
    - Job Cancellation, Retry, and Requeue
    """

    def __init__(
        self,
        control_repository: ControlRepository,
        state_repository: RemoteStorageStateRepository,
        storage_driver: StorageDriver,
    ):
        self.control_repo = control_repository
        self.state_repo = state_repository
        self.storage = storage_driver

    async def get_state(self) -> SystemControlState:
        return await self.control_repo.get_state()

    async def emergency_stop(self, operator: str, reason: str) -> SystemControlState:
        """
        Activates global EMERGENCY STOP:
        - Blocks any new ingestion or clipping workflows
        - Blocks all YouTube publishing
        - Signals cooperative cancellation
        """
        current = await self.control_repo.get_state()
        now = datetime.now(timezone.utc)

        updated = current.model_copy(update={
            "mode": SystemOperatingMode.EMERGENCY_STOPPED,
            "emergency_stopped": True,
            "automation_paused": True,
            "publishing_locked": True,
            "last_changed_by": operator,
            "reason": reason,
            "updated_at": now,
            "version": current.version + 1,
        })
        await self.control_repo.save_state(updated)

        audit = ControlAuditRecord(
            audit_id=f"audit_stop_{uuid.uuid4().hex[:10]}",
            action="EMERGENCY_STOP",
            previous_mode=current.mode,
            new_mode=updated.mode,
            operator=operator,
            reason=reason,
            timestamp=now,
        )
        await self.control_repo.record_audit(audit)
        logger.error("EMERGENCY STOP TRIGGERED BY OPERATOR", operator=operator, reason=reason)
        return updated

    async def resume_automation(self, operator: str, reason: Optional[str] = None) -> SystemControlState:
        """Clears emergency stop and pause states, restoring normal autonomous operations."""
        current = await self.control_repo.get_state()
        now = datetime.now(timezone.utc)

        updated = current.model_copy(update={
            "mode": SystemOperatingMode.OPERATIONAL,
            "emergency_stopped": False,
            "automation_paused": False,
            "publishing_locked": False,
            "last_changed_by": operator,
            "reason": reason or "Normal operations resumed",
            "updated_at": now,
            "version": current.version + 1,
        })
        await self.control_repo.save_state(updated)

        audit = ControlAuditRecord(
            audit_id=f"audit_resume_{uuid.uuid4().hex[:10]}",
            action="RESUME_AUTOMATION",
            previous_mode=current.mode,
            new_mode=updated.mode,
            operator=operator,
            reason=reason,
            timestamp=now,
        )
        await self.control_repo.record_audit(audit)
        logger.info("Automation resumed by operator", operator=operator)
        return updated

    async def pause_automation(self, operator: str, reason: Optional[str] = None) -> SystemControlState:
        """Temporarily pauses scheduling and ingestion without triggering emergency locks."""
        current = await self.control_repo.get_state()
        now = datetime.now(timezone.utc)

        updated = current.model_copy(update={
            "mode": SystemOperatingMode.AUTOMATION_PAUSED,
            "automation_paused": True,
            "last_changed_by": operator,
            "reason": reason or "Automation paused by operator",
            "updated_at": now,
            "version": current.version + 1,
        })
        await self.control_repo.save_state(updated)

        audit = ControlAuditRecord(
            audit_id=f"audit_pause_{uuid.uuid4().hex[:10]}",
            action="PAUSE_AUTOMATION",
            previous_mode=current.mode,
            new_mode=updated.mode,
            operator=operator,
            reason=reason,
            timestamp=now,
        )
        await self.control_repo.record_audit(audit)
        logger.info("Automation paused by operator", operator=operator)
        return updated

    async def set_publishing_lock(self, locked: bool, operator: str, reason: Optional[str] = None) -> SystemControlState:
        """Enables or disables the global YouTube publishing lock."""
        current = await self.control_repo.get_state()
        now = datetime.now(timezone.utc)

        updated = current.model_copy(update={
            "publishing_locked": locked,
            "last_changed_by": operator,
            "reason": reason,
            "updated_at": now,
            "version": current.version + 1,
        })
        await self.control_repo.save_state(updated)

        audit = ControlAuditRecord(
            audit_id=f"audit_publock_{uuid.uuid4().hex[:10]}",
            action="LOCK_PUBLISHING" if locked else "UNLOCK_PUBLISHING",
            previous_mode=current.mode,
            new_mode=current.mode,
            operator=operator,
            reason=reason,
            timestamp=now,
        )
        await self.control_repo.record_audit(audit)
        logger.info("Publishing lock updated", locked=locked, operator=operator)
        return updated

    async def cancel_job(self, job_id: str, operator: str, reason: str) -> None:
        """Cancels an active or queued job cooperatively."""
        job = await self.state_repo.get_job(job_id)
        if not job:
            raise ValueError(f"Job not found: {job_id}")

        await self.state_repo.update_job_state(
            job_id=job_id,
            new_state=JobState.FAILED,
            new_stage=PipelineStage.INGESTION,
            reason=f"Cancelled by operator {operator}: {reason}",
        )
        logger.warning("Job cancelled by operator", job_id=job_id, operator=operator, reason=reason)

    async def retry_job(self, job_id: str, operator: str, reason: str) -> None:
        """Resets a failed or cancelled job back to CREATED for worker re-execution."""
        job = await self.state_repo.get_job(job_id)
        if not job:
            raise ValueError(f"Job not found: {job_id}")

        await self.state_repo.update_job_state(
            job_id=job_id,
            new_state=JobState.CREATED,
            new_stage=PipelineStage.INGESTION,
            reason=f"Retried by operator {operator}: {reason}",
        )
        logger.info("Job requeued for retry by operator", job_id=job_id, operator=operator)

    async def requeue_job(self, job_id: str, operator: str, reason: str) -> None:
        return await self.retry_job(job_id, operator, reason)
