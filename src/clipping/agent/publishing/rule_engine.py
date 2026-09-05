"""Campaign Submission Rule Engine.

Evaluates live campaign terms, quotas, dates, budgets, content rules, and duplicate
prevention before authorizing a content submission. Escalates immediately upon encountering
contradictory or ambiguous rules.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field

from clipping.agent.campaign.models import CampaignPlatform, CampaignRecord, CampaignStatus
from clipping.agent.escalation import EscalationContext, EscalationReason, EscalationSeverity
from clipping.agent.publishing.models import PublishingContentMetadata
from clipping.agent.publishing.repository import CampaignSubmissionRepository
from clipping.agent.vault.models import AccountMetadata, AccountPlatform, AccountStatus
from clipping.logging.logger import get_logger

logger = get_logger("clipping.agent.publishing.rule_engine")


class SubmissionValidationResult(BaseModel):
    """Authoritative result from the Submission Rule Engine."""
    model_config = ConfigDict(frozen=True)

    is_valid: bool
    reasons: List[str] = Field(default_factory=list)
    escalation_required: bool = False
    escalation_context: Optional[EscalationContext] = None
    is_quota_exhausted: bool = False
    quota_reset_time: Optional[datetime] = None


class SubmissionRuleEngine:
    """
    Validates submission candidates against real-time campaign terms and quotas.
    Enforces the Core Safety Rule:
    If any requirement is contradictory, impossible, or ambiguous, STOP and escalate.
    """

    def __init__(self, repository: CampaignSubmissionRepository):
        self.repository = repository

    async def validate_submission(
        self,
        campaign: CampaignRecord,
        account: AccountMetadata,
        clip_id: str,
        clip_duration_seconds: float,
        metadata: PublishingContentMetadata,
        target_platform: AccountPlatform,
        task_id: str = "task_publish_eval",
    ) -> SubmissionValidationResult:
        cid = campaign.campaign_id
        now = datetime.now(timezone.utc)
        reasons: List[str] = []

        # 1. Contradiction & Ambiguity Check in Campaign Rules
        contradiction = campaign.validate_rules()
        if contradiction:
            logger.warning("Contradictory campaign terms detected during submission evaluation", campaign_id=cid, error=contradiction)
            return SubmissionValidationResult(
                is_valid=False,
                reasons=[f"Contradictory campaign terms: {contradiction}"],
                escalation_required=True,
                escalation_context=EscalationContext(
                    what_happened=f"Submission blocked: Campaign '{campaign.name}' has contradictory terms",
                    why_it_happened=contradiction,
                    decision_required="Resolve contradictory campaign terms before submitting content",
                    available_options=["resolve_contradiction", "reject_submission"],
                    reason=EscalationReason.CONTRADICTORY_INSTRUCTIONS,
                    severity=EscalationSeverity.HIGH,
                    metadata={"task_id": task_id, "campaign_id": cid, "clip_id": clip_id},
                ),
            )

        # 2. Campaign Active State Check
        if campaign.status != CampaignStatus.ACTIVE:
            reasons.append(f"Campaign is not active (current status: {campaign.status.value})")

        # 3. Campaign Timeline & Expiration Check
        if campaign.duration_terms.check_expired_at(now):
            reasons.append(f"Campaign deadline has expired (deadline: {campaign.duration_terms.deadline or campaign.duration_terms.end_date})")

        # 4. Budget Availability Check
        if not campaign.payout_terms.is_healthy_budget():
            reasons.append(f"Campaign budget is exhausted or depleted (remaining: ${campaign.payout_terms.remaining_budget})")

        # 5. Clip Duration Boundaries Check
        min_sec = campaign.posting_requirements.min_duration_seconds
        max_sec = campaign.posting_requirements.max_duration_seconds
        if clip_duration_seconds < min_sec or clip_duration_seconds > max_sec:
            reasons.append(
                f"Clip duration ({clip_duration_seconds:.1f}s) outside allowed bounds [{min_sec}s - {max_sec}s]"
            )

        # 6. Platform Eligibility Check
        allowed_platforms = [p.value.lower() for p in campaign.required_platforms]
        if not any(target_platform.value.lower() in p for p in allowed_platforms):
            reasons.append(
                f"Platform '{target_platform.value}' not in campaign permitted platforms ({allowed_platforms})"
            )

        # 7. Account Eligibility Check
        if account.status != AccountStatus.ACTIVE:
            reasons.append(f"Account '{account.account_id}' is not active (status: {account.status.value})")

        if not campaign.account_requirements.allow_account_reuse:
            if account.campaign_association and account.campaign_association != cid:
                reasons.append(
                    f"Campaign requires dedicated account, but account '{account.account_id}' is associated with '{account.campaign_association}'"
                )

        # 8. Required Hashtags Check
        meta_tags_lower = {t.lower() for t in metadata.hashtags}
        for req_tag in campaign.posting_requirements.required_hashtags:
            clean_tag = req_tag.lower()
            if not clean_tag.startswith("#"):
                clean_tag = f"#{clean_tag}"
            if clean_tag not in meta_tags_lower:
                reasons.append(f"Missing required hashtag: {req_tag}")

        # 9. Required Mentions Check
        meta_mentions_lower = {m.lower() for m in metadata.mentions}
        for req_mention in campaign.posting_requirements.required_mentions:
            clean_mention = req_mention.lower()
            if not clean_mention.startswith("@"):
                clean_mention = f"@{clean_mention}"
            if clean_mention not in meta_mentions_lower:
                reasons.append(f"Missing required mention: {req_mention}")

        # 10. Prohibited Content Rules Check
        combined_text = f"{metadata.title} {metadata.description} {' '.join(metadata.hashtags)}".lower()
        for prohibited in campaign.prohibited_content_rules:
            p_clean = prohibited.lower().replace("_", " ")
            if p_clean in combined_text:
                reasons.append(f"Metadata violates prohibited content rule: '{prohibited}'")

        # 11. Duplicate Submission Check (Clip already submitted for this campaign)
        existing_sub = await self.repository.get_submission_by_clip(cid, clip_id)
        if existing_sub and existing_sub.current_status not in ("failed", "cancelled", "rejected"):
            reasons.append(
                f"Clip '{clip_id}' has already been submitted for campaign '{cid}' (status: {existing_sub.current_status.value})"
            )

        # 12. Quota & Rate Limit Checks
        # A. Account daily limit
        account_today_count = await self.repository.count_submissions_today(account.account_id, cid)
        daily_creator_limit = campaign.quotas.daily_creator_limit
        if account_today_count >= daily_creator_limit:
            reasons.append(
                f"Daily creator submission quota reached for account ({account_today_count}/{daily_creator_limit})"
            )
            return SubmissionValidationResult(
                is_valid=False,
                reasons=reasons,
                is_quota_exhausted=True,
            )

        # B. Campaign total submissions cap
        if campaign.quotas.campaign_total_clip_cap:
            if campaign.quotas.current_total_submissions >= campaign.quotas.campaign_total_clip_cap:
                reasons.append(
                    f"Campaign total submission cap reached ({campaign.quotas.current_total_submissions}/{campaign.quotas.campaign_total_clip_cap})"
                )
                return SubmissionValidationResult(
                    is_valid=False,
                    reasons=reasons,
                    is_quota_exhausted=True,
                )

        if reasons:
            logger.info("Submission validation failed", campaign_id=cid, clip_id=clip_id, reasons=reasons)
            return SubmissionValidationResult(is_valid=False, reasons=reasons)

        logger.info("Submission validation passed", campaign_id=cid, clip_id=clip_id, platform=target_platform.value)
        return SubmissionValidationResult(is_valid=True)
