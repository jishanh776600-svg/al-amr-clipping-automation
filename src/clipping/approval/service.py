"""Core Business Logic for Clip Approvals and Telegram Callbacks."""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from clipping.approval.models import (
    ApprovalRequest,
    ApprovalStatus,
    ApprovalAction,
    ApprovalAuditRecord,
    TelegramCallbackPayload,
)
from clipping.approval.repository import ApprovalRepository
from clipping.approval.transport import TelegramTransport
from clipping.approval.security import SecurityValidator, SecurityError
from clipping.logging.logger import get_logger

logger = get_logger("clipping.approval.service")


def format_time_hms(seconds: float) -> str:
    """Formats seconds into HH:MM:SS format."""
    total_sec = int(seconds)
    hours = total_sec // 3600
    minutes = (total_sec % 3600) // 60
    secs = total_sec % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


class ApprovalService:
    """Coordinates clip approval lifecycle, Telegram message dispatch, and secure callback resolution."""

    def __init__(
        self,
        repository: ApprovalRepository,
        transport: TelegramTransport,
        security_validator: SecurityValidator,
    ):
        self.repository = repository
        self.transport = transport
        self.security = security_validator

    def format_approval_card(self, request: ApprovalRequest) -> str:
        """Formats an HTML card for Telegram displaying clip metrics and hook info."""
        start_str = format_time_hms(request.start_time)
        end_str = format_time_hms(request.end_time)
        hook_text = f"<i>\"{request.hook_sentence}\"</i>\n" if request.hook_sentence else ""

        card = (
            f"🎬 <b>Clip #{request.clip_index}: {request.title}</b>\n"
            f"{hook_text}\n"
            f"⏱ <b>Duration:</b> {request.duration:.1f}s\n"
            f"📈 <b>Score:</b> {request.score:.1f}/100\n"
            f"🕒 <b>Timeline:</b> {start_str} → {end_str}\n"
            f"🛡 <b>QA Check:</b> {request.qa_status}\n"
            f"📦 <b>Job:</b> <code>{request.job_id}</code>\n"
            f"🔑 <b>Clip ID:</b> <code>{request.clip_id}</code>"
        )
        return card

    def build_keyboard(self, request: ApprovalRequest) -> Dict[str, Any]:
        """Constructs an inline keyboard with compact callback actions."""
        approve_cb = TelegramCallbackPayload(
            action=ApprovalAction.APPROVE,
            approval_request_id=request.approval_request_id,
        ).serialize()

        reject_cb = TelegramCallbackPayload(
            action=ApprovalAction.REJECT,
            approval_request_id=request.approval_request_id,
        ).serialize()

        return {
            "inline_keyboard": [
                [
                    {"text": "✅ APPROVE", "callback_data": approve_cb},
                    {"text": "❌ REJECT", "callback_data": reject_cb},
                ]
            ]
        }

    async def create_and_send_request(
        self,
        request: ApprovalRequest,
        chat_id: int,
    ) -> ApprovalRequest:
        """
        Idempotently persists an approval request and sends its Telegram card.
        If the request was already sent, returns existing request without re-spamming Telegram.
        """
        existing = await self.repository.get_request(request.job_id, request.approval_request_id)
        if existing and existing.telegram_message_id is not None:
            logger.info("Approval request already dispatched to Telegram", approval_request_id=request.approval_request_id)
            return existing

        # 1. Save initial record
        await self.repository.save_request(request)

        # 2. Dispatch card to Telegram
        text = self.format_approval_card(request)
        keyboard = self.build_keyboard(request)
        msg_id = await self.transport.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)

        # 3. Update record with telegram identifiers
        updated_request = ApprovalRequest(
            approval_request_id=request.approval_request_id,
            job_id=request.job_id,
            source_video_id=request.source_video_id,
            clip_id=request.clip_id,
            clip_index=request.clip_index,
            title=request.title,
            hook_sentence=request.hook_sentence,
            start_time=request.start_time,
            end_time=request.end_time,
            duration=request.duration,
            score=request.score,
            qa_status=request.qa_status,
            video_storage_key=request.video_storage_key,
            status=request.status,
            telegram_message_id=msg_id,
            telegram_chat_id=chat_id,
            created_at=request.created_at,
            version=request.version,
        )

        await self.repository.save_request(updated_request)
        logger.info(
            "Dispatched approval card to Telegram",
            approval_request_id=request.approval_request_id,
            message_id=msg_id,
            chat_id=chat_id,
        )
        return updated_request

    async def handle_callback_query(self, callback_query: Dict[str, Any]) -> Dict[str, Any]:
        """
        Securely processes an incoming Telegram inline button tap.
        Handles authorization, replay protection, state transitions, and audit trails.
        """
        query_id = callback_query.get("id", "")
        from_user = callback_query.get("from", {})
        user_id = from_user.get("id")
        raw_data = callback_query.get("data", "")
        message = callback_query.get("message", {})
        message_id = message.get("message_id")
        chat_id = message.get("chat", {}).get("id")

        logger.info("Received callback query", query_id=query_id, user_id=user_id, chat_id=chat_id)

        # 1. User & Chat Authorization Guard
        if not user_id or not self.security.is_user_authorized(user_id):
            logger.warning("Unauthorized user attempted clip approval", user_id=user_id)
            await self.transport.answer_callback_query(
                callback_query_id=query_id,
                text="⛔ Unauthorized user.",
                show_alert=True,
            )
            return {"status": "unauthorized"}

        if chat_id and not self.security.is_chat_authorized(chat_id):
            logger.warning("Callback from unauthorized chat", chat_id=chat_id)
            await self.transport.answer_callback_query(
                callback_query_id=query_id,
                text="⛔ Unauthorized chat.",
                show_alert=True,
            )
            return {"status": "unauthorized"}

        # 2. Parse & Validate Payload
        try:
            payload = self.security.validate_callback_payload(raw_data)
        except SecurityError as e:
            logger.warning("Malformed callback payload", error=str(e), raw_data=raw_data)
            await self.transport.answer_callback_query(
                callback_query_id=query_id,
                text="⚠️ Invalid callback format.",
                show_alert=False,
            )
            return {"status": "malformed_payload"}

        # 3. Retrieve Approval Request
        req = await self.repository.get_request_by_id(payload.approval_request_id)
        if not req:
            logger.warning("Approval request not found", approval_request_id=payload.approval_request_id)
            await self.transport.answer_callback_query(
                callback_query_id=query_id,
                text="⚠️ Request not found or expired.",
                show_alert=True,
            )
            return {"status": "not_found"}

        # 4. Replay & Idempotency Check
        if req.status == ApprovalStatus.APPROVED:
            logger.info("Idempotent callback: request already APPROVED", approval_request_id=req.approval_request_id)
            await self.transport.answer_callback_query(
                callback_query_id=query_id,
                text="ℹ️ Clip is already APPROVED.",
                show_alert=False,
            )
            return {"status": "already_approved", "request_id": req.approval_request_id}

        if req.status == ApprovalStatus.REJECTED:
            logger.info("Idempotent callback: request already REJECTED", approval_request_id=req.approval_request_id)
            await self.transport.answer_callback_query(
                callback_query_id=query_id,
                text="ℹ️ Clip is already REJECTED.",
                show_alert=False,
            )
            return {"status": "already_rejected", "request_id": req.approval_request_id}

        # 5. Execute State Transition
        new_status = (
            ApprovalStatus.APPROVED if payload.action == ApprovalAction.APPROVE else ApprovalStatus.REJECTED
        )
        now = datetime.now(timezone.utc)

        updated_request = ApprovalRequest(
            approval_request_id=req.approval_request_id,
            job_id=req.job_id,
            source_video_id=req.source_video_id,
            clip_id=req.clip_id,
            clip_index=req.clip_index,
            title=req.title,
            hook_sentence=req.hook_sentence,
            start_time=req.start_time,
            end_time=req.end_time,
            duration=req.duration,
            score=req.score,
            qa_status=req.qa_status,
            video_storage_key=req.video_storage_key,
            status=new_status,
            telegram_message_id=req.telegram_message_id,
            telegram_chat_id=req.telegram_chat_id,
            created_at=req.created_at,
            decided_at=now,
            decided_by=user_id,
            decision_source="telegram",
            version=req.version + 1,
        )

        # Save updated state
        await self.repository.save_request(updated_request)

        # 6. Record Immutable Audit Trail
        audit = ApprovalAuditRecord(
            audit_id=f"aud_{uuid.uuid4().hex[:12]}",
            approval_request_id=req.approval_request_id,
            job_id=req.job_id,
            clip_id=req.clip_id,
            previous_status=req.status,
            new_status=new_status,
            telegram_user_id=user_id,
            telegram_chat_id=chat_id or 0,
            callback_query_id=query_id,
            timestamp=now,
        )
        await self.repository.record_audit(audit)

        # 7. Update Telegram Card UI & Acknowledge
        decision_badge = "✅ <b>APPROVED</b>" if new_status == ApprovalStatus.APPROVED else "❌ <b>REJECTED</b>"
        user_mention = from_user.get("first_name", f"User {user_id}")
        card_text = self.format_approval_card(updated_request)
        updated_card_text = f"{decision_badge} (by {user_mention})\n\n{card_text}"

        if chat_id and message_id:
            try:
                # Remove buttons upon decision
                await self.transport.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=updated_card_text,
                    reply_markup={"inline_keyboard": []},
                )
            except Exception as e:
                logger.warning("Failed to edit Telegram message text after decision", error=str(e))

        toast_msg = "✅ Clip approved!" if new_status == ApprovalStatus.APPROVED else "❌ Clip rejected."
        await self.transport.answer_callback_query(
            callback_query_id=query_id,
            text=toast_msg,
            show_alert=False,
        )

        return {
            "status": "success",
            "decision": new_status.value,
            "approval_request_id": req.approval_request_id,
        }
