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
            candidates = self.vault.discover_candidates(cue, recent_asset_ids=recent_asset_ids)
            if not candidates:
                # Dynamically generate a fresh unique procedural visual asset
                card_p = self.vault.card_generator.generate_card(
                    concept=cue.concept,
                    metadata=cue.metadata,
                    variant_idx=len(recent_asset_ids) + 1,
                )
                candidates = [
                    VisualAsset(
                        asset_id=f"card_{cue.concept}_{cue.cue_id}_dyn_{len(recent_asset_ids)+1}",
                        file_path=card_p,
                        visual_type=cue.visual_type,
                        concept=cue.concept,
                        presentation_mode=PresentationMode.PARTIAL_OVERLAY,
                        width=1080,
                        height=1920,
                        duration_s=cue.duration_s,
                        tags=[cue.concept, "dynamic_procedural"],
                        source_provider="generated_ui",
                        is_video=False,
                    )
                ]

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

            # 3. Confidence Gate Check & Guaranteed Fulfillment
            if not best_score.is_approved:
                # If penalized due to repetition, immediately acquire a fresh unique procedural asset
                dyn_card_path = self.vault.card_generator.generate_card(
                    concept=cue.concept,
                    metadata=cue.metadata,
                    variant_idx=len(recent_asset_ids) + 1,
                )
                best_asset = VisualAsset(
                    asset_id=f"card_{cue.concept}_{cue.cue_id}_fresh_{len(recent_asset_ids)+1}",
                    file_path=dyn_card_path,
                    visual_type=cue.visual_type,
                    concept=cue.concept,
                    presentation_mode=PresentationMode.PARTIAL_OVERLAY,
                    width=1080,
                    height=1920,
                    duration_s=cue.duration_s,
                    tags=[cue.concept, "fresh_unique_variant"],
                    source_provider="generated_ui",
                    is_video=False,
                )
                best_score = self.scorer.score_candidate(
                    cue=cue,
                    asset=best_asset,
                    recent_asset_ids=[],
                    recent_concepts=[],
                )

            # 4. Approved: Add to EDL
            debug_info = {
                "spoken_text": cue.trigger_phrase,
                "semantic_concept": cue.concept,
                "query": getattr(cue, "context_query", "") or cue.trigger_word,
                "candidate_urls": [
                    str(c.metadata.get("pexels_url", "")) for c in candidates if c.metadata.get("pexels_url")
                ],
                "selected_asset": best_asset.asset_id,
                "selection_score": round(best_score.total_score, 3),
            }
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
                debug_info=debug_info,
            )
            edl.entries.append(entry)
            recent_asset_ids.append(best_asset.asset_id)
            recent_concepts.append(cue.concept)

            log.info(
                "EDL APPROVED: [%.2fs - %.2fs] concept=%s mode=%s asset=%s score=%.2f query='%s'",
                entry.start_s,
                entry.end_s,
                entry.concept,
                entry.presentation_mode.value,
                best_asset.asset_id,
                best_score.total_score,
                debug_info["query"],
            )

        return edl
