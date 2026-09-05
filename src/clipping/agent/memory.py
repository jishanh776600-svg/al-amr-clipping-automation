"""Persistent Scoped Agent Memory Store backed by StorageDriver."""

import json
from enum import Enum
from typing import Any, Dict, List, Optional

from clipping.agent.events import mask_sensitive_data
from clipping.storage.base import StorageDriver
from clipping.logging.logger import get_logger

logger = get_logger("clipping.agent.memory")


class MemoryScope(str, Enum):
    """Hierarchical memory partitions separating distinct operational domains."""
    OPERATIONAL = "operational"
    TASK = "task"
    CAMPAIGN = "campaign"
    ACCOUNT = "account"
    WORKING = "working"


class AgentMemoryStore:
    """
    Durable, partitioned memory for the Master Agent.
    Persists structured state envelopes into the canonical StorageDriver (Google Drive / Local Vault).
    Enforces strict secret sanitization before storage.
    """

    def __init__(self, storage_driver: StorageDriver):
        self.storage = storage_driver

    def _build_key(self, scope: MemoryScope | str, key: str) -> str:
        clean_scope = scope.value if isinstance(scope, MemoryScope) else str(scope)
        clean_key = key.strip().lstrip("/")
        return f"memory/{clean_scope}/{clean_key}.json"

    async def get(self, scope: MemoryScope | str, key: str) -> Optional[Dict[str, Any]]:
        """Retrieves and deserializes a scoped memory document."""
        storage_key = self._build_key(scope, key)
        if not await self.storage.exists(storage_key):
            return None

        try:
            raw = await self.storage.download_bytes(storage_key)
            return json.loads(raw.decode("utf-8"))
        except Exception as e:
            logger.error("Failed to read agent memory", key=storage_key, error=str(e))
            return None

    async def set(self, scope: MemoryScope | str, key: str, value: Dict[str, Any]) -> None:
        """Sanitizes and persists a scoped memory document."""
        storage_key = self._build_key(scope, key)
        safe_value = mask_sensitive_data(value)
        payload = json.dumps(safe_value, indent=2).encode("utf-8")

        await self.storage.upload_bytes(
            data=payload,
            storage_key=storage_key,
            content_type="application/json",
        )
        logger.info("Persisted agent memory document", key=storage_key)

    async def delete(self, scope: MemoryScope | str, key: str) -> bool:
        """Deletes a scoped memory document if it exists."""
        storage_key = self._build_key(scope, key)
        if await self.storage.exists(storage_key):
            await self.storage.delete(storage_key)
            logger.info("Deleted agent memory document", key=storage_key)
            return True
        return False

    async def list_keys(self, scope: MemoryScope | str) -> List[str]:
        """Lists all keys existing within a specific memory scope."""
        clean_scope = scope.value if isinstance(scope, MemoryScope) else str(scope)
        prefix = f"memory/{clean_scope}/"
        try:
            files = await self.storage.list_files(prefix)
            keys: List[str] = []
            for f in files:
                k = f.storage_key
                if k.startswith(prefix) and k.endswith(".json"):
                    item_key = k[len(prefix):-5]
                    keys.append(item_key)
            return sorted(keys)
        except Exception as e:
            logger.error("Failed to list memory keys", scope=clean_scope, error=str(e))
            return []
