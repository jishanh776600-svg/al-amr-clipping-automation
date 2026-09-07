"""Autonomous Worker Daemon for Headless Production Execution on Render.

Continuous worker service that:
1. Polls the Cloud Task Queue and executes autonomous clipping jobs.
2. Polls Telegram for operator review decisions (APPROVE / REVISE) and OTP challenge responses.
3. Ensures crash resilience, checkpoint persistence, and graceful termination.
"""

import argparse
import asyncio
import os
import signal
import sys
from typing import Optional

from clipping.agent.capabilities.clipping_adapter import MediaClippingCapability
from clipping.agent.capabilities.registry import CapabilityRegistry
from clipping.agent.cloud.worker import CloudAgentWorker
from clipping.agent.models import AgentTask
from clipping.agent.state import TaskState
from clipping.approval.dispatcher import TelegramApprovalDispatcher
from clipping.approval.repository import ApprovalRepository
from clipping.approval.security import SecurityValidator
from clipping.approval.service import ApprovalService
from clipping.approval.transport import HttpTelegramTransport, MockTelegramTransport
from clipping.config.settings import Settings, get_settings
from clipping.logging.logger import get_logger
from clipping.storage.base import StorageDriver
from clipping.storage.factory import create_storage_driver

logger = get_logger("clipping.cli.worker_daemon")


class WorkerDaemon:
    """Production 24/7 background worker for Render deployment."""

    def __init__(self, poll_interval: float = 3.0, storage_driver: Optional[StorageDriver] = None):
        self.poll_interval = poll_interval
        self._running = False
        self.settings = get_settings()
        self.storage = storage_driver or create_storage_driver(self.settings)

        # 1. Initialize Cloud Agent Worker
        worker_id = os.getenv("RENDER_INSTANCE_ID", os.getenv("WORKER_ID", f"worker_{os.getpid()}"))
        cap_registry = CapabilityRegistry()
        cap_registry.register(MediaClippingCapability())
        self.worker = CloudAgentWorker(
            worker_id=worker_id,
            capabilities=cap_registry,
            storage_driver=self.storage,
        )

        # 2. Initialize Telegram Approval Dispatcher
        token = self.settings.TELEGRAM_BOT_TOKEN.get_secret_value() if self.settings.TELEGRAM_BOT_TOKEN else ""
        if token:
            transport = HttpTelegramTransport(bot_token=token)
            repo = ApprovalRepository(storage_driver=self.storage)
            security = SecurityValidator(
                allowed_user_ids=self.settings.get_allowed_telegram_user_ids(),
                allowed_chat_ids=self.settings.get_allowed_telegram_chat_ids(),
            )
            service = ApprovalService(repository=repo, transport=transport, security_validator=security)
            self.dispatcher: Optional[TelegramApprovalDispatcher] = TelegramApprovalDispatcher(
                approval_service=service,
                transport=transport,
                storage_driver=self.storage,
            )
            logger.info("Telegram approval dispatcher initialized for background worker")
        else:
            self.dispatcher = None
            logger.info("TELEGRAM_BOT_TOKEN not configured; Telegram polling disabled in worker")

    def stop(self) -> None:
        """Signals worker to terminate gracefully after current iteration."""
        logger.info("WorkerDaemon received shutdown signal")
        self._running = False

    async def step_once(self) -> Optional[AgentTask]:
        """Executes a single polling iteration: processes next operator task or remains idle."""
        task = await self.worker.run_next_task()
        if task:
            logger.info(
                "WorkerDaemon processed task",
                task_id=task.task_id,
                status=task.status.value,
            )
        else:
            logger.debug("WorkerDaemon idle; waiting for operator-created production tasks")

        if self.dispatcher:
            try:
                decisions = await self.dispatcher.poll_and_process_once(limit=25)
                if decisions > 0:
                    logger.info("WorkerDaemon processed Telegram updates", count=decisions)
            except Exception as te:
                logger.warning("Telegram poll error in background worker", error=str(te))

        return task

    async def run(self) -> None:
        """Main continuous execution loop."""
        self._running = True
        logger.info(
            "WorkerDaemon started (operator-driven production worker)",
            poll_interval=self.poll_interval,
            storage=self.storage.__class__.__name__,
        )

        while self._running:
            try:
                await self.step_once()
                # Sleep before next poll
                await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("WorkerDaemon iteration error", error=str(e))
                await asyncio.sleep(self.poll_interval * 2)

        logger.info("WorkerDaemon stopped cleanly")


async def main_async(poll_interval: float) -> int:
    daemon = WorkerDaemon(poll_interval=poll_interval)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, daemon.stop)
        except NotImplementedError:
            # Windows does not support add_signal_handler
            pass

    await daemon.run()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Autonomous Worker Daemon for Render")
    parser.add_argument("--poll-interval", type=float, default=3.0, help="Poll interval in seconds")
    args = parser.parse_args()

    try:
        sys.exit(asyncio.run(main_async(args.poll_interval)))
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
