"""Candidate Evaluation Engine for AL AMR.

Evaluates candidate highlights against structured campaign briefs.
Separates deterministic hard rules (rejection criteria) from soft rules (ranking penalties),
guaranteeing complete explainability with zero paid API costs.
"""

from __future__ import annotations

import re
from typing import Sequence

from ..pipeline.prepare import Silence
from ..pipeline.transcript import Transcript, Word
from .cta import analyze_cta
from .density import analyze_speech_density_and_silence
from .hook import analyze_hook
from .models import CampaignBrief, CandidateEvaluation, RuleResult


def _clean_tokens(text: str) -> set[str]:
    """Tokenize text into lowercase alphanumeric words."""
    return set(re.findall(r"\b\w+\b", text.lower()))


def _phrase_matches(phrase: str, full_text: str) -> bool:
    """Check if a word or multi-word phrase matches full_text with word boundaries.

    Guarantees that 'art' does NOT match 'partial', but 'AI' matches 'AI automation'.
    """
    pattern = r"\b" + re.escape(phrase.strip()) + r"\b"
    return bool(re.search(pattern, full_text, re.IGNORECASE))


class CampaignEvaluator:
    """Evaluates candidate highlights against a structured CampaignBrief."""

    def __init__(self, campaign: CampaignBrief) -> None:
        self.campaign = campaign

    def evaluate_candidate(
        self,
        *,
        candidate_id: str,
        start_s: float,
        end_s: float,
        words: Sequence[Word],
        base_viral_score: float,  # Normalized or 0-100
        silences: Sequence[Silence] | None = None,
        clip_title: str = "",
        clip_hook: str = "",
        clip_reason: str = "",
        speaker_count: int | None = None,
    ) -> CandidateEvaluation:
        """Run all hard and soft campaign rules on a single candidate highlight."""
        hard_failures: list[str] = []
        soft_warnings: list[str] = []
        rule_results: dict[str, RuleResult] = {}

        duration_s = max(0.1, end_s - start_s)
        full_text = " ".join(w.text for w in words).strip()
        combined_text = f"{full_text} {clip_title} {clip_hook} {clip_reason}"

        # -------------------------------------------------------------------
        # 1. DURATION (Hard Rule + Soft Preferred Warning)
        # -------------------------------------------------------------------
        dur_passed = (
            self.campaign.minimum_duration <= duration_s <= self.campaign.maximum_duration
        )
        if not dur_passed:
            hard_failures.append(
                f"Duration {duration_s:.1f}s is outside allowed range "
                f"[{self.campaign.minimum_duration:.1f}s - {self.campaign.maximum_duration:.1f}s]."
            )
        rule_results["duration"] = RuleResult(
            name="duration",
            passed=dur_passed,
            score=10.0 if dur_passed else 0.0,
            details=f"{duration_s:.1f}s (min: {self.campaign.minimum_duration}, max: {self.campaign.maximum_duration})",
            is_hard=True,
        )

        if dur_passed and self.campaign.preferred_duration is not None:
            diff = abs(duration_s - self.campaign.preferred_duration)
            if diff > 5.0:
                soft_warnings.append(
                    f"Duration {duration_s:.1f}s deviates by {diff:.1f}s from preferred {self.campaign.preferred_duration:.1f}s."
                )

        # -------------------------------------------------------------------
        # 2. BANNED WORDS (Hard Rule - Strict Word Boundary Matching)
        # -------------------------------------------------------------------
        found_banned: list[str] = []
        for banned in self.campaign.banned_words:
            if _phrase_matches(banned, full_text):
                found_banned.append(banned)

        if found_banned:
            hard_failures.append(f"Candidate contains banned words: {found_banned}.")
            rule_results["banned_words"] = RuleResult(
                name="banned_words",
                passed=False,
                score=0.0,
                details=f"Violations: {found_banned}",
                is_hard=True,
            )
        else:
            rule_results["banned_words"] = RuleResult(
                name="banned_words",
                passed=True,
                score=10.0,
                details="No banned words detected.",
                is_hard=True,
            )

        # -------------------------------------------------------------------
        # 3. BANNED TOPICS (Hard Rule)
        # -------------------------------------------------------------------
        found_banned_topics: list[str] = []
        for b_topic in self.campaign.banned_topics:
            if _phrase_matches(b_topic, combined_text):
                found_banned_topics.append(b_topic)

        if found_banned_topics:
            hard_failures.append(f"Candidate contains banned topics: {found_banned_topics}.")
            rule_results["banned_topics"] = RuleResult(
                name="banned_topics",
                passed=False,
                score=0.0,
                details=f"Violations: {found_banned_topics}",
                is_hard=True,
            )
        else:
            rule_results["banned_topics"] = RuleResult(
                name="banned_topics",
                passed=True,
                score=10.0,
                details="No banned topics detected.",
                is_hard=True,
            )

        # -------------------------------------------------------------------
        # 4. REQUIRED TOPICS / CONCEPTS (Hard Rule)
        # -------------------------------------------------------------------
        missing_required: list[str] = []
        for req in self.campaign.required_topics + self.campaign.required_concepts:
            if not _phrase_matches(req, combined_text):
                missing_required.append(req)

        if missing_required:
            hard_failures.append(f"Missing required topics/concepts: {missing_required}.")
            rule_results["required_topics"] = RuleResult(
                name="required_topics",
                passed=False,
                score=0.0,
                details=f"Missing: {missing_required}",
                is_hard=True,
            )
        else:
            rule_results["required_topics"] = RuleResult(
                name="required_topics",
                passed=True,
                score=10.0,
                details="All required topics/concepts present.",
                is_hard=True,
            )

        # -------------------------------------------------------------------
        # 5. HOOK EVALUATION (Hard or Soft Threshold)
        # -------------------------------------------------------------------
        hook_eval = analyze_hook(
            words,
            start_s,
            hook_window_s=self.campaign.hook_window_seconds,
        )
        hook_passed = True
        if self.campaign.hook_required:
            if hook_eval.score < self.campaign.minimum_hook_score:
                hook_passed = False
                hard_failures.append(
                    f"Hook score {hook_eval.score:.1f}/10 is below required minimum "
                    f"{self.campaign.minimum_hook_score:.1f}/10. ({hook_eval.reason})"
                )

        rule_results["hook"] = RuleResult(
            name="hook",
            passed=hook_passed,
            score=hook_eval.score,
            details=f"Score: {hook_eval.score}/10, Type: {hook_eval.hook_type}. {hook_eval.reason}",
            is_hard=self.campaign.hook_required,
        )

        # -------------------------------------------------------------------
        # 6. CALL TO ACTION (CTA) EVALUATION
        # -------------------------------------------------------------------
        cta_eval = analyze_cta(
            words,
            end_s,
            cta_window_s=self.campaign.cta_window_seconds,
            required_types=self.campaign.cta_types,
        )
        cta_passed = True
        if self.campaign.cta_required:
            if not cta_eval.has_cta or cta_eval.score < self.campaign.minimum_cta_score:
                cta_passed = False
                hard_failures.append(
                    f"Required CTA missing or below minimum score {self.campaign.minimum_cta_score:.1f}/10 "
                    f"(achieved {cta_eval.score:.1f}/10). {cta_eval.details}"
                )

        rule_results["cta"] = RuleResult(
            name="cta",
            passed=cta_passed,
            score=cta_eval.score,
            details=cta_eval.details,
            is_hard=self.campaign.cta_required,
        )

        # -------------------------------------------------------------------
        # 7. CONTENT DENSITY & DEAD AIR
        # -------------------------------------------------------------------
        density_eval = analyze_speech_density_and_silence(
            words,
            start_s,
            end_s,
            silences=silences,
            minimum_density=self.campaign.minimum_content_density,
            maximum_silence_s=self.campaign.maximum_silence_seconds,
        )
        density_passed = density_eval.max_silence_gap_s <= self.campaign.maximum_silence_seconds
        if not density_passed:
            hard_failures.append(
                f"Dead-air silence pause of {density_eval.max_silence_gap_s:.2f}s exceeds "
                f"hard limit of {self.campaign.maximum_silence_seconds:.2f}s."
            )

        if density_eval.words_per_second < self.campaign.minimum_content_density:
            soft_warnings.append(
                f"Speech density of {density_eval.words_per_second:.2f} w/s is below target "
                f"{self.campaign.minimum_content_density:.2f} w/s."
            )

        rule_results["density_and_silence"] = RuleResult(
            name="density_and_silence",
            passed=density_passed,
            score=density_eval.density_score,
            details=density_eval.details,
            is_hard=True,
        )

        # -------------------------------------------------------------------
        # 8. VIRAL & ENGAGEMENT THRESHOLDS
        # -------------------------------------------------------------------
        # Normalize viral score to 0.0 - 10.0 scale if provided as 0-100
        normalized_viral = (
            base_viral_score / 10.0 if base_viral_score > 10.0 else base_viral_score
        )
        viral_passed = normalized_viral >= self.campaign.minimum_viral_score
        if not viral_passed:
            hard_failures.append(
                f"Viral score {normalized_viral:.1f}/10 is below campaign threshold "
                f"{self.campaign.minimum_viral_score:.1f}/10."
            )

        rule_results["viral_score"] = RuleResult(
            name="viral_score",
            passed=viral_passed,
            score=normalized_viral,
            details=f"Normalized score: {normalized_viral:.2f}/10 (minimum: {self.campaign.minimum_viral_score:.1f})",
            is_hard=True,
        )

        # -------------------------------------------------------------------
        # 9. SPEAKER COUNT PREFERENCE (Soft Rule)
        # -------------------------------------------------------------------
        if (
            self.campaign.preferred_speaker_count is not None
            and speaker_count is not None
            and speaker_count != self.campaign.preferred_speaker_count
        ):
            soft_warnings.append(
                f"Speaker count ({speaker_count}) does not match preferred count "
                f"({self.campaign.preferred_speaker_count})."
            )

        # -------------------------------------------------------------------
        # 10. FINAL SCORE AGGREGATION
        # -------------------------------------------------------------------
        # Weighting:
        # Base Virality: 40%
        # Hook Quality:  35%
        # CTA:           15% (if cta_required, else 5% with 10% shifted to virality)
        # Density:       10%
        if self.campaign.cta_required:
            w_viral, w_hook, w_cta, w_density = 0.40, 0.35, 0.15, 0.10
        else:
            w_viral, w_hook, w_cta, w_density = 0.50, 0.35, 0.05, 0.10

        weighted_base = (
            normalized_viral * w_viral
            + hook_eval.score * w_hook
            + cta_eval.score * w_cta
            + density_eval.density_score * w_density
        )

        # Apply soft warning discount: each soft warning applies a small discount (5%)
        penalty_factor = max(0.5, 1.0 - (len(soft_warnings) * 0.05))
        final_score = round(weighted_base * penalty_factor, 2)

        approved = len(hard_failures) == 0

        return CandidateEvaluation(
            candidate_id=candidate_id,
            campaign_id=self.campaign.campaign_id,
            approved=approved,
            final_score=final_score if approved else 0.0,
            base_viral_score=round(normalized_viral, 2),
            hook_score=round(hook_eval.score, 2),
            cta_score=round(cta_eval.score, 2),
            density_score=round(density_eval.density_score, 2),
            hard_failures=hard_failures,
            soft_warnings=soft_warnings,
            rule_results={k: v.model_dump() for k, v in rule_results.items()},
        )
