"""Autonomous Multi-Clip Candidate Discovery & Contradiction-Aware Scoring Engine for AL AMR.

Implements Step 15:
- Autonomous candidate window discovery respecting campaign duration and boundaries.
- Timestamp-aware narrative milestone detection (Opening Hook, Setup, Escalation, Climax, CTA).
- Deterministic contradiction-aware scoring with explicit requirements weighted 2.0x over inferred heuristics.
- Strict penalization and rejection of banned topics/words, conflicts, and silence dead air.
- Top-N non-overlapping candidate selection with full provenance and rejection auditing.
- Zero paid API costs and zero hallucination.
"""

from __future__ import annotations

import difflib
import logging
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from ..db.models import Clip, ClipCandidateRecord, new_id, utcnow
from ..pipeline.prepare import Silence
from ..pipeline.transcript import Transcript, Word
from .models import CampaignBrief
from .models_intelligence import CampaignConflict, CampaignSpecification, RequirementItem

log = logging.getLogger(__name__)

# Curiosity, emotion, and viral lexical markers
CURIOSITY_HOOK_PATTERNS = [
    r"\b(?:the secret|nobody talks about|never do this|why you should|stop doing|the truth about)\b",
    r"\b(?:how to actually|most people don't know|what happens when|this changes everything)\b",
    r"\b(?:i was wrong|the real reason|number one rule|if you want to)\b",
    r"\b(?:watch this|listen closely|here's the trick|biggest mistake)\b",
]

EMOTIONAL_INTENSITY_WORDS = {
    "insane", "crazy", "shocking", "unbelievable", "huge", "massive",
    "secret", "truth", "never", "always", "destroyed", "saved", "brutal",
    "genius", "impossible", "worst", "best", "warning", "danger", "discovered",
    "game-changer", "transform", "breakthrough", "millionaire", "free", "hack"
}

CTA_PATTERNS = [
    (r"\b(?:link in (?:bio|description)|check the link|click (?:the )?link)\b", "link"),
    (r"\b(?:follow (?:for more|me|us)|hit (?:the )?follow)\b", "follow"),
    (r"\b(?:subscribe|hit that subscribe|leave a like|share this)\b", "subscribe"),
    (r"\b(?:comment below|let me know in the comments|drop a comment)\b", "comment"),
    (r"\b(?:join (?:our|the) (?:community|discord|telegram|whop|group))\b", "community"),
    (r"\b(?:download (?:the app|now|our)|sign up (?:today|now))\b", "download"),
    (r"\b(?:dm me|message me|send me a message)\b", "dm"),
]


@dataclass
class NarrativeMilestones:
    hook_start_s: float = 0.0
    hook_end_s: float = 0.0
    hook_text: str = ""
    hook_type: str = "statement"
    hook_score: float = 0.0

    setup_start_s: float = 0.0
    setup_end_s: float = 0.0

    escalation_start_s: float = 0.0
    escalation_end_s: float = 0.0

    climax_start_s: float = 0.0
    climax_end_s: float = 0.0
    climax_text: str = ""
    climax_score: float = 0.0

    cta_start_s: float = 0.0
    cta_end_s: float = 0.0
    cta_text: str = ""
    cta_type: str = "none"
    cta_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "hook": {
                "start_s": round(self.hook_start_s, 2),
                "end_s": round(self.hook_end_s, 2),
                "text": self.hook_text,
                "type": self.hook_type,
                "score": round(self.hook_score, 2),
            },
            "setup": {
                "start_s": round(self.setup_start_s, 2),
                "end_s": round(self.setup_end_s, 2),
            },
            "escalation": {
                "start_s": round(self.escalation_start_s, 2),
                "end_s": round(self.escalation_end_s, 2),
            },
            "climax": {
                "start_s": round(self.climax_start_s, 2),
                "end_s": round(self.climax_end_s, 2),
                "text": self.climax_text,
                "score": round(self.climax_score, 2),
            },
            "cta": {
                "start_s": round(self.cta_start_s, 2),
                "end_s": round(self.cta_end_s, 2),
                "text": self.cta_text,
                "type": self.cta_type,
                "score": round(self.cta_score, 2),
            },
        }


@dataclass
class CandidateScore:
    total_score: float = 0.0
    explicit_match_score: float = 0.0
    inferred_match_score: float = 0.0
    hook_score: float = 0.0
    climax_score: float = 0.0
    cta_score: float = 0.0
    speech_density_score: float = 0.0
    emotional_intensity_score: float = 0.0
    coherence_score: float = 0.0
    penalties: float = 0.0

    breakdown: dict[str, Any] = field(default_factory=dict)
    requirement_matches: list[dict[str, Any]] = field(default_factory=list)
    rejection_reasons: list[str] = field(default_factory=list)
    approved: bool = True


def _clean_tokens(text: str) -> set[str]:
    return set(re.findall(r"\b\w+\b", text.lower()))


def _phrase_matches(phrase: str, full_text: str) -> bool:
    if not phrase or not full_text:
        return False
    pattern = r"\b" + re.escape(phrase.strip()) + r"\b"
    return bool(re.search(pattern, full_text, re.IGNORECASE))


def compute_iou(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    intersection = max(0.0, min(a_end, b_end) - max(a_start, b_start))
    if intersection <= 0.0:
        return 0.0
    union = (a_end - a_start) + (b_end - b_start) - intersection
    return intersection / union if union > 0 else 0.0


def compute_text_similarity(text_a: str, text_b: str) -> float:
    if not text_a or not text_b:
        return 0.0
    return difflib.SequenceMatcher(None, text_a.lower(), text_b.lower()).ratio()


class CandidateDiscoveryEngine:
    """Discovers, scores, and selects autonomous clip candidates against CampaignSpecification."""

    def __init__(
        self,
        campaign_spec: CampaignSpecification | None = None,
        campaign_brief: CampaignBrief | None = None,
        job_settings: dict[str, Any] | None = None,
    ) -> None:
        self.spec = campaign_spec
        self.brief = campaign_brief
        self.settings = job_settings or {}

        # Resolve duration limits with explicit priority
        from .duration import resolve_duration_limits

        self.min_duration_s, self.max_duration_s = resolve_duration_limits(
            job_settings=self.settings,
            campaign_spec=self.spec,
            campaign_brief=self.brief,
            default_min=20.0,
            default_max=60.0,
        )
        self.preferred_duration_s: float | None = None
        self.max_silence_s = 2.0
        self.target_clip_count = 3

        self._configure_limits()

    def _configure_limits(self) -> None:
        if self.spec:
            dur_pref = getattr(self.spec, "duration_preferred_s", None)
            if dur_pref and getattr(dur_pref, "value", None):
                self.preferred_duration_s = float(dur_pref.value)

            out_count = getattr(self.spec, "output_count", None)
            if out_count and getattr(out_count, "value", None):
                self.target_clip_count = int(out_count.value)

            sil_lim = getattr(self.spec, "max_silence_s", None) or getattr(self.spec, "max_silence_gap_seconds", None)
            if sil_lim and getattr(sil_lim, "value", None):
                self.max_silence_s = float(sil_lim.value)

        elif self.brief:
            self.preferred_duration_s = self.brief.preferred_duration
            self.target_clip_count = min(self.brief.output_count, self.brief.maximum_candidates)
            self.max_silence_s = self.brief.maximum_silence_seconds

        if "max_clips" in self.settings:
            self.target_clip_count = int(self.settings["max_clips"])

    # ----------------------------------------------------------------------
    # 1. MULTI-CANDIDATE WINDOW DISCOVERY
    # ----------------------------------------------------------------------

    def discover_windows(
        self,
        transcript: Transcript,
        silences: Sequence[Silence] | None = None,
    ) -> list[tuple[int, int, float, float]]:
        """Finds candidate windows (start_word, end_word, start_s, end_s) on natural boundaries."""
        words = transcript.words
        total_words = len(words)
        if total_words < 4:
            return []

        total_duration = words[-1].end - words[0].start
        min_dur = self.min_duration_s
        max_dur = self.max_duration_s

        # If source video is shorter than configured min duration, do not silently produce short clips
        if total_duration < min_dur:
            log.warning(
                "Source duration (%.1fs) is less than configured minimum duration (%.1fs); no valid candidates possible.",
                total_duration,
                min_dur,
            )
            return []

        # Identify sentence/phrase boundary indices
        boundaries: list[int] = [0]
        for i, w in enumerate(words):
            text = w.text.strip()
            # Sentence ending punctuation
            if any(text.endswith(p) for p in (".", "!", "?", "...", ";")):
                boundaries.append(min(i + 1, total_words - 1))
            # Pauses between words
            elif i + 1 < total_words and (words[i + 1].start - w.end) >= 0.4:
                boundaries.append(i + 1)
        if (total_words - 1) not in boundaries:
            boundaries.append(total_words - 1)
        boundaries = sorted(list(set(boundaries)))

        candidate_spans: list[tuple[int, int, float, float]] = []
        dangling_openers = {"and", "but", "or", "so", "because", "yet", "however"}

        for b_idx, start_idx in enumerate(boundaries):
            if start_idx >= total_words - 4:
                break

            # Avoid starting on awkward dangling conjunctions
            first_w = words[start_idx].text.strip().lower()
            if first_w in dangling_openers and start_idx + 1 < total_words:
                start_idx += 1

            start_s = words[start_idx].start

            # Scan forward through boundary candidates
            for end_idx in boundaries[b_idx + 1 :]:
                if end_idx <= start_idx:
                    continue
                end_s = words[end_idx].end
                dur = end_s - start_s

                if dur < min_dur:
                    continue
                if dur > max_dur:
                    break  # Exceeded max duration window

                # Snap close to end of word
                candidate_spans.append((start_idx, end_idx, start_s, end_s))

        # Filter excessive local overlap during generation
        filtered_spans: list[tuple[int, int, float, float]] = []
        for span in candidate_spans:
            s_w, e_w, s_s, e_s = span
            # Don't add if nearly identical (<1.5s difference) to last span
            if filtered_spans:
                prev_s_w, prev_e_w, prev_s_s, prev_e_s = filtered_spans[-1]
                if abs(s_s - prev_s_s) < 1.0 and abs(e_s - prev_e_s) < 2.0:
                    continue
            filtered_spans.append(span)

        log.info(
            "CandidateDiscoveryEngine: Discovered %d candidate window(s) across %.1fs transcript.",
            len(filtered_spans),
            total_duration,
        )
        return filtered_spans

    # ----------------------------------------------------------------------
    # 2. NARRATIVE MILESTONE DETECTION
    # ----------------------------------------------------------------------

    def detect_milestones(
        self,
        words: Sequence[Word],
        start_s: float,
        end_s: float,
    ) -> NarrativeMilestones:
        """Detects Hook (0-5s), Setup, Escalation, Climax, and CTA (closing 5-8s)."""
        milestones = NarrativeMilestones()
        if not words:
            return milestones

        duration = end_s - start_s
        full_text = " ".join(w.text for w in words)

        # 1. Opening Hook (First 3.5 to 5.0 seconds)
        hook_cutoff_s = start_s + min(5.0, duration * 0.35)
        hook_words = [w for w in words if w.start <= hook_cutoff_s]
        if not hook_words:
            hook_words = list(words[: min(8, len(words))])

        hook_text = " ".join(w.text for w in hook_words).strip()
        milestones.hook_start_s = words[0].start
        milestones.hook_end_s = hook_words[-1].end
        milestones.hook_text = hook_text

        # Classify and score hook
        hook_score = 5.0
        hook_type = "statement"
        if hook_text.endswith("?"):
            hook_type = "question"
            hook_score += 2.0
        for pattern in CURIOSITY_HOOK_PATTERNS:
            if re.search(pattern, hook_text, re.IGNORECASE):
                hook_type = "curiosity"
                hook_score += 3.0
                break
        for emo in EMOTIONAL_INTENSITY_WORDS:
            if _phrase_matches(emo, hook_text):
                hook_score += 1.0
                break
        milestones.hook_score = min(10.0, hook_score)
        milestones.hook_type = hook_type

        # 2. Setup & Escalation (Body)
        body_start_s = milestones.hook_end_s
        body_end_s = start_s + (duration * 0.75)
        midpoint_s = (body_start_s + body_end_s) / 2.0

        milestones.setup_start_s = body_start_s
        milestones.setup_end_s = midpoint_s
        milestones.escalation_start_s = midpoint_s
        milestones.escalation_end_s = body_end_s

        # 3. Climax / Payoff (Around 65% - 85% of clip duration)
        climax_window_start = start_s + (duration * 0.60)
        climax_window_end = start_s + (duration * 0.85)
        climax_words = [w for w in words if climax_window_start <= w.start <= climax_window_end]

        if climax_words:
            climax_text = " ".join(w.text for w in climax_words).strip()
            milestones.climax_start_s = climax_words[0].start
            milestones.climax_end_s = climax_words[-1].end
            milestones.climax_text = climax_text[:120]

            # Climax score based on intensity & punctuation
            climax_score = 6.0
            if "!" in climax_text:
                climax_score += 1.5
            for emo in EMOTIONAL_INTENSITY_WORDS:
                if _phrase_matches(emo, climax_text):
                    climax_score += 1.5
                    break
            milestones.climax_score = min(10.0, climax_score)
        else:
            milestones.climax_start_s = body_end_s
            milestones.climax_end_s = end_s
            milestones.climax_text = hook_text
            milestones.climax_score = 5.0

        # 4. CTA / Conversion Moment (Last 5.0 to 8.0 seconds)
        cta_window_start = max(start_s, end_s - 8.0)
        cta_words = [w for w in words if w.start >= cta_window_start]
        cta_text = " ".join(w.text for w in cta_words).strip()

        milestones.cta_start_s = cta_words[0].start if cta_words else end_s
        milestones.cta_end_s = end_s
        milestones.cta_text = cta_text

        cta_score = 0.0
        cta_type = "none"
        for pattern, ctype in CTA_PATTERNS:
            if re.search(pattern, cta_text, re.IGNORECASE) or re.search(pattern, full_text, re.IGNORECASE):
                cta_score = 8.5
                cta_type = ctype
                break
        milestones.cta_score = cta_score
        milestones.cta_type = ctype

        return milestones

    # ----------------------------------------------------------------------
    # 3. CAMPAIGN-AWARE SCORING (Explicit 2.0x vs Inferred 1.0x)
    # ----------------------------------------------------------------------

    def score_candidate(
        self,
        words: Sequence[Word],
        start_s: float,
        end_s: float,
        milestones: NarrativeMilestones,
        silences: Sequence[Silence] | None = None,
    ) -> CandidateScore:
        """Calculates deterministic score with explicit vs inferred campaign weighting."""
        score_res = CandidateScore()
        duration_s = max(0.1, end_s - start_s)
        full_text = " ".join(w.text for w in words).strip()
        full_text_lower = full_text.lower()

        # ------------------------------------------------------------------
        # HARD FAILURE 1: Duration constraints (strict hard bounds)
        # ------------------------------------------------------------------
        if duration_s < self.min_duration_s or duration_s > self.max_duration_s:
            score_res.rejection_reasons.append(
                f"Duration {duration_s:.2f}s violates limits [{self.min_duration_s:.1f}s - {self.max_duration_s:.1f}s]"
            )
            score_res.approved = False

        # ------------------------------------------------------------------
        # HARD FAILURE 2: Banned Words / Topics
        # ------------------------------------------------------------------
        banned_words: list[str] = []
        banned_topics: list[str] = []

        if self.spec:
            banned_words.extend([item.value for item in self.spec.banned_words])
            banned_topics.extend([item.value for item in self.spec.banned_topics])
        elif self.brief:
            banned_words.extend(self.brief.banned_words)
            banned_topics.extend(self.brief.banned_topics)

        for bw in banned_words:
            if _phrase_matches(bw, full_text):
                score_res.rejection_reasons.append(f"Candidate contains banned word: '{bw}'")
                score_res.approved = False

        for bt in banned_topics:
            if _phrase_matches(bt, full_text):
                score_res.rejection_reasons.append(f"Candidate contains banned topic: '{bt}'")
                score_res.approved = False

        # ------------------------------------------------------------------
        # HARD FAILURE 3: Excessive Silence Dead Air
        # ------------------------------------------------------------------
        if silences:
            internal_silences = [
                s for s in silences
                if s.start >= start_s and s.end <= end_s and (s.end - s.start) > 0.3
            ]
            max_silence = max([s.end - s.start for s in internal_silences], default=0.0)
            if max_silence > self.max_silence_s:
                score_res.rejection_reasons.append(
                    f"Dead air silence of {max_silence:.2f}s exceeds threshold {self.max_silence_s:.2f}s"
                )
                score_res.approved = False

        # ------------------------------------------------------------------
        # 4. Explicit vs Inferred Requirements Scoring
        # ------------------------------------------------------------------
        # Explicit requirements MUST have higher weight (2.0x) than inferred heuristics (1.0x)
        EXPLICIT_WEIGHT = 2.0
        INFERRED_WEIGHT = 1.0

        explicit_points = 0.0
        explicit_max = 0.0
        inferred_points = 0.0
        inferred_max = 0.0

        if self.spec:
            # Desired topics
            for req in self.spec.desired_topics:
                is_explicit = req.confidence == "explicit"
                weight = EXPLICIT_WEIGHT if is_explicit else INFERRED_WEIGHT
                matched = _phrase_matches(req.value, full_text)

                score_res.requirement_matches.append({
                    "type": "desired_topic",
                    "value": req.value,
                    "confidence": req.confidence,
                    "matched": matched,
                    "weight": weight,
                })

                if is_explicit:
                    explicit_max += 10.0 * weight
                    if matched:
                        explicit_points += 10.0 * weight
                else:
                    inferred_max += 10.0 * weight
                    if matched:
                        inferred_points += 10.0 * weight

            # Target Audience match
            target_aud = getattr(self.spec, "target_audience", "")
            if isinstance(target_aud, str) and target_aud:
                matched = any(_phrase_matches(tok, full_text) for tok in target_aud.split() if len(tok) > 3)
                explicit_max += 5.0 * EXPLICIT_WEIGHT
                if matched:
                    explicit_points += 5.0 * EXPLICIT_WEIGHT
            elif getattr(self.spec, "audience", None) and self.spec.audience.value:
                aud_item = self.spec.audience
                is_explicit = aud_item.confidence == "explicit"
                weight = EXPLICIT_WEIGHT if is_explicit else INFERRED_WEIGHT
                matched = any(_phrase_matches(tok, full_text) for tok in aud_item.value.split() if len(tok) > 3)
                if is_explicit:
                    explicit_max += 5.0 * weight
                    if matched:
                        explicit_points += 5.0 * weight
                else:
                    inferred_max += 5.0 * weight
                    if matched:
                        inferred_points += 5.0 * weight

            # CTA requirement match
            cta_req = getattr(self.spec, "cta_required", None)
            if cta_req and getattr(cta_req, "value", False):
                is_explicit = cta_req.confidence == "explicit"
                weight = EXPLICIT_WEIGHT if is_explicit else INFERRED_WEIGHT
                matched = milestones.cta_score >= 5.0
                if is_explicit:
                    explicit_max += 8.0 * weight
                    if matched:
                        explicit_points += 8.0 * weight
                else:
                    inferred_max += 8.0 * weight
                    if matched:
                        inferred_points += 8.0 * weight
            elif getattr(self.spec, "cta", None) and self.spec.cta.value:
                cta_item = self.spec.cta
                is_explicit = cta_item.confidence == "explicit"
                weight = EXPLICIT_WEIGHT if is_explicit else INFERRED_WEIGHT
                matched = milestones.cta_score >= 5.0 or _phrase_matches(cta_item.value, full_text)
                if is_explicit:
                    explicit_max += 8.0 * weight
                    if matched:
                        explicit_points += 8.0 * weight
                else:
                    inferred_max += 8.0 * weight
                    if matched:
                        inferred_points += 8.0 * weight

        elif self.brief:
            # Fallback to CampaignBrief topics
            for top in self.brief.required_topics:
                matched = _phrase_matches(top, full_text)
                explicit_max += 10.0 * EXPLICIT_WEIGHT
                if matched:
                    explicit_points += 10.0 * EXPLICIT_WEIGHT
                else:
                    score_res.rejection_reasons.append(f"Missing required campaign topic: '{top}'")
                    score_res.approved = False

            for kw in self.brief.optional_keywords:
                matched = _phrase_matches(kw, full_text)
                inferred_max += 5.0 * INFERRED_WEIGHT
                if matched:
                    inferred_points += 5.0 * INFERRED_WEIGHT

        # Calculate normalized 0-100 scores
        score_res.explicit_match_score = (
            (explicit_points / explicit_max * 100.0) if explicit_max > 0 else 80.0
        )
        score_res.inferred_match_score = (
            (inferred_points / inferred_max * 100.0) if inferred_max > 0 else 75.0
        )

        # ------------------------------------------------------------------
        # 5. Content & Narrative Virality Signals
        # ------------------------------------------------------------------
        score_res.hook_score = milestones.hook_score * 10.0  # 0-100
        score_res.climax_score = milestones.climax_score * 10.0  # 0-100
        score_res.cta_score = milestones.cta_score * 10.0  # 0-100

        # Emotional intensity
        found_emotions = sum(1 for e in EMOTIONAL_INTENSITY_WORDS if _phrase_matches(e, full_text))
        score_res.emotional_intensity_score = min(100.0, 50.0 + (found_emotions * 10.0))

        # Speech density (words / sec)
        wps = len(words) / duration_s
        # Sweet spot: 2.2 to 3.8 words/sec
        if 2.0 <= wps <= 4.0:
            density_score = 90.0
        elif 1.4 <= wps < 2.0 or 4.0 < wps <= 5.0:
            density_score = 75.0
        else:
            density_score = 50.0
        score_res.speech_density_score = density_score

        # Coherence & Sentence Completeness
        coherence = 85.0
        first_word = words[0].text.strip()
        last_word = words[-1].text.strip()
        if not first_word[0].isupper() and not first_word.startswith(("\"", "'")):
            coherence -= 10.0
        if not any(last_word.endswith(p) for p in (".", "!", "?", "\"")):
            coherence -= 15.0
        score_res.coherence_score = max(0.0, coherence)

        # ------------------------------------------------------------------
        # 6. Campaign Conflicts Penalties
        # ------------------------------------------------------------------
        penalties = 0.0
        if self.spec and self.spec.conflicts:
            for conflict in self.spec.conflicts:
                if conflict.resolution_status != "resolved":
                    # Check if candidate is near conflict category
                    if conflict.rule_category == "duration":
                        penalties += 10.0
                    elif conflict.rule_category == "topics" and conflict.document_a in full_text_lower:
                        penalties += 15.0
        score_res.penalties = penalties

        # ------------------------------------------------------------------
        # 7. Total Composite Score Calculation
        # ------------------------------------------------------------------
        # Weighted combination:
        # Explicit Campaign Fit:  30%
        # Inferred Campaign Fit:  15%
        # Hook Quality:           20%
        # Climax / Payoff:        15%
        # Speech Density & Pace:  10%
        # Coherence:              10%
        raw_composite = (
            score_res.explicit_match_score * 0.30
            + score_res.inferred_match_score * 0.15
            + score_res.hook_score * 0.20
            + score_res.climax_score * 0.15
            + score_res.speech_density_score * 0.10
            + score_res.coherence_score * 0.10
        ) - penalties

        final_total = max(0.0, min(100.0, round(raw_composite, 1)))
        score_res.total_score = final_total if score_res.approved else 0.0

        score_res.breakdown = {
            "explicit_match": round(score_res.explicit_match_score, 1),
            "inferred_match": round(score_res.inferred_match_score, 1),
            "hook": round(score_res.hook_score, 1),
            "climax": round(score_res.climax_score, 1),
            "cta": round(score_res.cta_score, 1),
            "density": round(score_res.speech_density_score, 1),
            "emotional_intensity": round(score_res.emotional_intensity_score, 1),
            "coherence": round(score_res.coherence_score, 1),
            "penalties": round(score_res.penalties, 1),
            "words_per_second": round(wps, 2),
            "duration_s": round(duration_s, 1),
        }

        return score_res

    # ----------------------------------------------------------------------
    # 4. TOP-N SELECTION & DEDUPLICATION
    # ----------------------------------------------------------------------

    def select_top_candidates(
        self,
        candidates: list[ClipCandidateRecord],
        target_count: int | None = None,
        max_overlap_iou: float = 0.35,
        max_text_similarity: float = 0.65,
    ) -> list[ClipCandidateRecord]:
        """Greedily selects Top-N non-overlapping approved candidates and logs rejections."""
        limit = target_count or self.target_clip_count

        # Separate approved vs rejected
        approved = [c for c in candidates if c.status != "rejected" and c.score > 0]
        # Sort by total score descending
        approved.sort(key=lambda c: c.score, reverse=True)

        selected: list[ClipCandidateRecord] = []

        for cand in approved:
            # Check overlap against already selected candidates
            conflict_reason: str | None = None
            for sel in selected:
                iou = compute_iou(cand.start_s, cand.end_s, sel.start_s, sel.end_s)
                if iou > max_overlap_iou:
                    conflict_reason = (
                        f"Overlaps with higher-ranked candidate '{sel.title}' (IoU: {iou:.2f} > {max_overlap_iou:.2f})"
                    )
                    break

                sim = compute_text_similarity(cand.transcript_slice, sel.transcript_slice)
                if sim > max_text_similarity:
                    conflict_reason = (
                        f"Transcript redundant with candidate '{sel.title}' (similarity: {sim:.2f} > {max_text_similarity:.2f})"
                    )
                    break

            if conflict_reason:
                cand.selected = False
                cand.status = "rejected"
                cand.rejection_reasons.append(conflict_reason)
                continue

            # Approved and non-overlapping!
            if len(selected) < limit:
                cand.selected = True
                cand.status = "selected"
                cand.rank = len(selected) + 1
                selected.append(cand)
            else:
                cand.selected = False
                cand.status = "scored"

        log.info(
            "CandidateDiscoveryEngine: Selected %d top candidate(s) out of %d evaluated.",
            len(selected),
            len(candidates),
        )
        return candidates

    # ----------------------------------------------------------------------
    # 5. EXECUTION ENTRYPOINT WITH REAL-TIME PROGRESS
    # ----------------------------------------------------------------------

    def run(
        self,
        transcript: Transcript,
        job_id: str,
        silences: Sequence[Silence] | None = None,
        on_progress: Callable[[str, float, dict[str, Any]], None] | None = None,
    ) -> tuple[list[ClipCandidateRecord], list[ClipCandidateRecord], dict[str, Any]]:
        """Executes full autonomous discovery and scoring pipeline.

        Returns (selected_candidates, all_candidates, telemetry).
        """
        start_time = time.monotonic()

        def emit(stage_name: str, frac: float, meta: dict[str, Any] | None = None) -> None:
            if on_progress:
                on_progress(stage_name, frac, meta or {})

        # Stage 1: DISCOVERING_CANDIDATES
        emit("DISCOVERING_CANDIDATES", 0.15, {"discovered": 0})
        windows = self.discover_windows(transcript, silences)
        total_windows = len(windows)
        emit("DISCOVERING_CANDIDATES", 0.30, {"discovered": total_windows})

        if total_windows == 0:
            log.warning("No candidate windows could be extracted from transcript.")
            telemetry = {
                "discovered_count": 0,
                "scored_count": 0,
                "rejected_count": 0,
                "selected_count": 0,
                "elapsed_s": round(time.monotonic() - start_time, 2),
            }
            return [], [], telemetry

        # Stage 2: SCORING_CANDIDATES
        emit("SCORING_CANDIDATES", 0.40, {"discovered": total_windows, "scored": 0})
        raw_candidates: list[ClipCandidateRecord] = []

        for idx, (start_w, end_w, start_s, end_s) in enumerate(windows):
            cand_words = transcript.slice(start_w, end_w)
            slice_text = " ".join(w.text for w in cand_words).strip()
            milestones = self.detect_milestones(cand_words, start_s, end_s)
            score_data = self.score_candidate(cand_words, start_s, end_s, milestones, silences)

            cid = new_id()
            title = slice_text[:45].strip()
            if len(slice_text) > 45:
                title += "..."

            hook_snippet = milestones.hook_text[:60].strip()

            reason = (
                f"Hook ({milestones.hook_type}, score: {milestones.hook_score:.1f}/10) | "
                f"Speech density: {score_data.breakdown.get('words_per_second', 0):.1f} w/s | "
                f"Campaign match: {score_data.explicit_match_score:.0f}%"
            )

            status = "scored" if score_data.approved else "rejected"

            record = ClipCandidateRecord(
                id=cid,
                job_id=job_id,
                rank=0,
                selected=False,
                status=status,
                start_s=round(start_s, 2),
                end_s=round(end_s, 2),
                duration_s=round(end_s - start_s, 2),
                start_word=start_w,
                end_word=end_w,
                title=title,
                hook_text=hook_snippet,
                reason=reason,
                transcript_slice=slice_text,
                score=score_data.total_score,
                score_breakdown=score_data.breakdown,
                hook_signals=milestones.to_dict()["hook"],
                climax_signals=milestones.to_dict()["climax"],
                cta_signals=milestones.to_dict()["cta"],
                requirement_matches=score_data.requirement_matches,
                rejection_reasons=score_data.rejection_reasons,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            raw_candidates.append(record)

            if (idx + 1) % max(1, total_windows // 5) == 0 or (idx + 1) == total_windows:
                progress_frac = 0.40 + (0.30 * ((idx + 1) / total_windows))
                emit(
                    "SCORING_CANDIDATES",
                    progress_frac,
                    {"discovered": total_windows, "scored": idx + 1},
                )

        # Stage 3: FILTERING_CANDIDATES & SELECTING_TOP_CANDIDATES
        emit("FILTERING_CANDIDATES", 0.75, {"scored": len(raw_candidates)})
        emit("SELECTING_TOP_CANDIDATES", 0.85, {"target": self.target_clip_count})

        all_candidates = self.select_top_candidates(raw_candidates, self.target_clip_count)
        selected_candidates = [c for c in all_candidates if c.selected]
        rejected_candidates = [c for c in all_candidates if c.status == "rejected"]

        elapsed_s = round(time.monotonic() - start_time, 2)
        telemetry = {
            "discovered_count": total_windows,
            "scored_count": len(all_candidates),
            "rejected_count": len(rejected_candidates),
            "selected_count": len(selected_candidates),
            "elapsed_s": elapsed_s,
        }

        # Stage 4: CANDIDATES_READY
        emit("CANDIDATES_READY", 1.0, telemetry)

        log.info(
            "CandidateDiscoveryEngine finished in %.2fs. Discovered: %d, Scored: %d, Rejected: %d, Selected: %d",
            elapsed_s,
            total_windows,
            len(all_candidates),
            len(rejected_candidates),
            len(selected_candidates),
        )

        return selected_candidates, all_candidates, telemetry


def candidates_to_clips(
    candidates: Sequence[ClipCandidateRecord],
    job_id: str,
) -> list[Clip]:
    """Bridges selected ClipCandidateRecord items into standard Clip objects for downstream rendering."""
    clips: list[Clip] = []
    for cand in candidates:
        clips.append(
            Clip(
                id=cand.id,
                job_id=job_id,
                rank=cand.rank,
                start_s=cand.start_s,
                end_s=cand.end_s,
                start_word=cand.start_word,
                end_word=cand.end_word,
                title=cand.title,
                hook=cand.hook_text,
                score=int(round(cand.score)),
                reason=cand.reason,
                status="candidate",
                user_trimmed=False,
                created_at=cand.created_at,
            )
        )
    return clips
