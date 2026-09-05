"""Structured Audit and Event Emission System with Strict Credential Masking."""

import json
import re
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
from pydantic import BaseModel, Field, ConfigDict

from clipping.storage.base import StorageDriver
from clipping.logging.logger import get_logger

logger = get_logger("clipping.agent.events")

SENSITIVE_KEY_PATTERNS = re.compile(
    r"(token|secret|password|key|auth|bearer|credential|refresh_token|client_secret)",
    re.IGNORECASE,
)


def mask_sensitive_data(obj: Any) -> Any:
    """Recursively masks secret values in dictionaries, lists, and strings."""
    if isinstance(obj, dict):
        masked: Dict[str, Any] = {}
        for k, v in obj.items():
            if SENSITIVE_KEY_PATTERNS.search(str(k)):
                masked[k] = "<MASKED_SECRET>"
            else:
                masked[k] = mask_sensitive_data(v)
        return masked
    elif isinstance(obj, list):
        return [mask_sensitive_data(item) for item in obj]
    elif isinstance(obj, str):
        # Mask typical token patterns e.g. bot token or bearer
        masked_str = re.sub(r"/bot[0-9]+:[A-Za-z0-9_-]+/", "/bot<MASKED_TOKEN>/", obj)
        masked_str = re.sub(r"Bearer\s+[A-Za-z0-9_\-\.]+", "Bearer <MASKED_TOKEN>", masked_str, flags=re.IGNORECASE)
        return masked_str
    return obj


class AgentEventType(str, Enum):
    """Categorical audit and telemetry event types emitted by the Master Agent."""
    TASK_CREATED = "task_created"
    TASK_STARTED = "task_started"
    CAPABILITY_INVOKED = "capability_invoked"
    TASK_SUCCEEDED = "task_succeeded"
    TASK_FAILED = "task_failed"
    RETRY_SCHEDULED = "retry_scheduled"
    POLICY_DECISION = "policy_decision"
    ESCALATION_CREATED = "escalation_created"
    HUMAN_DECISION = "human_decision"
    TASK_RESUMED = "task_resumed"
    TASK_CANCELLED = "task_cancelled"


class AgentEvent(BaseModel):
    """Structured, immutable audit event record."""
    model_config = ConfigDict(frozen=True)

    event_id: str = Field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:12]}")
    event_type: AgentEventType
    task_id: str
    campaign_id: Optional[str] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    actor: str = "agent"
    details: Dict[str, Any] = Field(default_factory=dict)
    schema_version: str = "1.0"


class AgentEventSystem:
    """
    Coordinates audit trail persistence and real-time event distribution.
    Persists events durably into StorageDriver and guarantees zero secret leakage.
    """

    def __init__(self, storage_driver: Optional[StorageDriver] = None):
        self.storage = storage_driver
        self._handlers: List[Callable[[AgentEvent], None]] = []
        self._in_memory_log: List[AgentEvent] = []

    def add_handler(self, handler: Callable[[AgentEvent], None]) -> None:
        """Registers an asynchronous or synchronous event observer callback."""
        self._handlers.append(handler)

    async def emit(
        self,
        event_type: AgentEventType,
        task_id: str,
        details: Optional[Dict[str, Any]] = None,
        campaign_id: Optional[str] = None,
        actor: str = "agent",
    ) -> AgentEvent:
        """Constructs, masks, records, and emits a structured audit event."""
        safe_details = mask_sensitive_data(details or {})

        event = AgentEvent(
            event_type=event_type,
            task_id=task_id,
            campaign_id=campaign_id,
            actor=actor,
            details=safe_details,
        )

        self._in_memory_log.append(event)
        logger.info(
            f"Agent Event: {event_type.value}",
            event_id=event.event_id,
            task_id=task_id,
            actor=actor,
        )

        # Notify registered subscribers
        for handler in self._handlers:
            try:
                res = handler(event)
                import asyncio
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:
                logger.warning("Event handler error", handler=str(handler), error=str(e))

        # Persist to StorageDriver if configured
        if self.storage:
            storage_key = f"audit/events/{task_id}/{event.event_id}.json"
            try:
                payload = event.model_dump_json(indent=2).encode("utf-8")
                await self.storage.upload_bytes(
                    data=payload,
                    storage_key=storage_key,
                    content_type="application/json",
                )
            except Exception as e:
                logger.error("Failed to persist audit event to storage", key=storage_key, error=str(e))

        return event

    def get_in_memory_events(self, task_id: Optional[str] = None) -> List[AgentEvent]:
        """Returns captured in-memory events filtered optionally by task_id."""
        if task_id:
            return [e for e in self._in_memory_log if e.task_id == task_id]
        return list(self._in_memory_log)
