"""Content Intelligence Engine for AL AMR: Best-Clip, Hook Optimization, Quality Scoring, and Diversity Selection.

Implements Objective B:
- 9-dimensional clip quality model (Hook, Content Value, Curiosity/Stakes, Emotional Impact,
  Info Density, Standalone/Cold-Start, Payoff, Narrative Coherence, Visual Potential).
- Concrete penalties for weak opening, context dependency, repetition, missing payoff, and dangling ends.
- Hook intelligence distinguishing Native Spoken Hook from Optional Editorial Headline Hook.
- Diversity Optimizer using Maximal Marginal Relevance (MMR) / Submodular selection across 5 clips.
- Structured selection rationale explaining why each clip was chosen.
- Remotion + FFmpeg V2 compatibility schema.
"""

from __future__ import annotations

import difflib
import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..pipeline.transcript import Word

log = logging.getLogger(__name__)

# Curiosity and Stakes patterns
CURIOSITY_PATTERNS = [
    r"\b(?:the secret|nobody talks about|never do this|why you should|stop doing|the truth about)\b",
    r"\b(?:most people don't know|what happens when|this changes everything|i was wrong|the real reason)\b",
    r"\b(?:biggest mistake|how to actually|the counter-intuitive|what they hide|rule number one)\b",
    r"\b(?:listen closely|here's what nobody understands|if you only do one thing)\b",
]

# High-stakes and emotional indicators
STAKES_WORDS = {
    "bankrupt", "million", "billion", "zero", "lose", "risk", "danger", "dead",
    "destroyed", "ruined", "survive", "scale", "profit", "revenue", "loss", "crash",
    "fired", "wealth", "secret", "trap", "massive", "catastrophic", "transform"
}

EMOTIONAL_WORDS = {
    "insane", "crazy", "shocking", "unbelievable", "brutal", "genius", "impossible",
    "worst", "best", "warning", "game-changer", "heartbreaking", "breakthrough",
    "obsessed", "devastating", "epic", "painful", "fascinating", "ridiculous"
}

# Concrete visual entity words triggering high visual potential
VISUAL_ENTITY_WORDS = {
    "money", "cash", "dollar", "screen", "computer", "phone", "chart", "graph",
    "contract", "office", "car", "building", "team", "meeting", "code", "database",
    "server", "dashboard", "product", "box", "factory", "document", "store", "customer"
}

# Dangling pronouns indicating context dependency if at the very start
DANGLING_OPENING_PRONOUNS = {
    "he", "she", "they", "him", "her", "them", "it", "his", "their", "this", "that", "these", "those"
}

# Conversational intro filler patterns to penalize
FILLER_INTRO_PATTERNS = [
    r"^(?:so|and|but|like|you know|well|i mean|um|uh|yeah)\b",
    r"^(?:welcome back|hey guys|in this video|today we|what's up)\b",
]

# Dangling terminal words
DANGLING_TERMINAL_WORDS = {
    "and", "but", "or", "because", "if", "that", "which", "when", "where",
    "who", "while", "as", "than", "with", "for", "to", "at", "by", "from",
    "in", "into", "of", "about", "the", "a", "an"
}


@dataclass
class QualityBreakdown:
    hook_strength: float = 0.0
    content_value: float = 0.0
    curiosity_stakes: float = 0.0
    emotional_impact: float = 0.0
    information_density: float = 0.0
    standalone_completeness: float = 0.0
    payoff_strength: float = 0.0
    narrative_coherence: float = 0.0
    visual_potential: float = 0.0
    penalties: float = 0.0
    composite_score: float = 0.0
    penalty_details: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "hook_strength": round(self.hook_strength, 1),
            "content_value": round(self.content_value, 1),
            "curiosity_stakes": round(self.curiosity_stakes, 1),
            "emotional_impact": round(self.emotional_impact, 1),
            "information_density": round(self.information_density, 1),
            "standalone_completeness": round(self.standalone_completeness, 1),
            "payoff_strength": round(self.payoff_strength, 1),
            "narrative_coherence": round(self.narrative_coherence, 1),
            "visual_potential": round(self.visual_potential, 1),
            "penalties": round(self.penalties, 1),
            "composite_score": round(self.composite_score, 1),
            "penalty_details": self.penalty_details,
        }


@dataclass
class HookAnalysis:
    native_hook: str  # Verbatim spoken words in opening 1-5s
    editorial_hook: str  # Punchy on-screen headline (<=60 chars)
    hook_type: str  # question, bold_claim, counter_intuitive, etc.
    hook_duration_s: float
    hook_strength_score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "native_hook": self.native_hook,
            "editorial_hook": self.editorial_hook,
            "hook_type": self.hook_type,
            "hook_duration_s": round(self.hook_duration_s, 2),
            "hook_strength_score": round(self.hook_strength_score, 1),
        }


@dataclass
class SelectionRationale:
    rank: int
    clip_id: str
    composite_score: float
    primary_strength: str
    hook: HookAnalysis
    quality: QualityBreakdown
    diversity_distance: float
    v2_engine_telemetry: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "clip_id": self.clip_id,
            "composite_score": round(self.composite_score, 1),
            "primary_strength": self.primary_strength,
            "hook": self.hook.to_dict(),
            "quality": self.quality.to_dict(),
            "diversity_distance": round(self.diversity_distance, 3),
            "v2_engine_telemetry": self.v2_engine_telemetry,
        }


class ClipQualityModel:
    """Evaluates candidates along 9 objective quality dimensions and applies strict penalties."""

    def evaluate_clip(
        self,
        words: Sequence[Word],
        duration_s: float,
        full_transcript_text: str = "",
    ) -> tuple[QualityBreakdown, HookAnalysis]:
        if not words:
            return QualityBreakdown(), HookAnalysis("", "", "none", 0.0, 0.0)

        clip_text = " ".join(w.text for w in words).strip()
        clip_lower = clip_text.lower()
        word_count = len(words)

        # ------------------------------------------------------------------
        # 1. HOOK STRENGTH & HOOK INTELLIGENCE (First 1-5 seconds)
        # ------------------------------------------------------------------
        first_words = [w for w in words if (w.start - words[0].start) <= 5.0]
        if not first_words:
            first_words = list(words[:min(10, len(words))])

        native_hook = " ".join(w.text for w in first_words).strip()
        hook_duration = first_words[-1].end - first_words[0].start if first_words else 0.0
        native_lower = native_hook.lower()

        hook_type = "statement"
        base_hook_score = 65.0

        if "?" in native_hook or any(native_lower.startswith(q) for q in ("why", "how", "what if", "did you know")):
            hook_type = "curiosity_question"
            base_hook_score = 88.0
        elif any(re.search(pat, native_lower) for pat in CURIOSITY_PATTERNS):
            hook_type = "counter_intuitive"
            base_hook_score = 92.0
        elif any(w in STAKES_WORDS for w in re.findall(r"\b\w+\b", native_lower)):
            hook_type = "high_stakes_claim"
            base_hook_score = 90.0
        elif any(w in EMOTIONAL_WORDS for w in re.findall(r"\b\w+\b", native_lower)):
            hook_type = "emotional_trigger"
            base_hook_score = 85.0
        elif re.search(r"\b(?:rule|step|framework|formula|how we)\b", native_lower):
            hook_type = "actionable_rule"
            base_hook_score = 86.0

        # Synthesize Optional Editorial Hook (punchy on-screen headline <= 55 chars)
        editorial_hook = self._generate_editorial_hook(native_hook, clip_text)

        hook_analysis = HookAnalysis(
            native_hook=native_hook,
            editorial_hook=editorial_hook,
            hook_type=hook_type,
            hook_duration_s=hook_duration,
            hook_strength_score=base_hook_score,
        )

        # ------------------------------------------------------------------
        # 2. CONTENT VALUE (Actionable takeaways, concrete concepts)
        # ------------------------------------------------------------------
        number_count = len(re.findall(r"\b\d+(?:\.\d+)?(?:k|m|%)?\b", clip_lower))
        concept_markers = len(re.findall(r"\b(?:because|means|strategy|system|process|principle|result|mistake)\b", clip_lower))
        content_val = min(100.0, 60.0 + (number_count * 8.0) + (concept_markers * 6.0))

        # ------------------------------------------------------------------
        # 3. CURIOSITY & STAKES
        # ------------------------------------------------------------------
        curiosity_hits = sum(1 for pat in CURIOSITY_PATTERNS if re.search(pat, clip_lower))
        stakes_hits = sum(1 for w in STAKES_WORDS if re.search(rf"\b{w}\b", clip_lower))
        curiosity_stakes = min(100.0, 50.0 + (curiosity_hits * 18.0) + (stakes_hits * 10.0))

        # ------------------------------------------------------------------
        # 4. EMOTIONAL IMPACT
        # ------------------------------------------------------------------
        emotional_hits = sum(1 for w in EMOTIONAL_WORDS if re.search(rf"\b{w}\b", clip_lower))
        emotional_impact = min(100.0, 55.0 + (emotional_hits * 12.0))

        # ------------------------------------------------------------------
        # 5. INFORMATION DENSITY (Words/sec pace sweet spot 2.2 - 3.8)
        # ------------------------------------------------------------------
        wps = word_count / max(0.1, duration_s)
        if 2.2 <= wps <= 3.8:
            info_density = 95.0
        elif 1.8 <= wps < 2.2 or 3.8 < wps <= 4.4:
            info_density = 80.0
        else:
            info_density = 60.0

        # ------------------------------------------------------------------
        # 6. STANDALONE COMPLETENESS / COLD-START STRENGTH
        # ------------------------------------------------------------------
        first_clean_word = re.sub(r"[^\w]", "", words[0].text.strip().lower())
        cold_start = 90.0

        # Check for dangling opening pronouns referring to unseen context
        if first_clean_word in DANGLING_OPENING_PRONOUNS:
            cold_start -= 25.0
        if any(clip_lower.startswith(p) for p in ("as i said", "like i mentioned", "so again", "and then")):
            cold_start -= 25.0

        # ------------------------------------------------------------------
        # 7. PAYOFF STRENGTH (Hook resolution & climax punchline)
        # ------------------------------------------------------------------
        last_words = words[max(0, len(words) - 10):]
        last_text = " ".join(w.text for w in last_words).lower()
        has_payoff_resolution = any(
            w in last_text for w in ("that's why", "result", "finally", "which is", "happens", "now you", "bottom line")
        ) or any(last_words[-1].text.strip().endswith(p) for p in (".", "!", "?"))
        payoff_strength = 90.0 if has_payoff_resolution else 65.0

        # ------------------------------------------------------------------
        # 8. NARRATIVE COHERENCE
        # ------------------------------------------------------------------
        coherence = 90.0
        if not words[0].text.strip()[0].isupper() and not words[0].text.strip().startswith(("\"", "'")):
            coherence -= 10.0
        if not any(words[-1].text.strip().endswith(p) for p in (".", "!", "?")):
            coherence -= 15.0

        # ------------------------------------------------------------------
        # 9. VISUAL POTENTIAL (Concrete nouns and visual entity triggers)
        # ------------------------------------------------------------------
        visual_entities = sum(1 for w in VISUAL_ENTITY_WORDS if re.search(rf"\b{w}\b", clip_lower))
        visual_potential = min(100.0, 50.0 + (visual_entities * 12.0))

        # ------------------------------------------------------------------
        # PENALTIES
        # ------------------------------------------------------------------
        penalties = 0.0
        penalty_details: list[str] = []

        # Penalty A: Weak Opening / Rambling Intro
        for pat in FILLER_INTRO_PATTERNS:
            if re.search(pat, native_lower):
                penalties += 15.0
                penalty_details.append("weak_opening_filler")
                break

        # Penalty B: Context Dependency (Dangling pronoun at start)
        first_few_clean = [re.sub(r"[^\w]", "", w.text.strip().lower()) for w in words[:min(3, len(words))]]
        dangling_found = [p for p in first_few_clean if p in DANGLING_OPENING_PRONOUNS]
        if dangling_found:
            penalties += 15.0
            penalty_details.append(f"context_dependency_pronoun({dangling_found[0]})")

        # Penalty C: Repetition & Stuttering
        word_list = [w.text.strip().lower() for w in words]
        repeated_consecutive = sum(1 for i in range(len(word_list) - 1) if word_list[i] == word_list[i + 1] and len(word_list[i]) > 2)
        if repeated_consecutive > 1:
            penalties += 10.0
            penalty_details.append(f"repetition_stutter({repeated_consecutive})")

        # Penalty D: No Payoff / Cliffhanger ending abruptly
        if not any(words[-1].text.strip().endswith(p) for p in (".", "!", "?")):
            last_clean = re.sub(r"[^\w]", "", words[-1].text.strip().lower())
            if last_clean in DANGLING_TERMINAL_WORDS:
                penalties += 20.0
                penalty_details.append(f"dangling_terminal_word({last_clean})")

        # ------------------------------------------------------------------
        # COMPOSITE SCORE
        # ------------------------------------------------------------------
        composite = (
            (base_hook_score * 0.22)
            + (content_val * 0.16)
            + (curiosity_stakes * 0.14)
            + (payoff_strength * 0.14)
            + (cold_start * 0.10)
            + (emotional_impact * 0.08)
            + (info_density * 0.06)
            + (coherence * 0.05)
            + (visual_potential * 0.05)
        ) - penalties

        composite_score = max(0.0, min(100.0, round(composite, 1)))

        breakdown = QualityBreakdown(
            hook_strength=base_hook_score,
            content_value=content_val,
            curiosity_stakes=curiosity_stakes,
            emotional_impact=emotional_impact,
            information_density=info_density,
            standalone_completeness=cold_start,
            payoff_strength=payoff_strength,
            narrative_coherence=coherence,
            visual_potential=visual_potential,
            penalties=penalties,
            composite_score=composite_score,
            penalty_details=penalty_details,
        )

        return breakdown, hook_analysis

    def _generate_editorial_hook(self, native_hook: str, clip_text: str) -> str:
        """Synthesizes a high-impact, punchy on-screen headline (<=55 chars)."""
        clean_native = re.sub(r"\.{2,}", "", native_hook).strip().rstrip(".!?,;: ")
        # Strip common spoken conversational fillers from start
        fillers = (
            "so basically", "you know", "i mean", "like", "and so", "well,", "well",
            "actually", "honestly", "look,", "listen,"
        )
        lower_native = clean_native.lower()
        for f in fillers:
            if lower_native.startswith(f):
                clean_native = clean_native[len(f):].strip().lstrip(",;: ")
                lower_native = clean_native.lower()
                break

        # Look for peak phrase in native hook
        m = re.search(r"\b(?:the secret|why you should|stop doing|the truth about|biggest mistake|how to|the reason why|how we built|the key to)\s+[^.,?!]{5,35}", native_hook, re.IGNORECASE)
        if m:
            headline = m.group(0).strip().rstrip(".!?,;: ")
            return headline[:50].title()

        if len(clean_native) <= 50 and any(clean_native.lower().startswith(q) for q in ("why", "how", "what", "the", "never", "this")):
            return clean_native.title() if clean_native.islower() else clean_native

        # Fallback to concise native snippet without mid-sentence cut-offs
        words = clean_native.split()
        if len(words) > 7:
            words = words[:7]
        # Drop dangling prepositions or conjunctions at the end
        while words and words[-1].lower() in ("and", "or", "so", "the", "a", "an", "to", "of", "in", "for", "with", "is", "at", "by", "that"):
            words.pop()
        short_snippet = " ".join(words).strip()
        if short_snippet.islower():
            short_snippet = short_snippet.title()
        return short_snippet or "Unfiltered Founder Insight"


class DiversityOptimizer:
    """Selects the optimal SET of 5 clips using Maximal Marginal Relevance (MMR)

    and submodular set coverage to eliminate duplicate topics and ensure broad source coverage.
    """

    def __init__(self, lambda_balance: float = 0.70) -> None:
        # lambda_balance: weight for quality (0.70) vs diversity distance (0.30)
        self.lambda_balance = lambda_balance

    def compute_similarity(self, clip_a_text: str, clip_a_start: float, clip_a_end: float,
                           clip_b_text: str, clip_b_start: float, clip_b_end: float) -> float:
        """Computes multi-faceted similarity (temporal IoU + bi-gram Jaccard)."""
        # Temporal IoU
        intersection = max(0.0, min(clip_a_end, clip_b_end) - max(clip_a_start, clip_b_start))
        union = max(0.1, max(clip_a_end, clip_b_end) - min(clip_a_start, clip_b_start))
        temporal_iou = intersection / union

        # Text Bi-gram Jaccard Similarity
        words_a = re.findall(r"\b\w+\b", clip_a_text.lower())
        words_b = re.findall(r"\b\w+\b", clip_b_text.lower())

        bigrams_a = set(zip(words_a[:-1], words_a[1:])) if len(words_a) > 1 else set(words_a)
        bigrams_b = set(zip(words_b[:-1], words_b[1:])) if len(words_b) > 1 else set(words_b)

        if not bigrams_a or not bigrams_b:
            text_sim = 0.0
        else:
            jaccard = len(bigrams_a & bigrams_b) / len(bigrams_a | bigrams_b)
            text_sim = jaccard

        # Combined similarity score
        return max(temporal_iou, (0.5 * temporal_iou) + (0.5 * text_sim))

    def select_diverse_set(
        self,
        candidate_items: list[dict[str, Any]],
        target_count: int = 5,
    ) -> list[dict[str, Any]]:
        """Greedy MMR selection picking the top 5 diverse, high-quality candidates."""
        if len(candidate_items) <= target_count:
            return candidate_items

        unselected = list(candidate_items)
        selected: list[dict[str, Any]] = []

        # 1. Pick the single highest scoring candidate first
        unselected.sort(key=lambda x: x["quality"].composite_score, reverse=True)
        first_pick = unselected.pop(0)
        first_pick["diversity_distance"] = 1.0
        selected.append(first_pick)

        # 2. Iteratively pick the candidate maximizing MMR:
        # MMR = lambda * Quality - (1 - lambda) * max_similarity_to_selected
        while len(selected) < target_count and unselected:
            best_cand = None
            best_mmr_score = -float("inf")
            best_idx = -1
            best_div_dist = 1.0

            for idx, cand in enumerate(unselected):
                quality_norm = cand["quality"].composite_score / 100.0

                max_sim = 0.0
                for sel in selected:
                    sim = self.compute_similarity(
                        cand["text"], cand["start_s"], cand["end_s"],
                        sel["text"], sel["start_s"], sel["end_s"]
                    )
                    if sim > max_sim:
                        max_sim = sim

                div_dist = 1.0 - max_sim
                mmr_score = (self.lambda_balance * quality_norm) + ((1.0 - self.lambda_balance) * div_dist)

                if mmr_score > best_mmr_score:
                    best_mmr_score = mmr_score
                    best_cand = cand
                    best_idx = idx
                    best_div_dist = div_dist

            if best_cand is not None and best_idx >= 0:
                best_cand["diversity_distance"] = best_div_dist
                selected.append(best_cand)
                unselected.pop(best_idx)
            else:
                break

        return selected


def build_v2_engine_telemetry(
    clip_id: str,
    start_s: float,
    end_s: float,
    hook: HookAnalysis,
    quality: QualityBreakdown,
) -> dict[str, Any]:
    """Exposes structured metadata for future Remotion & FFmpeg V2 rendering."""
    duration_s = max(0.1, end_s - start_s)
    return {
        "engine_version": "v2_ready",
        "clip_id": clip_id,
        "timeline": {
            "start_time_s": round(start_s, 2),
            "end_time_s": round(end_s, 2),
            "duration_s": round(duration_s, 2),
            "hook_duration_s": round(hook.hook_duration_s, 2),
        },
        "hook_overlay": {
            "native_hook_text": hook.native_hook,
            "editorial_headline": hook.editorial_hook,
            "hook_type": hook.hook_type,
            "display_duration_s": min(4.5, hook.hook_duration_s),
            "suggested_animation": "slide_down_fade",
        },
        "visual_cues": {
            "visual_potential_score": quality.visual_potential,
            "b_roll_recommended": quality.visual_potential >= 70.0,
            "energy_level": "high" if quality.emotional_impact >= 75.0 else "medium",
        },
        "audio_profile": {
            "target_lufs": -14.0,
            "speech_density": quality.information_density,
        },
    }
