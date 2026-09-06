"""Production Orchestrator CLI Entrypoint for AL AMR CLIPPING.

Provides durable invocation, preflight verification, single-cycle execution,
dry-run safety protection, and continuous autonomous loop scheduling with
graceful signal handling.

Supports the 4 Canonical Activation Modes:
- MODE A: PREFLIGHT       (--mode preflight / --preflight)
- MODE B: DRY RUN         (--mode dry-run / --dry-run)
- MODE C: SINGLE LIVE     (--mode single-live / --once)
- MODE D: CONTINUOUS      (--mode continuous / --continuous)
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
    """Executes preflight and coordinates activation modes."""
    storage_driver = StorageFactory.create()
    control_repo = ControlRepository(storage_driver)

    # 1. Resolve Execution Mode
    mode = args.mode
    if args.preflight:
        mode = "preflight"
    elif args.dry_run:
        mode = "dry-run"
    elif args.continuous:
        mode = "continuous"
    elif args.once:
        mode = "single-live"
    elif not mode:
        mode = "single-live"

    logger.info("Initializing AL AMR CLIPPING Orchestrator", active_mode=mode)

    # 2. Preflight Validation
    if mode == "preflight" or not args.skip_preflight:
        logger.info("Executing system preflight verification before engine activation...")
        validator = SystemPreflightValidator(storage_driver=storage_driver, control_repository=control_repo)
        report = await validator.validate()

        if mode == "preflight":
            if args.json:
                print(report.model_dump_json(indent=2))
            else:
                from clipping.cli.preflight import run_preflight
                await run_preflight(args)
            return 0 if report.ready else 1

        if not report.ready:
            logger.error(
                "Mandatory preflight check failed; aborting orchestrator startup",
                summary=report.summary,
            )
            print(f"\n[!] CANNOT ACTIVATE ORCHESTRATOR: {report.summary}")
            print("    Run 'python -m clipping.cli.preflight' to view missing prerequisites.\n")
            return 1
        elif report.status == OverallPreflightStatus.READY_WITH_WARNINGS:
            logger.warning("Preflight passed with non-fatal warnings", summary=report.summary)
        else:
            logger.info("Preflight verification passed cleanly")

    # 3. Dry-Run Safety Enforcement
    is_dry_run = (mode == "dry-run")
    if is_dry_run:
        logger.warning(
            "MODE B (DRY-RUN) ACTIVE: Live publishing strictly prohibited; "
            "all clip productions will stage in storage without external platform deployment."
        )

    # 4. Initialize Autonomous Orchestration Engine
    escalation_notifier = TelegramEscalationNotifier()
    task_repo = AgentTaskRepository(storage_driver=storage_driver, escalation_notifier=escalation_notifier)
    vault = EncryptedCredentialVault(storage_driver=storage_driver)

    engine = AutonomousOrchestrationEngine(
        storage_driver=storage_driver,
        control_repository=control_repo,
        campaign_repository=None,
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
            pass

    # 5. Execution Mode: Single Cycle (Dry-Run or Single Live)
    if mode in ("dry-run", "single-live"):
        logger.info(
            f"Executing {'Mode B (Dry-Run)' if is_dry_run else 'Mode C (Single Live Campaign)'}",
            source=args.source,
            target_campaign=args.target_campaign,
        )
        try:
            summary = await engine.run_orchestration_cycle(
                source_name=args.source,
                max_campaigns_to_process=args.max_campaigns,
                target_campaign_id=args.target_campaign,
                dry_run=is_dry_run,
            )
            logger.info(
                "Autonomous cycle completed",
                cycle_id=summary.cycle_id,
                mode=mode,
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

    # 6. Execution Mode: Mode D — Continuous Autonomous Loop
    logger.info(
        "Starting MODE D (Continuous Autonomous Loop)",
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
                dry_run=is_dry_run,
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
    parser.add_argument(
        "--mode",
        choices=["preflight", "dry-run", "single-live", "continuous"],
        default=None,
        help="Activation mode: preflight (Mode A), dry-run (Mode B), single-live (Mode C), or continuous (Mode D)",
    )
    parser.add_argument("--preflight", action="store_true", help="Mode A: Run preflight validation and exit")
    parser.add_argument("--dry-run", action="store_true", help="Mode B: Safe discovery/production without live uploads")
    parser.add_argument("--once", action="store_true", help="Mode C: Execute single live campaign and exit")
    parser.add_argument("--continuous", action="store_true", help="Mode D: Run continuous autonomous loop")
    parser.add_argument("--skip-preflight", action="store_true", help="Skip preflight checks (not recommended)")
    parser.add_argument("--interval", type=int, default=300, help="Interval in seconds between cycles in continuous mode (default: 300)")
    parser.add_argument("--target-campaign", type=str, default=None, help="Target a specific campaign ID")
    parser.add_argument("--source", type=str, default="whop", help="Campaign discovery source (default: whop)")
    parser.add_argument("--max-campaigns", type=int, default=5, help="Max campaigns to evaluate per cycle (default: 5)")
    parser.add_argument("--json", action="store_true", help="Output report in raw JSON format (preflight mode)")
    parser.add_argument("--strict", action="store_true", help="Fail with non-zero exit code on any warning (preflight mode)")
    args = parser.parse_args()

    try:
        exit_code = asyncio.run(run_orchestrator(args))
        sys.exit(exit_code)
    except KeyboardInterrupt:
        logger.info("Interrupted by user. Exiting cleanly.")
        sys.exit(0)


if __name__ == "__main__":
    main()
