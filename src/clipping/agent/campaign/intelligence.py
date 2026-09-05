"""Lifecycle Anomaly, Duplicate & Term Change Intelligence Engine."""

import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from clipping.agent.campaign.models import (
    CampaignRecord,
    CampaignStatus,
    TermChangeRecord,
)
from clipping.logging.logger import get_logger

logger = get_logger("clipping.agent.campaign.intelligence")


class CampaignAnomalyType:
    DUPLICATE = "duplicate"
    TERM_CHANGE = "term_change"
    CONTRADICTION = "contradiction"
    EXPIRED = "expired"
    BUDGET_EXHAUSTED = "budget_exhausted"
    LOW_OPPORTUNITY = "low_opportunity"


class CampaignIntelligenceEngine:
    """
    Monitors, deduplicates, and analyzes campaign contracts for term drift,
    expired deadlines, conflicting requirements, and economic viability.
    """

    @staticmethod
    def detect_duplicate(
        candidate: CampaignRecord,
        existing_campaigns: List[CampaignRecord],
    ) -> Optional[CampaignRecord]:
        """
        Detects if candidate is an existing duplicate campaign based on:
        1. Exact campaign_id match
        2. Canonical URL match
        3. Normalized title and source token similarity
        """
        candidate_norm_title = CampaignIntelligenceEngine._normalize_text(candidate.name)
        candidate_src = candidate.source.strip().lower()

        for existing in existing_campaigns:
            if existing.campaign_id == candidate.campaign_id:
                return existing

            # Canonical URL match
            if candidate.canonical_url and existing.canonical_url:
                if candidate.canonical_url.strip().lower() == existing.canonical_url.strip().lower():
                    return existing

            # Normalized name and source matching
            existing_norm_title = CampaignIntelligenceEngine._normalize_text(existing.name)
            existing_src = existing.source.strip().lower()

            if candidate_norm_title == existing_norm_title and (candidate_src == existing_src or "whop" in candidate_src and "whop" in existing_src):
                return existing

        return None

    @staticmethod
    def detect_term_changes(
        existing: CampaignRecord,
        latest: CampaignRecord,
        now: Optional[datetime] = None,
    ) -> List[TermChangeRecord]:
        """
        Detects significant shifts in campaign economics, requirements, or duration.
        """
        timestamp = now or datetime.now(timezone.utc)
        changes: List[TermChangeRecord] = []

        # 1. CPM / Payout Changes
        old_cpm = existing.payout_terms.cpm_rate
        new_cpm = latest.payout_terms.cpm_rate
        if old_cpm is not None and new_cpm is not None and abs(old_cpm - new_cpm) > 0.01:
            diff = new_cpm - old_cpm
            impact = f"CPM increased by ${diff:.2f}" if diff > 0 else f"CPM decreased by ${abs(diff):.2f}"
            changes.append(
                TermChangeRecord(
                    field_name="payout_terms.cpm_rate",
                    old_value=old_cpm,
                    new_value=new_cpm,
                    changed_at=timestamp,
                    impact_summary=impact,
                )
            )

        # 2. Budget Changes
        old_budget = existing.payout_terms.remaining_budget
        new_budget = latest.payout_terms.remaining_budget
        if old_budget is not None and new_budget is not None and abs(old_budget - new_budget) > 10.0:
            changes.append(
                TermChangeRecord(
                    field_name="payout_terms.remaining_budget",
                    old_value=old_budget,
                    new_value=new_budget,
                    changed_at=timestamp,
                    impact_summary=f"Remaining budget updated from ${old_budget:,.2f} to ${new_budget:,.2f}",
                )
            )

        # 3. Deadline / Duration Changes
        old_deadline = existing.duration_terms.deadline or existing.duration_terms.end_date
        new_deadline = latest.duration_terms.deadline or latest.duration_terms.end_date
        if old_deadline != new_deadline and new_deadline is not None:
            changes.append(
                TermChangeRecord(
                    field_name="duration_terms.deadline",
                    old_value=old_deadline.isoformat() if old_deadline else None,
                    new_value=new_deadline.isoformat(),
                    changed_at=timestamp,
                    impact_summary=f"Campaign deadline shifted to {new_deadline.isoformat()}",
                )
            )

        # 4. Posting Requirement Changes
        old_hashtags = set(existing.posting_requirements.required_hashtags)
        new_hashtags = set(latest.posting_requirements.required_hashtags)
        if old_hashtags != new_hashtags:
            added = new_hashtags - old_hashtags
            removed = old_hashtags - new_hashtags
            changes.append(
                TermChangeRecord(
                    field_name="posting_requirements.required_hashtags",
                    old_value=list(old_hashtags),
                    new_value=list(new_hashtags),
                    changed_at=timestamp,
                    impact_summary=f"Hashtags updated: +{list(added)} -{list(removed)}",
                )
            )

        # 5. Content Rules Changes
        old_prohib = set(existing.prohibited_content_rules)
        new_prohib = set(latest.prohibited_content_rules)
        if old_prohib != new_prohib:
            new_rules = new_prohib - old_prohib
            changes.append(
                TermChangeRecord(
                    field_name="prohibited_content_rules",
                    old_value=list(old_prohib),
                    new_value=list(new_prohib),
                    changed_at=timestamp,
                    impact_summary=f"New restrictions added: {list(new_rules)}",
                )
            )

        return changes

    @staticmethod
    def audit_and_merge(
        existing: CampaignRecord,
        latest: CampaignRecord,
        now: Optional[datetime] = None,
    ) -> CampaignRecord:
        """
        Merges new crawl data into existing record while durably preserving
        historical term changes and auditing modifications.
        """
        changes = CampaignIntelligenceEngine.detect_term_changes(existing, latest, now)
        merged_history = list(existing.term_changes) + changes

        # Combine source URIs
        all_uris = list(dict.fromkeys(existing.discovered_source_uris + latest.discovered_source_uris))

        updated = existing.model_copy(
            update={
                "name": latest.name or existing.name,
                "description": latest.description or existing.description,
                "status": latest.status,
                "payout_terms": latest.payout_terms,
                "duration_terms": latest.duration_terms,
                "source_material": latest.source_material,
                "quotas": latest.quotas,
                "posting_requirements": latest.posting_requirements,
                "allowed_content_rules": latest.allowed_content_rules or existing.allowed_content_rules,
                "prohibited_content_rules": latest.prohibited_content_rules or existing.prohibited_content_rules,
                "discovered_source_uris": all_uris,
                "term_changes": merged_history,
                "opportunity_score": latest.opportunity_score or existing.opportunity_score,
                "opportunity_tier": latest.opportunity_tier or existing.opportunity_tier,
                "updated_at": now or datetime.now(timezone.utc),
            }
        )
        return updated

    @staticmethod
    def _normalize_text(text: str) -> str:
        return re.sub(r"[^a-zA-Z0-9]+", "", text).lower()
