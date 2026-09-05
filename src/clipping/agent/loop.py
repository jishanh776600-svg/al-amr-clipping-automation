"""Autonomous Operations Loop linking Campaign Discovery, Policy Decisions, and Production Clipping."""

from typing import Any, Dict, List, Optional
from clipping.agent.account.lifecycle import AccountLifecycleService, CampaignCompletionResult
from clipping.agent.bridge.campaign_clipping_bridge import CampaignClippingBridge
from clipping.agent.campaign.decision import CampaignDecisionEngine
from clipping.agent.campaign.discovery import CampaignDiscoveryCapability
from clipping.agent.campaign.models import CampaignLifecycleState, CampaignPlatform, CampaignRecord, CampaignStatus
from clipping.agent.campaign.repository import CampaignRepository
from clipping.agent.capabilities.base import CapabilityContext
from clipping.agent.cloud.queue import CloudTaskQueue
from clipping.agent.models import AgentTask
from clipping.agent.repository import TaskRepository
from clipping.agent.vault.models import AccountPlatform
from clipping.logging.logger import get_logger
from clipping.storage.base import StorageDriver

logger = get_logger("clipping.agent.loop")


class AutonomousOperationsLoop:
    """
    Coordinates the autonomous end-to-end campaign lifecycle:
    Campaign Discovery -> Policy Evaluation -> Autonomous Account Assignment ->
    Clipping Task Enqueueing -> Production Pipeline Execution -> Post-Campaign Disposition.
    """

    def __init__(
        self,
        discovery_capability: CampaignDiscoveryCapability,
        campaign_repository: CampaignRepository,
        decision_engine: CampaignDecisionEngine,
        clipping_bridge: CampaignClippingBridge,
        task_repository: TaskRepository,
        storage_driver: StorageDriver,
        account_service: Optional[AccountLifecycleService] = None,
    ):
        self.discovery = discovery_capability
        self.campaign_repo = campaign_repository
        self.decision_engine = decision_engine
        self.clipping_bridge = clipping_bridge
        self.task_repo = task_repository
        self.storage = storage_driver
        self.account_service = account_service or AccountLifecycleService(
            vault=decision_engine.vault,
            policy=decision_engine.policy,
        )

    async def run_discovery_and_dispatch_cycle(
        self,
        source_url: Optional[str] = None,
        raw_campaigns: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Executes one autonomous cycle:
        1. Discover campaigns.
        2. Evaluate against accounts, economics, and policies.
        3. Autonomously resolve, brand, and assign creator accounts.
        4. Re-verify terms (pre-flight check) and transition states.
        5. Dispatch production clipping tasks to cloud queue for eligible campaigns.
        6. Escalate only genuine exceptions.
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

            # Update state to EVALUATING
            campaign = campaign.model_copy(update={"lifecycle_state": CampaignLifecycleState.EVALUATING})
            await self.campaign_repo.save_campaign(campaign)

            decision = await self.decision_engine.evaluate_campaign_for_execution(campaign)

            if decision.escalation_required and decision.escalation_context:
                esc = await self.task_repo.create_escalation(decision.escalation_context)
                escalations_raised.append(esc.escalation_id)
                campaign = campaign.model_copy(update={"lifecycle_state": CampaignLifecycleState.ESCALATED})
                await self.campaign_repo.save_campaign(campaign)
                logger.warning("Campaign evaluation escalated", campaign_id=cid, reason=decision.decision_reason)
                continue

            if not decision.is_approved:
                # Blocked / rejected campaign
                next_state = decision.lifecycle_state or CampaignLifecycleState.BLOCKED
                campaign = campaign.model_copy(update={"lifecycle_state": next_state})
                await self.campaign_repo.save_campaign(campaign)
                logger.info("Campaign not approved for execution", campaign_id=cid, reason=decision.decision_reason)
                continue

            if decision.is_approved and decision.selected_source_uri:
                # 3. Autonomous Account Resolution (Reuse vs Dedicated Provisioning)
                target_platform = campaign.required_platforms[0] if campaign.required_platforms else CampaignPlatform.YOUTUBE_SHORTS
                vault_platform = AccountPlatform.YOUTUBE if "youtube" in target_platform.value else AccountPlatform.INSTAGRAM

                account_res = await self.account_service.select_or_create_account(campaign, platform=vault_platform)

                if account_res.escalation_required and account_res.escalation_context:
                    esc = await self.task_repo.create_escalation(account_res.escalation_context)
                    escalations_raised.append(esc.escalation_id)
                    campaign = campaign.model_copy(update={"lifecycle_state": CampaignLifecycleState.ESCALATED})
                    await self.campaign_repo.save_campaign(campaign)
                    logger.warning("Account resolution escalated", campaign_id=cid)
                    continue

                assigned_account = account_res.account
                account_id = assigned_account.account_id if assigned_account else decision.selected_account_id

                # 4. Pre-flight Term Verification immediately before task dispatch
                preflight_error = campaign.validate_rules()
                if preflight_error:
                    logger.error("Pre-flight rule verification failed immediately before task dispatch", campaign_id=cid, error=preflight_error)
                    continue

                # Transition state to CONTENT_PRODUCTION
                campaign = campaign.model_copy(
                    update={
                        "lifecycle_state": CampaignLifecycleState.CONTENT_PRODUCTION,
                    }
                )
                await self.campaign_repo.save_campaign(campaign)

                # 5. Bridge into REAL media clipping task
                task = await self.clipping_bridge.create_and_enqueue_clipping_job(
                    campaign=campaign,
                    source_uri=decision.selected_source_uri,
                    account_id=account_id,
                )
                enqueued_tasks.append(task.task_id)
                logger.info(
                    "Autonomous loop dispatched clipping task",
                    campaign_id=cid,
                    task_id=task.task_id,
                    account_id=account_id,
                    source_uri=decision.selected_source_uri,
                )

        return {
            "cycle_status": "completed",
            "campaigns_discovered": len(campaign_ids),
            "tasks_enqueued": enqueued_tasks,
            "escalations_raised": escalations_raised,
        }

    async def finalize_campaign(
        self,
        campaign_id: str,
        payment_status: str = "confirmed",
    ) -> CampaignCompletionResult:
        """
        Closes out a completed campaign, resolves associated account,
        applies post-campaign disposition rules, and tracks payment status.
        """
        camp = await self.campaign_repo.get_campaign(campaign_id)
        if not camp:
            raise ValueError(f"Campaign '{campaign_id}' not found in repository")

        target_platform = camp.required_platforms[0] if camp.required_platforms else CampaignPlatform.YOUTUBE_SHORTS
        vault_platform = AccountPlatform.YOUTUBE if "youtube" in target_platform.value else AccountPlatform.INSTAGRAM
        accounts = await self.account_service.vault.list_accounts(platform=vault_platform)

        # Locate associated account
        assoc_acc = next((a for a in accounts if a.campaign_association == campaign_id), None)
        if not assoc_acc:
            assoc_acc = accounts[0] if accounts else None
            if not assoc_acc:
                raise ValueError(f"No account found to finalize for campaign '{campaign_id}'")

        completion_res = await self.account_service.finalize_campaign_lifecycle(
            campaign=camp,
            account=assoc_acc,
            payment_status=payment_status,
        )

        # Update durable campaign record to completed with its disposition state
        updated_camp = camp.model_copy(
            update={
                "lifecycle_state": completion_res.lifecycle_state,
                "status": CampaignStatus.COMPLETED,
            }
        )
        await self.campaign_repo.save_campaign(updated_camp)

        logger.info(
            "Campaign successfully finalized",
            campaign_id=campaign_id,
            state=completion_res.lifecycle_state.value,
            reuse_eligible=completion_res.reuse_eligible,
        )
        return completion_res
