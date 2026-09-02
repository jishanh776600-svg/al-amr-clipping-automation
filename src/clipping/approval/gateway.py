"""High-level Telegram Approval Gateway for Pipeline Integration."""

from typing import Dict, List, Optional
from clipping.approval.models import (
    ApprovalRequest,
    ApprovalStatus,
    ApprovalSummary,
)
from clipping.approval.service import ApprovalService
from clipping.approval.repository import ApprovalRepository
from clipping.contracts.clip import RankedCandidate
from clipping.contracts.rendering import RenderOutput
from clipping.logging.logger import get_logger

logger = get_logger("clipping.approval.gateway")


class TelegramApprovalGateway:
    """
    High-level facade orchestrating approval request distribution and status aggregation
    for the Clipping Automation pipeline.
    """

    def __init__(
        self,
        approval_service: ApprovalService,
        approval_repository: ApprovalRepository,
    ):
        self.service = approval_service
        self.repository = approval_repository

    async def dispatch_candidate_clips(
        self,
        job_id: str,
        source_video_id: str,
        ranked_candidates: List[RankedCandidate],
        render_outputs: Dict[str, RenderOutput],
        chat_id: int,
    ) -> List[ApprovalRequest]:
        """
        Creates and dispatches Telegram approval request cards for all rendered candidates.
        """
        created_requests: List[ApprovalRequest] = []

        for idx, candidate in enumerate(ranked_candidates, start=1):
            clip_id = candidate.candidate.candidate_id
            render_out = render_outputs.get(clip_id)
            if not render_out:
                logger.warning("No render output found for candidate", clip_id=clip_id)
                continue

            request_id = f"req_{clip_id[:16]}"
            title = candidate.candidate.hook_sentence[:60] if candidate.candidate.hook_sentence else f"Clip {clip_id}"

            req = ApprovalRequest(
                approval_request_id=request_id,
                job_id=job_id,
                source_video_id=source_video_id,
                clip_id=clip_id,
                clip_index=idx,
                title=title,
                hook_sentence=candidate.candidate.hook_sentence,
                start_time=candidate.candidate.start_time,
                end_time=candidate.candidate.end_time,
                duration=candidate.candidate.duration,
                score=candidate.score.overall_virality_score,
                qa_status="PASS",
                video_storage_key=render_out.output_storage_key,
                status=ApprovalStatus.AWAITING_APPROVAL,
            )

            dispatched = await self.service.create_and_send_request(req, chat_id=chat_id)
            created_requests.append(dispatched)

        logger.info(f"Dispatched {len(created_requests)} approval cards to Telegram", job_id=job_id)
        return created_requests

    async def get_approval_summary(self, job_id: str) -> ApprovalSummary:
        """Retrieves and calculates aggregate approval decision metrics for a job."""
        requests = await self.repository.list_requests_for_job(job_id)
        total = len(requests)
        approved = sum(1 for r in requests if r.status == ApprovalStatus.APPROVED)
        rejected = sum(1 for r in requests if r.status == ApprovalStatus.REJECTED)
        awaiting = sum(1 for r in requests if r.status == ApprovalStatus.AWAITING_APPROVAL)

        all_decided = (total > 0) and (awaiting == 0)

        return ApprovalSummary(
            job_id=job_id,
            total_clips=total,
            approved_count=approved,
            rejected_count=rejected,
            awaiting_count=awaiting,
            all_decided=all_decided,
        )

    async def get_approved_clips(self, job_id: str) -> List[ApprovalRequest]:
        """Returns all approved clips ready for downstream publishing."""
        requests = await self.repository.list_requests_for_job(job_id)
        return [r for r in requests if r.status == ApprovalStatus.APPROVED]
