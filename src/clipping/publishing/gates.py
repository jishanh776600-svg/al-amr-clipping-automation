"""Approval and QA Gate Enforcers for Production Publishing."""

from typing import Optional, Tuple
from clipping.approval.models import ApprovalStatus
from clipping.approval.repository import ApprovalRepository
from clipping.contracts.qa import QAReport
from clipping.control.repository import ControlRepository
from clipping.publishing.models import PublishStatus
from clipping.storage.base import StorageDriver
from clipping.logging.logger import get_logger

logger = get_logger("clipping.publishing.gates")


class PublishingGateEnforcer:
    """
    Enforces strict pre-upload gates:
    0. Master Control Gate: System MUST NOT be Emergency Stopped or Publishing Locked.
    1. Approval Gate: Clip MUST be explicitly APPROVED in canonical Google Drive state.
    2. QA Gate: Clip MUST have passed all QA validation checks.
    3. Artifact Gate: Rendered MP4 video MUST exist in remote storage.
    """

    def __init__(
        self,
        approval_repository: ApprovalRepository,
        storage_driver: StorageDriver,
        control_repository: Optional[ControlRepository] = None,
    ):
        self.approval_repo = approval_repository
        self.storage = storage_driver
        self.control_repo = control_repository

    async def verify_control_gate(self) -> Tuple[bool, PublishStatus, str]:
        """
        Validates global Master Control state. If emergency stopped or publishing locked,
        all publishing is safely deferred.
        """
        if not self.control_repo:
            return True, PublishStatus.READY, "No control gate configured"

        if await self.control_repo.is_emergency_stopped():
            return False, PublishStatus.DEFERRED, "Global EMERGENCY STOP active: publishing blocked"

        if await self.control_repo.is_publishing_locked():
            return False, PublishStatus.DEFERRED, "Global Publishing Lock active: publishing blocked"

        return True, PublishStatus.READY, "Master Control gate passed"

    async def verify_approval_gate(
        self,
        job_id: str,
        approval_request_id: str,
    ) -> Tuple[bool, PublishStatus, str]:
        """
        Validates canonical remote approval state.
        Returns (is_approved, target_status, reason).
        """
        req = await self.approval_repo.get_request_by_id(approval_request_id)
        if not req:
            return False, PublishStatus.SKIPPED, f"Approval request not found: {approval_request_id}"

        if req.status == ApprovalStatus.REJECTED:
            return False, PublishStatus.SKIPPED, "Clip is REJECTED by reviewer"

        if req.status == ApprovalStatus.AWAITING_APPROVAL:
            return False, PublishStatus.DEFERRED, "Clip is AWAITING_APPROVAL"

        if req.status == ApprovalStatus.APPROVED:
            return True, PublishStatus.READY, "Clip is APPROVED"

        return False, PublishStatus.SKIPPED, f"Unknown approval status: {req.status}"

    async def verify_qa_gate(self, clip_id: str) -> Tuple[bool, str]:
        """
        Validates that QA checks were completed and that can_publish is True.
        """
        qa_key = f"clips/{clip_id}/qa_report.json"
        if not await self.storage.exists(qa_key):
            return False, f"Missing QA report in storage: {qa_key}"

        try:
            raw = await self.storage.download_bytes(qa_key)
            report = QAReport.model_validate_json(raw.decode("utf-8"))
            if not report.can_publish:
                return False, f"QA gate blocked clip (status: {report.overall_status.value})"
            return True, "QA gate passed"
        except Exception as e:
            logger.error("Failed to parse QA report for gate verification", clip_id=clip_id, error=str(e))
            return False, f"Invalid QA report: {str(e)}"

    async def verify_artifact_gate(self, video_storage_key: str) -> Tuple[bool, str]:
        """
        Confirms that the rendered video artifact physically exists in storage.
        """
        if not await self.storage.exists(video_storage_key):
            return False, f"Rendered video artifact not found in storage: {video_storage_key}"
        return True, "Artifact verified"
