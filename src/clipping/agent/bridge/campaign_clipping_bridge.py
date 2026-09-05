"""Campaign to Production Clipping Pipeline Bridge."""

import hashlib
from typing import Any, Dict, Optional
from clipping.agent.campaign.models import CampaignRecord
from clipping.agent.cloud.queue import CloudTaskQueue
from clipping.agent.models import AgentTask, TaskPriority, TaskType
from clipping.agent.repository import TaskRepository
from clipping.logging.logger import get_logger

logger = get_logger("clipping.agent.bridge.clipping")


class CampaignClippingBridge:
    """
    Bridges autonomous campaign discovery and account evaluation directly into the
    existing 9-stage REAL production video clipping pipeline via CloudTaskQueue and MediaClippingCapability.
    Guarantees no dummy media, no mocked pipelines, and deterministic task creation.
    """

    def __init__(
        self,
        queue: CloudTaskQueue,
        task_repository: TaskRepository,
    ):
        self.queue = queue
        self.task_repo = task_repository

    async def create_and_enqueue_clipping_job(
        self,
        campaign: CampaignRecord,
        source_uri: str,
        account_id: Optional[str] = None,
        custom_job_id: Optional[str] = None,
    ) -> AgentTask:
        """
        Validates source URL and enqueues a REAL media clipping pipeline task.
        The worker executing this task invokes MediaClippingCapability which runs
        the full 9-stage pipeline (Ingest -> Transcribe -> Understand -> Discover -> Reframe -> Render -> QA -> Approval -> Publish).
        """
        if not source_uri or not (source_uri.startswith("http://") or source_uri.startswith("https://")):
            raise ValueError(f"Invalid real media source URI for production clipping pipeline: '{source_uri}'")

        if not campaign.is_eligible_source_url(source_uri):
            raise ValueError(f"Source URI '{source_uri}' does not satisfy campaign '{campaign.campaign_id}' criteria")

        url_hash = hashlib.sha256(source_uri.encode("utf-8")).hexdigest()[:8]
        cid_slug = campaign.campaign_id.replace("camp_", "")[:16]
        task_id = f"task_clip_{cid_slug}_{url_hash}"
        job_id = custom_job_id or f"job_{cid_slug}_{url_hash}"

        task = AgentTask(
            task_id=task_id,
            task_type=TaskType.MEDIA_CLIPPING,
            objective=f"Autonomous 9-stage clipping for campaign '{campaign.name}' from {source_uri}",
            inputs={
                "capability": "media_clipping",
                "source_uri": source_uri,
                "campaign_id": campaign.campaign_id,
                "job_id": job_id,
                "account_id": account_id,
                "hashtags": campaign.posting_requirements.required_hashtags,
                "mentions": campaign.posting_requirements.required_mentions,
            },
            priority=TaskPriority.HIGH,
        )

        # Persist task in repository
        await self.task_repo.save_task(task)

        # Enqueue into durable cloud queue for cloud worker processing
        await self.queue.enqueue(task_id=task.task_id, priority=int(TaskPriority.HIGH))

        logger.info(
            "Enqueued real media clipping task from Campaign Bridge",
            task_id=task.task_id,
            job_id=job_id,
            campaign_id=campaign.campaign_id,
            source_uri=source_uri,
        )
        return task
