"""Autonomous Orchestration Engine for AL AMR CLIPPING.

Durable, self-running coordination engine:
Whop-first discovery -> economic intelligence ($2 preferred CPM, $1-$5 target) ->
opportunity selection -> account provisioning/reuse -> source acquisition ->
9-stage real clipping dispatch -> QA gate -> campaign submission & publishing ->
platform reconciliation -> campaign finalization -> continuous loop.
"""

import asyncio
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid

from clipping.agent.account.lifecycle import AccountLifecycleService, CampaignCompletionResult
from clipping.agent.bridge.campaign_clipping_bridge import CampaignClippingBridge
from clipping.agent.campaign.discovery import CampaignDiscoveryCapability
from clipping.agent.campaign.evaluator import CampaignEvaluator, OpportunityTier
from clipping.agent.campaign.models import (
    CampaignLifecycleState,
    CampaignPlatform,
    CampaignRecord,
    CampaignStatus,
)
from clipping.agent.campaign.repository import CampaignRepository
from clipping.agent.campaign.sources.registry import CampaignSourceRegistry
from clipping.agent.capabilities.base import CapabilityContext
from clipping.agent.cloud.queue import CloudTaskQueue
from clipping.agent.cloud.telemetry import CloudTelemetryEngine, TelemetryEventType
from clipping.agent.escalation import EscalationContext, EscalationReason, EscalationSeverity
from clipping.agent.events import AgentEventSystem, AgentEventType
from clipping.agent.models import AgentTask
from clipping.agent.orchestration.models import (
    CampaignOrchestrationRecord,
    OrchestrationCycleSummary,
    OrchestrationStage,
)
from clipping.agent.orchestration.repository import OrchestrationRepository
from clipping.agent.policy import PolicyEngine
from clipping.agent.publishing.capability import PublishingCapability
from clipping.agent.publishing.reconciliation import PublishingReconciliationService
from clipping.agent.publishing.repository import CampaignSubmissionRepository
from clipping.agent.repository import AgentTaskRepository
from clipping.agent.state import TaskState
from clipping.agent.vault.models import AccountPlatform
from clipping.agent.vault.vault import EncryptedCredentialVault
from clipping.control.repository import ControlRepository
from clipping.logging.logger import get_logger
from clipping.storage.base import StorageDriver

logger = get_logger("clipping.agent.orchestration.engine")


class AutonomousOrchestrationEngine:
    """
    Central Autonomous Orchestration Engine coordinating the end-to-end
    campaign operations lifecycle with durable state checkpoints and bounded recovery.
    """

    def __init__(
        self,
        storage_driver: StorageDriver,
        control_repository: ControlRepository,
        campaign_repository: CampaignRepository,
        task_repository: AgentTaskRepository,
        orchestration_repository: Optional[OrchestrationRepository] = None,
        source_registry: Optional[CampaignSourceRegistry] = None,
        discovery_capability: Optional[CampaignDiscoveryCapability] = None,
        evaluator: Optional[CampaignEvaluator] = None,
        account_service: Optional[AccountLifecycleService] = None,
        clipping_bridge: Optional[CampaignClippingBridge] = None,
        publishing_capability: Optional[PublishingCapability] = None,
        submission_repository: Optional[CampaignSubmissionRepository] = None,
        reconciler: Optional[PublishingReconciliationService] = None,
        policy_engine: Optional[PolicyEngine] = None,
        credential_vault: Optional[EncryptedCredentialVault] = None,
        task_queue: Optional[CloudTaskQueue] = None,
        worker: Optional[Any] = None,
        telemetry_engine: Optional[CloudTelemetryEngine] = None,
        event_system: Optional[AgentEventSystem] = None,
    ):
        self.storage = storage_driver
        self.control_repo = control_repository
        self.campaign_repo = campaign_repository or CampaignRepository(storage_driver)
        self.task_repo = task_repository
        self.orchestration_repo = orchestration_repository or OrchestrationRepository(storage_driver)
        self.submission_repo = submission_repository or CampaignSubmissionRepository(storage_driver)
        self.policy = policy_engine or PolicyEngine()
        self.vault = credential_vault or EncryptedCredentialVault(storage_driver)
        self.evaluator = evaluator or CampaignEvaluator(preferred_cpm=2.0, min_viable_cpm=1.0, max_target_cpm=5.0)
        self.source_registry = source_registry or CampaignSourceRegistry()
        self.discovery = discovery_capability or CampaignDiscoveryCapability(
            repository=self.campaign_repo,
            source_registry=self.source_registry,
            evaluator=self.evaluator,
        )
        self.account_service = account_service or AccountLifecycleService(
            vault=self.vault,
            policy=self.policy,
        )
        queue = task_queue or CloudTaskQueue(storage_driver=storage_driver)
        self.clipping_bridge = clipping_bridge or CampaignClippingBridge(
            queue=queue,
            task_repository=self.task_repo,
        )
        if worker is not None:
            self.worker = worker
        else:
            try:
                from clipping.agent.capabilities.clipping_adapter import MediaClippingCapability
                from clipping.agent.capabilities.registry import CapabilityRegistry
                from clipping.agent.cloud.worker import CloudAgentWorker

                cap_reg = CapabilityRegistry()
                cap_reg.register(MediaClippingCapability())
                self.worker = CloudAgentWorker(
                    worker_id=f"orchestrator_worker_{os.getpid()}",
                    capabilities=cap_reg,
                    storage_driver=self.storage,
                    task_repository=self.task_repo,
                    queue=queue,
                    policy_engine=self.policy,
                    control_repository=self.control_repo,
                )
            except Exception as w_err:
                logger.warning("Could not instantiate integrated worker", error=str(w_err))
                self.worker = None

        self.telemetry = telemetry_engine
        self.events = event_system

        if publishing_capability:
            self.publishing_capability = publishing_capability
        else:
            self.publishing_capability = PublishingCapability(
                submission_repository=self.submission_repo,
                campaign_repository=self.campaign_repo,
                vault=self.vault,
                control_repository=self.control_repo,
                policy_engine=self.policy,
                telemetry_engine=self.telemetry,
            )

        self.reconciler = reconciler or (self.publishing_capability.reconciler if self.publishing_capability else None)

    async def run_orchestration_cycle(
        self,
        source_name: Optional[str] = "whop",
        max_campaigns_to_process: int = 5,
        target_campaign_id: Optional[str] = None,
        dry_run: bool = False,
    ) -> OrchestrationCycleSummary:
        """
        Executes a single end-to-end autonomous orchestration cycle:
        1. Master Control safety pre-flight check (emergency stop, automation pause).
        2. Platform reconciliation & stale upload recovery.
        3. Whop-first campaign discovery.
        4. Economic intelligence & multi-factor evaluation ($2 CPM preferred).
        5. Autonomous opportunity ranking and selection.
        6. Autonomous creator account assignment & provisioning.
        7. Pre-flight verification & 9-stage clipping production dispatch.
        8. QA verification & media safety gate check.
        9. Campaign submission & authorized platform publishing.
        10. Finalization when quotas reached or deadline elapsed.
        """
        started_at = datetime.now(timezone.utc)
        cycle_id = f"cycle_{uuid.uuid4().hex[:10]}"

        # 1. Global Master Control Safety Pre-flight Check
        if await self.control_repo.is_emergency_stopped():
            logger.error("Global Master Control Emergency Stop active; cycle aborted", cycle_id=cycle_id)
            summary = OrchestrationCycleSummary(
                cycle_id=cycle_id,
                status="emergency_stopped",
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
                errors=["Global Master Control Emergency Stop active"],
            )
            await self.orchestration_repo.save_cycle_summary(summary)
            return summary

        if await self.control_repo.is_automation_paused():
            logger.warning("Global Automation Paused; cycle deferred", cycle_id=cycle_id)
            summary = OrchestrationCycleSummary(
                cycle_id=cycle_id,
                status="safety_paused",
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
                errors=["Global Automation Paused"],
            )
            await self.orchestration_repo.save_cycle_summary(summary)
            return summary

        summary = OrchestrationCycleSummary(
            cycle_id=cycle_id,
            status="running",
            started_at=started_at,
        )

        try:
            # 2. Platform Reconciliation Phase
            if self.reconciler:
                try:
                    reconciled_results = await self.reconciler.reconcile_all_active()
                    summary.reconciliations_run = len(reconciled_results)
                    logger.info("Platform reconciliation phase completed", count=len(reconciled_results))
                except Exception as e:
                    logger.warning("Reconciliation phase encountered transient warning", error=str(e))
                    summary.errors.append(f"Reconciliation warning: {str(e)}")

            # 3. Whop-First Campaign Discovery Phase
            if self.discovery and not target_campaign_id:
                disc_context = CapabilityContext(
                    task_id=f"disc_{cycle_id}",
                    inputs={"source": source_name or "whop"},
                    storage_driver=self.storage,
                )
                try:
                    disc_res = await self.discovery.execute(disc_context)
                    if disc_res.success:
                        disc_ids = disc_res.outputs.get("campaign_ids", [])
                        summary.campaigns_discovered = len(disc_ids)
                    elif disc_res.escalation_required and disc_res.escalation_context:
                        esc = await self.task_repo.create_escalation(disc_res.escalation_context)
                        summary.escalations_raised += 1
                        logger.warning("Discovery raised human escalation", escalation_id=esc.escalation_id)
                except Exception as e:
                    logger.error("Campaign discovery encountered error", error=str(e))
                    summary.errors.append(f"Discovery error: {str(e)}")

            # 4. Campaign Intelligence & Economic Evaluation
            vault_accounts = await self.vault.list_accounts()
            active_campaigns = await self.campaign_repo.list_campaigns(status=CampaignStatus.ACTIVE)

            if target_campaign_id:
                active_campaigns = [c for c in active_campaigns if c.campaign_id == target_campaign_id]

            evaluated_opportunities: List[tuple[CampaignRecord, Any]] = []

            for camp in active_campaigns:
                summary.campaigns_evaluated += 1
                score = self.evaluator.evaluate(camp, vault_accounts)

                # Persist evaluated opportunity score
                updated_camp = camp.model_copy(update={"opportunity_score": score.overall_score})
                await self.campaign_repo.save_campaign(updated_camp)

                if score.is_worth_pursuing() or (target_campaign_id and camp.campaign_id == target_campaign_id):
                    evaluated_opportunities.append((updated_camp, score))
                else:
                    reason = score.tier.value
                    summary.skipped_reasons[reason] = summary.skipped_reasons.get(reason, 0) + 1

            # 5. Opportunity Ranking & Selection
            evaluated_opportunities.sort(key=lambda item: item[1].overall_score, reverse=True)
            selected_opportunities = evaluated_opportunities[:max_campaigns_to_process]

            for campaign, score in selected_opportunities:
                cid = campaign.campaign_id

                # Retrieve or initialize durable orchestration record
                record = await self.orchestration_repo.get_record(cid)
                if not record:
                    record = CampaignOrchestrationRecord(
                        orchestration_id=f"orch_{cid}",
                        campaign_id=cid,
                        current_stage=OrchestrationStage.OPPORTUNITY_SELECTED,
                        opportunity_score=score.overall_score,
                        opportunity_tier=score.tier.value,
                    )
                    record = record.record_stage(
                        OrchestrationStage.OPPORTUNITY_SELECTED,
                        {"score": score.overall_score, "tier": score.tier.value},
                    )
                    await self.orchestration_repo.save_record(record)
                    summary.opportunities_selected += 1

                # Skip finalized or hard blocked campaigns
                if record.current_stage in (OrchestrationStage.FINALIZED, OrchestrationStage.BLOCKED):
                    continue

                # Handle Human Escalated State
                if record.current_stage == OrchestrationStage.ESCALATED:
                    logger.info("Campaign orchestration in ESCALATED state; awaiting operator resolution", campaign_id=cid)
                    continue

                # Stage 6: Autonomous Account Resolution & Provisioning
                if record.current_stage in (
                    OrchestrationStage.OPPORTUNITY_SELECTED,
                    OrchestrationStage.EVALUATING,
                    OrchestrationStage.DISCOVERY,
                ):
                    target_platform = campaign.required_platforms[0] if campaign.required_platforms else CampaignPlatform.YOUTUBE_SHORTS
                    vault_platform = AccountPlatform.YOUTUBE if "youtube" in target_platform.value else AccountPlatform.INSTAGRAM

                    acc_res = await self.account_service.select_or_create_account(campaign, platform=vault_platform)

                    if acc_res.escalation_required and acc_res.escalation_context:
                        esc = await self.task_repo.create_escalation(acc_res.escalation_context)
                        record = record.record_stage(
                            OrchestrationStage.ESCALATED,
                            {"escalation_id": esc.escalation_id, "reason": "Account resolution required escalation"},
                        )
                        record = record.model_copy(update={"escalation_id": esc.escalation_id})
                        await self.orchestration_repo.save_record(record)
                        summary.escalations_raised += 1
                        continue

                    if not acc_res.account:
                        record = record.record_stage(
                            OrchestrationStage.BLOCKED,
                            {"reason": acc_res.error or "No eligible account could be provisioned or assigned"},
                        )
                        record = record.model_copy(update={"blocking_reason": acc_res.error})
                        await self.orchestration_repo.save_record(record)
                        continue

                    record = record.record_stage(
                        OrchestrationStage.ACCOUNT_ASSIGNED,
                        {"account_id": acc_res.account.account_id, "was_created": acc_res.was_created},
                    )
                    record = record.model_copy(
                        update={
                            "account_id": acc_res.account.account_id,
                            "platform": vault_platform.value,
                        }
                    )
                    await self.orchestration_repo.save_record(record)
                    summary.accounts_provisioned_or_assigned += 1

                # Stage 7: Source Material Acquisition
                if record.current_stage == OrchestrationStage.ACCOUNT_ASSIGNED:
                    source_uris = campaign.source_material.video_urls or campaign.discovered_source_uris
                    if not source_uris:
                        record = record.record_stage(
                            OrchestrationStage.BLOCKED,
                            {"reason": "No valid source video URLs available for campaign"},
                        )
                        record = record.model_copy(update={"blocking_reason": "Missing source video URIs"})
                        await self.orchestration_repo.save_record(record)
                        continue

                    source_uri = source_uris[0]
                    contradiction = campaign.validate_rules()
                    if contradiction:
                        esc_ctx = EscalationContext(
                            what_happened=f"Campaign '{campaign.name}' brief contains contradictory requirements",
                            why_it_happened=contradiction,
                            decision_required="Human review required to resolve contradictory terms before clipping",
                            available_options=["resolve_contradiction", "reject_campaign"],
                            reason=EscalationReason.CONTRADICTORY_INSTRUCTIONS,
                            severity=EscalationSeverity.HIGH,
                            metadata={"campaign_id": cid},
                        )
                        esc = await self.task_repo.create_escalation(esc_ctx)
                        record = record.record_stage(
                            OrchestrationStage.ESCALATED,
                            {"escalation_id": esc.escalation_id, "reason": contradiction},
                        )
                        record = record.model_copy(update={"escalation_id": esc.escalation_id, "blocking_reason": contradiction})
                        await self.orchestration_repo.save_record(record)
                        summary.escalations_raised += 1
                        continue

                    record = record.record_stage(
                        OrchestrationStage.SOURCE_ACQUISITION,
                        {"source_uri": source_uri},
                    )
                    record = record.model_copy(update={"source_uri": source_uri})
                    await self.orchestration_repo.save_record(record)

                # Stage 8: Real 9-Stage Production Clipping Task Enqueueing
                if record.current_stage == OrchestrationStage.SOURCE_ACQUISITION:
                    if not record.production_task_id:
                        clip_task = await self.clipping_bridge.create_and_enqueue_clipping_job(
                            campaign=campaign,
                            source_uri=record.source_uri,
                            account_id=record.account_id,
                        )
                        record = record.record_stage(
                            OrchestrationStage.PRODUCTION_DISPATCHED,
                            {"task_id": clip_task.task_id},
                        )
                        record = record.model_copy(update={"production_task_id": clip_task.task_id})
                        await self.orchestration_repo.save_record(record)
                        summary.production_tasks_dispatched += 1

                        # If an integrated worker is attached, execute the task to advance autonomously
                        if self.worker:
                            try:
                                await self.worker.run_next_task()
                            except Exception as w_err:
                                logger.warning("Worker encountered error during inline task execution", error=str(w_err))

                # Stage 9: Production Completion & QA Verification Gate
                if record.current_stage == OrchestrationStage.PRODUCTION_DISPATCHED:
                    if record.production_task_id:
                        task = await self.task_repo.get_task(record.production_task_id)
                        if task:
                            if task.status == TaskState.SUCCEEDED:
                                qa_stat = task.outputs.get("qa_status", "passed") if task.outputs else "passed"
                                record = record.record_stage(
                                    OrchestrationStage.PRODUCTION_COMPLETED,
                                    {"task_id": task.task_id, "outputs": task.outputs},
                                )
                                record = record.record_stage(
                                    OrchestrationStage.QA_VERIFIED,
                                    {"qa_status": qa_stat},
                                )
                                await self.orchestration_repo.save_record(record)
                            elif task.status == TaskState.FAILED:
                                err_info = task.error_info or (task.attempts[-1].error if task.attempts else None)
                                err_msg = err_info.error_message if err_info else "Production task failed"
                                err_type = err_info.error_type if err_info else ""
                                if err_type == "QAGatingFailure" or "qa verification" in err_msg.lower() or "0 clips passed qa" in err_msg.lower():
                                    esc_ctx = EscalationContext(
                                        what_happened=f"Production QA gating failed for campaign '{campaign.name}'",
                                        why_it_happened=err_msg,
                                        decision_required="Review clip QA failure or choose another source video",
                                        available_options=["retry_with_new_source", "override_qa", "abandon_campaign"],
                                        reason=EscalationReason.QUALITY_ASSURANCE_FAILURE,
                                        severity=EscalationSeverity.MEDIUM,
                                        metadata={"campaign_id": cid, "task_id": task.task_id},
                                    )
                                    esc = await self.task_repo.create_escalation(esc_ctx)
                                    record = record.record_stage(
                                        OrchestrationStage.ESCALATED,
                                        {"escalation_id": esc.escalation_id, "reason": err_msg},
                                    )
                                    record = record.model_copy(update={"escalation_id": esc.escalation_id, "blocking_reason": err_msg})
                                    await self.orchestration_repo.save_record(record)
                                    summary.escalations_raised += 1
                                    continue
                                elif not task.can_retry():
                                    record = record.record_stage(
                                        OrchestrationStage.BLOCKED,
                                        {"reason": f"Production task failed and retries exhausted: {err_msg}"},
                                    )
                                    record = record.model_copy(update={"blocking_reason": err_msg})
                                    await self.orchestration_repo.save_record(record)
                                    continue
                            elif task.status == TaskState.ESCALATED:
                                record = record.record_stage(
                                    OrchestrationStage.ESCALATED,
                                    {"escalation_id": task.escalation_id},
                                )
                                record = record.model_copy(update={"escalation_id": task.escalation_id})
                                await self.orchestration_repo.save_record(record)
                                summary.escalations_raised += 1
                                continue
                            else:
                                # Production job still in progress
                                continue

                # Stage 10: Campaign Submission & Authorized Publishing Operations
                if record.current_stage in (
                    OrchestrationStage.PRODUCTION_COMPLETED,
                    OrchestrationStage.QA_VERIFIED,
                    OrchestrationStage.SUBMISSION_PENDING,
                ):
                    if dry_run or await self.control_repo.is_publishing_locked():
                        reason = "Dry-run mode active; live publishing suppressed" if dry_run else "Publishing Locked by Master Control"
                        logger.warning(reason, campaign_id=cid)
                        record = record.model_copy(update={"blocking_reason": reason})
                        await self.orchestration_repo.save_record(record)
                        continue

                    if self.publishing_capability:
                        record = record.record_stage(OrchestrationStage.SUBMISSION_PENDING)
                        await self.orchestration_repo.save_record(record)

                        task = await self.task_repo.get_task(record.production_task_id) if record.production_task_id else None
                        outputs = task.outputs if (task and task.outputs) else {}
                        clip_id = outputs.get("clip_id") or f"clip_{cid[:8]}"
                        media_path = outputs.get("media_path") or f"rendered/{clip_id}.mp4"
                        duration = float(outputs.get("duration_seconds", 35.0))
                        source_vid = outputs.get("source_video_id") or record.source_video_id or "src_1"
                        qa_stat = outputs.get("qa_status", "passed")
                        qa_rec = {"status": qa_stat, "duration_seconds": duration}

                        pub_context = CapabilityContext(
                            task_id=f"pub_{cid[:8]}_{clip_id}",
                            inputs={
                                "campaign_id": cid,
                                "clip_id": clip_id,
                                "account_id": record.account_id,
                                "media_path": media_path,
                                "platform": record.platform or "youtube",
                                "publishing_mode": "draft",
                                "duration_seconds": duration,
                                "qa_record": qa_rec,
                                "source_video_id": source_vid,
                            },
                            storage_driver=self.storage,
                        )

                        pub_res = await self.publishing_capability.execute(pub_context)
                        if pub_res.success:
                            sub_payload = pub_res.outputs.get("submission", {})
                            sub_id = sub_payload.get("submission_id", f"sub_{clip_id}")
                            post_id = sub_payload.get("platform_post_id") or ""

                            # Safety boundary: strictly reject synthetic post IDs in live mode
                            if not dry_run:
                                is_synthetic = (
                                    not post_id
                                    or post_id.startswith(("yt_mock", "mock_", "ig_mock", "synthetic_"))
                                    or "mock" in post_id.lower()
                                    or "synthetic" in post_id.lower()
                                )
                                if is_synthetic:
                                    logger.error("Live publishing returned synthetic post ID; rejecting publication", post_id=post_id)
                                    esc_ctx = EscalationContext(
                                        what_happened="Live publishing returned synthetic/mock post ID",
                                        why_it_happened=f"Platform adapter returned post ID '{post_id}' which is synthetic",
                                        decision_required="Verify platform credentials and adapter configuration",
                                        available_options=["verify_credentials", "retry_live_publish"],
                                        reason=EscalationReason.POLICY_VIOLATION,
                                        severity=EscalationSeverity.CRITICAL,
                                        metadata={"campaign_id": cid, "submission_id": sub_id},
                                    )
                                    esc = await self.task_repo.create_escalation(esc_ctx)
                                    record = record.record_stage(
                                        OrchestrationStage.ESCALATED,
                                        {"escalation_id": esc.escalation_id, "reason": "Synthetic post ID detected in live mode"},
                                    )
                                    record = record.model_copy(update={"escalation_id": esc.escalation_id, "blocking_reason": "Synthetic post ID rejected"})
                                    await self.orchestration_repo.save_record(record)
                                    summary.escalations_raised += 1
                                    continue

                            record = record.record_stage(
                                OrchestrationStage.SUBMISSION_COMPLETED,
                                {"submission_id": sub_id},
                            )
                            record = record.record_stage(
                                OrchestrationStage.PUBLISHED,
                                {"platform_post_id": post_id},
                            )
                            record = record.model_copy(update={"submission_id": sub_id})
                            await self.orchestration_repo.save_record(record)
                            summary.submissions_processed += 1
                        elif pub_res.escalation_required and pub_res.escalation_context:
                            esc = await self.task_repo.create_escalation(pub_res.escalation_context)
                            record = record.record_stage(
                                OrchestrationStage.ESCALATED,
                                {"escalation_id": esc.escalation_id, "reason": pub_res.escalation_context.what_happened},
                            )
                            record = record.model_copy(update={"escalation_id": esc.escalation_id, "blocking_reason": pub_res.escalation_context.what_happened})
                            await self.orchestration_repo.save_record(record)
                            summary.escalations_raised += 1
                            continue
                        else:
                            err_msg = pub_res.error.error_message if pub_res.error else "Submission error"
                            record = record.record_stage(
                                OrchestrationStage.BLOCKED,
                                {"reason": err_msg},
                            )
                            record = record.model_copy(update={"blocking_reason": err_msg})
                            await self.orchestration_repo.save_record(record)
                            summary.errors.append(f"Submission failed for {cid}: {err_msg}")
                            continue

                # Stage 11: Submission Reconciliation Check
                if record.current_stage == OrchestrationStage.PUBLISHED:
                    if self.reconciler and record.submission_id:
                        try:
                            rec_outcome = await self.reconciler.reconcile_submission(cid, record.submission_id)
                            record = record.record_stage(
                                OrchestrationStage.RECONCILED,
                                {"reconciled_status": rec_outcome.reconciled_status.value},
                            )
                            await self.orchestration_repo.save_record(record)
                            summary.reconciliations_run += 1
                        except Exception as e:
                            logger.warning("Individual submission reconciliation notice", error=str(e))

                # Stage 12: Campaign Finalization & Account Disposition
                if record.current_stage in (OrchestrationStage.PUBLISHED, OrchestrationStage.RECONCILED):
                    is_complete = False
                    if campaign.quotas.daily_creator_limit and summary.submissions_processed >= campaign.quotas.daily_creator_limit:
                        is_complete = True
                    if campaign.payout_terms.budget_exhausted:
                        is_complete = True

                    if is_complete:
                        fin_outcome = await self.finalize_campaign(cid)
                        record = record.record_stage(
                            OrchestrationStage.FINALIZED,
                            {"reuse_eligible": fin_outcome.reuse_eligible},
                        )
                        await self.orchestration_repo.save_record(record)
                        summary.campaigns_finalized += 1

            summary.status = "completed"

        except Exception as e:
            logger.error("Unhandled exception in orchestration cycle", error=str(e))
            summary.status = "failed"
            summary.errors.append(str(e))

        finally:
            completed_at = datetime.now(timezone.utc)
            duration = round((completed_at - started_at).total_seconds(), 3)
            summary = summary.model_copy(
                update={
                    "completed_at": completed_at,
                    "duration_seconds": duration,
                }
            )
            await self.orchestration_repo.save_cycle_summary(summary)

        return summary

    async def resume_orchestration(self, campaign_id: str) -> Optional[CampaignOrchestrationRecord]:
        """Resumes an interrupted campaign orchestration from its last recorded checkpoint."""
        record = await self.orchestration_repo.get_record(campaign_id)
        if not record:
            return None

        logger.info(
            "Resuming campaign orchestration from checkpoint",
            campaign_id=campaign_id,
            stage=record.current_stage.value,
        )

        # Run targeted cycle to advance the campaign
        await self.run_orchestration_cycle(target_campaign_id=campaign_id)
        return await self.orchestration_repo.get_record(campaign_id)

    async def finalize_campaign(
        self,
        campaign_id: str,
        payment_status: str = "confirmed",
    ) -> CampaignCompletionResult:
        """Applies post-campaign lifecycle rules and releases or locks creator accounts."""
        camp = await self.campaign_repo.get_campaign(campaign_id)
        if not camp:
            raise ValueError(f"Campaign '{campaign_id}' not found in repository")

        target_platform = camp.required_platforms[0] if camp.required_platforms else CampaignPlatform.YOUTUBE_SHORTS
        vault_platform = AccountPlatform.YOUTUBE if "youtube" in target_platform.value else AccountPlatform.INSTAGRAM
        accounts = await self.vault.list_accounts(platform=vault_platform)

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

        updated_camp = camp.model_copy(
            update={
                "lifecycle_state": completion_res.lifecycle_state,
                "status": CampaignStatus.COMPLETED,
            }
        )
        await self.campaign_repo.save_campaign(updated_camp)
        return completion_res

    async def run_continuous(
        self,
        poll_interval_seconds: int = 60,
        max_cycles: Optional[int] = None,
    ) -> None:
        """
        Runs continuous autonomous orchestration loop in the cloud.
        Durable, crash-resilient, and bounded.
        """
        cycles_run = 0
        logger.info("Starting continuous autonomous orchestration loop", interval=poll_interval_seconds)

        while max_cycles is None or cycles_run < max_cycles:
            try:
                summary = await self.run_orchestration_cycle()
                cycles_run += 1
                logger.info(
                    "Completed autonomous orchestration cycle in continuous loop",
                    cycle_id=summary.cycle_id,
                    status=summary.status,
                    cycle_number=cycles_run,
                )

                if summary.status in ("emergency_stopped", "safety_paused"):
                    logger.info("Automation paused or stopped; backing off", status=summary.status)
                    await asyncio.sleep(min(poll_interval_seconds * 2, 300))
                    continue

            except Exception as e:
                logger.error("Continuous orchestration loop encountered error", error=str(e))

            await asyncio.sleep(poll_interval_seconds)
