"""CLI Entrypoint for AL AMR CLIPPING System Preflight Verification."""

import argparse
import asyncio
import sys

from clipping.preflight.validator import (
    OverallPreflightStatus,
    PreflightStatus,
    SystemPreflightValidator,
)


async def run_preflight(args: argparse.Namespace) -> int:
    validator = SystemPreflightValidator()
    report = await validator.validate()

    if args.json:
        print(report.model_dump_json(indent=2))
    else:
        print("=" * 88)
        print("                 AL AMR CLIPPING  SYSTEM PREFLIGHT & ACTIVATION REPORT")
        print("=" * 88)
        print(f" Timestamp: {report.timestamp}")
        print(f" Overall Status: {report.status.value}")
        print(f" Can Operate Live Now: {'YES (Ready for Autonomous Operation)' if report.can_operate_now else 'NO (Prerequisites Pending)'}")
        print("-" * 88)

        m = report.activation_matrix
        print(" ACTIVATION READINESS CHECKLIST:")
        print(f"   [+] CODE READY:              {'READY' if m.code_ready else 'NOT READY'}")
        print(f"   [{'+' if m.environment_ready else '!'}] ENVIRONMENT READY:       {'READY' if m.environment_ready else 'NOT READY (FFmpeg/FFprobe missing in PATH)'}")
        print(f"   [{'+' if m.credential_ready else '!'}] CREDENTIAL READY:        {'READY' if m.credential_ready else 'WARNING (ENCRYPTION_MASTER_KEY not set in env)'}")
        print(f"   [{'+' if m.account_ready else '!'}] ACCOUNT READY:           {'READY' if m.account_ready else 'NOT READY (No creator accounts registered in vault)'}")
        print(f"   [{'+' if m.campaign_source_ready else '!'}] CAMPAIGN SOURCE READY:  {'READY' if m.campaign_source_ready else 'WARNING (WHOP_API_KEY missing, using cache)'}")
        print(f"   [{'+' if m.media_pipeline_ready else '!'}] MEDIA PIPELINE READY:   {'READY' if m.media_pipeline_ready else 'NOT READY (FFmpeg/FFprobe required for video)'}")
        print(f"   [{'+' if m.storage_ready else '!'}] STORAGE READY:          {'READY' if m.storage_ready else 'NOT READY (Storage probe failed)'}")
        print(f"   [{'+' if m.worker_ready else '!'}] WORKER READY:           {'READY' if m.worker_ready else 'NOT READY (Queue / lease engine probe failed)'}")
        print(f"   [{'+' if m.publishing_ready else '!'}] PUBLISHING READY:       {'READY' if m.publishing_ready else 'WARNING (YouTube/Instagram live credentials missing)'}")
        print(f"   [{'+' if m.escalation_ready else '!'}] ESCALATION READY:       {'READY' if m.escalation_ready else 'WARNING (Telegram credentials missing)'}")
        print("-" * 88)

        print(" SUPPORTED EXECUTION MODES:")
        print(f"   MODE A [PREFLIGHT]:         {'[OK] ENABLED' if m.can_run_preflight else '[X] DISABLED'}")
        print(f"   MODE B [DRY RUN]:           {'[OK] ENABLED (Safe Mode - no external uploads)' if m.can_run_dry_run else '[X] BLOCKED (Requires Storage + Media Pipeline + Worker)'}")
        print(f"   MODE C [SINGLE LIVE]:       {'[OK] ENABLED' if m.can_run_single_live else '[X] BLOCKED (Requires Publishing Credentials + Creator Account)'}")
        print(f"   MODE D [CONTINUOUS]:        {'[OK] ENABLED' if m.can_run_continuous else '[X] BLOCKED (Requires Live Mode + Telegram Escalation)'}")
        print("-" * 88)

        print(" DETAILED SUBSYSTEM CHECKS:")
        for check in report.checks:
            tag = f"[{check.status.value}]"
            req = "MANDATORY" if check.is_mandatory else "OPTIONAL "
            icon = "[PASS]" if check.status == PreflightStatus.PASS else ("[WARN]" if check.status == PreflightStatus.WARN else "[FAIL]")
            print(f" {icon:<6} | {req} | {check.name:<32} | {check.message}")
            if check.status != PreflightStatus.PASS:
                print(f"         Why Required: {check.why_required}")
                print(f"         Fix:          {check.configuration_requirement}")
                print(f"         Impact:       Blocks Dry-Run: {check.blocks_dry_run} | Blocks Live Publishing: {check.blocks_live_publishing}")

        if report.actionable_recommendations:
            print("-" * 88)
            print(" ACTIONABLE ACTIVATION STEPS TO REACH FULL OPERATIONAL STATUS:")
            for idx, rec in enumerate(report.actionable_recommendations, 1):
                print(f"   {idx}. {rec}")

        print("=" * 88)
        print(f" SUMMARY: {report.summary}")
        print("=" * 88)

    if args.strict:
        return 0 if report.status == OverallPreflightStatus.READY else 1

    return 0 if report.ready else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="AL AMR CLIPPING System Preflight Validator")
    parser.add_argument("--json", action="store_true", help="Output report in raw JSON format")
    parser.add_argument("--strict", action="store_true", help="Fail with non-zero exit code on any warning")
    args = parser.parse_args()

    exit_code = asyncio.run(run_preflight(args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
