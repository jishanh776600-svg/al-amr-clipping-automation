"""Temporal & Semantic Candidate Deduplication Engine."""

from typing import List, Optional, Set, Tuple
from clipping.contracts.clip import ClipCandidate, ClipScore
from clipping.discovery.config import ClipDiscoveryConfig
from clipping.logging.logger import get_logger

logger = get_logger("clipping.discovery.dedup")


def calculate_temporal_iou(c1: ClipCandidate, c2: ClipCandidate) -> float:
    """Calculates temporal Intersection-over-Union between two clip time spans."""
    inter_start = max(c1.start_time, c2.start_time)
    inter_end = min(c1.end_time, c2.end_time)
    intersection = max(0.0, inter_end - inter_start)

    union_start = min(c1.start_time, c2.start_time)
    union_end = max(c1.end_time, c2.end_time)
    union = max(0.001, union_end - union_start)

    return intersection / union


def calculate_text_jaccard(text1: str, text2: str) -> float:
    """Calculates word-token Jaccard similarity between two transcripts."""
    tokens1: Set[str] = set(re_tokenize(text1))
    tokens2: Set[str] = set(re_tokenize(text2))

    if not tokens1 or not tokens2:
        return 0.0

    intersection = len(tokens1.intersection(tokens2))
    union = len(tokens1.union(tokens2))
    return float(intersection) / float(union) if union > 0 else 0.0


def re_tokenize(text: str) -> List[str]:
    import re
    return [w.lower() for w in re.findall(r"\b\w{3,}\b", text)]


class CandidateDeduplicator:
    """
    Suppresses redundant candidate clips covering essentially the same conversational moment,
    preserving the single highest-scoring representative.
    """

    def __init__(self, config: Optional[ClipDiscoveryConfig] = None):
        self.config = config or ClipDiscoveryConfig()

    def deduplicate(
        self,
        scored_candidates: List[Tuple[ClipCandidate, ClipScore]],
    ) -> List[Tuple[ClipCandidate, ClipScore]]:
        if not scored_candidates:
            return []

        # 1. Sort by total score descending (highest quality first)
        sorted_pairs = sorted(
            scored_candidates,
            key=lambda pair: pair[1].overall_virality_score,
            reverse=True,
        )

        accepted: List[Tuple[ClipCandidate, ClipScore]] = []

        # 2. Greedy Non-Maximum Suppression (NMS)
        for cand, score in sorted_pairs:
            is_duplicate = False

            for acc_cand, _ in accepted:
                # Temporal IoU check
                iou = calculate_temporal_iou(cand, acc_cand)
                if iou >= self.config.iou_dedup_threshold:
                    is_duplicate = True
                    break

                # Transcript text similarity check
                text_sim = calculate_text_jaccard(cand.transcript_text, acc_cand.transcript_text)
                if text_sim >= self.config.text_sim_dedup_threshold:
                    is_duplicate = True
                    break

            if not is_duplicate:
                accepted.append((cand, score))

        logger.info(
            "Candidate deduplication completed",
            initial_count=len(scored_candidates),
            deduplicated_count=len(accepted),
        )
        return accepted
