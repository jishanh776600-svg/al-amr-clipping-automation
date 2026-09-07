"""Rich Telegram Review Package, Interactive Inline Approval, and Human Gate."""

import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from clipping.contracts.production import (
    ProductionArtifact,
    ProductionStatus,
    ReviewStatus,
    RevisionRecord,
)
from clipping.production.repository import ProductionRepository
from clipping.approval.transport import TelegramTransport
from clipping.logging.logger import get_logger

logger = get_logger("clipping.production.telegram_review")


class TelegramReviewSystem:
    """
    Manages operator Telegram review packages, media attachments, inline keyboards,
    explicit commands, and immutable human approval/revision state transitions.
    """

    def __init__(
        self,
        repository: ProductionRepository,
        transport: Optional[TelegramTransport] = None,
    ):
        self.repository = repository
        self.transport = transport

    def format_review_package(
        self,
        artifact: ProductionArtifact,
        total_clips: int = 1,
        campaign_name: str = "AL AMR Campaign",
    ) -> str:
        """
        Formats the structured Telegram review package with requirement checklist,
        metadata, titles, and exact operator instructions.
        """
        # Requirement checks checklist
        comp = artifact.compliance_result
        checklist_items = [
            f"✓ Duration: {artifact.duration:.1f}s",
            f"✓ Aspect ratio: {artifact.aspect_ratio}",
            f"✓ Resolution: {artifact.resolution}",
            f"✓ Watermark: {artifact.branding_status.upper()}",
            f"✓ Hashtags: {len(artifact.hashtags)} configured",
        ]
        if artifact.transcript:
            checklist_items.append("✓ Captions: Present")
        if comp and comp.is_compliant:
            checklist_items.append("✓ Strict Compliance Gate: PASSED (100%)")
        elif comp and not comp.is_compliant:
            checklist_items.append(f"⚠️ Strict Compliance Gate: BLOCKED ({len(comp.blockers)} issues)")

        req_check_str = "\n".join(checklist_items)
        tags_str = " ".join(artifact.hashtags) if artifact.hashtags else "(None)"

        msg = (
            f"CAMPAIGN: {campaign_name}\n"
            f"CLIP: {artifact.clip_number}/{total_clips}\n"
            f"STATUS: READY FOR REVIEW\n\n"
            f"REQUIREMENT CHECK:\n"
            f"{req_check_str}\n\n"
            f"TITLE:\n"
            f"{artifact.title}\n\n"
            f"DESCRIPTION:\n"
            f"{artifact.description}\n\n"
            f"INSTAGRAM CAPTION:\n"
            f"{artifact.caption}\n\n"
            f"HASHTAGS:\n"
            f"{tags_str}\n\n"
            f"ARTIFACT ID:\n"
            f"<code>{artifact.artifact_id}</code>\n\n"
            f"ACTION:\n"
            f"Click [✓ GOOD / APPROVE] to approve for publishing,\n"
            f"or [✕ BAD / REVISE] to request changes."
        )
        return msg

    def build_keyboard(self, artifact_id: str) -> Dict[str, Any]:
        """Constructs Telegram inline keyboard with compact callback actions (<= 64 bytes)."""
        clean_id = artifact_id[:40]
        approve_data = f"art:A:{clean_id}"
        reject_data = f"art:R:{clean_id}"
        return {
            "inline_keyboard": [
                [
                    {"text": "✓ GOOD / APPROVE", "callback_data": approve_data},
                    {"text": "✕ BAD / REVISE", "callback_data": reject_data},
                ]
            ]
        }

    async def dispatch_review_package(
        self,
        artifact: ProductionArtifact,
        chat_id: int,
        total_clips: int = 1,
        campaign_name: str = "AL AMR Campaign",
    ) -> Optional[int]:
        """
        Dispatches the complete review package to the operator Telegram chat.
        Attaches actual local video file when available.
        """
        if not self.transport:
            logger.info("Telegram transport not configured; skipping remote dispatch", artifact_id=artifact.artifact_id)
            return None

        # If artifact is blocked by compliance, do not send as ready
        if artifact.compliance_result and not artifact.compliance_result.is_compliant:
            blocker_str = "\n".join([f"• {b}" for b in artifact.compliance_result.blockers])
            blocked_msg = (
                f"🚨 PRODUCTION BLOCKED\n"
                f"Campaign: {campaign_name}\n"
                f"Clip: #{artifact.clip_number}\n"
                f"Artifact ID: <code>{artifact.artifact_id}</code>\n\n"
                f"Blockers:\n{blocker_str}\n\n"
                f"Status: Cannot proceed to review until compliance blockers are resolved."
            )
            return await self.transport.send_message(chat_id=chat_id, text=blocked_msg)

        text = self.format_review_package(artifact, total_clips=total_clips, campaign_name=campaign_name)
        keyboard = self.build_keyboard(artifact.artifact_id)

        # Send actual media if video exists locally and transport supports send_video
        msg_id = None
        if (
            artifact.local_output_path
            and os.path.isfile(artifact.local_output_path)
            and hasattr(self.transport, "send_video")
        ):
            try:
                msg_id = await self.transport.send_video(
                    chat_id=chat_id,
                    video_path=artifact.local_output_path,
                    caption=text[:1024],
                    reply_markup=keyboard,
                )
            except Exception as e:
                logger.warning("Failed to send video via send_video; falling back to message", error=str(e))

        if not msg_id:
            msg_id = await self.transport.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=keyboard,
            )

        logger.info(
            "Dispatched Telegram review package",
            artifact_id=artifact.artifact_id,
            chat_id=chat_id,
            message_id=msg_id,
        )
        return msg_id

    async def approve_artifact(
        self,
        artifact_id: str,
        operator_id: str,
    ) -> Optional[ProductionArtifact]:
        """
        Explicit operator human approval.
        Transitions review_status to APPROVED. Publishing is unlocked only after this call.
        """
        artifact = await self.repository.get_artifact(artifact_id)
        if not artifact:
            logger.warning("Cannot approve nonexistent artifact", artifact_id=artifact_id)
            return None

        # Verify not hard blocked
        if artifact.compliance_result and not artifact.compliance_result.is_compliant:
            logger.error("Cannot approve artifact with active compliance blockers", artifact_id=artifact_id)
            return None

        updated = await self.repository.update_review_status(
            artifact_id=artifact_id,
            status=ReviewStatus.APPROVED,
            operator_id=operator_id,
        )
        logger.info(
            "Human operator explicitly approved artifact for publishing",
            artifact_id=artifact_id,
            operator_id=operator_id,
        )
        return updated

    async def reject_artifact_for_revision(
        self,
        artifact_id: str,
        operator_id: str,
        feedback: str,
        requested_changes: Optional[List[str]] = None,
    ) -> Tuple[Optional[ProductionArtifact], Optional[RevisionRecord]]:
        """
        Operator rejection triggering the immutable revision loop.
        Never overwrites original artifact; records feedback and transitions to REVISION_REQUIRED.
        """
        artifact = await self.repository.get_artifact(artifact_id)
        if not artifact:
            logger.warning("Cannot reject nonexistent artifact", artifact_id=artifact_id)
            return None, None

        rev_num = artifact.revision_count + 1
        rev_id = f"rev_{uuid.uuid4().hex[:8]}"

        changes = requested_changes or [feedback]
        revision = RevisionRecord(
            revision_id=rev_id,
            original_artifact_id=artifact_id,
            revision_number=rev_num,
            operator_id=operator_id,
            operator_feedback=feedback,
            requested_changes=changes,
            created_at=datetime.now(timezone.utc),
        )
        await self.repository.save_revision(revision)

        updated_art = await self.repository.update_review_status(
            artifact_id=artifact_id,
            status=ReviewStatus.REVISION_REQUIRED,
            operator_id=operator_id,
            feedback=feedback,
        )
        logger.info(
            "Operator requested artifact revision",
            artifact_id=artifact_id,
            revision_id=rev_id,
            operator_id=operator_id,
            feedback=feedback,
        )
        return updated_art, revision
