"""Cloud-friendly Telegram Update Polling & Callback Dispatcher."""

import argparse
import asyncio
import json
import sys
from typing import Optional
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
    ):
        self.service = approval_service
        self.transport = transport
        self.storage = storage_driver
        self.offset_key = offset_storage_key

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

    async def poll_and_process_once(self, limit: int = 100) -> int:
        """Polls up to limit updates, processes any callback queries, and checkpoints offset."""
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
                result = await self.service.handle_callback_query(cb)
                logger.info("Processed callback query", result=result)
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
