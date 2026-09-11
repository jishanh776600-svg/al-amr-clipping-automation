"""Campaign Ranking and Candidate Selection for AL AMR.

Filters rejected candidates, sorts approved candidates by final campaign score,
and assigns 1-indexed ranks.
"""

from __future__ import annotations

import logging
from typing import Sequence

from ..db.models import Clip
from .models import CampaignBrief, CandidateEvaluation

log = logging.getLogger(__name__)


def rank_and_filter_candidates(
    clips: Sequence[Clip],
    evaluations: Sequence[CandidateEvaluation],
    campaign: CampaignBrief,
) -> tuple[list[Clip], list[CandidateEvaluation]]:
    """Filter out rejected candidates and rank survivors by campaign final_score."""
    eval_map = {e.candidate_id: e for e in evaluations}

    approved_clips: list[tuple[Clip, CandidateEvaluation]] = []
    rejected_count = 0

    for clip in clips:
        ev = eval_map.get(clip.id)
        if ev is None:
            continue

        if ev.approved:
            # Update clip score to reflect final campaign score (scaled back to 0-100 for AutoClip UI/DB consistency)
            clip.score = int(round(ev.final_score * 10.0))
            approved_clips.append((clip, ev))
        else:
            rejected_count += 1
            log.info(
                "Clip %s rejected by campaign rules: %s",
                clip.id,
                "; ".join(ev.hard_failures),
            )

    log.info(
        "Campaign evaluation summary: %d approved, %d rejected out of %d candidates.",
        len(approved_clips),
        rejected_count,
        len(clips),
    )

    # Sort approved clips by final_score descending
    approved_clips.sort(key=lambda item: item[1].final_score, reverse=True)

    # Limit to campaign output_count or maximum_candidates
    limit = min(campaign.output_count, campaign.maximum_candidates)
    selected = approved_clips[:limit]

    ranked_clips: list[Clip] = []
    ranked_evals: list[CandidateEvaluation] = []

    for rank, (clip, ev) in enumerate(selected, start=1):
        clip.rank = rank
        ev.clip_id = clip.id
        ranked_clips.append(clip)
        ranked_evals.append(ev)

    return ranked_clips, ranked_evals
