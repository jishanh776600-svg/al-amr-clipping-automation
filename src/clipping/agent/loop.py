"""Autonomous Operations Loop linking Campaign Discovery, Policy Decisions, and Production Clipping."""

from typing import Any, Dict, List, Optional
from clipping.agent.bridge.campaign_clipping_bridge import CampaignClippingBridge
from clipping.agent.campaign.decision import CampaignDecisionEngine
from clipping.agent.campaign.discovery import CampaignDiscoveryCapability
from clipping.agent.campaign.models import CampaignRecord, CampaignStatus
from clipping.agent.campaign.repository import CampaignRepository
from clipping.agent.capabilities.base import CapabilityContext
from clipping.agent.cloud.queue import CloudTaskQueue
from clipping.agent.models import AgentTask
from clipping.agent.repository import TaskRepository
from clipping.logging.logger import get_logger
from clipping.storage.base import StorageDriver

logger = get_logger("clipping.agent.loop")


class AutonomousOperationsLoop:
    """
    Coordinates the autonomous end-to-end campaign lifecycle:
    Campaign Discovery -> Policy Evaluation -> Clipping Task Enqueueing -> Production Pipeline Execution.
    """

    def __init__(
        self,
        discovery_capability: CampaignDiscoveryCapability,
        campaign_repository: CampaignRepository,
        decision_engine: CampaignDecisionEngine,
        clipping_bridge: CampaignClippingBridge,
        task_repository: TaskRepository,
        storage_driver: StorageDriver,
    ):
        self.discovery = discovery_capability
        self.campaign_repo = campaign_repository
        self.decision_engine = decision_engine
        self.clipping_bridge = clipping_bridge
        self.task_repo = task_repository
        self.storage = storage_driver

    async def run_discovery_and_dispatch_cycle(
        self,
        source_url: Optional[str] = None,
        raw_campaigns: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Executes one autonomous cycle:
        1. Discover campaigns.
        2. Evaluate against accounts and policies.
        3. Dispatch production clipping tasks to cloud queue for eligible campaigns.
        4. Escalate only genuine exceptions.
        """
        logger.info("Starting Autonomous Operations Cycle", source=source_url or "spec")

        # 1. Campaign Discovery
        context = CapabilityContext(
            task_id="loop_discovery_run",
            inputs={"source": source_url or "direct", "campaigns": raw_campaigns or []},
            storage_driver=self.storage,
        )
        disc_result = await self.discovery.execute(context)

        if not disc_result.success:
            if disc_result.escalation_required and disc_result.escalation_context:
                await self.task_repo.create_escalation(disc_result.escalation_context)
                logger.warning("Discovery raised escalation", reason=disc_result.escalation_context.reason.value)
            return {
                "cycle_status": "discovery_failed",
                "escalated": disc_result.escalation_required,
                "error": disc_result.error.error_message if disc_result.error else None,
            }

        campaign_ids = disc_result.outputs.get("campaign_ids", [])
        enqueued_tasks: List[str] = []
        escalations_raised: List[str] = []

        # 2. Load and prioritize discovered campaigns by opportunity score
        loaded_campaigns: List[CampaignRecord] = []
        for cid in campaign_ids:
            camp = await self.campaign_repo.get_campaign(cid)
            if camp and camp.status == CampaignStatus.ACTIVE:
                loaded_campaigns.append(camp)

        # Prioritize highest opportunity scores first
        loaded_campaigns.sort(key=lambda c: (c.opportunity_score or 0.0), reverse=True)

        for campaign in loaded_campaigns:
            cid = campaign.campaign_id
            decision = await self.decision_engine.evaluate_campaign_for_execution(campaign)

            if decision.escalation_required and decision.escalation_context:
                esc = await self.task_repo.create_escalation(decision.escalation_context)
                escalations_raised.append(esc.escalation_id)
                logger.warning("Campaign evaluation escalated", campaign_id=cid, reason=decision.decision_reason)
                continue

            if decision.is_approved and decision.selected_source_uri:
                # 3. Bridge into REAL media clipping task
                task = await self.clipping_bridge.create_and_enqueue_clipping_job(
                    campaign=campaign,
                    source_uri=decision.selected_source_uri,
                    account_id=decision.selected_account_id,
                )
                enqueued_tasks.append(task.task_id)
                logger.info(
                    "Autonomous loop dispatched clipping task",
                    campaign_id=cid,
                    task_id=task.task_id,
                    source_uri=decision.selected_source_uri,
                )

        return {
            "cycle_status": "completed",
            "campaigns_discovered": len(campaign_ids),
            "tasks_enqueued": enqueued_tasks,
            "escalations_raised": escalations_raised,
        }
