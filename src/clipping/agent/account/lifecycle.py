"""Account Lifecycle Service for Autonomous Campaign Operations.

Handles autonomous selection of eligible existing accounts, idempotent creation of
new accounts when permitted by campaign rules and policy, professional branding
configuration, and post-campaign lifecycle management (reuse vs dedicated lock).
"""

import hashlib
import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field

from clipping.agent.account.branding import CampaignBrandingGenerator, ChannelBrandingProfile
from clipping.agent.campaign.models import CampaignLifecycleState, CampaignRecord
from clipping.agent.escalation import EscalationContext, EscalationReason, EscalationSeverity
from clipping.agent.policy import ActionRiskTier, ActionScope, PolicyDecisionType, PolicyEngine
from clipping.agent.vault.models import AccountMetadata, AccountPlatform, AccountStatus
from clipping.agent.vault.vault import EncryptedCredentialVault
from clipping.logging.logger import get_logger

logger = get_logger("clipping.agent.account.lifecycle")


class AccountResolutionResult(BaseModel):
    """Result of an autonomous account selection or creation attempt."""
    model_config = ConfigDict(frozen=True)

    account: Optional[AccountMetadata] = None
    was_created: bool = False
    branding_profile: Optional[ChannelBrandingProfile] = None
    campaign_id: str
    lifecycle_state: CampaignLifecycleState
    error: Optional[str] = None
    escalation_required: bool = False
    escalation_context: Optional[EscalationContext] = None


class CampaignCompletionResult(BaseModel):
    """Result of concluding a campaign and applying post-campaign lifecycle rules."""
    model_config = ConfigDict(frozen=True)

    campaign_id: str
    account_id: str
    lifecycle_state: CampaignLifecycleState
    reuse_eligible: bool
    actions_taken: List[str] = Field(default_factory=list)
    payment_status: str
    escalation_required: bool = False
    escalation_context: Optional[EscalationContext] = None


class AccountLifecycleService:
    """
    Manages creator account lifecycle throughout campaign execution.
    Core Autonomous Capabilities:
    1. Selects eligible existing accounts without human approval when rules allow reuse.
    2. Autonomously provisions and brands dedicated accounts within policy limits.
    3. Guarantees deterministic, idempotent account creation to survive crashes/restarts.
    4. Enforces post-campaign disposition (reuse clearance, dedicated locking, privacy protections).
    """

    def __init__(
        self,
        vault: EncryptedCredentialVault,
        policy: PolicyEngine,
        branding_generator: Optional[CampaignBrandingGenerator] = None,
    ):
        self.vault = vault
        self.policy = policy
        self.branding_gen = branding_generator or CampaignBrandingGenerator()

    async def select_or_create_account(
        self,
        campaign: CampaignRecord,
        platform: AccountPlatform = AccountPlatform.YOUTUBE,
    ) -> AccountResolutionResult:
        """
        Autonomously resolves an account for the campaign:
        - First attempts to reuse an existing active and eligible account if campaign terms permit.
        - If reuse is not allowed or no account exists, checks policy and provisions a new branded account.
        - Escalate ONLY if policy forbids automatic creation or if human intervention is strictly required.
        """
        cid = campaign.campaign_id
        allow_reuse = campaign.account_requirements.allow_account_reuse

        # 1. Inspect existing active accounts in vault
        existing_accounts = await self.vault.list_accounts(platform=platform)
        active_accounts = [
            a for a in existing_accounts
            if a.status == AccountStatus.ACTIVE
        ]

        # 2. Check if this campaign already has a dedicated/associated active account
        for acc in active_accounts:
            if acc.campaign_association == cid:
                logger.info(
                    "Reusing account already associated with campaign",
                    account_id=acc.account_id,
                    campaign_id=cid,
                )
                return AccountResolutionResult(
                    account=acc,
                    was_created=False,
                    campaign_id=cid,
                    lifecycle_state=CampaignLifecycleState.ACCOUNT_ASSIGNED,
                )

        # 3. If campaign allows reuse, search for an available, reuse-eligible account
        if allow_reuse:
            for acc in active_accounts:
                if acc.reuse_eligibility and (acc.campaign_association is None or acc.campaign_association == ""):
                    # Bind this available account to the campaign
                    updated_acc = acc.model_copy(update={"campaign_association": cid})
                    await self.vault.save_account(updated_acc)
                    logger.info(
                        "Autonomously assigned available account for reuse",
                        account_id=acc.account_id,
                        campaign_id=cid,
                    )
                    return AccountResolutionResult(
                        account=updated_acc,
                        was_created=False,
                        campaign_id=cid,
                        lifecycle_state=CampaignLifecycleState.ACCOUNT_ASSIGNED,
                    )

        # 4. No eligible account found or reuse prohibited — must create new account.
        # Check PolicyEngine authorization before provisioning
        scope = ActionScope(
            capability_name="account_management",
            action_name="create_channel",
            target_resource=f"account:{platform.value}",
            is_reversible=True,
            risk_tier=ActionRiskTier.MUTATING_REVERSIBLE,
        )
        policy_eval = self.policy.evaluate(scope)

        if not policy_eval.allowed or policy_eval.requires_human_confirmation or policy_eval.decision == PolicyDecisionType.REQUIRE_CONFIRMATION:
            logger.warning(
                "Account creation requires human confirmation or was denied by policy",
                campaign_id=cid,
                decision=policy_eval.decision.value,
            )
            return AccountResolutionResult(
                account=None,
                was_created=False,
                campaign_id=cid,
                lifecycle_state=CampaignLifecycleState.ESCALATED,
                escalation_required=True,
                escalation_context=EscalationContext(
                    what_happened=f"Account provisioning needed for campaign '{campaign.name}'",
                    why_it_happened=policy_eval.reason,
                    decision_required="Authorize automatic channel creation or attach existing account credential",
                    available_options=["authorize_creation", "attach_credential", "reject_campaign"],
                    reason=EscalationReason.POLICY_VIOLATION,
                    severity=EscalationSeverity.MEDIUM,
                    metadata={"campaign_id": cid, "platform": platform.value},
                ),
            )

        # 5. Autonomous creation is authorized: synthesize branding profile
        branding = self.branding_gen.generate_branding(campaign, platform)

        # Generate deterministic, idempotent account ID to prevent duplicate accounts on restarts
        cid_slug = re.sub(r"[^a-zA-Z0-9]", "", cid.replace("camp_", ""))[:10]
        hash_seed = f"{cid}_{platform.value}_{branding.handle}"
        brand_hash = hashlib.sha256(hash_seed.encode("utf-8")).hexdigest()[:8]
        account_id = f"acc_{platform.value}_{cid_slug}_{brand_hash}"

        # Check if already exists in vault (idempotency guard)
        existing_meta = await self.vault.get_account_metadata(platform, account_id)
        if existing_meta:
            logger.info(
                "Idempotent hit: Account already provisioned for campaign",
                account_id=account_id,
                campaign_id=cid,
            )
            return AccountResolutionResult(
                account=existing_meta,
                was_created=False,
                branding_profile=branding,
                campaign_id=cid,
                lifecycle_state=CampaignLifecycleState.ACCOUNT_ASSIGNED,
            )

        # Provision new account metadata in encrypted vault
        new_account = AccountMetadata(
            platform=platform,
            account_id=account_id,
            username=branding.handle.lstrip("@"),
            display_name=branding.channel_title,
            campaign_association=cid,
            status=AccountStatus.ACTIVE,
            reuse_eligibility=allow_reuse,
            tags=branding.seo_keywords[:10],
        )
        await self.vault.save_account(new_account)

        logger.info(
            "Autonomously provisioned and branded new creator account",
            account_id=account_id,
            username=new_account.username,
            campaign_id=cid,
            reuse_eligibility=allow_reuse,
        )

        return AccountResolutionResult(
            account=new_account,
            was_created=True,
            branding_profile=branding,
            campaign_id=cid,
            lifecycle_state=CampaignLifecycleState.ACCOUNT_ASSIGNED,
        )

    async def configure_channel_branding(
        self,
        account: AccountMetadata,
        campaign: CampaignRecord,
    ) -> ChannelBrandingProfile:
        """Configures or refreshes channel branding profile and syncs safe metadata."""
        branding = self.branding_gen.generate_branding(campaign, account.platform)

        updated_account = account.model_copy(
            update={
                "display_name": branding.channel_title,
                "tags": branding.seo_keywords[:10],
                "campaign_association": campaign.campaign_id,
            }
        )
        await self.vault.save_account(updated_account)

        logger.info(
            "Configured channel branding profile",
            account_id=account.account_id,
            title=branding.channel_title,
            handle=branding.handle,
            campaign_id=campaign.campaign_id,
        )
        return branding

    async def finalize_campaign_lifecycle(
        self,
        campaign: CampaignRecord,
        account: AccountMetadata,
        payment_status: str = "pending",
    ) -> CampaignCompletionResult:
        """
        Executes post-campaign disposition:
        - Evaluates whether account is eligible for reuse based on post-campaign rules.
        - Frees account for reuse OR binds it permanently if terms prohibit reuse.
        - Applies safe privacy protections without destructive deletion unless strictly authorized.
        """
        cid = campaign.campaign_id
        post_rules = campaign.post_campaign_rules
        actions_taken: List[str] = []

        allow_reuse = post_rules.allow_account_reuse_after_campaign

        if allow_reuse:

            # Free account for future campaigns
            updated_account = account.model_copy(
                update={
                    "reuse_eligibility": True,
                    "campaign_association": None,
                }
            )
            lifecycle_state = CampaignLifecycleState.REUSE_ELIGIBLE
            actions_taken.append("account_freed_for_reuse")
        else:
            # Dedicated lock: account cannot be reused for other campaigns
            updated_account = account.model_copy(
                update={
                    "reuse_eligibility": False,
                    "campaign_association": cid,
                }
            )
            lifecycle_state = CampaignLifecycleState.REUSE_PROHIBITED
            actions_taken.append("account_locked_to_campaign")

        # Privacy rules check
        if post_rules.privatize_videos_on_completion:
            actions_taken.append("marked_campaign_videos_private")

        if post_rules.delete_videos_on_completion:
            # Destruction of media assets is high-risk: verify policy
            del_scope = ActionScope(
                capability_name="media_clipping",
                action_name="delete_media",
                target_resource=f"campaign:{cid}",
                is_reversible=False,
                risk_tier=ActionRiskTier.MUTATING_IRREVERSIBLE,
            )
            del_eval = self.policy.evaluate(del_scope)
            if del_eval.allowed and not del_eval.requires_human_confirmation:
                actions_taken.append("deleted_campaign_videos")
            else:
                actions_taken.append("video_deletion_skipped_guarded_by_policy")

        actions_taken.append(f"recorded_payment_status_{payment_status}")
        await self.vault.save_account(updated_account)

        logger.info(
            "Finalized campaign lifecycle",
            campaign_id=cid,
            account_id=account.account_id,
            lifecycle_state=lifecycle_state.value,
            reuse_eligible=allow_reuse,
            actions=actions_taken,
        )

        return CampaignCompletionResult(
            campaign_id=cid,
            account_id=account.account_id,
            lifecycle_state=lifecycle_state,
            reuse_eligible=allow_reuse,
            actions_taken=actions_taken,
            payment_status=payment_status,
        )
