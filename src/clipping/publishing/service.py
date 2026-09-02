"""Core Publishing Service Orchestration & Lifecycle Management."""

import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
from clipping.core.workspace import WorkerScratchWorkspace
from clipping.publishing.models import (
    PublishRequest,
    PublishStatus,
    PublishAuditRecord,
    PublishSummary,
    YouTubeVideoMetadata,
    FailureClassification,
)
from clipping.publishing.repository import PublishingRepository
from clipping.publishing.client import YouTubeClient, YouTubeClientError
from clipping.publishing.gates import PublishingGateEnforcer
from clipping.storage.base import StorageDriver
from clipping.logging.logger import get_logger

logger = get_logger("clipping.publishing.service")


class PublishingService:
    """
    Orchestrates the YouTube publishing pipeline:
    - Enforces Approval and QA gates against Google Drive state.
    - Guarantees idempotency and prevents duplicate video uploads.
    - Manages ephemeral worker scratch storage and channel identity verification.
    """

    def __init__(
        self,
        repository: PublishingRepository,
        client: YouTubeClient,
        gate_enforcer: PublishingGateEnforcer,
        storage_driver: StorageDriver,
    ):
        self.repository = repository
        self.client = client
        self.gates = gate_enforcer
        self.storage = storage_driver

    def generate_idempotency_key(self, job_id: str, clip_id: str, revision: int = 1) -> str:
        return f"{job_id}:{clip_id}:rev{revision}"

    async def publish_clip(
        self,
        job_id: str,
        clip_id: str,
        approval_request_id: str,
        video_storage_key: str,
        metadata: YouTubeVideoMetadata,
        expected_channel_id: Optional[str] = None,
        scheduled_publish_at: Optional[datetime] = None,
    ) -> PublishRequest:
        now = datetime.now(timezone.utc)
        idemp_key = self.generate_idempotency_key(job_id, clip_id)

        # 1. Check existing record for Idempotency
        existing = await self.repository.get_record_by_idempotency(idemp_key)
        if existing:
            if existing.status == PublishStatus.PUBLISHED:
                logger.info("Idempotent check: clip is already PUBLISHED", clip_id=clip_id, youtube_id=existing.youtube_video_id)
                return existing
            if existing.status == PublishStatus.PUBLISHING and existing.youtube_video_id:
                # Video already uploaded before interruption
                logger.info("Interrupted publish recovered with video ID", clip_id=clip_id, youtube_id=existing.youtube_video_id)
                return existing

        initial_request = PublishRequest(
            job_id=job_id,
            clip_id=clip_id,
            approval_request_id=approval_request_id,
            idempotency_key=idemp_key,
            video_storage_key=video_storage_key,
            metadata=metadata,
            status=PublishStatus.READY,
            scheduled_publish_at=scheduled_publish_at,
            created_at=now,
        )

        # 1.5 Master Control Gate Verification (Emergency Stop or Publishing Lock)
        ctrl_ok, ctrl_status, ctrl_reason = await self.gates.verify_control_gate()
        if not ctrl_ok:
            logger.warning("Publishing blocked by Master Control Gate", clip_id=clip_id, status=ctrl_status.value, reason=ctrl_reason)
            blocked_req = initial_request.model_copy(update={
                "status": ctrl_status,
                "failure_reason": ctrl_reason,
            })
            await self.repository.save_record(blocked_req)
            await self._record_audit(blocked_req, PublishStatus.READY, ctrl_status, ctrl_reason)
            return blocked_req

        # 2. Approval Gate Verification
        is_approved, target_status, app_reason = await self.gates.verify_approval_gate(job_id, approval_request_id)
        if not is_approved:
            logger.warning("Publishing blocked by Approval Gate", clip_id=clip_id, status=target_status.value, reason=app_reason)
            blocked_req = initial_request.model_copy(update={
                "status": target_status,
                "failure_reason": app_reason,
            })
            await self.repository.save_record(blocked_req)
            await self._record_audit(blocked_req, PublishStatus.READY, target_status, app_reason)
            return blocked_req

        # 3. QA Gate Verification
        qa_ok, qa_reason = await self.gates.verify_qa_gate(clip_id)
        if not qa_ok:
            logger.warning("Publishing blocked by QA Gate", clip_id=clip_id, reason=qa_reason)
            blocked_req = initial_request.model_copy(update={
                "status": PublishStatus.SKIPPED,
                "failure_reason": qa_reason,
            })
            await self.repository.save_record(blocked_req)
            await self._record_audit(blocked_req, PublishStatus.READY, PublishStatus.SKIPPED, qa_reason)
            return blocked_req

        # 4. Artifact Gate Verification
        art_ok, art_reason = await self.gates.verify_artifact_gate(video_storage_key)
        if not art_ok:
            logger.error("Publishing blocked by missing artifact", clip_id=clip_id, reason=art_reason)
            blocked_req = initial_request.model_copy(update={
                "status": PublishStatus.FAILED,
                "failure_reason": art_reason,
                "failure_type": FailureClassification.NON_RETRYABLE,
            })
            await self.repository.save_record(blocked_req)
            await self._record_audit(blocked_req, PublishStatus.READY, PublishStatus.FAILED, art_reason)
            return blocked_req

        # 5. Scheduling Check: If scheduled in the future, defer
        if scheduled_publish_at and scheduled_publish_at > now:
            logger.info("Clip scheduled for future release; deferring upload", clip_id=clip_id, scheduled_at=scheduled_publish_at.isoformat())
            deferred_req = initial_request.model_copy(update={"status": PublishStatus.DEFERRED})
            await self.repository.save_record(deferred_req)
            await self._record_audit(deferred_req, PublishStatus.READY, PublishStatus.DEFERRED, "Deferred for future schedule")
            return deferred_req

        # 6. Channel Identity Verification
        if expected_channel_id:
            channel_matches = await self.client.verify_channel(expected_channel_id)
            if not channel_matches:
                err_msg = f"Authenticated user does not match target channel: {expected_channel_id}"
                logger.error("Channel identity verification failed", clip_id=clip_id, expected=expected_channel_id)
                failed_req = initial_request.model_copy(update={
                    "status": PublishStatus.FAILED,
                    "failure_reason": err_msg,
                    "failure_type": FailureClassification.NON_RETRYABLE,
                })
                await self.repository.save_record(failed_req)
                await self._record_audit(failed_req, PublishStatus.READY, PublishStatus.FAILED, err_msg)
                return failed_req

        # 7. Execute Upload via Ephemeral Scratch Workspace
        publishing_req = initial_request.model_copy(update={
            "status": PublishStatus.PUBLISHING,
            "attempt_count": initial_request.attempt_count + 1,
        })
        await self.repository.save_record(publishing_req)
        await self._record_audit(publishing_req, PublishStatus.READY, PublishStatus.PUBLISHING, "Upload initiated")

        with WorkerScratchWorkspace(job_id=job_id) as workspace:
            local_video_path = workspace.get_path(f"staged_{clip_id}.mp4")
            try:
                # Download from canonical Google Drive to ephemeral scratch disk
                await self.storage.download(video_storage_key, local_video_path)

                # Stream upload to YouTube
                ref = await self.client.upload_video(local_video_path, metadata)

                # Record success
                published_req = publishing_req.model_copy(update={
                    "status": PublishStatus.PUBLISHED,
                    "youtube_video_id": ref.video_id,
                    "youtube_url": ref.watch_url,
                    "channel_id": ref.channel_id,
                    "published_at": ref.published_at,
                    "version": publishing_req.version + 1,
                })
                await self.repository.save_record(published_req)
                await self._record_audit(published_req, PublishStatus.PUBLISHING, PublishStatus.PUBLISHED, "Uploaded successfully")
                logger.info("Clip successfully published to YouTube", clip_id=clip_id, video_id=ref.video_id)
                return published_req

            except YouTubeClientError as e:
                logger.error(
                    "YouTube upload failed",
                    clip_id=clip_id,
                    error=str(e),
                    reason=e.reason,
                    failure_type=e.failure_type.value,
                )
                if e.reason in ("quotaExceeded", "uploadLimitExceeded"):
                    # Quota or channel daily limit hit: defer clip for next scheduled cycle rather than permanent failure
                    defer_until = now + timedelta(hours=4)
                    deferred_req = publishing_req.model_copy(update={
                        "status": PublishStatus.DEFERRED,
                        "failure_reason": f"Temporarily deferred: {str(e)}",
                        "failure_type": e.failure_type,
                        "scheduled_publish_at": defer_until,
                        "version": publishing_req.version + 1,
                    })
                    await self.repository.save_record(deferred_req)
                    await self._record_audit(
                        deferred_req,
                        PublishStatus.PUBLISHING,
                        PublishStatus.DEFERRED,
                        str(e),
                        e.failure_type,
                    )
                    return deferred_req
                else:
                    failed_req = publishing_req.model_copy(update={
                        "status": PublishStatus.FAILED,
                        "failure_reason": str(e),
                        "failure_type": e.failure_type,
                        "version": publishing_req.version + 1,
                    })
                    await self.repository.save_record(failed_req)
                    await self._record_audit(
                        failed_req,
                        PublishStatus.PUBLISHING,
                        PublishStatus.FAILED,
                        str(e),
                        e.failure_type,
                    )
                    return failed_req

            except Exception as e:
                logger.error("Unexpected error during publishing", clip_id=clip_id, error=str(e))
                failed_req = publishing_req.model_copy(update={
                    "status": PublishStatus.FAILED,
                    "failure_reason": f"Unexpected error: {str(e)}",
                    "failure_type": FailureClassification.RETRYABLE,
                    "version": publishing_req.version + 1,
                })
                await self.repository.save_record(failed_req)
                await self._record_audit(failed_req, PublishStatus.PUBLISHING, PublishStatus.FAILED, str(e), FailureClassification.RETRYABLE)
                return failed_req

    async def batch_publish_approved_clips(
        self,
        job_id: str,
        items: List[Dict[str, Any]],
        expected_channel_id: Optional[str] = None,
    ) -> PublishSummary:
        """
        Publishes a list of clips independently.
        Failure of one clip does not block or corrupt other clips.
        """
        total = len(items)
        published = 0
        skipped = 0
        deferred = 0
        failed = 0

        for item in items:
            result = await self.publish_clip(
                job_id=job_id,
                clip_id=item["clip_id"],
                approval_request_id=item["approval_request_id"],
                video_storage_key=item["video_storage_key"],
                metadata=item["metadata"],
                expected_channel_id=expected_channel_id,
                scheduled_publish_at=item.get("scheduled_publish_at"),
            )
            if result.status == PublishStatus.PUBLISHED:
                published += 1
            elif result.status == PublishStatus.SKIPPED:
                skipped += 1
            elif result.status == PublishStatus.DEFERRED:
                deferred += 1
            elif result.status == PublishStatus.FAILED:
                failed += 1

        return PublishSummary(
            job_id=job_id,
            total_clips=total,
            published_count=published,
            skipped_count=skipped,
            deferred_count=deferred,
            failed_count=failed,
            all_processed=(total == (published + skipped + deferred + failed)),
        )

    async def _record_audit(
        self,
        request: PublishRequest,
        prev_status: PublishStatus,
        new_status: PublishStatus,
        msg: str,
        failure_type: Optional[FailureClassification] = None,
    ) -> None:
        audit = PublishAuditRecord(
            audit_id=f"pub_aud_{uuid.uuid4().hex[:12]}",
            job_id=request.job_id,
            clip_id=request.clip_id,
            approval_request_id=request.approval_request_id,
            idempotency_key=request.idempotency_key,
            attempt_number=request.attempt_count,
            previous_status=prev_status,
            new_status=new_status,
            youtube_video_id=request.youtube_video_id,
            error_message=msg if new_status == PublishStatus.FAILED else None,
            failure_type=failure_type,
        )
        await self.repository.record_audit(audit)
