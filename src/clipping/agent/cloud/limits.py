"""Configurable Cloud Resource Limits and Execution Boundaries."""

from pydantic import BaseModel, Field, ConfigDict
from clipping.agent.exceptions import ResourceLimitExceededError


class CloudResourceLimits(BaseModel):
    """
    Explicit, auditable resource constraints governing cloud workers and tasks.
    Prevents runaway execution, cost overruns, and resource exhaustion.
    """
    model_config = ConfigDict(frozen=True)

    max_worker_runtime_seconds: int = Field(
        default=3600,
        ge=60,
        le=21600,
        description="Maximum execution seconds for a single cloud worker invocation (e.g. 60m default, 360m max)",
    )
    max_concurrent_workers: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Maximum active concurrent cloud workers across the system",
    )
    max_task_attempts: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Maximum total execution attempts permitted per task before permanent failure",
    )
    max_media_duration_seconds: float = Field(
        default=7200.0,
        ge=60.0,
        le=28800.0,
        description="Maximum source video duration in seconds (2 hours default, 8 hours ceiling)",
    )
    max_storage_bytes_per_job: int = Field(
        default=5 * 1024 * 1024 * 1024,  # 5 GB
        ge=100 * 1024 * 1024,
        description="Maximum storage volume permitted per job artifacts",
    )
    max_task_graph_depth: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum DAG dependency depth to prevent infinite planning loops",
    )

    def verify_runtime(self, elapsed_seconds: float) -> None:
        """Verifies elapsed runtime against max_worker_runtime_seconds."""
        if elapsed_seconds > self.max_worker_runtime_seconds:
            raise ResourceLimitExceededError(
                f"Worker runtime {elapsed_seconds:.1f}s exceeded limit of {self.max_worker_runtime_seconds}s"
            )

    def verify_attempts(self, current_attempt: int) -> None:
        """Verifies attempt count against max_task_attempts."""
        if current_attempt > self.max_task_attempts:
            raise ResourceLimitExceededError(
                f"Task attempt count {current_attempt} exceeded limit of {self.max_task_attempts}"
            )

    def verify_media_duration(self, duration_seconds: float) -> None:
        """Verifies media length against max_media_duration_seconds."""
        if duration_seconds > self.max_media_duration_seconds:
            raise ResourceLimitExceededError(
                f"Source media duration {duration_seconds:.1f}s exceeded limit of {self.max_media_duration_seconds:.1f}s"
            )
