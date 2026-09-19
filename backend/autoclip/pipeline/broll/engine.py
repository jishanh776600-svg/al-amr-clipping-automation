"""Contextual Evidence Engine orchestrating Semantic Visual Matching and EDL compilation."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from autoclip.pipeline.transcript import Transcript, Word
from .models import (
    EDLEntry,
    EditDecisionList,
    PresentationMode,
    RelevanceScore,
    SemanticVisualCue,
    VisualAsset,
    VisualType,
)
from .scorer import DEFAULT_CONFIDENCE_THRESHOLD, VisualRelevanceScorer
from .semantic_parser import ContextualSemanticParser
from .vault import VisualAssetVault

log = logging.getLogger(__name__)


class SemanticBrollEngine:
    """Orchestrates speech-synchronized semantic visual matching and EDL generation."""

    def __init__(
        self,
        vault: VisualAssetVault | None = None,
        parser: ContextualSemanticParser | None = None,
        scorer: VisualRelevanceScorer | None = None,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    ) -> None:
        self.vault = vault or VisualAssetVault()
        self.parser = parser or ContextualSemanticParser()
        self.scorer = scorer or VisualRelevanceScorer(confidence_threshold=confidence_threshold)

    def generate_edl(
        self,
        words: list[Word],
        clip_id: str = "clip",
        clip_start_s: float = 0.0,
        clip_end_s: float = 30.0,
    ) -> EditDecisionList:
        """Compiles an Edit Decision List (EDL) aligned to spoken concepts."""
        edl = EditDecisionList(
            clip_id=clip_id,
            clip_start_s=clip_start_s,
            clip_end_s=clip_end_s,
        )

        # 1. Extract semantic cues from transcript slice
        cues = self.parser.parse_transcript_segment(
            words=words,
            clip_start_s=clip_start_s,
            clip_end_s=clip_end_s,
        )

        recent_asset_ids: list[str] = []
        recent_concepts: list[str] = []

        # 2. Process each semantic cue
        for cue in cues:
            candidates = self.vault.discover_candidates(cue)
            if not candidates:
                fallback_info = {
                    "cue_id": cue.cue_id,
                    "concept": cue.concept,
                    "trigger_phrase": cue.trigger_phrase,
                    "start_s": cue.start_s,
                    "fallback_action": "a_roll_punch_in",
                    "reason": "No candidate visual assets found in vault.",
                }
                edl.fallbacks.append(fallback_info)
                log.info("Visual fallback for cue %s: no candidates found", cue.cue_id)
                continue

            # Score each candidate
            scored_candidates: list[tuple[VisualAsset, RelevanceScore]] = []
            for asset in candidates:
                score = self.scorer.score_candidate(
                    cue=cue,
                    asset=asset,
                    recent_asset_ids=recent_asset_ids,
                    recent_concepts=recent_concepts,
                )
                scored_candidates.append((asset, score))

            # Select highest scoring candidate
            scored_candidates.sort(key=lambda x: x[1].total_score, reverse=True)
            best_asset, best_score = scored_candidates[0]

            # 3. Confidence Gate Check
            if not best_score.is_approved:
                fallback_info = {
                    "cue_id": cue.cue_id,
                    "concept": cue.concept,
                    "trigger_phrase": cue.trigger_phrase,
                    "start_s": cue.start_s,
                    "best_asset": best_asset.asset_id,
                    "score": best_score.total_score,
                    "fallback_action": "a_roll_punch_in",
                    "reason": f"Confidence gate rejected: {best_score.rejection_reason}",
                }
                edl.fallbacks.append(fallback_info)
                log.info("Visual fallback for cue %s: score %.2f below threshold %.2f (%s)", cue.cue_id, best_score.total_score, best_score.confidence_threshold, best_score.rejection_reason)
                continue

            # 4. Approved: Add to EDL
            entry = EDLEntry(
                entry_id=f"edl_{len(edl.entries)+1}_{cue.concept}",
                start_s=cue.start_s,
                end_s=cue.end_s,
                presentation_mode=cue.preferred_mode,
                visual_type=best_asset.visual_type,
                asset_path=best_asset.file_path,
                trigger_phrase=cue.trigger_phrase,
                concept=cue.concept,
                relevance_score=best_score,
                transition="hard_cut",
                is_video=best_asset.is_video,
            )
            edl.entries.append(entry)
            recent_asset_ids.append(best_asset.asset_id)
            recent_concepts.append(cue.concept)

            log.info(
                "EDL APPROVED: [%.2fs - %.2fs] concept=%s mode=%s asset=%s score=%.2f",
                entry.start_s,
                entry.end_s,
                entry.concept,
                entry.presentation_mode.value,
                best_asset.asset_id,
                best_score.total_score,
            )

        return edl
