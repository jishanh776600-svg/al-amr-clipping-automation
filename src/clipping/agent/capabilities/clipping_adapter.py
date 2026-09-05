"""Adapter exposing the canonical 9-stage video clipping pipeline as a Master Agent Capability."""

from typing import Any, Callable, Dict, Optional
from clipping.agent.capabilities.base import AgentCapability, CapabilityContext, CapabilityResult
from clipping.cli.pipeline_runner import run_pipeline
from clipping.logging.logger import get_logger

logger = get_logger("clipping.agent.capabilities.clipping")


class MediaClippingCapability(AgentCapability):
    """
    Thin, non-invasive adapter that bridges the Master Agent architecture
    with the battle-tested, 9-stage autonomous clipping pipeline.
    """

    def __init__(self, runner_fn: Optional[Callable[..., Any]] = None):
        self._runner = runner_fn or run_pipeline

    @property
    def name(self) -> str:
        return "media_clipping"

    @property
    def description(self) -> str:
        return "Executes the 9-stage autonomous vertical media clipping and reframing pipeline"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def is_idempotent(self) -> bool:
        return True

    @property
    def is_reversible(self) -> bool:
        return False  # Video rendering creates media artifacts

    async def execute(self, context: CapabilityContext) -> CapabilityResult:
        source_uri = context.inputs.get("source_uri")
        if not source_uri:
            return CapabilityResult.failed(
                error_type="MissingArgumentError",
                message="Missing required input 'source_uri'",
                is_transient=False,
            )

        campaign_id = context.inputs.get("campaign_id", context.campaign_id or "default_campaign")
        job_id = context.inputs.get("job_id", f"job_agent_{context.task_id[:12]}")

        logger.info(
            "Executing MediaClippingCapability",
            task_id=context.task_id,
            source_uri=source_uri,
            campaign_id=campaign_id,
            job_id=job_id,
        )

        try:
            exit_code = await self._runner(
                source_uri=source_uri,
                campaign_id=campaign_id,
                job_id=job_id,
                storage=context.storage_driver,
            )

            if exit_code == 0:
                return CapabilityResult.successful(
                    outputs={
                        "job_id": job_id,
                        "campaign_id": campaign_id,
                        "source_uri": source_uri,
                        "pipeline_status": "awaiting_approval",
                    },
                    checkpoint={"last_completed_job_id": job_id},
                )
            else:
                return CapabilityResult.failed(
                    error_type="ClippingPipelineError",
                    message=f"Clipping pipeline exited with nonzero code {exit_code}",
                    is_transient=True,
                    details={"job_id": job_id, "exit_code": exit_code},
                    should_retry=True,
                )

        except Exception as e:
            logger.error("MediaClippingCapability execution crashed", task_id=context.task_id, error=str(e))
            return CapabilityResult.failed(
                error_type=type(e).__name__,
                message=str(e),
                is_transient=True,
                details={"job_id": job_id},
                should_retry=True,
            )
