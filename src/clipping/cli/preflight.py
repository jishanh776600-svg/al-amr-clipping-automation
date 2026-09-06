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
        print("=" * 80)
        print(" AL AMR CLIPPING  SYSTEM PREFLIGHT VERIFICATION")
        print("=" * 80)
        print(f"Timestamp: {report.timestamp}")
        print(f"Overall Status: {report.status.value}")
        print("-" * 80)

        for check in report.checks:
            tag = f"[{check.status.value}]"
            req = "MANDATORY" if check.is_mandatory else "OPTIONAL "
            icon = "?" if check.status == PreflightStatus.PASS else ("?" if check.status == PreflightStatus.WARN else "?")
            print(f" {icon} {tag:<7} | {req} | {check.name:<30} | {check.message}")

        print("=" * 80)
        print(f"Summary: {report.summary}")
        print("=" * 80)

    if args.strict:
        # Strict mode requires ZERO warnings and ZERO failures
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
