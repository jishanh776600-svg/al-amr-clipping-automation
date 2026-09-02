"""State Repository and Persistence Layer."""

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from clipping.state.models import (
    Base,
    JobModel,
    StateTransitionModel,
    JobRecord,
    StateTransitionRecord,
    JobState,
    PipelineStage,
)


class StateRepository(ABC):
    """Abstract interface for pipeline job state management."""

    @abstractmethod
    async def init_db(self) -> None:
        """Initializes database tables."""
        pass

    @abstractmethod
    async def create_job(
        self,
        job_id: str,
        campaign_id: str,
        source_video_id: str,
        idempotency_key: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> JobRecord:
        """Creates a new pipeline job."""
        pass

    @abstractmethod
    async def get_job(self, job_id: str) -> Optional[JobRecord]:
        """Retrieves a job by its unique ID."""
        pass

    @abstractmethod
    async def get_job_by_idempotency_key(self, idempotency_key: str) -> Optional[JobRecord]:
        """Retrieves a job by its unique idempotency key."""
        pass

    @abstractmethod
    async def update_job_state(
        self,
        job_id: str,
        new_state: JobState,
        new_stage: Optional[PipelineStage] = None,
        reason: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
    ) -> JobRecord:
        """Transitions a job to a new state and records the transition."""
        pass

    @abstractmethod
    async def increment_retry_count(self, job_id: str) -> int:
        """Increments the retry counter for a job."""
        pass

    @abstractmethod
    async def list_jobs(
        self,
        state: Optional[JobState] = None,
        limit: int = 50
    ) -> List[JobRecord]:
        """Lists jobs with optional state filtering."""
        pass

    @abstractmethod
    async def list_transitions(self, job_id: str) -> List[StateTransitionRecord]:
        """Lists historical state transitions for a job."""
        pass


class SqlAlchemyStateRepository(StateRepository):
    """SQLAlchemy-backed implementation supporting SQLite and PostgreSQL asynchronously."""

    def __init__(self, db_url: str):
        self.engine = create_async_engine(db_url, echo=False)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def init_db(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def create_job(
        self,
        job_id: str,
        campaign_id: str,
        source_video_id: str,
        idempotency_key: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> JobRecord:
        now = datetime.now(timezone.utc)
        job_model = JobModel(
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

        async with self.session_factory() as session:
            async with session.begin():
                session.add(job_model)

        return JobRecord.model_validate(job_model)

    async def get_job(self, job_id: str) -> Optional[JobRecord]:
        async with self.session_factory() as session:
            result = await session.execute(select(JobModel).where(JobModel.job_id == job_id))
            job = result.scalar_one_or_none()
            if job:
                return JobRecord.model_validate(job)
            return None

    async def get_job_by_idempotency_key(self, idempotency_key: str) -> Optional[JobRecord]:
        async with self.session_factory() as session:
            result = await session.execute(
                select(JobModel).where(JobModel.idempotency_key == idempotency_key)
            )
            job = result.scalar_one_or_none()
            if job:
                return JobRecord.model_validate(job)
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
        async with self.session_factory() as session:
            async with session.begin():
                result = await session.execute(select(JobModel).where(JobModel.job_id == job_id))
                job = result.scalar_one_or_none()
                if not job:
                    raise KeyError(f"Job not found: {job_id}")

                from_state = job.current_state
                stage_to_record = new_stage or job.current_stage

                # Update job fields
                job.current_state = new_state
                if new_stage:
                    job.current_stage = new_stage
                if error_message is not None:
                    job.error_message = error_message
                if metadata:
                    merged = dict(job.metadata_json)
                    merged.update(metadata)
                    job.metadata_json = merged
                job.updated_at = datetime.now(timezone.utc)

                # Record transition
                transition = StateTransitionModel(
                    job_id=job_id,
                    from_state=from_state,
                    to_state=new_state,
                    stage=stage_to_record,
                    reason=reason,
                    transition_metadata=metadata or {},
                    created_at=datetime.now(timezone.utc),
                )
                session.add(transition)

        return await self.get_job(job_id)  # type: ignore

    async def increment_retry_count(self, job_id: str) -> int:
        async with self.session_factory() as session:
            async with session.begin():
                result = await session.execute(select(JobModel).where(JobModel.job_id == job_id))
                job = result.scalar_one_or_none()
                if not job:
                    raise KeyError(f"Job not found: {job_id}")
                job.retry_count += 1
                job.updated_at = datetime.now(timezone.utc)
                count = job.retry_count
        return count

    async def list_jobs(
        self,
        state: Optional[JobState] = None,
        limit: int = 50
    ) -> List[JobRecord]:
        async with self.session_factory() as session:
            query = select(JobModel).order_by(JobModel.created_at.desc()).limit(limit)
            if state:
                query = query.where(JobModel.current_state == state)
            result = await session.execute(query)
            jobs = result.scalars().all()
            return [JobRecord.model_validate(j) for j in jobs]

    async def list_transitions(self, job_id: str) -> List[StateTransitionRecord]:
        async with self.session_factory() as session:
            query = (
                select(StateTransitionModel)
                .where(StateTransitionModel.job_id == job_id)
                .order_by(StateTransitionModel.created_at.asc())
            )
            result = await session.execute(query)
            records = result.scalars().all()
            return [StateTransitionRecord.model_validate(r) for r in records]
