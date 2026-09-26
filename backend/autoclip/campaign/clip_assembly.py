"""Campaign-aware clip assembly, smart boundaries, and pre-render quality gate.

Step 16: Takes Top-N selected ClipCandidates from Step 15, applies natural speech
boundary optimization, hook enhancement, climax/payoff protection, CTA preservation,
silence cleanup, and runs a deterministic Pre-Render Quality Gate before rendering.
"""

from __future__ import annotations

import difflib
import logging
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from autoclip.campaign.candidate_discovery import (
    INTRO_GREETING_PATTERNS,
    SUBSTANTIVE_HOOK_PATTERNS,
    compute_iou,
    compute_text_similarity,
)
from autoclip.campaign.extractor import ENGLISH_STOPWORDS
from autoclip.campaign.models_intelligence import CampaignSpecification, RequirementItem
from autoclip.campaign.models import CampaignBrief
from autoclip.db.models import Clip, ClipCandidateRecord, ClipSpecificationRecord, new_id, utcnow
from autoclip.pipeline.prepare import Silence
from autoclip.pipeline.transcript import Transcript, Word

log = logging.getLogger(__name__)

# Common filler tokens and prefixes that degrade clip opening quality
FILLER_TOKENS: set[str] = {
    "so", "and", "but", "well", "you know", "like", "actually",
    "basically", "now", "i mean", "okay", "um", "uh", "right",
    "anyway", "anyways", "so basically", "so like", "see", "look",
}

# Words that strictly cannot end a sentence under any circumstance (articles, prepositions, conjunctions)
ALWAYS_DANGLING_TOKENS: set[str] = {
    "and", "but", "or", "because", "if", "that", "which", "when",
    "where", "who", "whom", "whose", "while", "though", "although", "since",
    "as", "than", "with", "for", "to", "at", "by", "from", "in", "into",
    "onto", "of", "about", "the", "a", "an", "their", "my", "your",
    "our", "its", "such",
}

# Conversational words that only dangle if unpunctuated; with terminal punctuation (. ! ?) they represent complete thoughts
CONDITIONAL_DANGLING_TOKENS: set[str] = {
    "you", "know", "well", "actually", "basically", "just", "really", "even",
    "so", "then", "like", "her", "his", "him", "me", "them", "us", "it",
}

DANGLING_END_TOKENS: set[str] = ALWAYS_DANGLING_TOKENS | CONDITIONAL_DANGLING_TOKENS

SENTENCE_TERMINALS: set[str] = {".", "!", "?", ";"}


def is_true_sentence_terminal(word_text: str) -> bool:
    """True sentence terminals (. ! ? ;) excluding trailing ellipses (...) and hesitation."""
    wt = word_text.strip()
    if wt.endswith("...") or wt.endswith("…"):
        return False
    return any(wt.endswith(p) for p in SENTENCE_TERMINALS)


HOOK_PATTERNS: list[tuple[str, str, float]] = [
    (r"\b(why|how|what if|have you ever|did you know|who is|can you)\b", "question", 9.2),
    (r"\b(the secret|nobody tells you|stop doing|never do|biggest mistake|truth about)\b", "bold_claim", 9.5),
    (r"\b(shocking|insane|unbelievable|crazy fact|millionaire|billionaire|broke the internet)\b", "surprising_fact", 9.0),
    (r"\b(controversial|everyone is wrong|lying to you|don't believe|scam|myth)\b", "controversy", 9.4),
    (r"\b(changed my life|ruined everything|i regret|heartbroken|worst nightmare|tears)\b", "emotional_statement", 8.8),
    (r"\b(this one thing|here's what happened|wait until the end|the real reason)\b", "curiosity_gap", 8.9),
    (r"\b(in this video|today we will|let's talk about|welcome back|first of all)\b", "narrative_setup", 6.0),
]

CTA_PATTERNS: list[tuple[str, str]] = [
    (r"\b(link in bio|check the link|link below|click the link)\b", "link_in_bio"),
    (r"\b(subscribe|sub to|hit subscribe|follow for more|follow us|follow me)\b", "follow_subscribe"),
    (r"\b(join our community|join the discord|join us|sign up)\b", "community_signup"),
    (r"\b(download now|get the app|grab your copy|check it out)\b", "download_product"),
    (r"\b(comment below|drop a comment|let me know in the comments|share this)\b", "engagement"),
]


@dataclass
class BoundaryOptimization:
    original_start_s: float
    original_end_s: float
    optimized_start_s: float
    optimized_end_s: float
    start_word: int
    end_word: int
    duration_s: float
    hook_start_s: float
    hook_end_s: float
    hook_type: str
    hook_score: float
    climax_start_s: float | None
    climax_end_s: float | None
    cta_start_s: float | None
    cta_end_s: float | None
    cta_type: str
    adjustments: list[str] = field(default_factory=list)
    start_delta_s: float = 0.0
    end_delta_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_start_s": round(self.original_start_s, 2),
            "original_end_s": round(self.original_end_s, 2),
            "optimized_start_s": round(self.optimized_start_s, 2),
            "optimized_end_s": round(self.optimized_end_s, 2),
            "start_word": self.start_word,
            "end_word": self.end_word,
            "duration_s": round(self.duration_s, 2),
            "start_delta_s": round(self.start_delta_s, 2),
            "end_delta_s": round(self.end_delta_s, 2),
            "adjustments": self.adjustments,
        }


class SmartBoundaryEngine:
    """Intelligently detects speech boundaries, trims filler, avoids mid-sentence cuts,
    and protects narrative climax/CTA milestones.
    """

    def __init__(
        self,
        padding_s: float = 0.08,
        min_pause_s: float = 0.35,
        max_silence_trim_s: float = 2.0,
    ) -> None:
        self.padding_s = padding_s
        self.min_pause_s = min_pause_s
        self.max_silence_trim_s = max_silence_trim_s

    def optimize(
        self,
        candidate: ClipCandidateRecord,
        transcript: Transcript,
        silences: list[Silence] | None = None,
        duration_min_s: float = 20.0,
        duration_max_s: float = 60.0,
        require_cta: bool = False,
    ) -> BoundaryOptimization:
        all_words = transcript.words
        total_words = len(all_words)
        if total_words == 0:
            return BoundaryOptimization(
                original_start_s=candidate.start_s,
                original_end_s=candidate.end_s,
                optimized_start_s=candidate.start_s,
                optimized_end_s=candidate.end_s,
                start_word=0,
                end_word=0,
                duration_s=candidate.duration_s,
                hook_start_s=candidate.start_s,
                hook_end_s=min(candidate.end_s, candidate.start_s + 4.0),
                hook_type="none",
                hook_score=0.0,
                climax_start_s=None,
                climax_end_s=None,
                cta_start_s=None,
                cta_end_s=None,
                cta_type="",
                adjustments=["empty_transcript"],
            )

        start_idx = max(0, min(candidate.start_word, total_words - 1))
        end_idx = max(start_idx, min(candidate.end_word, total_words - 1))

        # Re-resolve word indices by timestamp if candidate word index is out of bounds
        if all_words[start_idx].start > candidate.start_s + 2.0 or all_words[start_idx].end < candidate.start_s - 2.0:
            start_idx = self._find_word_index_for_time(all_words, candidate.start_s)
        if all_words[end_idx].end > candidate.end_s + 2.0 or all_words[end_idx].start < candidate.end_s - 2.0:
            end_idx = self._find_word_index_for_time(all_words, candidate.end_s, is_end=True)

        if end_idx <= start_idx:
            end_idx = min(total_words - 1, start_idx + 15)

        adjustments: list[str] = []

        # Ensure candidate initial span reaches at least duration_min_s if transcript has enough material
        current_dur = all_words[end_idx].end - all_words[start_idx].start
        if current_dur < duration_min_s:
            for cand_end in range(end_idx + 1, total_words):
                new_dur = all_words[cand_end].end - all_words[start_idx].start
                if new_dur > duration_max_s:
                    break
                if any(all_words[cand_end].text.strip().endswith(p) for p in SENTENCE_TERMINALS):
                    end_idx = cand_end
                    if new_dur >= duration_min_s:
                        adjustments.append(f"expanded_to_sentence_boundary({new_dur:.1f}s)")
                        break
            current_dur = all_words[end_idx].end - all_words[start_idx].start
            if current_dur < duration_min_s and end_idx < total_words - 1:
                for cand_end in range(end_idx + 1, total_words):
                    if all_words[cand_end].end - all_words[start_idx].start >= duration_min_s:
                        end_idx = cand_end
                        adjustments.append("expanded_to_meet_min_duration")
                        break

        # ------------------------------------------------------------------
        # 1. Filler Stripping & Natural Sentence Opening
        # ------------------------------------------------------------------
        while start_idx < end_idx:
            # Check two-word filler phrases ("you know", "so basically")
            if start_idx + 1 < end_idx:
                two_word = f"{all_words[start_idx].text} {all_words[start_idx+1].text}".strip().lower()
                two_word_clean = re.sub(r"[^\w\s]", "", two_word)
                if two_word_clean in FILLER_TOKENS or two_word_clean in ("you know", "i mean", "so like", "so basically"):
                    test_dur = all_words[end_idx].end - all_words[start_idx + 2].start
                    if test_dur >= duration_min_s:
                        start_idx += 2
                        adjustments.append(f"stripped_filler_phrase({two_word_clean})")
                        continue

            # Check single-word filler
            first_word_clean = re.sub(r"[^\w]", "", all_words[start_idx].text.strip().lower())
            if first_word_clean in FILLER_TOKENS:
                test_dur = all_words[end_idx].end - all_words[start_idx + 1].start
                if test_dur >= duration_min_s:
                    start_idx += 1
                    adjustments.append(f"stripped_filler_word({first_word_clean})")
                    continue
            break

        # ------------------------------------------------------------------
        # 2. Hook Optimization (Analyze & Shift if Superior Hook in 0-5s)
        # ------------------------------------------------------------------
        hook_type, hook_score, hook_start_w, hook_end_w = self._evaluate_hook(
            all_words, start_idx, min(end_idx, start_idx + 25)
        )

        # If opening is weak narrative setup or conversational filler, but a punchy question/claim starts 1.5-4s in
        if hook_type in ("weak/none", "narrative_setup") and hook_start_w > start_idx:
            test_dur = all_words[end_idx].end - all_words[hook_start_w].start
            if test_dur >= duration_min_s:
                is_clean_shift = False
                prev_word = all_words[hook_start_w - 1]
                curr_word = all_words[hook_start_w]
                if (curr_word.start - prev_word.end) >= 0.25:
                    is_clean_shift = True
                elif prev_word.text.endswith(tuple(SENTENCE_TERMINALS)):
                    is_clean_shift = True

                if is_clean_shift:
                    start_idx = hook_start_w
                    adjustments.append(f"shifted_hook_start_forward({hook_type}->{hook_score:.1f})")
                    hook_type, hook_score, hook_start_w, hook_end_w = self._evaluate_hook(
                        all_words, start_idx, min(end_idx, start_idx + 25)
                    )

        # ------------------------------------------------------------------
        # 3. Incomplete Thought Guard & Sentence Terminal Snapping
        # ------------------------------------------------------------------
        while end_idx > start_idx:
            last_word_raw = all_words[end_idx].text.strip()
            last_word_clean = re.sub(r"[^\w]", "", last_word_raw.lower())
            is_terminal = is_true_sentence_terminal(last_word_raw)
            is_dangling = (last_word_clean in DANGLING_END_TOKENS) or last_word_raw.endswith("...") or last_word_raw.endswith("…")

            if is_dangling and (not is_terminal or last_word_clean in DANGLING_END_TOKENS):
                test_dur = all_words[end_idx - 1].end - all_words[start_idx].start
                if test_dur >= duration_min_s:
                    end_idx -= 1
                    adjustments.append(f"trimmed_dangling_end_token({last_word_clean or 'ellipsis'})")
                    continue
                else:
                    break
            else:
                break

        # Look backward up to 6 words to snap to a complete sentence terminal if close
        if not is_true_sentence_terminal(all_words[end_idx].text):
            for step_back in range(1, min(7, end_idx - start_idx)):
                candidate_terminal_word = all_words[end_idx - step_back]
                if is_true_sentence_terminal(candidate_terminal_word.text):
                    test_dur = candidate_terminal_word.end - all_words[start_idx].start
                    if test_dur >= duration_min_s:
                        end_idx = end_idx - step_back
                        adjustments.append(f"snapped_backward_to_sentence_terminal({candidate_terminal_word.text.strip()})")
                        break

        # ------------------------------------------------------------------
        # 4. Climax / Payoff Protection
        # ------------------------------------------------------------------
        climax_sig = candidate.climax_signals or {}
        climax_start_s = climax_sig.get("start_s")
        climax_end_s = climax_sig.get("end_s")
        if climax_start_s is not None and climax_end_s is not None:
            if all_words[end_idx].end < climax_end_s:
                target_end = self._find_word_index_for_time(all_words, climax_end_s, is_end=True)
                test_dur = all_words[target_end].end - all_words[start_idx].start
                if test_dur <= duration_max_s:
                    end_idx = target_end
                    adjustments.append("expanded_boundary_to_protect_climax")
                    while end_idx < total_words - 1 and (all_words[end_idx].end - all_words[start_idx].start) <= duration_max_s:
                        if any(all_words[end_idx].text.strip().endswith(p) for p in SENTENCE_TERMINALS):
                            break
                        end_idx += 1

        # ------------------------------------------------------------------
        # 5. CTA Protection (If Required or Naturally Present)
        # ------------------------------------------------------------------
        cta_sig = candidate.cta_signals or {}
        cta_start_s = cta_sig.get("start_s")
        cta_end_s = cta_sig.get("end_s")
        cta_type = cta_sig.get("type", "")

        if not cta_type:
            tail_slice = all_words[max(start_idx, end_idx - 15) : end_idx + 1]
            tail_text = " ".join(w.text for w in tail_slice)
            for pattern, c_type in CTA_PATTERNS:
                m = re.search(pattern, tail_text, re.IGNORECASE)
                if m:
                    cta_type = c_type
                    cta_start_s = tail_slice[0].start
                    cta_end_s = tail_slice[-1].end
                    adjustments.append(f"detected_tail_cta({cta_type})")
                    break

        if require_cta and cta_end_s is not None and all_words[end_idx].end < cta_end_s:
            target_end = self._find_word_index_for_time(all_words, cta_end_s, is_end=True)
            test_dur = all_words[target_end].end - all_words[start_idx].start
            if test_dur <= duration_max_s:
                end_idx = target_end
                adjustments.append("expanded_boundary_to_preserve_required_cta")

        # Post-Expansion Incomplete Thought Guard: ensure boundary expansions didn't end on dangling tokens
        while end_idx > start_idx:
            last_word_raw = all_words[end_idx].text.strip()
            last_word_clean = re.sub(r"[^\w]", "", last_word_raw.lower())
            is_terminal = is_true_sentence_terminal(last_word_raw)
            is_dangling = (
                last_word_clean in ALWAYS_DANGLING_TOKENS
                or (last_word_clean in CONDITIONAL_DANGLING_TOKENS and not is_terminal)
                or last_word_raw.endswith("...")
                or last_word_raw.endswith("…")
            )
            if is_dangling:
                test_dur = all_words[end_idx - 1].end - all_words[start_idx].start
                if test_dur >= duration_min_s:
                    end_idx -= 1
                    adjustments.append(f"trimmed_dangling_end_token({last_word_clean or 'ellipsis'})")
                    continue
                break
            break

        # ------------------------------------------------------------------
        # 6. Padding & Silence Cleanup
        # ------------------------------------------------------------------
        speech_start = all_words[start_idx].start
        speech_end = all_words[end_idx].end

        prev_boundary = all_words[start_idx - 1].end if start_idx > 0 else 0.0
        next_boundary = all_words[end_idx + 1].start if end_idx < total_words - 1 else speech_end + 5.0

        opt_start_s = max(prev_boundary, speech_start - self.padding_s)
        opt_end_s = min(next_boundary, speech_end + self.padding_s)
        final_duration_s = max(0.1, opt_end_s - opt_start_s)

        hook_start_time = speech_start
        hook_end_time = min(opt_end_s, all_words[min(end_idx, start_idx + 8)].end)

        return BoundaryOptimization(
            original_start_s=candidate.start_s,
            original_end_s=candidate.end_s,
            optimized_start_s=opt_start_s,
            optimized_end_s=opt_end_s,
            start_word=start_idx,
            end_word=end_idx,
            duration_s=final_duration_s,
            hook_start_s=hook_start_time,
            hook_end_s=hook_end_time,
            hook_type=hook_type,
            hook_score=hook_score,
            climax_start_s=climax_start_s,
            climax_end_s=climax_end_s,
            cta_start_s=cta_start_s,
            cta_end_s=cta_end_s,
            cta_type=cta_type,
            adjustments=adjustments,
            start_delta_s=opt_start_s - candidate.start_s,
            end_delta_s=opt_end_s - candidate.end_s,
        )

    def _find_word_index_for_time(
        self, words: list[Word], target_time: float, is_end: bool = False
    ) -> int:
        if not words:
            return 0
        if is_end:
            for idx in range(len(words) - 1, -1, -1):
                if words[idx].end <= target_time + 0.5:
                    return idx
            return len(words) - 1
        for idx in range(len(words)):
            if words[idx].start >= target_time - 0.5:
                return idx
        return 0

    def _evaluate_hook(
        self, words: list[Word], start_idx: int, max_idx: int
    ) -> tuple[str, float, int, int]:
        slice_words = words[start_idx : max_idx + 1]
        if not slice_words:
            return ("weak/none", 4.0, start_idx, start_idx)

        slice_text = " ".join(w.text for w in slice_words)

        best_type = "weak/none"
        best_score = 5.0
        best_match_start_idx = start_idx
        best_match_end_idx = min(len(words) - 1, start_idx + 6)

        for pattern, h_type, base_score in HOOK_PATTERNS:
            m = re.search(pattern, slice_text, re.IGNORECASE)
            if m:
                if base_score > best_score:
                    best_score = base_score
                    best_type = h_type
                    char_offset = m.start()
                    curr_char = 0
                    for rel_i, w in enumerate(slice_words):
                        if curr_char >= char_offset:
                            best_match_start_idx = start_idx + rel_i
                            break
                        curr_char += len(w.text) + 1

        return (best_type, best_score, best_match_start_idx, best_match_end_idx)


@dataclass
class QualityGateResult:
    status: str  # QUALITY_PASS, QUALITY_WARN, QUALITY_REJECT
    quality_score: float
    rejection_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    rule_checks: list[dict[str, Any]] = field(default_factory=list)

    @property
    def is_approved(self) -> bool:
        return self.status in ("QUALITY_PASS", "QUALITY_WARN")


class PreRenderQualityGate:
    """Deterministic quality gate that strictly validates candidate compliance
    against CampaignSpecification before expensive rendering begins.
    """

    def __init__(
        self,
        campaign_spec: CampaignSpecification | None = None,
        campaign_brief: CampaignBrief | None = None,
        job_settings: dict[str, Any] | None = None,
    ) -> None:
        self.campaign_spec = campaign_spec
        self.campaign_brief = campaign_brief
        self.job_settings = job_settings or {}

    def evaluate(
        self,
        optimization: BoundaryOptimization,
        transcript: Transcript,
        silences: list[Silence] | None = None,
        existing_approved_specs: list[ClipSpecificationRecord] | None = None,
    ) -> QualityGateResult:
        all_words = transcript.words
        start_w = optimization.start_word
        end_w = optimization.end_word
        clip_words = all_words[start_w : end_w + 1] if all_words else []
        clip_text = " ".join(w.text for w in clip_words)
        duration_s = optimization.duration_s

        hard_rejections: list[str] = []
        soft_warnings: list[str] = []
        rule_checks: list[dict[str, Any]] = []

        # 1. Duration Compliance Check (strict hard bounds)
        from .duration import resolve_duration_limits

        min_dur, max_dur = resolve_duration_limits(
            job_settings=self.job_settings,
            campaign_spec=self.campaign_spec,
            campaign_brief=self.campaign_brief,
            default_min=20.0,
            default_max=30.0,
        )

        if duration_s < min_dur:
            hard_rejections.append(f"duration_under_min({duration_s:.2f}s < {min_dur:.2f}s)")
            rule_checks.append({"rule": "duration_min", "passed": False, "weight": 2.0})
        elif duration_s > max_dur:
            hard_rejections.append(f"duration_over_max({duration_s:.2f}s > {max_dur:.2f}s)")
            rule_checks.append({"rule": "duration_max", "passed": False, "weight": 2.0})
        else:
            rule_checks.append({"rule": "duration_bounds", "passed": True, "weight": 2.0})

        # 2. Transcript Completeness & Word Timing
        if len(clip_words) < 6:
            hard_rejections.append(f"insufficient_word_count({len(clip_words)} words)")
        else:
            # Deterministically repair minor zero-duration or inverted timestamps before declaring corruption
            for i, w in enumerate(clip_words):
                if w.start >= w.end:
                    next_start = clip_words[i + 1].start if (i + 1 < len(clip_words) and clip_words[i + 1].start > w.start) else None
                    if next_start is not None:
                        w.end = max(w.start + 0.05, min(w.start + 0.08, next_start))
                    else:
                        w.end = w.start + 0.08
            if any(w.start >= w.end or math.isnan(w.start) or math.isnan(w.end) or w.start < 0 for w in clip_words):
                hard_rejections.append("corrupted_word_timestamps")
            else:
                rule_checks.append({"rule": "transcript_integrity", "passed": True, "weight": 1.0})

        # 3. Narrative Hook Quality
        if optimization.hook_type == "weak/none":
            soft_warnings.append("weak_or_missing_hook")
            rule_checks.append({"rule": "hook_quality", "passed": False, "weight": 1.0})
        else:
            rule_checks.append({"rule": "hook_quality", "passed": True, "weight": 1.5, "type": optimization.hook_type})

        # 4. Climax / Payoff Presence
        if optimization.climax_start_s is not None and optimization.climax_end_s is not None:
            if (
                optimization.climax_end_s > optimization.optimized_end_s + 0.5
                or optimization.climax_start_s < optimization.optimized_start_s - 0.5
            ):
                hard_rejections.append("climax_payoff_cut_off_by_boundaries")
                rule_checks.append({"rule": "climax_integrity", "passed": False, "weight": 2.0})
            else:
                rule_checks.append({"rule": "climax_integrity", "passed": True, "weight": 1.5})

        # 5. CTA Compliance Check
        cta_required = False
        if self.campaign_spec:
            cta_item = getattr(self.campaign_spec, "cta_required", None) or getattr(self.campaign_spec, "cta", None)
            if cta_item is not None:
                val = getattr(cta_item, "value", cta_item)
                conf = getattr(cta_item, "confidence", "")
                cta_required = bool(val) and (conf == "explicit" or getattr(cta_item, "is_explicit", False))
        elif self.campaign_brief:
            cta_required = bool(
                getattr(self.campaign_brief, "cta_required", False)
                or getattr(self.campaign_brief, "call_to_action", False)
            )

        if cta_required:
            if not optimization.cta_type:
                hard_rejections.append("campaign_mandated_cta_missing")
                rule_checks.append({"rule": "required_cta", "passed": False, "weight": 2.0})
            else:
                rule_checks.append({"rule": "required_cta", "passed": True, "weight": 2.0, "type": optimization.cta_type})

        # 6. Speech Density & Pacing
        wps = len(clip_words) / max(0.1, duration_s)
        if wps < 0.9:
            hard_rejections.append(f"speech_density_critically_low({wps:.2f}_wps)")
        elif wps < 1.3:
            soft_warnings.append(f"speech_density_slow({wps:.2f}_wps)")
        elif wps > 4.5:
            soft_warnings.append(f"speech_density_rapid({wps:.2f}_wps)")
        else:
            rule_checks.append({"rule": "speech_density", "passed": True, "weight": 1.0, "wps": round(wps, 2)})

        # 7. Silence & Dead-Air Cleanup
        dead_air_count = 0
        if silences:
            for sil in silences:
                if sil.start >= optimization.optimized_start_s and sil.end <= optimization.optimized_end_s:
                    if sil.duration >= 3.0:
                        dead_air_count += 1
                        hard_rejections.append(f"excessive_dead_air({sil.duration:.1f}s)")
                    elif sil.duration >= 1.8:
                        soft_warnings.append(f"long_pause_detected({sil.duration:.1f}s)")

        # 8. Banned Topics & Keywords Check (Explicit Priority)
        banned_terms: list[str] = []
        if self.campaign_spec:
            for item in getattr(self.campaign_spec, "banned_topics", []):
                val = getattr(item, "value", item)
                if val:
                    banned_terms.append(str(val).lower())
            for item in getattr(self.campaign_spec, "banned_words", []):
                val = getattr(item, "value", item)
                if val:
                    banned_terms.append(str(val).lower())
        elif self.campaign_brief:
            banned_words = getattr(self.campaign_brief, "banned_words", None) or getattr(self.campaign_brief, "banned_keywords", None) or []
            banned_topics = getattr(self.campaign_brief, "banned_topics", None) or []
            banned_terms.extend([str(w).lower() for w in banned_words if w])
            banned_terms.extend([str(w).lower() for w in banned_topics if w])

        # Deduplicate terms and exclude common English stopwords
        deduped_banned_terms: list[str] = []
        seen_banned_terms: set[str] = set()
        for t in banned_terms:
            clean_t = t.strip().lower()
            if clean_t and clean_t not in seen_banned_terms and clean_t not in ENGLISH_STOPWORDS:
                seen_banned_terms.add(clean_t)
                deduped_banned_terms.append(clean_t)

        for banned in deduped_banned_terms:
            if not banned:
                continue
            if re.search(r"\b" + re.escape(banned) + r"\b", clip_text, re.IGNORECASE):
                reason = f"contains_banned_content({banned})"
                if reason not in hard_rejections:
                    hard_rejections.append(reason)
                rule_checks.append({"rule": "banned_check", "passed": False, "term": banned, "weight": 2.0})

        # 8b. Intro/Greeting Dominance & Substantive Hook Disambiguation
        opening_words = clip_words[:min(18, len(clip_words))]
        opening_text = " ".join(w.text for w in opening_words).lower()
        has_intro_greeting = any(re.search(pat, opening_text) for pat in INTRO_GREETING_PATTERNS)
        has_substantive_hook = any(re.search(pat, opening_text) for pat in SUBSTANTIVE_HOOK_PATTERNS)

        if has_intro_greeting and not has_substantive_hook and optimization.optimized_start_s < 20.0:
            hard_rejections.append("dominated_by_intro_greeting_filler")
            rule_checks.append({"rule": "no_intro_greeting", "passed": False, "weight": 2.5})
        else:
            rule_checks.append({"rule": "no_intro_greeting", "passed": True, "weight": 1.0})

        # 8c. Semantic Completeness Check
        last_word_raw = clip_words[-1].text.strip() if clip_words else ""
        last_clean = re.sub(r"[^\w]", "", last_word_raw.lower())
        is_terminal = is_true_sentence_terminal(last_word_raw)

        is_dangling = False
        if last_clean in ALWAYS_DANGLING_TOKENS:
            is_dangling = True
        elif last_clean in CONDITIONAL_DANGLING_TOKENS and not is_terminal:
            is_dangling = True
        elif last_word_raw.endswith("...") or last_word_raw.endswith("…"):
            is_dangling = True

        if is_dangling:
            hard_rejections.append(f"dangling_sentence_ending({last_clean or 'ellipsis'})")
            rule_checks.append({"rule": "complete_thought_ending", "passed": False, "weight": 1.5})
        else:
            rule_checks.append({"rule": "complete_thought_ending", "passed": True, "weight": 1.0})

        # 9. Deduplication against Already Approved Specifications
        if existing_approved_specs:
            for prev_spec in existing_approved_specs:
                iou = compute_iou(
                    optimization.optimized_start_s,
                    optimization.optimized_end_s,
                    prev_spec.start_time,
                    prev_spec.end_time,
                )
                if iou > 0.35:
                    hard_rejections.append(f"overlap_with_approved_spec({prev_spec.id}, iou={iou:.2f})")
                    break

        # 10. Quality Score Calculation & Final Status Assignment
        base_score = 80.0
        if optimization.hook_score >= 8.0:
            base_score += 8.0
        if optimization.climax_start_s is not None:
            base_score += 6.0
        if optimization.cta_type:
            base_score += 4.0
        if 2.0 <= wps <= 3.4:
            base_score += 5.0

        base_score -= len(soft_warnings) * 8.0
        if hard_rejections:
            base_score = min(40.0, base_score - len(hard_rejections) * 25.0)

        quality_score = max(0.0, min(100.0, round(base_score, 1)))

        if hard_rejections:
            status = "QUALITY_REJECT"
        elif quality_score >= 68.0 and not soft_warnings:
            status = "QUALITY_PASS"
        elif quality_score >= 50.0:
            status = "QUALITY_WARN"
        else:
            status = "QUALITY_REJECT"

        metrics = {
            "duration_s": round(duration_s, 2),
            "word_count": len(clip_words),
            "words_per_sec": round(wps, 2),
            "hook_type": optimization.hook_type,
            "hook_score": round(optimization.hook_score, 1),
            "cta_type": optimization.cta_type,
            "dead_air_count": dead_air_count,
        }

        return QualityGateResult(
            status=status,
            quality_score=quality_score,
            rejection_reasons=hard_rejections,
            warnings=soft_warnings,
            metrics=metrics,
            rule_checks=rule_checks,
        )


class ClipAssemblyEngine:
    """Orchestrates candidate boundary optimization, milestone verification,
    and pre-render quality gating with robust timeout & per-clip error isolation.
    """

    def __init__(
        self,
        campaign_spec: CampaignSpecification | None = None,
        campaign_brief: CampaignBrief | None = None,
        job_settings: dict[str, Any] | None = None,
        per_clip_timeout_s: float = 10.0,
    ) -> None:
        self.campaign_spec = campaign_spec
        self.campaign_brief = campaign_brief
        self.job_settings = job_settings or {}
        self.per_clip_timeout_s = per_clip_timeout_s
        self.boundary_engine = SmartBoundaryEngine()
        self.quality_gate = PreRenderQualityGate(
            campaign_spec=campaign_spec,
            campaign_brief=campaign_brief,
            job_settings=job_settings,
        )

    def assemble(
        self,
        candidates: list[ClipCandidateRecord],
        transcript: Transcript,
        job_id: str,
        source_id: str,
        silences: list[Silence] | None = None,
        on_progress: Callable[[str, float, dict[str, Any]], None] | None = None,
        target_count: int | None = None,
    ) -> tuple[list[ClipSpecificationRecord], list[ClipSpecificationRecord], dict[str, Any]]:
        """Assembles production-grade ClipSpecification records from Step 15 candidates."""
        start_time = time.time()
        total_candidates = len(candidates)

        from .duration import resolve_duration_limits

        duration_min, duration_max = resolve_duration_limits(
            job_settings=self.job_settings,
            campaign_spec=self.campaign_spec,
            campaign_brief=self.campaign_brief,
            default_min=20.0,
            default_max=30.0,
        )
        require_cta = False
        if self.campaign_spec:
            cta_item = getattr(self.campaign_spec, "cta_required", None) or getattr(self.campaign_spec, "cta", None)
            if cta_item is not None:
                val = getattr(cta_item, "value", cta_item)
                conf = getattr(cta_item, "confidence", "")
                require_cta = bool(val) and (conf == "explicit" or getattr(cta_item, "is_explicit", False))
        elif self.campaign_brief:
            require_cta = bool(
                getattr(self.campaign_brief, "cta_required", False)
                or getattr(self.campaign_brief, "call_to_action", False)
            )

        all_specs: list[ClipSpecificationRecord] = []
        approved_specs: list[ClipSpecificationRecord] = []

        if total_candidates == 0:
            log.info("ClipAssemblyEngine: No candidates received.")
            telemetry = {
                "candidates_received": 0,
                "clips_optimized": 0,
                "clips_passed": 0,
                "clips_warned": 0,
                "clips_rejected": 0,
                "elapsed_s": 0.0,
            }
            return ([], [], telemetry)

        for rank, cand in enumerate(candidates, start=1):
            clip_start_t = time.time()
            spec_id = new_id()

            if on_progress:
                frac = (rank - 1) / total_candidates
                meta = {
                    "current": rank,
                    "total": total_candidates,
                    "candidate_id": cand.id,
                }
                on_progress("OPTIMIZING_BOUNDARIES", frac, meta)

            try:
                opt = self.boundary_engine.optimize(
                    candidate=cand,
                    transcript=transcript,
                    silences=silences,
                    duration_min_s=duration_min,
                    duration_max_s=duration_max,
                    require_cta=require_cta,
                )

                if on_progress:
                    on_progress("ANALYZING_HOOK", frac + (0.05 / total_candidates), meta)
                    on_progress("VERIFYING_PAYOFF", frac + (0.10 / total_candidates), meta)
                    if require_cta:
                        on_progress("VERIFYING_CTA", frac + (0.15 / total_candidates), meta)
                    on_progress("RUNNING_QUALITY_GATE", frac + (0.20 / total_candidates), meta)

                qg_result = self.quality_gate.evaluate(
                    optimization=opt,
                    transcript=transcript,
                    silences=silences,
                    existing_approved_specs=approved_specs,
                )

                spec = ClipSpecificationRecord(
                    id=spec_id,
                    job_id=job_id,
                    candidate_id=cand.id,
                    source_id=source_id,
                    start_time=round(opt.optimized_start_s, 2),
                    end_time=round(opt.optimized_end_s, 2),
                    duration=round(opt.duration_s, 2),
                    start_word=opt.start_word,
                    end_word=opt.end_word,
                    hook_start=round(opt.hook_start_s, 2),
                    hook_end=round(opt.hook_end_s, 2),
                    hook_type=opt.hook_type,
                    climax_start=round(opt.climax_start_s, 2) if opt.climax_start_s else None,
                    climax_end=round(opt.climax_end_s, 2) if opt.climax_end_s else None,
                    cta_start=round(opt.cta_start_s, 2) if opt.cta_start_s else None,
                    cta_end=round(opt.cta_end_s, 2) if opt.cta_end_s else None,
                    boundary_adjustments=opt.to_dict(),
                    requirement_matches=qg_result.rule_checks,
                    quality_score=qg_result.quality_score,
                    quality_status=qg_result.status,
                    rejection_reasons=qg_result.rejection_reasons,
                    warnings=qg_result.warnings,
                    final_rank=rank,
                    version=1,
                    telemetry={
                        "processing_time_s": round(time.time() - clip_start_t, 3),
                        "metrics": qg_result.metrics,
                    },
                    created_at=utcnow(),
                    updated_at=utcnow(),
                )

                all_specs.append(spec)

                if qg_result.is_approved:
                    approved_specs.append(spec)
                    if on_progress:
                        on_progress("CLIPS_APPROVED", (rank) / total_candidates, meta)
                    if target_count is not None and len(approved_specs) >= target_count:
                        log.info(
                            "ClipAssemblyEngine: Target count %d reached after evaluating %d candidate(s).",
                            target_count,
                            rank,
                        )
                        break
                else:
                    if on_progress:
                        on_progress("CLIPS_REJECTED", (rank) / total_candidates, meta)

            except Exception as e:
                log.exception("Error optimizing candidate %s: %s", cand.id, e)
                failed_spec = ClipSpecificationRecord(
                    id=spec_id,
                    job_id=job_id,
                    candidate_id=cand.id,
                    source_id=source_id,
                    start_time=cand.start_s,
                    end_time=cand.end_s,
                    duration=cand.duration_s,
                    start_word=cand.start_word,
                    end_word=cand.end_word,
                    quality_score=0.0,
                    quality_status="QUALITY_REJECT",
                    rejection_reasons=[f"assembly_exception: {str(e)}"],
                    final_rank=rank,
                    created_at=utcnow(),
                    updated_at=utcnow(),
                )
                all_specs.append(failed_spec)

        elapsed_s = round(time.time() - start_time, 2)
        passed_count = sum(1 for s in all_specs if s.quality_status == "QUALITY_PASS")
        warned_count = sum(1 for s in all_specs if s.quality_status == "QUALITY_WARN")
        rejected_count = sum(1 for s in all_specs if s.quality_status == "QUALITY_REJECT")

        telemetry = {
            "candidates_received": total_candidates,
            "clips_optimized": len(all_specs),
            "clips_passed": passed_count,
            "clips_warned": warned_count,
            "clips_rejected": rejected_count,
            "elapsed_s": elapsed_s,
        }

        log.info(
            "ClipAssemblyEngine: Processed %d candidates in %.2fs -> %d PASS, %d WARN, %d REJECT.",
            total_candidates,
            elapsed_s,
            passed_count,
            warned_count,
            rejected_count,
        )

        return (approved_specs, all_specs, telemetry)


def specifications_to_clips(
    specs: list[ClipSpecificationRecord],
    candidates: list[ClipCandidateRecord] | None = None,
) -> list[Clip]:
    """Bridges approved ClipSpecificationRecord objects to standard Clip rows
    using the OPTIMIZED speech boundaries.
    """
    cand_map = {c.id: c for c in candidates} if candidates else {}
    clips: list[Clip] = []

    for rank, spec in enumerate(specs, start=1):
        cand = cand_map.get(spec.candidate_id)
        title = cand.title if cand and cand.title else f"Clip {rank}"
        hook = cand.hook_text if cand and cand.hook_text else spec.hook_type
        reason = cand.reason if cand and cand.reason else f"Quality Score: {spec.quality_score:.1f}"

        clips.append(
            Clip(
                id=spec.id,
                job_id=spec.job_id,
                start_s=spec.start_time,
                end_s=spec.end_time,
                rank=rank,
                start_word=spec.start_word,
                end_word=spec.end_word,
                title=title,
                hook=hook,
                score=int(round(spec.quality_score)),
                reason=reason,
                status="candidate",
                user_trimmed=False,
                created_at=spec.created_at,
            )
        )
    return clips
