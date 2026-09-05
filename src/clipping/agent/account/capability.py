"""Account and Channel Operations Capability."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from clipping.agent.capabilities.base import AgentCapability, CapabilityContext, CapabilityResult
from clipping.agent.escalation import EscalationContext, EscalationReason, EscalationSeverity
from clipping.agent.vault.models import AccountMetadata, AccountPlatform, AccountStatus
from clipping.agent.vault.vault import EncryptedCredentialVault
from clipping.logging.logger import get_logger

logger = get_logger("clipping.agent.account.capability")


class AccountManagementCapability(AgentCapability):
    """
    Capability managing routine channel profile configuration, SEO metadata,
    campaign associations, and account lifecycle records within policy limits.
    """

    MAX_ACCOUNTS_PER_CAMPAIGN = 3

    def __init__(self, vault: Optional[EncryptedCredentialVault] = None):
        self._vault = vault

    @property
    def name(self) -> str:
        return "account_management"

    @property
    def description(self) -> str:
        return "Configures channel metadata, campaign association, and tracks account lifecycle safely"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def is_idempotent(self) -> bool:
        return True

    @property
    def is_reversible(self) -> bool:
        return True

    async def execute(self, context: CapabilityContext) -> CapabilityResult:
        vault = self._vault or EncryptedCredentialVault(storage_driver=context.storage_driver)
        inputs = context.inputs
        action = inputs.get("action", "configure_profile")

        platform_str = inputs.get("platform", "youtube")
        platform = AccountPlatform(platform_str)
        account_id = inputs.get("account_id")

        if action == "configure_profile":
            if not account_id:
                return CapabilityResult.failed(
                    error_type="MissingArgumentError",
                    message="Missing 'account_id' for profile configuration",
                )
            meta = await vault.get_account_metadata(platform, account_id)
            if not meta:
                return CapabilityResult.failed(
                    error_type="AccountNotFoundError",
                    message=f"Account '{account_id}' not found in vault",
                )

            display_name = inputs.get("display_name", meta.display_name)
            campaign_id = inputs.get("campaign_id", meta.campaign_association)
            tags = inputs.get("tags", meta.tags)

            updated = meta.model_copy(
                update={
                    "display_name": display_name,
                    "campaign_association": campaign_id,
                    "tags": tags,
                }
            )
            await vault.save_account(updated)
            return CapabilityResult.successful(outputs={"account": updated.to_safe_dict()})

        elif action == "associate_campaign":
            if not account_id:
                return CapabilityResult.failed(
                    error_type="MissingArgumentError",
                    message="Missing 'account_id'",
                )
            campaign_id = inputs.get("campaign_id")
            if not campaign_id:
                return CapabilityResult.failed(
                    error_type="MissingArgumentError",
                    message="Missing 'campaign_id'",
                )

            # Check bounded account limit per campaign
            existing = await vault.list_accounts(platform=platform)
            associated = [a for a in existing if a.campaign_association == campaign_id]
            if len(associated) >= self.MAX_ACCOUNTS_PER_CAMPAIGN and account_id not in [a.account_id for a in associated]:
                return CapabilityResult.failed(
                    error_type="PolicyLimitExceeded",
                    message=f"Campaign '{campaign_id}' already has {len(associated)} associated accounts (limit: {self.MAX_ACCOUNTS_PER_CAMPAIGN})",
                )

            meta = await vault.get_account_metadata(platform, account_id)
            if not meta:
                return CapabilityResult.failed(
                    error_type="AccountNotFoundError",
                    message=f"Account '{account_id}' not found",
                )

            updated = meta.model_copy(update={"campaign_association": campaign_id})
            await vault.save_account(updated)
            return CapabilityResult.successful(outputs={"account": updated.to_safe_dict()})

        elif action == "create_channel_record":
            # Creates or records a permitted channel record
            username = inputs.get("username")
            if not username:
                return CapabilityResult.failed(
                    error_type="MissingArgumentError",
                    message="Missing 'username'",
                )

            acc_id = account_id or f"acc_{platform.value}_{username.lower()}"
            new_meta = AccountMetadata(
                platform=platform,
                account_id=acc_id,
                username=username,
                display_name=inputs.get("display_name", username),
                campaign_association=inputs.get("campaign_id"),
                status=AccountStatus.ACTIVE,
                reuse_eligibility=inputs.get("reuse_eligibility", True),
                tags=inputs.get("tags", []),
            )
            sensitive_data = inputs.get("sensitive_credentials")
            await vault.save_account(new_meta, sensitive_credentials=sensitive_data)
            return CapabilityResult.successful(outputs={"account": new_meta.to_safe_dict()})

        elif action == "escalate_challenge":
            challenge = inputs.get("challenge", "mfa")
            reason = EscalationReason.CAPTCHA_CHALLENGE if challenge == "captcha" else EscalationReason.MFA_REQUIRED
            return CapabilityResult.escalate(
                EscalationContext(
                    what_happened=f"Account operation paused: {challenge.upper()} requires manual verification",
                    why_it_happened=f"Platform {platform_str} triggered {challenge.upper()} on account {account_id}",
                    decision_required=f"Perform manual verification / {challenge.upper()} resolution for account",
                    available_options=["resolve_verification", "cancel_account_operation"],
                    metadata={"task_id": context.task_id, "reason": reason.value, "account_id": account_id, "platform": platform_str},
                )
            )

        return CapabilityResult.failed(
            error_type="UnsupportedActionError",
            message=f"Action '{action}' is not supported",
        )
