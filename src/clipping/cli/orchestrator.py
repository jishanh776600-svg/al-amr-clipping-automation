"""Production Orchestrator CLI Entrypoint for AL AMR CLIPPING.

Provides durable invocation, preflight verification, single-cycle execution,
dry-run safety protection, and continuous autonomous loop scheduling with
graceful signal handling.
"""

import argparse
import asyncio
import os
import signal
import sys
from typing import Optional

from clipping.agent.orchestration.engine import AutonomousOrchestrationEngine
from clipping.agent.repository import AgentTaskRepository
from clipping.agent.vault.vault import EncryptedCredentialVault
from clipping.approval.escalation_notifier import TelegramEscalationNotifier
from clipping.control.repository import ControlRepository
from clipping.logging.logger import get_logger
from clipping.preflight.validator import OverallPreflightStatus, SystemPreflightValidator
from clipping.storage.factory import StorageFactory

logger = get_logger("clipping.cli.orchestrator")


async def run_orchestrator(args: argparse.Namespace) -> int:
    """Executes preflight and coordinates single-cycle or continuous autonomous loop."""
    storage_driver = StorageFactory.create()
    control_repo = ControlRepository(storage_driver)

    # 1. Preflight Validation
    if args.preflight or not args.skip_preflight:
        logger.info("Executing system preflight verification before engine activation...")
        validator = SystemPreflightValidator(storage_driver=storage_driver, control_repository=control_repo)
        report = await validator.validate()

        if not report.ready:
            logger.error("Preflight check failed; aborting orchestrator startup", summary=report.summary)
            if args.preflight:
                print(report.model_dump_json(indent=2))
            return 1
        elif report.status == OverallPreflightStatus.READY_WITH_WARNINGS:
            logger.warning("Preflight passed with non-fatal warnings", summary=report.summary)
        else:
            logger.info("Preflight verification passed cleanly")

        if args.preflight:
            print(report.model_dump_json(indent=2))
            return 0

    # 2. Dry-run safety enforcement
    if args.dry_run:
        logger.warning("SAFE MODE (DRY-RUN) ACTIVE: Publishing lock enforced; no external uploads will be executed")
        # Ensure durable state has publishing locked if dry run requested
        state = await control_repo.get_state()
        if not state.publishing_locked:
            state.publishing_locked = True
            await control_repo.save_state(state)

    # 3. Initialize Autonomous Orchestration Engine
    escalation_notifier = TelegramEscalationNotifier()
    task_repo = AgentTaskRepository(storage_driver=storage_driver, escalation_notifier=escalation_notifier)
    vault = EncryptedCredentialVault(storage_driver=storage_driver)

    engine = AutonomousOrchestrationEngine(
        storage_driver=storage_driver,
        control_repository=control_repo,
        campaign_repository=None,  # Defaults to repo on storage_driver
        task_repository=task_repo,
        credential_vault=vault,
    )

    shutdown_event = asyncio.Event()

    def handle_signal():
        logger.info("Termination signal received; requesting graceful orchestrator shutdown...")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_signal)
        except (NotImplementedError, RuntimeError):
            # Windows signal handler fallback
            pass

    # 4. Execution Mode: Single Cycle
    if args.once or not args.continuous:
        logger.info("Running single autonomous orchestration cycle", source=args.source)
        try:
            summary = await engine.run_orchestration_cycle(
                source_name=args.source,
                max_campaigns_to_process=args.max_campaigns,
                target_campaign_id=args.target_campaign,
            )
            logger.info(
                "Autonomous cycle completed",
                cycle_id=summary.cycle_id,
                discovered=summary.campaigns_discovered,
                selected=summary.campaigns_selected,
                produced=summary.clips_produced,
                submitted=summary.submissions_completed,
                escalations=len(summary.escalations_raised),
            )
            return 0
        except Exception as e:
            logger.error("Autonomous orchestration cycle failed", error=str(e))
            return 1

    # 5. Execution Mode: Continuous Loop
    logger.info(
        "Starting continuous autonomous orchestration loop",
        interval_seconds=args.interval,
        source=args.source,
        max_campaigns=args.max_campaigns,
    )

    cycle_count = 0
    while not shutdown_event.is_set():
        cycle_count += 1
        logger.info("Starting scheduled orchestration cycle", cycle_number=cycle_count)
        try:
            summary = await engine.run_orchestration_cycle(
                source_name=args.source,
                max_campaigns_to_process=args.max_campaigns,
                target_campaign_id=args.target_campaign,
            )
            logger.info(
                "Completed scheduled orchestration cycle",
                cycle_number=cycle_count,
                cycle_id=summary.cycle_id,
                discovered=summary.campaigns_discovered,
                selected=summary.campaigns_selected,
                produced=summary.clips_produced,
            )
        except Exception as e:
            logger.error("Error during scheduled orchestration cycle", cycle_number=cycle_count, error=str(e))

        if shutdown_event.is_set():
            break

        logger.info("Orchestrator sleeping until next cycle", sleep_seconds=args.interval)
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=float(args.interval))
        except asyncio.TimeoutError:
            pass

    logger.info("Continuous autonomous orchestration loop exited cleanly")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="AL AMR CLIPPING Autonomous Orchestration Engine")
    parser.add_argument("--preflight", action="store_true", help="Run preflight validation and exit")
    parser.add_argument("--skip-preflight", action="store_true", help="Skip preflight checks (not recommended)")
    parser.add_argument("--once", action="store_true", help="Execute a single cycle and exit (default)")
    parser.add_argument("--continuous", action="store_true", help="Run continuous loop with interval sleep")
    parser.add_argument("--interval", type=int, default=300, help="Interval in seconds between cycles in continuous mode (default: 300)")
    parser.add_argument("--dry-run", action="store_true", help="Enforce publishing lock (safe mode - no uploads)")
    parser.add_argument("--target-campaign", type=str, default=None, help="Target a specific campaign ID")
    parser.add_argument("--source", type=str, default="whop", help="Campaign discovery source (default: whop)")
    parser.add_argument("--max-campaigns", type=int, default=5, help="Max campaigns to evaluate per cycle (default: 5)")
    args = parser.parse_args()

    try:
        exit_code = asyncio.run(run_orchestrator(args))
        sys.exit(exit_code)
    except KeyboardInterrupt:
        logger.info("Interrupted by user. Exiting cleanly.")
        sys.exit(0)


if __name__ == "__main__":
    main()
