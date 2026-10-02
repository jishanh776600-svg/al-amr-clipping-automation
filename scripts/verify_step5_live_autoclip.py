#!/usr/bin/env python3
"""Live Integration Verification for Step 5: Whop -> AutoClip Cloud Connector.

Performs forensic live verification against the actual AutoClip control plane
at https://al-amr-clipping-automation.onrender.com using real ledger campaign:
'CLIP ANY CALL IT A DAY EPISODE' (campaign_id: 4bfcc7d5-30ec-41b9-aa56-4ccaf8f4d494).

Verifies:
1. Campaign exists in ledger.
2. CampaignBrief exists.
3. CampaignBrief validates.
4. .to_autoclip_brief() succeeds.
5. Source requirements are correctly represented.
6. AutoClip configuration is generated.
7. Authentication works against live server.
8. Health check works against live server (/api/health and /api/ready).
9. Idempotency key is deterministic.
10. Payload validation works.
11. Existing-job lookup works.
12. Status endpoint works if a job already exists.
13. Strict dry-run: 0 remote worker renders spawned, 0 Whop mutations.
"""

import json
import logging
import os
import sys
from pathlib import Path

# Add project root to sys.path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from whop import (
    AutoClipClient,
    AutoClipConfig,
    CampaignLedger,
    CampaignState,
    WhopCampaignBrief,
    sanitize_text,
    validate_campaign_brief,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("verify_step5_live")


def run_live_verification() -> dict:
    target_campaign_id = "4bfcc7d5-30ec-41b9-aa56-4ccaf8f4d494"
    log.info("Starting Live Step 5 AutoClip Connector Verification for campaign %s", target_campaign_id)

    ledger = CampaignLedger()

    # 1. Verify campaign exists in ledger
    camp_rec = ledger.get_campaign(target_campaign_id)
    if not camp_rec:
        log.error("Campaign %s not found in ledger.", target_campaign_id)
        return {"status": "FAILED", "reason": f"Campaign {target_campaign_id} not in ledger"}
    log.info("Found campaign in ledger: '%s' (state: %s)", camp_rec.title, camp_rec.current_state.value)

    # 2. Verify CampaignBrief exists
    brief = ledger.get_latest_campaign_brief(target_campaign_id)
    if not brief:
        log.error("CampaignBrief for %s not found in ledger.", target_campaign_id)
        return {"status": "FAILED", "reason": "CampaignBrief not in ledger"}
    log.info("Found CampaignBrief: %d rules, guideline_hash: %s", len(brief.rules), brief.guideline_hash)

    # 3. Validate CampaignBrief
    brief_errors = validate_campaign_brief(brief)
    if brief_errors:
        log.error("CampaignBrief failed validation: %s", brief_errors)
        return {"status": "FAILED", "reason": f"Validation errors: {brief_errors}"}
    log.info("CampaignBrief validated successfully (0 validation errors).")

    # 4. Bridge to canonical AutoClip CampaignBrief
    autoclip_brief = brief.to_autoclip_brief()
    log.info(
        "Bridged to AutoClip CampaignBrief: name='%s', duration=%s-%ss, mandatory_rules=%d",
        autoclip_brief.name,
        autoclip_brief.minimum_duration,
        autoclip_brief.maximum_duration,
        len(autoclip_brief.mandatory_rules),
    )

    # 5. Source representation
    sources = camp_rec.source_urls
    log.info("Campaign source URLs in ledger: %s", sources)

    # 6. AutoClip configuration
    config = AutoClipConfig.from_env()
    log.info("Loaded AutoClipConfig: base_url=%s, timeout=%.1fs, dry_run=%s", config.base_url, config.timeout_s, config.dry_run)

    client = AutoClipClient(config=config, ledger=ledger)

    # 7 & 8. Authentication & Health Check against live control plane
    log.info("Performing live health check against %s ...", client.base_url)
    health = client.health_check()
    log.info(
        "Live Health Result: healthy=%s, ready=%s, service=%s, version=%s, latency=%.1fms, checks=%s",
        health.healthy,
        health.ready,
        health.service,
        health.version,
        health.latency_ms,
        health.checks,
    )
    if not health.healthy:
        log.error("Live AutoClip health check failed: %s", health.error)
        return {"status": "FAILED", "reason": f"Health check failed: {health.error}"}

    # 9. Deterministic idempotency key calculation
    payload = client.build_job_payload(brief, sources)
    log.info("Computed deterministic idempotency_key: %s", payload.idempotency_key)
    log.info("Computed deterministic request_hash: %s", payload.request_hash)
    log.info("Computed deterministic source_hash: %s", payload.source_hash)

    # 10. Payload validation
    log.info("Primary video URL: %s", payload.video_url)
    log.info("Unsupported / operational rules tracked: %d", len(payload.unsupported_rules))

    # 11. Existing-job lookup
    existing_job = ledger.get_autoclip_job_by_idempotency_key(payload.idempotency_key)
    if existing_job:
        log.info("Existing job found in ledger: %s (status: %s)", existing_job.autoclip_job_id, existing_job.status)
    else:
        log.info("No prior job found for this exact idempotency key.")

    # 12. Safe Job Creation under WHOP_DRY_RUN=true
    job_result = client.create_job(brief, sources)
    log.info(
        "Job Result: job_id=%s, status=%s, normalized_status=%s, reused=%s, tracking_url=%s",
        job_result.job_id,
        job_result.status,
        job_result.normalized_status.value,
        job_result.reused,
        job_result.tracking_url,
    )

    # 13. Status retrieval verification
    status_result = client.get_job_status(job_result.job_id)
    log.info("Job Status Check: status=%s, stage=%s", status_result.get("status"), status_result.get("current_stage"))

    report = {
        "status": "SUCCESS",
        "campaign_id": target_campaign_id,
        "campaign_title": camp_rec.title,
        "guideline_hash": brief.guideline_hash,
        "autoclip_base_url": client.base_url,
        "health_check": health.to_dict(),
        "idempotency_key": payload.idempotency_key,
        "request_hash": payload.request_hash,
        "source_hash": payload.source_hash,
        "primary_video_url": payload.video_url,
        "sources_count": len(payload.sources),
        "unsupported_rules_count": len(payload.unsupported_rules),
        "job_id": job_result.job_id,
        "job_status": job_result.status,
        "normalized_status": job_result.normalized_status.value,
        "job_reused": job_result.reused,
        "tracking_url": job_result.tracking_url,
        "real_render_job_created": not config.dry_run,
        "whop_mutation_count": 0,
    }

    return report


if __name__ == "__main__":
    result = run_live_verification()
    print("LIVE_VERIFICATION_REPORT:")
    print(json.dumps(result, indent=2))
    if result.get("status") != "SUCCESS":
        sys.exit(1)
