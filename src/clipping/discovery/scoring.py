"""Deterministic Heuristic Clip Scoring Engine."""

import re
from typing import List, Optional
from clipping.contracts.clip import (
    ClipCandidate,
    ClipScore,
    CandidateScoreBreakdown,
)
from clipping.contracts.perception import ActiveSpeakerSegment
from clipping.discovery.config import ClipDiscoveryConfig
from clipping.logging.logger import get_logger

logger = get_logger("clipping.discovery.scoring")


class DeterministicClipScorer:
    """
    Evaluates candidate clip quality using explainable, deterministic lexical,
    structural, and multi-modal signals without external AI APIs.
    """

    HOOK_PATTERNS = [
        re.compile(r"\b(the\s+truth\s+is|what\s+nobody\s+tells\s+you|i\s+realized|the\s+problem\s+is)\b", re.IGNORECASE),
        re.compile(r"\b(the\s+biggest\s+(mistake|lesson|problem)|here\s+is\s+why|here\'s\s+why|what\s+happened\s+was)\b", re.IGNORECASE),
        re.compile(r"\b(most\s+people|i\s+was\s+wrong|stop\s+doing|never\s+do\s+this|everyone\s+gets\s+this\s+wrong)\b", re.IGNORECASE),
        re.compile(r"\b(the\s+secret\s+to|how\s+to\s+actually|if\s+you\s+want\s+to|the\s+reason\s+is)\b", re.IGNORECASE),
        re.compile(r"^(why\s+do|how\s+did|what\s+if|have\s+you\s+ever|did\s+you\s+know)\b", re.IGNORECASE),
    ]

    CURIOSITY_WORDS = ["secret", "paradox", "hidden", "insane", "trick", "breakthrough", "actually", "surprising", "shocking", "realized"]
    CONTRAST_WORDS = ["but", "however", "instead", "yet", "although", "contrary", "whereas"]
    CLOSURE_MARKERS = ["and that is why", "and that's why", "in the end", "so basically", "the lesson is", "which means", "how you fix it"]
    DANGLING_OPENERS = ["and", "so then", "because", "or", "then he", "then she", "then they", "also"]
    FILLER_WORDS = ["um", "uh", "like", "you know", "sort of", "kind of", "literally", "basically", "i mean"]
    INTENSITY_WORDS = ["incredible", "terrible", "crucial", "disaster", "breakthrough", "transform", "massive", "vital", "worst", "best", "losing"]

    OPINION_BOMB_PATTERNS = [
        re.compile(r"\b(unpopular\s+opinion|hot\s+take)\b", re.IGNORECASE),
        re.compile(r"\b(everyone\s+is\s+(wrong|lying|mistaken|delusional)|everyone\s+gets\s+this\s+wrong)\b", re.IGNORECASE),
        re.compile(r"\b(worst\s+(advice|mistake|decision|lie)|biggest\s+lie)\b", re.IGNORECASE),
        re.compile(r"\b(stop\s+doing|never\s+(do|say|buy|use)\s+this|you\s+need\s+to\s+stop)\b", re.IGNORECASE),
        re.compile(r"\b(nobody\s+wants\s+to\s+admit|hard\s+truth|brutal\s+truth|hard\s+pill\s+to\s+swallow)\b", re.IGNORECASE),
        re.compile(r"\b(is\s+a\s+scam|total\s+waste\s+of\s+(time|money))\b", re.IGNORECASE),
    ]

    REVELATION_PATTERNS = [
        re.compile(r"\b(the\s+paradox\s+(is|of)|here\s+is\s+the\s+paradox)\b", re.IGNORECASE),
        re.compile(r"\b(counterintuitive\s+(truth|reality|nature|fact)|counterintuitively)\b", re.IGNORECASE),
        re.compile(r"\b(it\s+actually\s+does\s+the\s+opposite|does\s+the\s+exact\s+opposite|the\s+opposite\s+is\s+true)\b", re.IGNORECASE),
        re.compile(r"\b(turns\s+out\s+(that|it)|what\s+actually\s+happens\s+is)\b", re.IGNORECASE),
        re.compile(r"\b(the\s+exact\s+reverse|it\s+sounded\s+crazy\s+until|everything\s+changed\s+when\s+i\s+realized)\b", re.IGNORECASE),
        re.compile(r"\b(revelation\s+was|the\s+breakthrough\s+moment|eye-opening\s+truth)\b", re.IGNORECASE),
    ]

    def __init__(self, config: Optional[ClipDiscoveryConfig] = None):
        self.config = config or ClipDiscoveryConfig()

    def score_candidate(
        self,
        candidate: ClipCandidate,
        active_speakers: Optional[List[ActiveSpeakerSegment]] = None,
    ) -> ClipScore:
        text = candidate.transcript_text
        words = candidate.words
        hook_text = candidate.hook_sentence

        # 1. Hook Score (0-100)
        hook_score = 40.0
        for pattern in self.HOOK_PATTERNS:
            if pattern.search(hook_text):
                hook_score += 20.0

        if re.search(r"\b\d+(\.\d+)?(%|k|m|x|\$)?\b", hook_text, re.IGNORECASE):
            hook_score += 15.0
        hook_score = min(100.0, hook_score)

        # 2. Completeness Score (0-100)
        completeness_score = 35.0
        if re.search(r"[.!?]$", text.strip()):
            completeness_score += 30.0
        if "?" in text and text.strip()[-1] != "?":
            # Contains question and follow-up answer
            completeness_score += 20.0
        for marker in self.CLOSURE_MARKERS:
            if marker in text.lower():
                completeness_score += 15.0
        completeness_score = min(100.0, completeness_score)

        # 3. Curiosity Score (0-100)
        curiosity_score = 30.0
        text_lower = text.lower()
        for w in self.CURIOSITY_WORDS:
            if w in text_lower:
                curiosity_score += 12.0
        for w in self.CONTRAST_WORDS:
            if f" {w} " in text_lower:
                curiosity_score += 8.0
        curiosity_score = min(100.0, curiosity_score)

        # 4. Specificity Score (0-100)
        specificity_score = 30.0
        num_matches = re.findall(r"\b\d+(\.\d+)?(%|k|m|x|\$)?\b", text, re.IGNORECASE)
        specificity_score += min(50.0, len(num_matches) * 15.0)
        if any(w[0].isupper() for w in text.split()[1:] if len(w) > 2):  # Proper nouns/entities
            specificity_score += 15.0
        specificity_score = min(100.0, specificity_score)

        # 5. Emotion / Story Score (0-100)
        emotion_score = 30.0
        if re.search(r"\b(i\s+was|we\s+were|my\s+story|i\s+felt|we\s+built|i\s+spent|first\s+startup)\b", text_lower):
            emotion_score += 25.0
        for w in self.INTENSITY_WORDS:
            if w in text_lower:
                emotion_score += 10.0
        emotion_score = min(100.0, emotion_score)

        # 6. Standalone Comprehensibility Score (0-100)
        standalone_score = 50.0
        first_word = text.strip().split()[0].lower() if text.strip() else ""
        if any(first_word == d or text_lower.startswith(d) for d in self.DANGLING_OPENERS):
            standalone_score -= 25.0
        else:
            standalone_score += 25.0
        standalone_score = max(0.0, min(100.0, standalone_score))

        # 7. Visual Score (0-100)
        visual_score = 75.0
        if active_speakers:
            c_start = candidate.start_time
            c_end = candidate.end_time
            matching_active = [
                s for s in active_speakers
                if max(c_start, s.start_time) < min(c_end, s.end_time)
            ]
            if matching_active:
                avg_conf = sum(s.speaking_confidence for s in matching_active) / len(matching_active)
                visual_score = 60.0 + (avg_conf * 35.0)
        visual_score = min(100.0, visual_score)

        # 8. Opinion Bomb Score (0-100)
        opinion_bomb_score = 0.0
        for pattern in self.OPINION_BOMB_PATTERNS:
            if pattern.search(text):
                opinion_bomb_score += 35.0
            if pattern.search(hook_text):
                opinion_bomb_score += 15.0
        opinion_bomb_score = min(100.0, opinion_bomb_score)

        # 9. Revelation / Contrarian Paradox Score (0-100)
        revelation_score = 0.0
        for pattern in self.REVELATION_PATTERNS:
            if pattern.search(text):
                revelation_score += 35.0
            if pattern.search(hook_text):
                revelation_score += 15.0
        revelation_score = min(100.0, revelation_score)

        # 10. Penalties Calculation
        # Filler Penalty
        filler_count = sum(1 for w in words if w.word.lower().strip(".,!?") in self.FILLER_WORDS)
        filler_ratio = filler_count / max(1, len(words))
        filler_penalty = min(30.0, filler_ratio * 100.0 * self.config.penalty_filler_scale * 0.1)

        # Silence Penalty (pauses > 1.8s)
        silence_gaps = 0
        for i in range(len(words) - 1):
            if (words[i + 1].start - words[i].end) > 1.8:
                silence_gaps += 1
        silence_penalty = min(25.0, silence_gaps * self.config.penalty_silence_scale)

        # Repetition Penalty (Repeated 3-word ngrams)
        word_tokens = [w.word.lower().strip(".,!?") for w in words if len(w.word) > 1]
        ngrams = [" ".join(word_tokens[i : i + 3]) for i in range(len(word_tokens) - 2)]
        duplicate_ngrams = len(ngrams) - len(set(ngrams))
        repetition_penalty = min(20.0, duplicate_ngrams * self.config.penalty_repetition_scale)

        # Boundary / Duration Penalty (if outside preferred duration range)
        boundary_penalty = 0.0
        dur = candidate.duration
        if dur < self.config.preferred_min_duration:
            boundary_penalty = (self.config.preferred_min_duration - dur) * 1.5
        elif dur > self.config.preferred_max_duration:
            boundary_penalty = (dur - self.config.preferred_max_duration) * 1.0
        boundary_penalty = min(20.0, boundary_penalty)

        # 11. Weighted Composite Total Score
        total_positive = (
            self.config.weight_hook * hook_score
            + self.config.weight_completeness * completeness_score
            + self.config.weight_curiosity * curiosity_score
            + self.config.weight_specificity * specificity_score
            + self.config.weight_emotion * emotion_score
            + self.config.weight_standalone * standalone_score
            + self.config.weight_visual * visual_score
        )

        virality_boost = min(5.0, (opinion_bomb_score * 0.025) + (revelation_score * 0.025))
        total_penalties = filler_penalty + silence_penalty + repetition_penalty + boundary_penalty
        total_score = max(0.0, min(100.0, total_positive + virality_boost - total_penalties))

        virality_rationale = self._generate_virality_rationale(
            hook_score=hook_score,
            curiosity_score=curiosity_score,
            specificity_score=specificity_score,
            emotion_score=emotion_score,
            completeness_score=completeness_score,
            opinion_bomb_score=opinion_bomb_score,
            revelation_score=revelation_score,
        )

        breakdown = CandidateScoreBreakdown(
            hook_score=round(hook_score, 1),
            completeness_score=round(completeness_score, 1),
            curiosity_score=round(curiosity_score, 1),
            specificity_score=round(specificity_score, 1),
            emotion_score=round(emotion_score, 1),
            standalone_score=round(standalone_score, 1),
            visual_score=round(visual_score, 1),
            opinion_bomb_score=round(opinion_bomb_score, 1),
            revelation_score=round(revelation_score, 1),
            filler_penalty=round(filler_penalty, 1),
            silence_penalty=round(silence_penalty, 1),
            repetition_penalty=round(repetition_penalty, 1),
            boundary_penalty=round(boundary_penalty, 1),
            total_score=round(total_score, 1),
            virality_rationale=virality_rationale,
        )

        reasoning = (
            f"Hook={breakdown.hook_score:.0f}, Completeness={breakdown.completeness_score:.0f}, "
            f"Curiosity={breakdown.curiosity_score:.0f}, Specificity={breakdown.specificity_score:.0f}, "
            f"OpinionBomb={breakdown.opinion_bomb_score:.0f}, Revelation={breakdown.revelation_score:.0f}, "
            f"Penalties={total_penalties:.1f}. Rationale: {virality_rationale}"
        )

        return ClipScore(
            candidate_id=candidate.candidate_id,
            hook_strength=round(hook_score, 1),
            narrative_completeness=round(completeness_score, 1),
            curiosity_factor=round(curiosity_score, 1),
            campaign_relevance=100.0,
            overall_virality_score=round(total_score, 1),
            breakdown=breakdown,
            reasoning=reasoning,
        )

    def _generate_virality_rationale(
        self,
        hook_score: float,
        curiosity_score: float,
        specificity_score: float,
        emotion_score: float,
        completeness_score: float,
        opinion_bomb_score: float,
        revelation_score: float,
    ) -> str:
        """
        Synthesizes a concise, deterministic virality rationale
        derived strictly from detected signals without LLM hallucination.
        """
        has_hook = hook_score >= 60.0
        has_curiosity = curiosity_score >= 50.0
        has_opinion_bomb = opinion_bomb_score >= 35.0
        has_revelation = revelation_score >= 35.0
        has_emotion = emotion_score >= 50.0
        has_specificity = specificity_score >= 55.0
        has_completeness = completeness_score >= 70.0

        # Primary opening hook clause
        if has_hook and has_opinion_bomb:
            lead = "Opens with a strong contrarian opinion hook"
        elif has_hook and has_curiosity:
            lead = "Opens with a strong curiosity trigger"
        elif has_opinion_bomb:
            lead = "Delivers a provocative contrarian opinion"
        elif has_hook:
            lead = "Opens with a high-retention hook"
        elif has_curiosity:
            lead = "Introduces an intriguing curiosity gap"
        elif has_specificity:
            lead = "Opens with high-specificity data"
        elif has_emotion:
            lead = "Anchored in personal narrative"
        else:
            lead = "Topic-focused discussion segment"

        # Narrative trajectory / climax clause
        trailing = []
        if has_revelation:
            trailing.append("builds toward a counterintuitive revelation")
        if has_emotion and not (has_revelation and "narrative" in lead):
            trailing.append("high emotional resonance")
        if has_specificity and "data" not in lead:
            trailing.append("concrete real-world evidence")
        if has_completeness:
            trailing.append("clear standalone closure")

        if trailing:
            if len(trailing) == 1:
                return f"{lead} and {trailing[0]}."
            elif len(trailing) == 2:
                return f"{lead} with {trailing[0]} and {trailing[1]}."
            else:
                return f"{lead} featuring {trailing[0]}, {trailing[1]}, and {trailing[2]}."
        else:
            if lead != "Topic-focused discussion segment":
                return f"{lead} with steady pacing."
            return "Standard narrative segment with neutral engagement signals."
