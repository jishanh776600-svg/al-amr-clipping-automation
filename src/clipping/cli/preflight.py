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
        print(" ACTIVATION READINESS CHECKLIST (12 CORE VECTORS):")
        print(f"   [+] 1.  CODE READY:                 {'READY' if m.code_ready else 'NOT READY'}")
        print(f"   [{'+' if m.environment_ready else '!'}] 2.  ENVIRONMENT READY:          {'READY' if m.environment_ready else 'NOT READY (FFmpeg missing)'}")
        print(f"   [{'+' if m.credential_ready else '!'}] 3.  CREDENTIAL READY:           {'READY' if m.credential_ready else 'WARNING (ENCRYPTION_MASTER_KEY not set)'}")
        print(f"   [{'+' if m.account_ready else '!'}] 4.  ACCOUNT READY:              {'READY' if m.account_ready else 'NOT READY (No creator accounts in vault)'}")
        print(f"   [{'+' if m.campaign_source_ready else '!'}] 5.  CAMPAIGN SOURCE READY:     {'READY' if m.campaign_source_ready else 'NOT READY (No active campaign source)'}")
        print(f"   [{'+' if m.media_pipeline_ready else '!'}] 6.  MEDIA PIPELINE READY:      {'READY' if m.media_pipeline_ready else 'NOT READY'}")
        print(f"   [{'+' if m.storage_ready else '!'}] 7.  STORAGE READY:             {'READY' if m.storage_ready else 'NOT READY'}")
        print(f"   [{'+' if m.worker_ready else '!'}] 8.  WORKER READY:              {'READY' if m.worker_ready else 'NOT READY'}")
        print(f"   [{'+' if m.publishing_ready else '!'}] 9.  PUBLISHING READY:          {'READY' if m.publishing_ready else 'WARNING (Platform credentials unconfigured)'}")
        print(f"   [{'+' if m.escalation_ready else '!'}] 10. ESCALATION READY:          {'READY' if m.escalation_ready else 'WARNING (Telegram unconfigured)'}")
        print(f"   [{'+' if m.real_integration_verified else '!'}] 11. REAL INTEGRATION VERIFIED: {'VERIFIED' if m.real_integration_verified else 'NOT VERIFIED (Awaiting credentials)'}")
        print(f"   [{'+' if m.live_operation_allowed else '!'}] 12. LIVE OPERATION ALLOWED:    {'APPROVED' if m.live_operation_allowed else 'PROHIBITED (Fail-closed gate active)'}")
        print("-" * 88)

        print(" SUPPORTED EXECUTION MODES:")
        print(f"   MODE A [PREFLIGHT]:         {'[OK] ENABLED' if m.can_run_preflight else '[X] DISABLED'}")
        print(f"   MODE B [DRY RUN]:           {'[OK] ENABLED (Safe Mode - no external uploads)' if m.can_run_dry_run else '[X] BLOCKED (Requires Storage + Media Pipeline + Worker)'}")
        print(f"   MODE C [SINGLE LIVE]:       {'[OK] ENABLED' if m.can_run_single_live else '[X] BLOCKED (Requires 12/12 Live Approval Gate)'}")
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

        # Real Media Smoke Test execution if requested
        if args.smoke_test:
            print("-" * 88)
            print(" EXECUTING REAL MEDIA PIPELINE SMOKE TEST (Zero Mocks)...")
            from clipping.preflight.media_smoke import RealMediaEnvironmentSmokeTest
            smoke_tester = RealMediaEnvironmentSmokeTest()
            smoke_res = await smoke_tester.execute()
            if smoke_res.success:
                print(f" [PASS] REAL MEDIA SMOKE TEST SUCCEEDED in {smoke_res.duration_seconds}s")
                print(f"        Output: {smoke_res.output_resolution} MP4 ({smoke_res.output_file_size_bytes} bytes)")
                print(f"        QA Passed: {smoke_res.qa_passed} | Idempotent Reuse: {smoke_res.idempotent_reuse_verified}")
            else:
                print(f" [FAIL] REAL MEDIA SMOKE TEST FAILED: {smoke_res.error}")
                print(f"        Failed QA checks: {smoke_res.qa_failed_checks}")

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
    parser.add_argument("--live-probe", action="store_true", help="Execute real read-only external service probes")
    parser.add_argument("--smoke-test", action="store_true", help="Execute real media rendering and QA smoke test")
    args = parser.parse_args()

    exit_code = asyncio.run(run_preflight(args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
