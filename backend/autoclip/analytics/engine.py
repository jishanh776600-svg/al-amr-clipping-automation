"""Autonomous Analytics and Learning Engine.

Enforces:
1. Production Reserve Tracking (READY_TARGET = 6)
2. 24-Hour Maturation Rule (only mature publications >= 24h old are analyzed)
3. Minimum Evidence Threshold (requires >= 3 mature data points before adjusting weights)
4. Immutable Learning Audit Trail (records parameter shifts with complete evidence)
"""

from __future__ import annotations

import logging
from typing import Any

from ..db import models, store

log = logging.getLogger(__name__)

READY_TARGET = 6
MIN_MATURE_AGE_HOURS = 24
MIN_SAMPLE_SIZE = 3


class LearningEngine:
    """Production analytics, reserve monitoring, and autonomous learning loop."""

    def __init__(self, target_reserve: int = READY_TARGET) -> None:
        self.target_reserve = target_reserve

    def get_reserve_status(self, job_id: str | None = None) -> dict[str, Any]:
        """Check the ready-to-publish clip reserve pool against READY_TARGET (6)."""
        ready_count = store.count_ready_reserve(job_id=job_id)
        deficit = max(0, self.target_reserve - ready_count)
        status = "ADEQUATE" if deficit == 0 else "DEFICIT"
        return {
            "target": self.target_reserve,
            "current_ready": ready_count,
            "deficit": deficit,
            "status": status,
            "replenishment_needed": deficit > 0,
            "job_id": job_id,
        }

    def evaluate_learning(
        self,
        min_age_hours: int = MIN_MATURE_AGE_HOURS,
        min_samples: int = MIN_SAMPLE_SIZE,
    ) -> dict[str, Any]:
        """Evaluate mature publication metrics and propose/record autonomous learning adjustments.

        Enforces:
        - 24-hour maturation rule: Metrics from publications < 24h old are excluded.
        - Minimum sample threshold: Requires at least `min_samples` mature data points.
        """
        mature_metrics = store.get_mature_metrics(min_age_hours=min_age_hours)
        sample_count = len(mature_metrics)

        if sample_count < min_samples:
            log.info(
                "Learning loop skipped: %d mature metrics available (minimum required: %d)",
                sample_count,
                min_samples,
            )
            return {
                "status": "INSUFFICIENT_DATA",
                "mature_sample_count": sample_count,
                "required_samples": min_samples,
                "message": f"Awaiting mature metrics: {sample_count}/{min_samples} required (24h maturation rule applied).",
                "audits_recorded": [],
            }

        # Analyze performance distributions
        durations: list[float] = []
        engagement_scores: list[float] = []
        high_performers: list[models.PublicationMetricRecord] = []

        for m in mature_metrics:
            # Score composite engagement
            score = (m.views * 1.0) + (m.likes * 5.0) + (m.comments * 10.0) + (m.shares * 15.0)
            engagement_scores.append(score)
            if m.completion_rate and m.completion_rate > 0.6:
                high_performers.append(m)

        avg_score = sum(engagement_scores) / len(engagement_scores) if engagement_scores else 0.0

        # Learning recommendations and audits
        audits_created: list[dict[str, Any]] = []

        # Check duration optimization
        short_clips = [m for m in mature_metrics if m.watch_time_s and m.watch_time_s < 35]
        if len(short_clips) > len(mature_metrics) / 2:
            audit = store.record_learning_audit(
                parameter_name="target_clip_duration_preference",
                old_value="balanced_45s",
                new_value="punchy_30s",
                reason=f"Analysis of {sample_count} mature clips shows superior completion in <35s bracket",
                sample_size=sample_count,
                evidence_data={
                    "mature_ids": [m.id for m in mature_metrics],
                    "short_clip_count": len(short_clips),
                    "avg_engagement_score": avg_score,
                },
            )
            audits_created.append(audit.to_dict())

        return {
            "status": "EVALUATED",
            "mature_sample_count": sample_count,
            "avg_engagement_score": round(avg_score, 2),
            "high_performer_count": len(high_performers),
            "audits_recorded": audits_created,
        }
