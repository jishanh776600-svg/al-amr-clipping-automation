"""Cloud-friendly Telegram Update Polling & Callback Dispatcher."""

import argparse
import asyncio
import json
import sys
from typing import Any, Dict, List, Optional
from clipping.approval.service import ApprovalService
from clipping.approval.transport import TelegramTransport, HttpTelegramTransport
from clipping.approval.repository import ApprovalRepository
from clipping.approval.security import SecurityValidator
from clipping.storage.base import StorageDriver
from clipping.storage.local import LocalStorageDriver
from clipping.storage.google_drive import GoogleDriveStorageDriver
from clipping.config.settings import Settings
from clipping.logging.logger import get_logger

logger = get_logger("clipping.approval.dispatcher")


class TelegramApprovalDispatcher:
    """
    Consumes pending Telegram updates via getUpdates in batch mode,
    processes callbacks through ApprovalService, and checkpoints the update offset.
    Ideal for serverless / GitHub Actions cloud runners ($0 compute).
    """

    def __init__(
        self,
        approval_service: ApprovalService,
        transport: TelegramTransport,
        storage_driver: StorageDriver,
        offset_storage_key: str = "telegram/update_offset.json",
        activation_manager: Optional[Any] = None,
    ):
        self.service = approval_service
        self.transport = transport
        self.storage = storage_driver
        self.offset_key = offset_storage_key
        if activation_manager is not None:
            self.activation_manager = activation_manager
        else:
            from clipping.agent.activation.manager import ActivationSessionManager
            self.activation_manager = ActivationSessionManager(storage_driver=self.storage)

    async def get_current_offset(self) -> Optional[int]:
        if not await self.storage.exists(self.offset_key):
            return None
        try:
            data = await self.storage.download_bytes(self.offset_key)
            payload = json.loads(data.decode("utf-8"))
            return payload.get("offset")
        except Exception:
            return None

    async def save_offset(self, offset: int) -> None:
        payload = json.dumps({"offset": offset}).encode("utf-8")
        await self.storage.upload_bytes(payload, self.offset_key, content_type="application/json")

    async def _handle_otp_message(
        self,
        user_id: Optional[int],
        chat_id: Optional[int],
        text: str,
    ) -> bool:
        """Processes incoming Telegram text messages for activation OTP verification."""
        if not self.activation_manager:
            return False

        clean_text = text.strip()
        parts = clean_text.split()
        if not parts:
            return False

        target_session_id = None
        otp_code = None

        if parts[0].lower() == "/otp":
            if len(parts) >= 3:
                target_session_id = parts[1]
                otp_code = parts[2]
            elif len(parts) == 2:
                otp_code = parts[1]
        elif clean_text.isdigit() and 4 <= len(clean_text) <= 10:
            otp_code = clean_text

        if not otp_code:
            return False

        # If session ID not explicitly passed, find the active waiting session
        if not target_session_id:
            waiting = await self.activation_manager.find_waiting_session()
            if waiting:
                target_session_id = waiting.session_id

        if not target_session_id:
            if chat_id:
                await self.transport.send_message(
                    chat_id=chat_id,
                    text="⚠️ No active activation challenge awaiting OTP. Specify session: `/otp <session_id> <code>`",
                )
            return False

        try:
            session = await self.activation_manager.submit_otp(
                session_id=target_session_id,
                otp_code=otp_code,
                sender_user_id=user_id,
                sender_chat_id=chat_id,
            )
            if chat_id:
                await self.transport.send_message(
                    chat_id=chat_id,
                    text=f"✅ Verification code accepted for `{session.service}` (Session `{session.session_id}`). Authentication proceeding.",
                )
            return True
        except Exception as e:
            logger.warning("Operator OTP submission rejected", error=str(e), session_id=target_session_id)
            if chat_id:
                await self.transport.send_message(
                    chat_id=chat_id,
                    text=f"❌ Verification code rejected: {str(e)}",
                )
            return False

    async def _handle_production_callback(self, cb: Dict[str, Any]) -> bool:
        data = cb.get("data", "")
        if data.startswith("art:"):
            parts = data.split(":")
            if len(parts) >= 3:
                action = parts[1]
                artifact_id = parts[2]
                from clipping.production.repository import ProductionRepository
                from clipping.production.telegram_review import TelegramReviewSystem
                repo = ProductionRepository(storage_driver=self.storage)
                review_sys = TelegramReviewSystem(repository=repo, transport=self.transport)
                op_id = str(cb.get("from", {}).get("id", "telegram_operator"))
                chat_id = cb.get("message", {}).get("chat", {}).get("id")

                if action == "A":
                    res = await review_sys.approve_artifact(artifact_id, operator_id=op_id)
                    if cb.get("id"):
                        await self.transport.answer_callback_query(cb["id"], text="✅ Clip APPROVED for publishing.")
                    if chat_id:
                        if res:
                            await self.transport.send_message(chat_id, text=f"✅ Clip `{artifact_id}` has been APPROVED. Ready for publishing.")
                        else:
                            await self.transport.send_message(chat_id, text=f"⚠️ Cannot approve `{artifact_id}`: Blocker exists or artifact not found.")
                    return True
                elif action == "R":
                    await review_sys.reject_artifact_for_revision(artifact_id, operator_id=op_id, feedback="Operator requested revision via Telegram")
                    if cb.get("id"):
                        await self.transport.answer_callback_query(cb["id"], text="❌ Clip marked for revision.")
                    if chat_id:
                        await self.transport.send_message(chat_id, text=f"📝 Clip `{artifact_id}` marked for REVISION. To add specific feedback, send: `/reject {artifact_id} <details>`")
                    return True
        elif data.startswith("res:"):
            parts = data.split(":")
            if len(parts) >= 2:
                inter_id = parts[1]
                from clipping.production.repository import ProductionRepository
                from clipping.production.challenge_escalation import ChallengeEscalationManager
                repo = ProductionRepository(storage_driver=self.storage)
                escalator = ChallengeEscalationManager(repository=repo, transport=self.transport)
                chat_id = cb.get("message", {}).get("chat", {}).get("id")
                await escalator.resume(inter_id, chat_id=chat_id)
                if cb.get("id"):
                    await self.transport.answer_callback_query(cb["id"], text="▶️ Checkpoint resumed.")
                return True
        return False

    async def _handle_production_command(self, user_id: Optional[int], chat_id: Optional[int], text: str) -> bool:
        clean = text.strip()
        if clean.lower().startswith("/approve"):
            parts = clean.split(maxsplit=1)
            if len(parts) >= 2:
                art_id = parts[1].strip()
                from clipping.production.repository import ProductionRepository
                from clipping.production.telegram_review import TelegramReviewSystem
                repo = ProductionRepository(storage_driver=self.storage)
                review_sys = TelegramReviewSystem(repository=repo, transport=self.transport)
                res = await review_sys.approve_artifact(art_id, operator_id=str(user_id or "telegram_operator"))
                if chat_id:
                    if res:
                        await self.transport.send_message(chat_id, text=f"✅ Clip `{art_id}` APPROVED. Status is now READY FOR PUBLISHING.")
                    else:
                        await self.transport.send_message(chat_id, text=f"❌ Could not approve `{art_id}`. Check ID or compliance blockers.")
                return True
        elif clean.lower().startswith("/reject"):
            parts = clean.split(maxsplit=2)
            if len(parts) >= 2:
                art_id = parts[1].strip()
                fb = parts[2].strip() if len(parts) > 2 else "Changes requested by operator"
                from clipping.production.repository import ProductionRepository
                from clipping.production.telegram_review import TelegramReviewSystem
                repo = ProductionRepository(storage_driver=self.storage)
                review_sys = TelegramReviewSystem(repository=repo, transport=self.transport)
                await review_sys.reject_artifact_for_revision(art_id, operator_id=str(user_id or "telegram_operator"), feedback=fb)
                if chat_id:
                    await self.transport.send_message(chat_id, text=f"📝 Clip `{art_id}` marked REVISION REQUIRED with feedback: '{fb}'.")
                return True
        elif clean.lower().startswith("/resume"):
            parts = clean.split(maxsplit=1)
            if len(parts) >= 2:
                inter_id = parts[1].strip()
                from clipping.production.repository import ProductionRepository
                from clipping.production.challenge_escalation import ChallengeEscalationManager
                repo = ProductionRepository(storage_driver=self.storage)
                escalator = ChallengeEscalationManager(repository=repo, transport=self.transport)
                await escalator.resume(inter_id, chat_id=chat_id)
                return True
        return False

    async def poll_and_process_once(self, limit: int = 100) -> int:
        """Polls up to limit updates, processes any callback queries or operator OTP messages, and checkpoints offset."""
        current_offset = await self.get_current_offset()
        updates = await self.transport.get_updates(offset=current_offset, limit=limit, timeout=5)
        if not updates:
            logger.info("No new Telegram updates to process")
            return 0

        logger.info(f"Processing {len(updates)} Telegram updates", current_offset=current_offset)
        highest_update_id = 0
        processed_callbacks = 0

        for update in updates:
            update_id = update.get("update_id", 0)
            if update_id > highest_update_id:
                highest_update_id = update_id

            if "callback_query" in update:
                cb = update["callback_query"]
                cb_data = cb.get("data", "")
                if cb_data.startswith("art:") or cb_data.startswith("res:"):
                    handled = await self._handle_production_callback(cb)
                    if handled:
                        processed_callbacks += 1
                else:
                    result = await self.service.handle_callback_query(cb)
                    logger.info("Processed callback query", result=result)
                    processed_callbacks += 1
            elif "message" in update:
                msg = update["message"]
                text = msg.get("text", "")
                user_id = msg.get("from", {}).get("id")
                chat_id = msg.get("chat", {}).get("id")
                if text:
                    prod_handled = await self._handle_production_command(user_id=user_id, chat_id=chat_id, text=text)
                    if prod_handled:
                        processed_callbacks += 1
                    else:
                        handled = await self._handle_otp_message(user_id=user_id, chat_id=chat_id, text=text)
                        if handled:
                            processed_callbacks += 1

        if highest_update_id > 0:
            # Checkpoint next offset (highest_update_id + 1)
            await self.save_offset(highest_update_id + 1)

        return processed_callbacks



async def run_dispatcher_cli(limit: int = 50) -> int:
    settings = Settings()
    if not settings.TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not configured")
        return 1

    token = settings.TELEGRAM_BOT_TOKEN.get_secret_value()
    allowed_users = settings.get_allowed_telegram_user_ids()
    allowed_chats = settings.get_allowed_telegram_chat_ids()

    # Storage driver resolution
    from clipping.storage.factory import create_storage_driver
    storage = create_storage_driver(settings)

    transport = HttpTelegramTransport(bot_token=token)
    repo = ApprovalRepository(storage_driver=storage)
    security = SecurityValidator(allowed_user_ids=allowed_users, allowed_chat_ids=allowed_chats)
    service = ApprovalService(repository=repo, transport=transport, security_validator=security)

    dispatcher = TelegramApprovalDispatcher(
        approval_service=service,
        transport=transport,
        storage_driver=storage,
    )

    count = await dispatcher.poll_and_process_once(limit=limit)
    logger.info(f"Dispatcher completed: processed {count} decisions")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Telegram Approval Callback Dispatcher")
    parser.add_argument("--limit", type=int, default=50, help="Max updates to poll")
    args = parser.parse_args()
    exit_code = asyncio.run(run_dispatcher_cli(limit=args.limit))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
