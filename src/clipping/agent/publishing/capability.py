"""Campaign Publishing and Submission Agent Capability.

Platform-agnostic capability coordinating:
- Pre-submission rule validation (dates, budgets, duration, quotas)
- Media safety verification (QA gate, format, size)
- Master Control safety gate & PolicyEngine authorization
- Deterministic idempotency & duplicate prevention
- Platform adapter execution (YouTube, Instagram)
- Automatic content metadata synthesis
- Lifecycle progression (CONTENT_READY -> SUBMISSION_ACTIVE -> CAMPAIGN_ACTIVE)
- Live result reconciliation
"""

import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from clipping.agent.campaign.models import (
    CampaignLifecycleState,
    CampaignPlatform,
    CampaignRecord,
    CampaignStatus,
)
from clipping.agent.campaign.repository import CampaignRepository
from clipping.agent.capabilities.base import (
    AgentCapability,
    CapabilityContext,
    CapabilityResult,
)
from clipping.agent.cloud.telemetry import CloudTelemetryEngine, TelemetryEventType
from clipping.agent.escalation import EscalationContext, EscalationReason, EscalationSeverity
from clipping.agent.policy import PolicyEngine
from clipping.agent.publishing.adapters.base import PlatformPublishingAdapter
from clipping.agent.publishing.adapters.instagram import InstagramPublishingAdapter
from clipping.agent.publishing.adapters.youtube import YouTubePublishingAdapter
from clipping.agent.publishing.media_safety import MediaSafetyVerifier
from clipping.agent.publishing.models import (
    CampaignSubmissionRecord,
    PublishingContentMetadata,
    PublishingMode,
    SubmissionStatus,
)
from clipping.agent.publishing.reconciliation import PublishingReconciliationService
from clipping.agent.publishing.repository import CampaignSubmissionRepository
from clipping.agent.publishing.rule_engine import SubmissionRuleEngine
from clipping.agent.publishing.safety_gate import PublishingSafetyGate
from clipping.agent.vault.models import AccountMetadata, AccountPlatform, AccountStatus
from clipping.agent.vault.vault import EncryptedCredentialVault
from clipping.control.repository import ControlRepository
from clipping.logging.logger import get_logger

logger = get_logger("clipping.agent.publishing.capability")


class PublishingCapability(AgentCapability):
    """Production Agent Capability executing real social media publishing and campaign submissions."""

    def __init__(
        self,
        submission_repository: CampaignSubmissionRepository,
        campaign_repository: CampaignRepository,
        vault: EncryptedCredentialVault,
        control_repository: ControlRepository,
        policy_engine: PolicyEngine,
        adapters: Optional[Dict[AccountPlatform, PlatformPublishingAdapter]] = None,
        telemetry_engine: Optional[CloudTelemetryEngine] = None,
    ):
        self.repo = submission_repository
        self.campaign_repo = campaign_repository
        self.vault = vault
        self.control_repo = control_repository
        self.policy = policy_engine
        self.telemetry = telemetry_engine

        # Initialize sub-services
        self.rule_engine = SubmissionRuleEngine(repository=self.repo)
        self.safety_gate = PublishingSafetyGate(control_repository=self.control_repo, policy_engine=self.policy)
        self.media_verifier = MediaSafetyVerifier(storage_driver=self.repo.storage)

        # Register platform adapters
        self.adapters = adapters or {
            AccountPlatform.YOUTUBE: YouTubePublishingAdapter(),
            AccountPlatform.INSTAGRAM: InstagramPublishingAdapter(),
        }

        self.reconciler = PublishingReconciliationService(
            repository=self.repo,
            adapters=self.adapters,
            vault=self.vault,
        )

    @property
    def name(self) -> str:
        return "campaign_publishing"

    @property
    def description(self) -> str:
        return "Executes real social media publishing, scheduling, rule validation, media safety verification, and submission tracking"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def is_idempotent(self) -> bool:
        return True

    @property
    def is_reversible(self) -> bool:
        return False

    def generate_idempotency_key(
        self,
        campaign_id: str,
        clip_id: str,
        platform: AccountPlatform,
        mode: PublishingMode,
    ) -> str:
        """Generates deterministic idempotency key to prevent duplicate uploads."""
        return f"{campaign_id}:{clip_id}:{platform.value}:{mode.value}"

    def synthesize_metadata(
        self,
        campaign: CampaignRecord,
        clip_id: str,
        clip_title: Optional[str] = None,
        clip_description: Optional[str] = None,
        user_metadata: Optional[Dict[str, Any]] = None,
        privacy_status: str = "private",
        scheduled_at: Optional[datetime] = None,
    ) -> PublishingContentMetadata:
        """Autonomously synthesizes campaign-compliant publishing metadata."""
        user_meta = user_metadata or {}

        # 1. Title formatting (<100 chars, no spam)
        base_title = clip_title or user_meta.get("title") or f"{campaign.name} Highlight"
        # Include required keywords if missing
        for kw in campaign.posting_requirements.required_title_keywords:
            if kw.lower() not in base_title.lower() and len(f"{base_title} | {kw}") <= 100:
                base_title = f"{base_title} | {kw}"
        clean_title = base_title[:100]

        # 2. Description & Hashtags
        hashtags = list(campaign.posting_requirements.required_hashtags)
        if "hashtags" in user_meta and isinstance(user_meta["hashtags"], list):
            for t in user_meta["hashtags"]:
                if t not in hashtags:
                    hashtags.append(t)

        mentions = list(campaign.posting_requirements.required_mentions)
        if "mentions" in user_meta and isinstance(user_meta["mentions"], list):
            for m in user_meta["mentions"]:
                if m not in mentions:
                    mentions.append(m)

        description = clip_description or user_meta.get("description") or f"Curated insights from {campaign.name}."
        if mentions:
            description += f"\n\nFeaturing: {' '.join(mentions)}"
        if hashtags:
            description += f"\n\n{' '.join(hashtags)}"

        return PublishingContentMetadata(
            title=clean_title,
            description=description[:5000],
            hashtags=hashtags,
            mentions=mentions,
            campaign_identifiers={"campaign_id": campaign.campaign_id, "clip_id": clip_id},
            platform_specific=user_meta.get("platform_specific", {}),
            privacy_status=privacy_status,
            scheduled_publish_at=scheduled_at,
        )

    async def execute(self, context: CapabilityContext) -> CapabilityResult:
        inputs = context.inputs
        action = inputs.get("action", "submit_and_publish")

        if action == "reconcile":
            cid = inputs.get("campaign_id")
            sub_id = inputs.get("submission_id")
            if not cid or not sub_id:
                return CapabilityResult.failed("MissingArgumentError", "Missing campaign_id or submission_id")
            rec_res = await self.reconciler.reconcile_submission(cid, sub_id)
            return CapabilityResult.successful(outputs={"reconciliation": rec_res.model_dump(mode="json")})

        campaign_id = inputs.get("campaign_id")
        clip_id = inputs.get("clip_id")
        account_id = inputs.get("account_id")
        media_path = inputs.get("media_path")
        platform_str = inputs.get("platform", "youtube")
        mode_str = inputs.get("publishing_mode", "draft")
        task_id = context.task_id or inputs.get("task_id", f"task_pub_{clip_id}")

        if not campaign_id or not clip_id or not account_id or not media_path:
            return CapabilityResult.failed(
                error_type="MissingArgumentError",
                message="Missing required parameters: campaign_id, clip_id, account_id, or media_path",
            )

        platform = AccountPlatform(platform_str)
        mode = PublishingMode(mode_str)
        idemp_key = self.generate_idempotency_key(campaign_id, clip_id, platform, mode)

        # 1. Idempotency Check: Avoid Duplicate Uploads
        existing_sub = await self.repo.get_submission_by_idempotency(idemp_key)
        if existing_sub:
            if existing_sub.current_status in (SubmissionStatus.PUBLISHED, SubmissionStatus.SCHEDULED, SubmissionStatus.SUBMITTED):
                logger.info(
                    "Idempotency hit: Clip already published or submitted",
                    submission_id=existing_sub.submission_id,
                    status=existing_sub.current_status.value,
                )
                return CapabilityResult.successful(outputs={"submission": existing_sub.model_dump(mode="json"), "idempotent_hit": True})

        # 2. Load Campaign & Account Entities
        campaign = await self.campaign_repo.get_campaign(campaign_id)
        if not campaign:
            return CapabilityResult.failed("CampaignNotFoundError", f"Campaign '{campaign_id}' not found")

        account = await self.vault.get_account_metadata(platform, account_id)
        if not account:
            return CapabilityResult.failed("AccountNotFoundError", f"Account '{account_id}' not found in vault")

        # 3. Content Metadata Synthesis
        privacy = inputs.get("privacy_status", "private" if mode in (PublishingMode.DRAFT, PublishingMode.SCHEDULED) else "public")
        scheduled_at = inputs.get("scheduled_publish_at")
        meta = self.synthesize_metadata(
            campaign=campaign,
            clip_id=clip_id,
            clip_title=inputs.get("title"),
            clip_description=inputs.get("description"),
            user_metadata=inputs.get("metadata"),
            privacy_status=privacy,
            scheduled_at=scheduled_at,
        )

        # 4. Submission Rule Engine Pre-Submission Validation
        clip_duration = float(inputs.get("duration_seconds", 45.0))
        rule_eval = await self.rule_engine.validate_submission(
            campaign=campaign,
            account=account,
            clip_id=clip_id,
            clip_duration_seconds=clip_duration,
            metadata=meta,
            target_platform=platform,
            task_id=task_id,
        )

        if not rule_eval.is_valid:
            if rule_eval.escalation_required and rule_eval.escalation_context:
                return CapabilityResult.escalate(rule_eval.escalation_context)
            return CapabilityResult.failed(
                error_type="RuleValidationFailed",
                message="; ".join(rule_eval.reasons),
            )

        # 5. Media Safety Verification
        media_eval = await self.media_verifier.verify_media(
            media_path=media_path,
            campaign_id=campaign_id,
            clip_id=clip_id,
            expected_platform=platform,
            qa_record=inputs.get("qa_record"),
            min_duration_seconds=campaign.posting_requirements.min_duration_seconds,
            max_duration_seconds=campaign.posting_requirements.max_duration_seconds,
        )
        if not media_eval.is_safe:
            return CapabilityResult.failed(
                error_type="MediaSafetyError",
                message="; ".join(media_eval.reasons),
            )

        # 6. Publishing Safety Gate Check (Master Control + PolicyEngine)
        safety_eval = await self.safety_gate.evaluate_safety(
            account_id=account_id,
            platform=platform,
            publishing_mode=mode,
        )
        if not safety_eval.can_proceed:
            # Blocked: persist submission in BLOCKED state so work is not lost
            sub_id = f"sub_{campaign_id}_{clip_id}_{platform.value[:2]}"
            blocked_sub = CampaignSubmissionRecord(
                submission_id=sub_id,
                campaign_id=campaign_id,
                account_id=account_id,
                platform=platform,
                clip_id=clip_id,
                source_video_id=inputs.get("source_video_id", ""),
                task_id=task_id,
                publishing_mode=mode,
                current_status=SubmissionStatus.BLOCKED,
                content_metadata=meta,
                media_path=media_path,
                idempotency_key=idemp_key,
                last_error=safety_eval.reason,
            )
            await self.repo.save_submission(blocked_sub)
            return CapabilityResult.failed(
                error_type="PublishingGateBlocked",
                message=safety_eval.reason,
            )

        # 7. Create & Persist Initial Submission Record (State: UPLOADING)
        sub_id = f"sub_{campaign_id}_{clip_id}_{platform.value[:2]}"
        submission = CampaignSubmissionRecord(
            submission_id=sub_id,
            campaign_id=campaign_id,
            account_id=account_id,
            platform=platform,
            clip_id=clip_id,
            source_video_id=inputs.get("source_video_id", ""),
            task_id=task_id,
            publishing_mode=mode,
            current_status=SubmissionStatus.UPLOADING,
            content_metadata=meta,
            media_path=media_path,
            idempotency_key=idemp_key,
        )
        await self.repo.save_submission(submission)

        # 8. Platform Adapter Upload / Publishing Execution
        adapter = self.adapters.get(platform)
        if not adapter:
            return CapabilityResult.failed("UnsupportedPlatformError", f"No adapter found for {platform.value}")

        credentials = {}
        try:
            sec = await self.vault.get_sensitive_secret(platform, account_id)
            if sec:
                credentials = sec
        except Exception:
            pass

        adapter_res = await adapter.publish(submission=submission, media_path=media_path, credentials=credentials)

        if not adapter_res.success:
            if adapter_res.escalation_required and adapter_res.escalation_context:
                submission = submission.transition_to(
                    new_status=SubmissionStatus.ESCALATED,
                    reason=adapter_res.error_message,
                )
                await self.repo.save_submission(submission)
                return CapabilityResult.escalate(adapter_res.escalation_context)

            submission = submission.transition_to(
                new_status=adapter_res.status,
                reason=adapter_res.error_message,
            )
            await self.repo.save_submission(submission)
            return CapabilityResult.failed(
                error_type="PlatformUploadError",
                message=adapter_res.error_message or "Upload failed",
            )

        # Upload Succeeded: Update submission record to target status
        post_id = adapter_res.platform_post_id or ""
        is_synthetic = (
            not post_id
            or post_id.startswith(("yt_mock", "mock_", "ig_mock", "synthetic_"))
            or "mock" in post_id.lower()
            or "synthetic" in post_id.lower()
        )
        if is_synthetic and mode in (PublishingMode.IMMEDIATE, PublishingMode.SCHEDULED):
            logger.error("Live publishing rejected: Synthetic or mock platform post ID detected", post_id=post_id)
            submission = submission.transition_to(
                new_status=SubmissionStatus.FAILED,
                reason=f"Live publishing rejected: Synthetic or mock post ID '{post_id}' detected from adapter",
            )
            await self.repo.save_submission(submission)
            return CapabilityResult.failed(
                error_type="SyntheticPostIdRejected",
                message=f"Live publishing rejected: Adapter returned synthetic/mock post ID '{post_id}' instead of real platform confirmation",
            )

        submission = submission.transition_to(
            new_status=adapter_res.status,
            platform_post_id=adapter_res.platform_post_id,
            platform_url=adapter_res.platform_url,
            reason="Platform upload completed successfully",
            details=adapter_res.raw_response,
        )
        await self.repo.save_submission(submission)

        # 9. Campaign Lifecycle State Progression
        if campaign.lifecycle_state in (
            CampaignLifecycleState.CONTENT_PRODUCTION,
            CampaignLifecycleState.CONTENT_READY,
            CampaignLifecycleState.SUBMISSION_ACTIVE,
        ):
            updated_camp = campaign.model_copy(
                update={"lifecycle_state": CampaignLifecycleState.CAMPAIGN_ACTIVE}
            )
            await self.campaign_repo.save_campaign(updated_camp)

        logger.info(
            "Campaign submission and publishing workflow completed",
            submission_id=submission.submission_id,
            platform_post_id=adapter_res.platform_post_id,
            status=submission.current_status.value,
        )

        return CapabilityResult.successful(outputs={"submission": submission.model_dump(mode="json")})
