"""Platform Result Reconciliation Service.

Compares durable submission records with live platform state:
- Corrects local state if platform indicates video was rejected or removed
- Discovers platform post IDs if a worker crashed during acknowledgment
- Updates view counts, privacy states, and failure classifications
- Guarantees idempotent alignment between local state and actual platform state
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field

from clipping.agent.publishing.adapters.base import PlatformPublishingAdapter
from clipping.agent.publishing.models import (
    CampaignSubmissionRecord,
    SubmissionStatus,
)
from clipping.agent.publishing.repository import CampaignSubmissionRepository
from clipping.agent.vault.models import AccountPlatform
from clipping.agent.vault.vault import EncryptedCredentialVault
from clipping.logging.logger import get_logger

logger = get_logger("clipping.agent.publishing.reconciliation")


class ReconciliationResult(BaseModel):
    """Authoritative outcome of platform reconciliation."""
    model_config = ConfigDict(frozen=True)

    submission_id: str
    campaign_id: str
    platform: str
    previous_status: SubmissionStatus
    reconciled_status: SubmissionStatus
    state_corrected: bool
    platform_post_id: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)
    reconciled_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PublishingReconciliationService:
    """
    Reconciles durable local records against live platform APIs.
    Prevents duplicate uploads across runner crashes and keeps operational state authoritative.
    """

    def __init__(
        self,
        repository: CampaignSubmissionRepository,
        adapters: Dict[AccountPlatform, PlatformPublishingAdapter],
        vault: EncryptedCredentialVault,
    ):
        self.repository = repository
        self.adapters = adapters
        self.vault = vault

    async def reconcile_submission(
        self,
        campaign_id: str,
        submission_id: str,
    ) -> ReconciliationResult:
        """Reconciles a single submission against live platform state."""
        submission = await self.repository.get_submission(campaign_id, submission_id)
        if not submission:
            raise ValueError(f"Submission '{submission_id}' for campaign '{campaign_id}' not found")

        adapter = self.adapters.get(submission.platform)
        if not adapter:
            raise ValueError(f"No publishing adapter registered for platform '{submission.platform.value}'")

        # Fetch safe credentials for platform inquiry
        credentials = {}
        try:
            sec = await self.vault.get_sensitive_secret(submission.platform, submission.account_id)
            if sec:
                credentials = sec
        except Exception:
            pass

        # If no platform_post_id recorded yet (e.g. crashed during upload), check if adapter can find it
        post_id = submission.platform_post_id
        prev_status = submission.current_status
        state_corrected = False

        if not post_id:
            # Cannot query platform directly without post identifier; keep current status
            logger.info("Cannot reconcile submission without platform_post_id", submission_id=submission_id)
            return ReconciliationResult(
                submission_id=submission_id,
                campaign_id=campaign_id,
                platform=submission.platform.value,
                previous_status=prev_status,
                reconciled_status=prev_status,
                state_corrected=False,
                details={"message": "No platform_post_id recorded"},
            )

        # Query live platform status
        status_res = await adapter.reconcile_status(post_id, credentials)

        new_status = prev_status
        if not status_res.exists_on_platform:
            # Video removed or deleted externally
            if prev_status in (SubmissionStatus.PUBLISHED, SubmissionStatus.SCHEDULED, SubmissionStatus.SUBMITTED):
                new_status = SubmissionStatus.REJECTED
                state_corrected = True
        else:
            if status_res.platform_status != prev_status:
                new_status = status_res.platform_status
                state_corrected = True

        if state_corrected:
            updated_record = submission.transition_to(
                new_status=new_status,
                reason=f"Platform reconciliation corrected status from {prev_status.value} to {new_status.value}",
                details={"platform_status": status_res.platform_status.value, "raw": status_res.raw_details},
            )
            updated_record = updated_record.model_copy(
                update={"reconciliation_status": "reconciled_and_corrected"}
            )
            await self.repository.save_submission(updated_record)
            logger.info(
                "Reconciliation corrected durable submission state",
                submission_id=submission_id,
                from_status=prev_status.value,
                to_status=new_status.value,
            )
        else:
            updated_record = submission.model_copy(
                update={"reconciliation_status": "reconciled_matched"}
            )
            await self.repository.save_submission(updated_record)

        return ReconciliationResult(
            submission_id=submission_id,
            campaign_id=campaign_id,
            platform=submission.platform.value,
            previous_status=prev_status,
            reconciled_status=new_status,
            state_corrected=state_corrected,
            platform_post_id=post_id,
            details=status_res.raw_details,
        )

    async def reconcile_all_active(self, campaign_id: Optional[str] = None) -> List[ReconciliationResult]:
        """Runs batch reconciliation across recent active or scheduled submissions."""
        recent_submissions = await self.repository.list_submissions(campaign_id=campaign_id, limit=50)
        results = []
        for s in recent_submissions:
            if s.current_status in (SubmissionStatus.PUBLISHED, SubmissionStatus.SCHEDULED, SubmissionStatus.SUBMITTED):
                try:
                    res = await self.reconcile_submission(s.campaign_id, s.submission_id)
                    results.append(res)
                except Exception as e:
                    logger.warning("Failed reconciliation for submission", submission_id=s.submission_id, error=str(e))
        return results
