"""Campaign Decision Engine integrating PolicyEngine and Account Vault."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field

from clipping.agent.campaign.models import CampaignPlatform, CampaignRecord, CampaignStatus
from clipping.agent.escalation import EscalationContext, EscalationReason, EscalationSeverity
from clipping.agent.policy import ActionRiskTier, ActionScope, PolicyDecisionType, PolicyEngine
from clipping.agent.vault.models import AccountMetadata, AccountPlatform, AccountStatus
from clipping.agent.vault.vault import EncryptedCredentialVault
from clipping.logging.logger import get_logger

logger = get_logger("clipping.agent.campaign.decision")


class CampaignDecisionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    is_approved: bool
    campaign_id: str
    selected_account_id: Optional[str] = None
    selected_account_platform: Optional[str] = None
    selected_source_uri: Optional[str] = None
    decision_reason: str
    escalation_required: bool = False
    escalation_context: Optional[EscalationContext] = None


class CampaignDecisionEngine:
    """
    Autonomous decision engine for campaign operations.
    Evaluates campaign rules, account eligibility, and system policies to make routine
    decisions automatically and escalate only for genuine exceptions.
    """

    def __init__(
        self,
        vault: EncryptedCredentialVault,
        policy_engine: PolicyEngine,
    ):
        self.vault = vault
        self.policy = policy_engine

    async def evaluate_campaign_for_execution(
        self,
        campaign: CampaignRecord,
        candidate_source_uri: Optional[str] = None,
        task_id: str = "task_eval",
    ) -> CampaignDecisionResult:
        cid = campaign.campaign_id

        # 1. Active Status Check
        if campaign.status != CampaignStatus.ACTIVE:
            return CampaignDecisionResult(
                is_approved=False,
                campaign_id=cid,
                decision_reason=f"Campaign is not active (current status: {campaign.status.value})",
            )

        # 2. Contradiction / Rule Validation Check
        contradiction = campaign.validate_rules()
        if contradiction:
            return CampaignDecisionResult(
                is_approved=False,
                campaign_id=cid,
                decision_reason="Contradictory campaign requirements",
                escalation_required=True,
                escalation_context=EscalationContext(
                    what_happened=f"Campaign '{campaign.name}' has conflicting rules",
                    why_it_happened=contradiction,
                    decision_required="Resolve contradictory campaign rules before continuing",
                    available_options=["resolve_contradiction", "reject_campaign"],
                    metadata={"task_id": task_id, "campaign_id": cid, "reason": EscalationReason.CONTRADICTORY_INSTRUCTIONS.value},
                ),
            )

        # 3. Platform & Account Eligibility Check
        target_platform = campaign.required_platforms[0] if campaign.required_platforms else CampaignPlatform.YOUTUBE_SHORTS
        vault_platform = AccountPlatform.YOUTUBE if "youtube" in target_platform.value else AccountPlatform.INSTAGRAM

        # List candidate accounts from vault
        accounts = await self.vault.list_accounts(platform=vault_platform)
        eligible_account: Optional[AccountMetadata] = None

        for acc in accounts:
            if acc.status == AccountStatus.SUSPENDED:
                continue
            if acc.status == AccountStatus.RESTRICTED:
                continue

            if campaign.account_requirements.allow_account_reuse and acc.reuse_eligibility:
                eligible_account = acc
                break
            elif not campaign.account_requirements.allow_account_reuse:
                if acc.campaign_association == cid:
                    eligible_account = acc
                    break

        if not eligible_account:
            # Check if policy allows creating an account or if operator must intervene
            scope = ActionScope(
                capability_name="account_management",
                action_name="create_channel",
                target_resource=f"account:{vault_platform.value}",
                is_reversible=False,
                risk_tier=ActionRiskTier.MUTATING_IRREVERSIBLE,
            )
            policy_eval = self.policy.evaluate(scope)
            if policy_eval.decision == PolicyDecisionType.REQUIRE_CONFIRMATION or not policy_eval.allowed:
                return CampaignDecisionResult(
                    is_approved=False,
                    campaign_id=cid,
                    decision_reason="No eligible account available and account creation requires operator confirmation",
                    escalation_required=True,
                    escalation_context=EscalationContext(
                        what_happened=f"No eligible account found for campaign '{campaign.name}'",
                        why_it_happened=f"Vault contains no active eligible account for platform {vault_platform.value} and account creation requires operator confirmation",
                        decision_required="Authorize creation of a new channel/account or attach an existing credential",
                        available_options=["create_new_account", "attach_existing_credential", "pause_campaign"],
                        reason=EscalationReason.POLICY_VIOLATION,
                        severity=EscalationSeverity.MEDIUM,
                        metadata={"task_id": task_id, "campaign_id": cid, "platform": vault_platform.value},
                    ),
                )

        # 4. Source Video Eligibility Check
        source_uri = candidate_source_uri
        if not source_uri and campaign.discovered_source_uris:
            source_uri = campaign.discovered_source_uris[0]

        if source_uri:
            if not campaign.is_eligible_source_url(source_uri):
                return CampaignDecisionResult(
                    is_approved=False,
                    campaign_id=cid,
                    decision_reason=f"Source URL '{source_uri}' does not satisfy campaign requirements",
                    escalation_required=True,
                    escalation_context=EscalationContext(
                        what_happened=f"Ineligible source URL for campaign '{campaign.name}'",
                        why_it_happened=f"URL '{source_uri}' fails campaign source requirements",
                        decision_required="Provide an approved source video URL",
                        available_options=["provide_url", "skip_video"],
                        metadata={"task_id": task_id, "campaign_id": cid, "source_uri": source_uri, "reason": EscalationReason.POLICY_VIOLATION.value},
                    ),
                )

        # 5. Routine Auto-Approval
        logger.info(
            "Campaign evaluated: APPROVED",
            campaign_id=cid,
            account_id=eligible_account.account_id if eligible_account else "auto_create",
            source_uri=source_uri,
        )
        return CampaignDecisionResult(
            is_approved=True,
            campaign_id=cid,
            selected_account_id=eligible_account.account_id if eligible_account else None,
            selected_account_platform=vault_platform.value,
            selected_source_uri=source_uri,
            decision_reason="Routine campaign requirements, account eligibility, and system policies verified",
        )
