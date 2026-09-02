"""Durable Remote Storage Repository for Global Control State and Audit Logs."""

from typing import List, Optional
from clipping.control.models import (
    SystemControlState,
    SystemOperatingMode,
    ControlAuditRecord,
)
from clipping.storage.base import StorageDriver
from clipping.logging.logger import get_logger

logger = get_logger("clipping.control.repository")

CONTROL_STATE_KEY = "system/control_state.json"
AUDIT_PREFIX = "system/audit/"


class ControlRepository:
    """
    Manages durable persistence of global Master Control state in Google Drive.
    Guarantees that emergency stop, pause, and publishing locks persist across
    container restarts and cloud worker replacement.
    """

    def __init__(self, storage_driver: StorageDriver):
        self.storage_driver = storage_driver

    async def get_state(self) -> SystemControlState:
        """Retrieves the current canonical control state, or returns default operational mode."""
        if not await self.storage_driver.exists(CONTROL_STATE_KEY):
            return SystemControlState()

        try:
            raw = await self.storage_driver.download_bytes(CONTROL_STATE_KEY)
            return SystemControlState.model_validate_json(raw.decode("utf-8"))
        except Exception as e:
            logger.error("Failed to read durable control state, defaulting to safe mode", error=str(e))
            return SystemControlState()

    async def save_state(self, state: SystemControlState) -> None:
        """Persists the updated control state envelope to canonical storage."""
        data = state.model_dump_json(indent=2).encode("utf-8")
        await self.storage_driver.upload_bytes(data, CONTROL_STATE_KEY, content_type="application/json")
        logger.info(
            "Updated durable Master Control state in canonical storage",
            mode=state.mode.value,
            emergency_stopped=state.emergency_stopped,
            publishing_locked=state.publishing_locked,
            version=state.version,
        )

    async def is_emergency_stopped(self) -> bool:
        state = await self.get_state()
        return state.emergency_stopped

    async def is_automation_paused(self) -> bool:
        state = await self.get_state()
        return state.automation_paused or state.emergency_stopped

    async def is_publishing_locked(self) -> bool:
        state = await self.get_state()
        return state.publishing_locked or state.emergency_stopped

    async def record_audit(self, record: ControlAuditRecord) -> None:
        audit_key = f"{AUDIT_PREFIX}{record.audit_id}.json"
        data = record.model_dump_json(indent=2).encode("utf-8")
        await self.storage_driver.upload_bytes(data, audit_key, content_type="application/json")
        logger.info(
            "Recorded Master Control audit event",
            audit_id=record.audit_id,
            action=record.action,
            operator=record.operator,
        )

    async def list_audits(self, limit: int = 50) -> List[ControlAuditRecord]:
        try:
            files = await self.storage_driver.list_files(AUDIT_PREFIX)
            audits: List[ControlAuditRecord] = []
            for f in files:
                if not f.storage_key.endswith(".json"):
                    continue
                raw = await self.storage_driver.download_bytes(f.storage_key)
                aud = ControlAuditRecord.model_validate_json(raw.decode("utf-8"))
                audits.append(aud)
            audits.sort(key=lambda a: a.timestamp, reverse=True)
            return audits[:limit]
        except Exception as e:
            logger.error("Failed to list Master Control audits", error=str(e))
            return []
