#!/usr/bin/env python3
"""Whop Campaign Guidelines to Structured CampaignBrief Parser Runner (Step 4).

Extracts and parses guidelines for eligible campaigns into validated, structured
WhopCampaignBrief records with deterministic hashing and audit trail logging.
Strictly read-only against Whop (WHOP_DRY_RUN=true).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add project root to sys.path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from whop import (
    CampaignLedger,
    CampaignState,
    ParsingStatus,
    WhopCampaignBrief,
    parse_campaign_guidelines,
    validate_campaign_brief,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("whop_parse_briefs")


def run_brief_generation(
    output_dir: Path,
    ledger_path: Optional[Path] = None,
    discovery_json_path: Optional[Path] = None,
    target_campaign_id: Optional[str] = None,
) -> int:
    """Processes eligible campaigns and generates structured CampaignBrief records."""
    output_dir.mkdir(parents=True, exist_ok=True)
    ledger = CampaignLedger(db_path=ledger_path)

    campaigns_to_process: List[Dict[str, Any]] = []

    # 1. Source campaigns: either from discovery JSON or directly from ledger
    if discovery_json_path and Path(discovery_json_path).exists():
        with open(discovery_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for c in data.get("campaigns", []):
            # Ensure campaign exists in ledger
            ledger.ingest_discovered_campaign(c, source="discovery_json")
            if c.get("eligible") and (not target_campaign_id or c.get("campaign_id") == target_campaign_id):
                campaigns_to_process.append(c)
    else:
        # Pull eligible campaigns from ledger
        eligible_records = ledger.list_campaigns(state=CampaignState.ELIGIBLE)
        for r in eligible_records:
            if not target_campaign_id or r.campaign_id == target_campaign_id:
                campaigns_to_process.append({
                    "campaign_id": r.campaign_id,
                    "title": r.title,
                    "campaign_url": r.campaign_url,
                    "payout_raw": r.payout_raw,
                    "cpm": r.cpm,
                    "platforms": r.platforms,
                    "source_urls": r.source_urls,
                    "guideline_urls": r.guideline_urls,
                    "raw_text": "",
                })

    if not campaigns_to_process:
        log.warning("No eligible campaigns found to parse.")
        return 0

    log.info("Processing %d eligible campaign(s) for CampaignBrief extraction...", len(campaigns_to_process))

    summary_rows: List[Dict[str, Any]] = []
    generated_briefs: List[WhopCampaignBrief] = []

    for c in campaigns_to_process:
        cid = c["campaign_id"]
        title = c["title"]
        log.info("Parsing guidelines for campaign: '%s' (%s)", title, cid)

        brief = parse_campaign_guidelines(
            campaign_id=cid,
            title=title,
            campaign_url=c.get("campaign_url", ""),
            raw_text=c.get("raw_text", ""),
            source_urls=c.get("source_urls", []),
            guideline_urls=c.get("guideline_urls", []),
            payout_raw=c.get("payout_raw", ""),
            cpm=c.get("cpm"),
            supported_platforms=c.get("platforms", []),
            download_external=True,
        )

        # Validate brief
        val_errors = validate_campaign_brief(brief)
        val_status = "VALID" if not val_errors else f"WARNINGS: {'; '.join(val_errors)}"

        # Save to persistent ledger
        saved_brief, is_new = ledger.save_campaign_brief(brief, source="whop_parse_briefs")

        # Save individual campaign brief JSON
        brief_file = output_dir / f"brief_{cid[:12]}.json"
        with open(brief_file, "w", encoding="utf-8") as f:
            json.dump(brief.to_dict(), f, indent=2, ensure_ascii=False)

        mandatory_count = sum(1 for r in brief.rules if r.mandatory)
        prohibited_count = sum(1 for r in brief.rules if r.prohibited)
        unresolved_count = sum(1 for r in brief.rules if r.status == "INTERPRETATION_REQUIRED")

        summary_rows.append({
            "campaign_id": cid,
            "title": title,
            "guideline_source": brief.guideline_source_type,
            "guideline_ref": brief.guideline_source_reference,
            "guideline_hash": brief.guideline_hash[:16] + "..." if brief.guideline_hash else "NONE",
            "parsing_status": brief.parsing_status.value,
            "rules_count": len(brief.rules),
            "mandatory": mandatory_count,
            "prohibited": prohibited_count,
            "unresolved": unresolved_count,
            "validation": val_status,
            "brief_file": str(brief_file),
            "is_new": is_new,
        })
        generated_briefs.append(brief)

    # Save summary report JSON
    summary_json_path = output_dir / "campaign_briefs_summary.json"
    with open(summary_json_path, "w", encoding="utf-8") as f:
        json.dump(summary_rows, f, indent=2, ensure_ascii=False)

    # Save summary Markdown
    summary_md_path = output_dir / "campaign_briefs_summary.md"
    with open(summary_md_path, "w", encoding="utf-8") as f:
        f.write("# Whop CampaignBrief Parsing Summary (Step 4)\n\n")
        f.write(f"Processed at: {datetime.now(timezone.utc).isoformat()}\n\n")
        f.write("| Campaign Title | ID | Status | Source Type | Hash | Rules | Mandatory | Prohibited | Unresolved | Validation |\n")
        f.write("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n")
        for row in summary_rows:
            f.write(
                f"| {row['title']} | `{row['campaign_id'][:8]}` | **{row['parsing_status']}** | {row['guideline_source']} | `{row['guideline_hash']}` | {row['rules_count']} | {row['mandatory']} | {row['prohibited']} | {row['unresolved']} | {row['validation']} |\n"
            )

    # Print console report
    print("\n================ WHOP CAMPAIGN BRIEFS PARSED ================")
    for r in summary_rows:
        print(f"[{r['parsing_status']}] {r['title']} ({r['campaign_id']})")
        print(f"  Source:      {r['guideline_source']} -> {r['guideline_ref']}")
        print(f"  Hash:        {r['guideline_hash']}")
        print(f"  Rules Total: {r['rules_count']} (Mandatory: {r['mandatory']}, Prohibited: {r['prohibited']}, Unresolved: {r['unresolved']})")
        print(f"  Validation:  {r['validation']}")
        print(f"  Brief JSON:  {r['brief_file']}")
        print(f"  Ledger Save: {'NEW' if r['is_new'] else 'REUSED (Idempotent)'}\n")
    print(f"Full Summary JSON: {summary_json_path}")
    print(f"Full Summary MD:   {summary_md_path}")
    print("=============================================================\n")

    return 0


def main():
    parser = argparse.ArgumentParser(description="Parse Whop Campaign Guidelines into CampaignBriefs (Step 4)")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/whop_briefs"),
        help="Directory to save brief artifacts",
    )
    parser.add_argument(
        "--ledger-path",
        type=Path,
        default=None,
        help="Path to SQLite ledger database",
    )
    parser.add_argument(
        "--discovery-json",
        type=Path,
        default=None,
        help="Path to discovery JSON (e.g. artifacts/whop_discovery/whop_campaign_discovery.json)",
    )
    parser.add_argument(
        "--campaign-id",
        type=str,
        default=None,
        help="Target single campaign ID to process",
    )
    args = parser.parse_args()

    exit_code = run_brief_generation(
        output_dir=args.output_dir,
        ledger_path=args.ledger_path,
        discovery_json_path=args.discovery_json,
        target_campaign_id=args.campaign_id,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
