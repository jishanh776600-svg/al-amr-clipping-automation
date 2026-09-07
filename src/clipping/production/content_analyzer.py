"""Production Content Analysis & Candidate Moment Selection Engine."""

import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from clipping.contracts.requirements import CampaignRequirements
from clipping.contracts.source import SourceResolutionResult
from clipping.logging.logger import get_logger

logger = get_logger("clipping.production.content_analyzer")


class ProductionCandidateSegment(BaseModel):
    """Represents an evaluated candidate segment for production clipping."""
    start_time: float
    end_time: float
    duration: float
    hook_sentence: str
    transcript_segment: str
    score: float
    talking_points_covered: List[str] = Field(default_factory=list)
    prohibited_content_detected: List[str] = Field(default_factory=list)
    rationale: str = ""


class ProductionContentAnalyzer:
    """
    Analyzes video source metadata, audio transcripts, and campaign requirements
    to select the best vertical clip moments without fabricating content.
    """

    def select_best_moments(
        self,
        source_result: SourceResolutionResult,
        requirements: Optional[CampaignRequirements] = None,
        transcript: Optional[str] = None,
        clip_count: Optional[int] = None,
    ) -> List[ProductionCandidateSegment]:
        """
        Deterministically selects top clip segments obeying duration limits,
        talking points, prohibited content rules, and hook strength.
        """
        total_duration = source_result.duration or 60.0

        # Duration requirements
        min_dur = 15.0
        max_dur = 60.0
        target_dur = 30.0
        desired_count = clip_count or 1

        if requirements and requirements.clips:
            if requirements.clips.min_duration_seconds:
                min_dur = float(requirements.clips.min_duration_seconds)
            if requirements.clips.max_duration_seconds:
                max_dur = float(requirements.clips.max_duration_seconds)
            if requirements.clips.target_duration_seconds:
                target_dur = float(requirements.clips.target_duration_seconds)
            if requirements.clips.clip_count_required:
                desired_count = requirements.clips.clip_count_required

        # Prohibited topics & content
        prohibited: List[str] = []
        if requirements:
            if requirements.content and requirements.content.prohibited_topics:
                prohibited.extend([p.lower() for p in requirements.content.prohibited_topics if p])
            if requirements.source and requirements.source.prohibited_content:
                prohibited.extend([p.lower() for p in requirements.source.prohibited_content if p])
            if requirements.text and requirements.text.prohibited_words:
                prohibited.extend([p.lower() for p in requirements.text.prohibited_words if p])

        # Required talking points
        talking_points: List[str] = []
        if requirements and requirements.content and requirements.content.required_talking_points:
            talking_points = [tp.lower() for tp in requirements.content.required_talking_points if tp]

        # Check total source length against min_dur
        if total_duration < min_dur:
            logger.warning(
                "Source duration is shorter than minimum required clip duration",
                source_duration=total_duration,
                min_duration=min_dur,
            )
            return []

        # Effective target segment length
        effective_dur = min(max(target_dur, min_dur), min(max_dur, total_duration))
        step_size = max(10.0, effective_dur * 0.5)

        candidates: List[ProductionCandidateSegment] = []

        # Generate window intervals across duration
        current_start = 0.0
        while current_start + min_dur <= total_duration:
            current_end = min(current_start + effective_dur, total_duration)
            seg_dur = current_end - current_start
            if seg_dur < min_dur:
                break

            # Text analysis if transcript is available
            seg_transcript = ""
            covered_tps: List[str] = []
            prohibited_found: List[str] = []
            hook = ""

            if transcript and transcript.strip():
                # Extract text chunk proportion
                ratio_start = current_start / total_duration
                ratio_end = current_end / total_duration
                t_words = transcript.strip().split()
                w_start = int(ratio_start * len(t_words))
                w_end = min(int(ratio_end * len(t_words)) + 1, len(t_words))
                seg_words = t_words[w_start:w_end]
                seg_transcript = " ".join(seg_words)

                # Check prohibited words
                seg_lower = seg_transcript.lower()
                for prob in prohibited:
                    if prob in seg_lower:
                        prohibited_found.append(prob)

                # Check talking points
                for tp in talking_points:
                    if tp in seg_lower:
                        covered_tps.append(tp)

                # Hook: first sentence or first 10 words
                sentences = re.split(r"[.!?]+", seg_transcript)
                hook = sentences[0].strip() if sentences and sentences[0].strip() else " ".join(seg_words[:8])
            else:
                hook = f"Key Insight from {source_result.title or 'Source'}"

            # Calculate deterministic score
            score = 60.0
            # Reward talking points
            score += len(covered_tps) * 15.0
            # Penalize prohibited content heavily
            if prohibited_found:
                score -= 100.0
            # Reward optimal duration proximity
            dur_diff = abs(seg_dur - target_dur)
            score += max(0.0, 10.0 - dur_diff * 0.5)
            # Reward early hook in the segment
            if hook and len(hook.split()) >= 4:
                score += 5.0

            candidates.append(
                ProductionCandidateSegment(
                    start_time=round(current_start, 2),
                    end_time=round(current_end, 2),
                    duration=round(seg_dur, 2),
                    hook_sentence=hook,
                    transcript_segment=seg_transcript,
                    score=round(score, 1),
                    talking_points_covered=covered_tps,
                    prohibited_content_detected=prohibited_found,
                    rationale=f"Window [{current_start:.1f}s - {current_end:.1f}s] with duration {seg_dur:.1f}s",
                )
            )
            current_start += step_size

        # Disqualify segments containing prohibited content
        valid_candidates = [c for c in candidates if not c.prohibited_content_detected]
        if not valid_candidates:
            logger.warning("All candidate segments violated prohibited content rules")
            return []

        # Sort by score descending
        valid_candidates.sort(key=lambda c: c.score, reverse=True)

        # Select non-overlapping top segments up to desired_count
        selected: List[ProductionCandidateSegment] = []
        for cand in valid_candidates:
            if len(selected) >= desired_count:
                break
            # Overlap check (>50% overlap with already selected)
            overlap = False
            for s in selected:
                overlap_start = max(cand.start_time, s.start_time)
                overlap_end = min(cand.end_time, s.end_time)
                if overlap_end > overlap_start:
                    overlap_dur = overlap_end - overlap_start
                    if overlap_dur > 0.5 * min(cand.duration, s.duration):
                        overlap = True
                        break
            if not overlap:
                selected.append(cand)

        # If desired count not fully met due to overlap, fill with remaining best
        if len(selected) < desired_count and valid_candidates:
            for cand in valid_candidates:
                if len(selected) >= desired_count:
                    break
                if cand not in selected:
                    selected.append(cand)

        return selected
