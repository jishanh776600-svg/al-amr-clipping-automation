"""Durable Remote Storage State Repository."""

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ConfigDict
from clipping.state.models import (
    JobRecord,
    StateTransitionRecord,
    JobState,
    PipelineStage,
)
from clipping.state.repository import StateRepository
from clipping.storage.base import StorageDriver
from clipping.logging.logger import get_logger

logger = get_logger("clipping.state.remote")


class RemoteJobStateEnvelope(BaseModel):
    """Encapsulation of persistent job state and transition audit log in remote storage."""
    model_config = ConfigDict(frozen=True)

    job: JobRecord
    transitions: List[StateTransitionRecord] = Field(default_factory=list)
    version: int = Field(default=1, ge=1)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RemoteStorageStateRepository(StateRepository):
    """
    Zero-Cost, durable state repository persisting job lifecycles and transitions
    directly into StorageDriver (Google Drive / S3 / Local Vault) as structured JSON.
    Enables complete cross-job persistence across ephemeral GitHub Actions cloud runners.
    """

    def __init__(self, storage_driver: StorageDriver):
        self.storage_driver = storage_driver

    async def init_db(self) -> None:
        """No-op for storage-driver-backed state."""
        pass

    async def create_job(
        self,
        job_id: str,
        campaign_id: str,
        source_video_id: str,
        idempotency_key: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> JobRecord:
        state_key = f"jobs/{job_id}/state.json"
        idemp_key = f"jobs/by_idempotency/{idempotency_key}.json"

        # Check existing job
        if await self.storage_driver.exists(state_key):
            existing = await self.get_job(job_id)
            if existing:
                return existing

        now = datetime.now(timezone.utc)
        job = JobRecord(
            job_id=job_id,
            campaign_id=campaign_id,
            source_video_id=source_video_id,
            idempotency_key=idempotency_key,
            current_state=JobState.CREATED,
            current_stage=PipelineStage.INITIALIZATION,
            retry_count=0,
            error_message=None,
            metadata_json=metadata or {},
            created_at=now,
            updated_at=now,
        )

        initial_transition = StateTransitionRecord(
            id=1,
            job_id=job_id,
            from_state=JobState.CREATED,
            to_state=JobState.CREATED,
            stage=PipelineStage.INITIALIZATION,
            reason="Job initialized",
            metadata_json=metadata or {},
            created_at=now,
        )

        envelope = RemoteJobStateEnvelope(
            job=job,
            transitions=[initial_transition],
            version=1,
            updated_at=now,
        )

        # Upload state envelope
        await self.storage_driver.upload_bytes(
            data=envelope.model_dump_json(indent=2).encode("utf-8"),
            storage_key=state_key,
            content_type="application/json",
        )

        # Link idempotency key
        idemp_payload = {"job_id": job_id, "created_at": now.isoformat()}
        await self.storage_driver.upload_bytes(
            data=json.dumps(idemp_payload, indent=2).encode("utf-8"),
            storage_key=idemp_key,
            content_type="application/json",
        )

        logger.info("Created durable remote job state", job_id=job_id, state_key=state_key)
        return job

    async def get_job(self, job_id: str) -> Optional[JobRecord]:
        state_key = f"jobs/{job_id}/state.json"
        if not await self.storage_driver.exists(state_key):
            return None

        try:
            data = await self.storage_driver.download_bytes(state_key)
            envelope = RemoteJobStateEnvelope.model_validate_json(data.decode("utf-8"))
            return envelope.job
        except Exception as e:
            logger.error("Failed to read remote job state", job_id=job_id, error=str(e))
            return None

    async def get_job_by_idempotency_key(self, idempotency_key: str) -> Optional[JobRecord]:
        idemp_key = f"jobs/by_idempotency/{idempotency_key}.json"
        if not await self.storage_driver.exists(idemp_key):
            return None

        try:
            data = await self.storage_driver.download_bytes(idemp_key)
            payload = json.loads(data.decode("utf-8"))
            job_id = payload.get("job_id")
            if job_id:
                return await self.get_job(job_id)
        except Exception as e:
            logger.error("Failed to lookup idempotency key", idempotency_key=idempotency_key, error=str(e))

        return None

    async def update_job_state(
        self,
        job_id: str,
        new_state: JobState,
        new_stage: Optional[PipelineStage] = None,
        reason: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
    ) -> JobRecord:
        state_key = f"jobs/{job_id}/state.json"
        if not await self.storage_driver.exists(state_key):
            raise ValueError(f"Job not found in remote storage: {job_id}")

        data = await self.storage_driver.download_bytes(state_key)
        envelope = RemoteJobStateEnvelope.model_validate_json(data.decode("utf-8"))

        old_job = envelope.job
        from_state = old_job.current_state
        stage = new_stage or old_job.current_stage

        now = datetime.now(timezone.utc)
        updated_meta = dict(old_job.metadata_json)
        if metadata:
            updated_meta.update(metadata)

        updated_job = JobRecord(
            job_id=old_job.job_id,
            campaign_id=old_job.campaign_id,
            source_video_id=old_job.source_video_id,
            idempotency_key=old_job.idempotency_key,
            current_state=new_state,
            current_stage=stage,
            metadata_json=updated_meta,
            error_message=error_message or old_job.error_message,
            retry_count=old_job.retry_count + (1 if new_state == JobState.FAILED else 0),
            created_at=old_job.created_at,
            updated_at=now,
        )

        new_transition = StateTransitionRecord(
            id=len(envelope.transitions) + 1,
            job_id=job_id,
            from_state=from_state,
            to_state=new_state,
            stage=stage,
            reason=reason or f"Transitioned to {new_state.value}",
            metadata_json=metadata or {},
            created_at=now,
        )

        new_transitions = list(envelope.transitions) + [new_transition]
        new_envelope = RemoteJobStateEnvelope(
            job=updated_job,
            transitions=new_transitions,
            version=envelope.version + 1,
            updated_at=now,
        )

        await self.storage_driver.upload_bytes(
            data=new_envelope.model_dump_json(indent=2).encode("utf-8"),
            storage_key=state_key,
            content_type="application/json",
        )

        logger.info(
            "Updated durable remote job state",
            job_id=job_id,
            from_state=from_state.value,
            to_state=new_state.value,
            stage=stage.value,
            version=new_envelope.version,
        )
        return updated_job

    async def increment_retry_count(self, job_id: str) -> int:
        job = await self.get_job(job_id)
        if not job:
            raise ValueError(f"Job not found: {job_id}")
        updated = await self.update_job_state(
            job_id=job_id,
            new_state=JobState.FAILED,
            reason="Incrementing retry counter",
        )
        return updated.retry_count

    async def list_jobs(
        self,
        state: Optional[JobState] = None,
        limit: int = 50,
    ) -> List[JobRecord]:
        try:
            all_files = await self.storage_driver.list_files("jobs/")
            state_keys = [f.storage_key for f in all_files if f.storage_key.endswith("/state.json")]
            jobs: List[JobRecord] = []
            for k in state_keys[:limit]:
                data = await self.storage_driver.download_bytes(k)
                envelope = RemoteJobStateEnvelope.model_validate_json(data.decode("utf-8"))
                if state is None or envelope.job.current_state == state:
                    jobs.append(envelope.job)
            return jobs
        except Exception:
            return []

    async def list_transitions(self, job_id: str) -> List[StateTransitionRecord]:
        return await self.get_job_history(job_id)

    async def get_job_history(self, job_id: str) -> List[StateTransitionRecord]:
        state_key = f"jobs/{job_id}/state.json"
        if not await self.storage_driver.exists(state_key):
            return []

        try:
            data = await self.storage_driver.download_bytes(state_key)
            envelope = RemoteJobStateEnvelope.model_validate_json(data.decode("utf-8"))
            return envelope.transitions
        except Exception as e:
            logger.error("Failed to read job history", job_id=job_id, error=str(e))
            return []
