"""Clip Selection, Ranking, and 5-10 Yield Governance Engine."""

from typing import List, Optional, Tuple
from clipping.contracts.clip import (
    ClipCandidate,
    ClipScore,
    RankedCandidate,
    ClipSelectionResult,
)
from clipping.discovery.config import ClipDiscoveryConfig
from clipping.logging.logger import get_logger

logger = get_logger("clipping.discovery.selection")


class ClipSelector:
    """
    Ranks candidates and selects top 5-10 high-retention clips.
    Enforces quality floor: NEVER pads with sub-threshold candidates merely to reach 5 clips.
    """

    def __init__(self, config: Optional[ClipDiscoveryConfig] = None):
        self.config = config or ClipDiscoveryConfig()

    def select_clips(
        self,
        source_video_id: str,
        deduplicated_candidates: List[Tuple[ClipCandidate, ClipScore]],
    ) -> ClipSelectionResult:
        # Sort by overall score descending
        sorted_pairs = sorted(
            deduplicated_candidates,
            key=lambda p: p[1].overall_virality_score,
            reverse=True,
        )

        selected: List[RankedCandidate] = []
        rejected: List[RankedCandidate] = []
        selection_reasons: List[str] = []

        rank = 1
        for cand, score in sorted_pairs:
            # 1. Quality Threshold Check
            if score.overall_virality_score < self.config.quality_threshold:
                rejected.append(
                    RankedCandidate(
                        candidate=cand,
                        score=score,
                        rank=rank,
                        selection_reason=f"Rejected: Score ({score.overall_virality_score:.1f}) below quality threshold ({self.config.quality_threshold:.1f})",
                        is_selected=False,
                    )
                )
                rank += 1
                continue

            # 2. Maximum Clip Cap (Hard Max: 10 clips)
            if len(selected) >= self.config.max_clips_target:
                rejected.append(
                    RankedCandidate(
                        candidate=cand,
                        score=score,
                        rank=rank,
                        selection_reason=f"Rejected: Exceeded max clip quota ({self.config.max_clips_target})",
                        is_selected=False,
                    )
                )
                rank += 1
                continue

            # 3. Temporal Spacing / Diversity Check
            too_close = False
            for sel in selected:
                if abs(cand.start_time - sel.candidate.start_time) < self.config.temporal_spacing_seconds:
                    too_close = True
                    break

            if too_close:
                rejected.append(
                    RankedCandidate(
                        candidate=cand,
                        score=score,
                        rank=rank,
                        selection_reason=f"Rejected: Too close to higher-ranked clip within {self.config.temporal_spacing_seconds}s",
                        is_selected=False,
                    )
                )
                rank += 1
                continue

            # 4. Accepted Candidate
            reason = f"Rank #{rank}: High engagement (Score={score.overall_virality_score:.1f}, Hook={score.hook_strength:.0f})"
            selected.append(
                RankedCandidate(
                    candidate=cand,
                    score=score,
                    rank=rank,
                    selection_reason=reason,
                    is_selected=True,
                )
            )
            selection_reasons.append(reason)
            rank += 1

        # Check if insufficient candidates qualify (< min_clips_target)
        insufficient_warning = False
        if len(selected) < self.config.min_clips_target:
            insufficient_warning = True
            logger.warning(
                "Fewer than 5 candidates passed quality threshold. Preserving strict quality floor without padding.",
                selected_count=len(selected),
                target_min=self.config.min_clips_target,
            )

        avg_score = (
            sum(s.score.overall_virality_score for s in selected) / len(selected)
            if selected
            else 0.0
        )
        total_duration = sum(s.candidate.duration for s in selected)

        coverage_metrics = {
            "total_candidates": len(deduplicated_candidates),
            "selected_count": len(selected),
            "rejected_count": len(rejected),
            "average_selected_score": round(avg_score, 1),
            "total_selected_duration_seconds": round(total_duration, 1),
        }

        return ClipSelectionResult(
            source_video_id=source_video_id,
            total_candidates_generated=len(deduplicated_candidates),
            quality_threshold=self.config.quality_threshold,
            selected_clips=selected,
            rejected_clips=rejected,
            selection_reasons=selection_reasons,
            coverage_metrics=coverage_metrics,
            insufficient_candidate_warning=insufficient_warning,
        )
