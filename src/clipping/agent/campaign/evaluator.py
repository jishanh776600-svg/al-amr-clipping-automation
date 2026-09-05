"""Intelligent Campaign Evaluation, Economics & Ranking Engine."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, ConfigDict, Field

from clipping.agent.campaign.models import (
    CampaignPlatform,
    CampaignRecord,
    CampaignStatus,
    PayoutModel,
)
from clipping.agent.vault.models import AccountMetadata, AccountPlatform, AccountStatus
from clipping.logging.logger import get_logger

logger = get_logger("clipping.agent.campaign.evaluator")


class OpportunityTier(str, Enum):
    STRONG_PURSUE = "strong_pursue"  # 80-100: Exceptional economics and zero friction
    PURSUE = "pursue"                # 60-79: Solid target CPM ($1-$5) and eligible
    NEUTRAL = "neutral"              # 40-59: Moderate return or higher effort
    AVOID = "avoid"                  # 20-39: Sub-optimal economics or heavy friction
    REJECT = "reject"                # 0-19: Ineligible, expired, or budget exhausted


class CampaignOpportunityScore(BaseModel):
    """Holistic multi-factor opportunity evaluation score."""
    model_config = ConfigDict(frozen=True)

    overall_score: float = Field(..., ge=0.0, le=100.0)
    tier: OpportunityTier
    cpm_score: float = Field(..., ge=0.0, le=100.0)
    economics_score: float = Field(..., ge=0.0, le=100.0)
    production_effort_score: float = Field(..., ge=0.0, le=100.0)
    content_availability_score: float = Field(..., ge=0.0, le=100.0)
    account_eligibility_score: float = Field(..., ge=0.0, le=100.0)
    duration_score: float = Field(..., ge=0.0, le=100.0)
    estimated_earning_potential: float = Field(default=0.0, ge=0.0, description="Estimated total earnings in USD")
    recommendation_notes: List[str] = Field(default_factory=list)

    def is_worth_pursuing(self) -> bool:
        """Determines if the opportunity meets minimum viability thresholds."""
        return self.overall_score >= 40.0 and self.tier not in (OpportunityTier.AVOID, OpportunityTier.REJECT)


class CampaignEvaluator:
    """
    Evaluates campaign brief economics, production viability, and eligibility.
    Targets $1-$5 CPM, with $2 CPM preferred, while holistically valuing
    expected views, production effort, competition, and earning potential.
    """

    DEFAULT_BASELINE_VIEWS_PER_CLIP = 25000  # Conservative estimate for optimized 9:16 clips

    def __init__(self, preferred_cpm: float = 2.0, min_viable_cpm: float = 1.0, max_target_cpm: float = 5.0):
        self.preferred_cpm = preferred_cpm
        self.min_viable_cpm = min_viable_cpm
        self.max_target_cpm = max_target_cpm

    def evaluate(
        self,
        campaign: CampaignRecord,
        vault_accounts: Optional[List[AccountMetadata]] = None,
        now: Optional[datetime] = None,
    ) -> CampaignOpportunityScore:
        """Computes comprehensive multi-factor opportunity score for a campaign."""
        current_time = now or datetime.now(timezone.utc)
        notes: List[str] = []

        # 1. Hard Disqualifications
        if campaign.status in (CampaignStatus.EXPIRED, CampaignStatus.COMPLETED, CampaignStatus.UNAVAILABLE):
            return self._build_terminal_score(0.0, OpportunityTier.REJECT, f"Campaign status is {campaign.status.value}")

        if campaign.duration_terms.check_expired_at(current_time):
            return self._build_terminal_score(0.0, OpportunityTier.REJECT, "Campaign has reached deadline or expired")

        if campaign.payout_terms.budget_exhausted or (campaign.payout_terms.remaining_budget is not None and campaign.payout_terms.remaining_budget <= 0.0):
            return self._build_terminal_score(5.0, OpportunityTier.REJECT, "Campaign budget is completely exhausted")

        # Check for contradictory rules
        contradiction = campaign.validate_rules()
        if contradiction:
            return self._build_terminal_score(10.0, OpportunityTier.REJECT, f"Contradictory campaign terms: {contradiction}")

        # 2. Factor 1: CPM & Rate Economics (Weight: 30%)
        cpm_score, est_earning = self._score_cpm_and_earnings(campaign, notes)

        # 3. Factor 2: Budget & Pool Health (Weight: 15%)
        econ_score = self._score_budget_health(campaign, notes)

        # 4. Factor 3: Content & Source Video Availability (Weight: 20%)
        content_score = self._score_content_availability(campaign, notes)

        # 5. Factor 4: Production Effort & Constraints (Weight: 15%)
        effort_score = self._score_production_effort(campaign, notes)

        # 6. Factor 5: Account & Platform Eligibility (Weight: 10%)
        eligibility_score = self._score_account_eligibility(campaign, vault_accounts, notes)

        # 7. Factor 6: Duration & Competition (Weight: 10%)
        duration_score = self._score_duration_and_competition(campaign, current_time, notes)

        # Compute Weighted Composite Score
        overall = (
            cpm_score * 0.30
            + econ_score * 0.15
            + content_score * 0.20
            + effort_score * 0.15
            + eligibility_score * 0.10
            + duration_score * 0.10
        )
        overall = round(max(0.0, min(100.0, overall)), 1)

        # Determine Tier
        if overall >= 80.0:
            tier = OpportunityTier.STRONG_PURSUE
        elif overall >= 60.0:
            tier = OpportunityTier.PURSUE
        elif overall >= 40.0:
            tier = OpportunityTier.NEUTRAL
        elif overall >= 20.0:
            tier = OpportunityTier.AVOID
        else:
            tier = OpportunityTier.REJECT

        return CampaignOpportunityScore(
            overall_score=overall,
            tier=tier,
            cpm_score=round(cpm_score, 1),
            economics_score=round(econ_score, 1),
            production_effort_score=round(effort_score, 1),
            content_availability_score=round(content_score, 1),
            account_eligibility_score=round(eligibility_score, 1),
            duration_score=round(duration_score, 1),
            estimated_earning_potential=round(est_earning, 2),
            recommendation_notes=notes,
        )

    def rank(
        self,
        campaigns: List[CampaignRecord],
        vault_accounts: Optional[List[AccountMetadata]] = None,
    ) -> List[Tuple[CampaignRecord, CampaignOpportunityScore]]:
        """Ranks a list of campaigns in descending order of opportunity score."""
        scored = [
            (camp, self.evaluate(camp, vault_accounts))
            for camp in campaigns
        ]
        scored.sort(key=lambda item: item[1].overall_score, reverse=True)
        return scored

    def _score_cpm_and_earnings(self, campaign: CampaignRecord, notes: List[str]) -> Tuple[float, float]:
        """Evaluates payout rate. Prefers $2 CPM, targets $1-$5, adapts for exceptional tiers."""
        payout = campaign.payout_terms
        cpm = payout.cpm_rate

        if payout.model == PayoutModel.FIXED_PER_CLIP:
            fixed = payout.fixed_amount or 20.0
            # Normalize fixed payout to equivalent CPM assuming baseline 25k views ($20 / 25 = $0.80 CPM)
            equiv_cpm = (fixed / self.DEFAULT_BASELINE_VIEWS_PER_CLIP) * 1000
            cpm = equiv_cpm
            notes.append(f"Fixed payout ${fixed:.2f}/clip mapped to ~${equiv_cpm:.2f} CPM equivalent")

        if cpm is None or cpm <= 0.0:
            notes.append("No explicit CPM or rate specified; using conservative baseline")
            cpm = 1.5

        # CPM Scoring Curve:
        # Preferred sweet spot: $2.00 - $3.00 -> 95-100 points
        # $1.00 - $1.99 -> 80-94 points
        # $3.01 - $5.00 -> 88-98 points
        # $5.01 - $10.00 -> 80-90 points (high potential, moderate cap risk)
        # > $10.00 -> 75 points (extreme payout, high competition / strict acceptance)
        # < $1.00 -> 40-70 points (low yield)
        if 1.80 <= cpm <= 3.20:
            score = 95.0 + (5.0 * (1.0 - abs(cpm - self.preferred_cpm) / 1.2))
            notes.append(f"${cpm:.2f} CPM matches preferred target sweet spot ($2.00/CPM)")
        elif 1.0 <= cpm < 1.80:
            score = 75.0 + ((cpm - 1.0) / 0.8) * 20.0
            notes.append(f"${cpm:.2f} CPM in target range ($1-$5)")
        elif 3.20 < cpm <= 5.00:
            score = 88.0 + ((5.0 - cpm) / 1.8) * 10.0
            notes.append(f"${cpm:.2f} CPM in premium target range")
        elif 5.00 < cpm <= 10.00:
            score = 82.0
            notes.append(f"Exceptional ${cpm:.2f} CPM; checked against creator caps and competition")
        elif cpm > 10.00:
            score = 72.0
            notes.append(f"Very high ${cpm:.2f} CPM; high competition and strict validation expected")
        else:  # cpm < 1.0
            score = max(25.0, cpm * 70.0)
            notes.append(f"Sub-target ${cpm:.2f} CPM requires high view volume")

        # Estimate earning potential:
        # (views per clip / 1000) * cpm * daily post limit * 14 days
        daily_clips = min(campaign.posting_requirements.daily_post_limit, campaign.quotas.daily_creator_limit)
        est_clip_earnings = (self.DEFAULT_BASELINE_VIEWS_PER_CLIP / 1000.0) * cpm
        est_total = est_clip_earnings * daily_clips * 14.0  # 2-week active horizon

        if payout.total_budget:
            est_total = min(est_total, payout.total_budget * 0.25)  # Cap creator share at 25% of pool

        return score, est_total

    def _score_budget_health(self, campaign: CampaignRecord, notes: List[str]) -> float:
        payout = campaign.payout_terms
        if not payout.total_budget:
            return 75.0  # open-ended pool

        if payout.remaining_budget is not None:
            rem_pct = payout.remaining_budget / max(1.0, payout.total_budget)
            if rem_pct > 0.60:
                return 95.0
            elif rem_pct > 0.25:
                return 80.0
            elif rem_pct > 0.05:
                notes.append(f"Pool {rem_pct * 100:.0f}% remaining; prioritize rapid execution")
                return 55.0
            else:
                notes.append("Pool near depletion (<5%)")
                return 30.0

        return 80.0

    def _score_content_availability(self, campaign: CampaignRecord, notes: List[str]) -> float:
        """Evaluates whether high-quality, valid source videos are ready to clip."""
        uris = campaign.source_material.video_urls or campaign.discovered_source_uris
        if uris and len(uris) >= 3:
            return 100.0
        elif uris and len(uris) >= 1:
            return 85.0
        elif campaign.source_material.google_drive_folder or campaign.source_material.podcast_stream_url:
            return 80.0
        else:
            notes.append("No direct source video URIs found; extraction required")
            return 35.0

    def _score_production_effort(self, campaign: CampaignRecord, notes: List[str]) -> float:
        """Evaluates difficulty: duration, prohibited rules, complex captions."""
        score = 85.0
        p_req = campaign.posting_requirements

        # Duration standard check
        if 20.0 <= p_req.min_duration_seconds <= 45.0 and p_req.max_duration_seconds <= 60.0:
            score += 10.0  # standard vertical short length

        # Excess restrictions reduce effort score
        if len(campaign.prohibited_content_rules) > 5:
            score -= 15.0
            notes.append("Strict negative constraints require thorough QA filtering")

        if campaign.account_requirements.disallowed_regions:
            score -= 5.0

        return max(20.0, min(100.0, score))

    def _score_account_eligibility(
        self,
        campaign: CampaignRecord,
        vault_accounts: Optional[List[AccountMetadata]],
        notes: List[str],
    ) -> float:
        """Checks whether we have ready, unbanned accounts matching platform requirements."""
        if not vault_accounts:
            return 70.0  # Neutral baseline if accounts not provided

        required = campaign.required_platforms or [CampaignPlatform.YOUTUBE_SHORTS]
        matching = [
            a for a in vault_accounts
            if a.status == AccountStatus.ACTIVE
            and any(p.value in a.platform.value for p in required)
        ]

        if matching:
            return 95.0
        else:
            notes.append("No active matching channel currently in vault; creation/attachment needed")
            return 45.0

    def _score_duration_and_competition(
        self,
        campaign: CampaignRecord,
        now: datetime,
        notes: List[str],
    ) -> float:
        """Checks time left before campaign deadline."""
        end = campaign.duration_terms.deadline or campaign.duration_terms.end_date
        if not end:
            return 80.0  # ongoing campaign

        diff = (end - now).total_seconds()
        days_left = diff / 86400.0

        if days_left >= 14.0:
            return 95.0
        elif days_left >= 5.0:
            return 80.0
        elif days_left >= 2.0:
            notes.append(f"Only {days_left:.1f} days remaining")
            return 60.0
        elif days_left > 0.0:
            notes.append("Impending deadline (<48h)")
            return 35.0
        else:
            return 10.0

    def _build_terminal_score(self, score: float, tier: OpportunityTier, reason: str) -> CampaignOpportunityScore:
        return CampaignOpportunityScore(
            overall_score=score,
            tier=tier,
            cpm_score=score,
            economics_score=score,
            production_effort_score=score,
            content_availability_score=score,
            account_eligibility_score=score,
            duration_score=score,
            estimated_earning_potential=0.0,
            recommendation_notes=[reason],
        )
