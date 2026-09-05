"""Cloud Execution Telemetry and Observability Engine."""

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ConfigDict

from clipping.agent.events import mask_sensitive_data
from clipping.storage.base import StorageDriver
from clipping.logging.logger import get_logger

logger = get_logger("clipping.agent.cloud.telemetry")


class TelemetryEventType(str, Enum):
    """Telemetry classification for cloud worker events."""
    WORKER_STARTED = "worker_started"
    TASK_CLAIMED = "task_claimed"
    HEARTBEAT_RENEWED = "heartbeat_renewed"
    CAPABILITY_STARTED = "capability_started"
    CAPABILITY_COMPLETED = "capability_completed"
    CHECKPOINT_SAVED = "checkpoint_saved"
    RETRY_SCHEDULED = "retry_scheduled"
    STALE_RECLAIMED = "stale_reclaimed"
    PREEMPTION_RECOVERED = "preemption_recovered"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    WORKER_STOPPED = "worker_stopped"


class CloudTelemetryEvent(BaseModel):
    """Structured telemetry envelope recorded per worker execution."""
    model_config = ConfigDict(frozen=True)

    telemetry_id: str = Field(default_factory=lambda: f"tel_{uuid.uuid4().hex[:12]}")
    event_type: TelemetryEventType
    task_id: str
    worker_id: str
    capability_name: Optional[str] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    duration_seconds: Optional[float] = None
    attempt_number: int = 1
    failure_classification: Optional[str] = None
    checkpoint_summary: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CloudTelemetryEngine:
    """Collects, masks, persists, and exports cloud worker execution telemetry."""

    def __init__(self, storage_driver: StorageDriver):
        self.storage = storage_driver
        self._buffer: List[CloudTelemetryEvent] = []

    async def record(
        self,
        event_type: TelemetryEventType,
        task_id: str,
        worker_id: str,
        capability_name: Optional[str] = None,
        duration_seconds: Optional[float] = None,
        attempt_number: int = 1,
        failure_classification: Optional[str] = None,
        checkpoint_summary: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> CloudTelemetryEvent:
        """Records and persists a telemetry event with strict secret masking."""
        safe_checkpoint = mask_sensitive_data(checkpoint_summary or {})
        safe_meta = mask_sensitive_data(metadata or {})

        event = CloudTelemetryEvent(
            event_type=event_type,
            task_id=task_id,
            worker_id=worker_id,
            capability_name=capability_name,
            duration_seconds=duration_seconds,
            attempt_number=attempt_number,
            failure_classification=failure_classification,
            checkpoint_summary=safe_checkpoint,
            metadata=safe_meta,
        )

        self._buffer.append(event)
        logger.info(
            f"Cloud Telemetry: {event_type.value}",
            telemetry_id=event.telemetry_id,
            task_id=task_id,
            worker_id=worker_id,
            capability=capability_name,
            duration=duration_seconds,
        )

        # Persist to remote storage
        storage_key = f"telemetry/{task_id}/{event.telemetry_id}.json"
        try:
            payload = event.model_dump_json(indent=2).encode("utf-8")
            await self.storage.upload_bytes(payload, storage_key, content_type="application/json")
        except Exception as e:
            logger.error("Failed to persist telemetry event", key=storage_key, error=str(e))

        return event

    def get_buffered_events(self, task_id: Optional[str] = None) -> List[CloudTelemetryEvent]:
        """Returns in-memory captured events."""
        if task_id:
            return [e for e in self._buffer if e.task_id == task_id]
        return list(self._buffer)
