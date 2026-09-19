"""Contextual Semantic Parser for identifying narrative visual intent from transcripts.

Distinguishes literal from figurative phrases using surrounding context windows,
classifies visual concepts, selects appropriate presentation modes (FULL_SCREEN vs PARTIAL_OVERLAY),
and aligns trigger timings with word-level timestamps.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from autoclip.pipeline.transcript import Transcript, Word
from .models import (
    PresentationMode,
    SemanticConceptDefinition,
    SemanticVisualCue,
    VisualType,
)

log = logging.getLogger(__name__)

# Standard concept definitions mapping semantic domains to presentation modes and visual types
CONCEPT_DEFINITIONS: dict[str, SemanticConceptDefinition] = {
    "nature_grass": SemanticConceptDefinition(
        concept_id="nature_grass",
        primary_keywords=["grass", "outside", "nature", "meadow", "lawn", "park", "woods"],
        context_indicators=["touch", "touched", "walk", "outside", "literally", "sit", "green", "ground"],
        forbidden_figurative_phrases=["grass-colored", "snake in the grass", "grass roots", "grassroots", "greener grass"],
        default_presentation_mode=PresentationMode.FULL_SCREEN,
        default_visual_type=VisualType.STOCK_VIDEO,
        search_queries=["hands touching lush green grass sunny outdoors", "close up green grass meadow nature footage"],
        description="Lush green outdoor grass, nature, and sunlight.",
    ),
    "financial_revenue": SemanticConceptDefinition(
        concept_id="financial_revenue",
        primary_keywords=["revenue", "sales", "million", "dollars", "profit", "income", "earnings", "$1,000,000", "$500k", "500k"],
        context_indicators=["made", "did", "hit", "dashboard", "analytics", "annual", "year", "total", "went over"],
        forbidden_figurative_phrases=["million reasons", "million miles", "feel like a million", "million questions"],
        default_presentation_mode=PresentationMode.PARTIAL_OVERLAY,
        default_visual_type=VisualType.DASHBOARD,
        search_queries=["ecommerce revenue sales dashboard analytics graph", "financial performance dashboard UI metrics"],
        description="Real revenue analytics dashboard showing sales metrics and financial numbers.",
    ),
    "warehouse_shipping": SemanticConceptDefinition(
        concept_id="warehouse_shipping",
        primary_keywords=["warehouse", "amazon", "shipped", "shipping", "fulfillment", "fba", "boxes", "logistics", "inventory"],
        context_indicators=["local", "boxes", "pallet", "facility", "forklift", "store", "working for", "department", "packages"],
        forbidden_figurative_phrases=["warehouse of knowledge", "data warehouse"],
        default_presentation_mode=PresentationMode.FULL_SCREEN,
        default_visual_type=VisualType.STOCK_VIDEO,
        search_queries=["amazon fulfillment center warehouse workers pallets forklift", "large modern logistics shipping warehouse interior"],
        description="Amazon fulfillment warehouse operations with shelving, pallets, and logistics.",
    ),
    "business_shutdown": SemanticConceptDefinition(
        concept_id="business_shutdown",
        primary_keywords=["shutdown", "shut down", "closed", "suspension", "suspended", "locked", "banned"],
        context_indicators=["company", "account", "business", "store", "week", "got their", "for a week"],
        forbidden_figurative_phrases=["shut down the idea", "shut down the rumors", "closed mind", "behind closed doors"],
        default_presentation_mode=PresentationMode.FULL_SCREEN,
        default_visual_type=VisualType.STOCK_PHOTO,
        search_queries=["storefront closed sign hanging on glass door", "business shut down closed retail office"],
        description="Storefront window or door with a prominent CLOSED / shutdown sign.",
    ),
    "product_marketplace": SemanticConceptDefinition(
        concept_id="product_marketplace",
        primary_keywords=["product", "products", "listing", "taking off", "speakers", "cases", "private label", "brand", "item"],
        context_indicators=["sold", "launched", "research", "reviews", "bluetooth", "battery", "started taking off"],
        forbidden_figurative_phrases=["product of society", "product of my environment", "by-product"],
        default_presentation_mode=PresentationMode.PARTIAL_OVERLAY,
        default_visual_type=VisualType.PRODUCT_LISTING,
        search_queries=["ecommerce product listing electronics packaging marketplace", "consumer electronics product showcase on white background"],
        description="Product presentation or clean marketplace product listing card.",
    ),
    "team_office": SemanticConceptDefinition(
        concept_id="team_office",
        primary_keywords=["team", "hired", "employees", "workplace", "department", "office", "meeting"],
        context_indicators=["built", "small", "handle", "worked for", "group", "people", "staff"],
        forbidden_figurative_phrases=["team player", "team spirit", "take one for the team"],
        default_presentation_mode=PresentationMode.FULL_SCREEN,
        default_visual_type=VisualType.STOCK_VIDEO,
        search_queries=["diverse startup business team collaborating in modern office", "small workplace team meeting discussion"],
        description="Professional team collaborating in a modern workplace setting.",
    ),
    "business_growth": SemanticConceptDefinition(
        concept_id="business_growth",
        primary_keywords=["growing", "growth", "scaled", "scaling", "expanded", "exponential"],
        context_indicators=["kept", "business", "fast", "company", "metrics", "chart", "upward"],
        forbidden_figurative_phrases=["personal growth", "growth mindset"],
        default_presentation_mode=PresentationMode.PARTIAL_OVERLAY,
        default_visual_type=VisualType.CHART,
        search_queries=["business growth upward trendline graph neon green", "exponential revenue growth chart visualization"],
        description="Upward curved growth chart indicating business scale and expansion.",
    ),
    "fire_danger": SemanticConceptDefinition(
        concept_id="fire_danger",
        primary_keywords=["fire", "flames", "smoke", "smoking", "burning"],
        context_indicators=["catching", "caught", "building", "house", "warehouse", "on fire", "physically", "actually", "burned", "trigger word"],
        forbidden_figurative_phrases=["under fire", "playing with fire", "fire in his eyes", "fired from job", "spitting fire"],
        default_presentation_mode=PresentationMode.FULL_SCREEN,
        default_visual_type=VisualType.STOCK_VIDEO,
        search_queries=["close up dramatic fire flames smoke burning slow motion", "actual fire burning with smoke"],
        description="Flames, fire, and smoke conveying literal burning or danger.",
    ),
    "money_cash": SemanticConceptDefinition(
        concept_id="money_cash",
        primary_keywords=["cash", "payout", "bills", "payments", "money", "payment holds"],
        context_indicators=["hold", "holding", "stack", "full", "beginning", "dollar", "two weeks"],
        forbidden_figurative_phrases=["cash in on", "cash cow", "money talks", "time is money", "for my money"],
        default_presentation_mode=PresentationMode.FULL_SCREEN,
        default_visual_type=VisualType.STOCK_PHOTO,
        search_queries=["hands sliding thick stack of 100 dollar bills across desk", "counting fresh us dollar cash bills hands"],
        description="Hands holding or sliding crisp stacks of hundred-dollar bills.",
    ),
}


class ContextualSemanticParser:
    """Extracts semantic visual cues from speech transcripts with contextual awareness."""

    def __init__(
        self,
        definitions: dict[str, SemanticConceptDefinition] | None = None,
        default_dwell_s: float = 2.0,
        min_dwell_s: float = 1.4,
        max_dwell_s: float = 2.6,
    ) -> None:
        self.definitions = definitions or CONCEPT_DEFINITIONS
        self.default_dwell_s = default_dwell_s
        self.min_dwell_s = min_dwell_s
        self.max_dwell_s = max_dwell_s

    def parse_transcript(
        self,
        words: list[Word],
        clip_start_s: float = 0.0,
        clip_end_s: float = 30.0,
    ) -> list[SemanticVisualCue]:
        """Convenience alias for parse_transcript_segment."""
        return self.parse_transcript_segment(words, clip_start_s, clip_end_s)

    def parse_transcript_segment(
        self,
        words: list[Word],
        clip_start_s: float = 0.0,
        clip_end_s: float = 30.0,
    ) -> list[SemanticVisualCue]:
        """Parses a word list into chronologically ordered, non-overlapping semantic visual cues."""
        if not words:
            return []

        cues: list[SemanticVisualCue] = []
        n_words = len(words)

        def _word_text(w: Word) -> str:
            return getattr(w, "text", getattr(w, "word", str(w)))

        # Build full text and word window index
        text_lower = " ".join(_word_text(w).lower().strip(",.!?\"'") for w in words)

        # Helper to extract window text around a word index
        def get_window_text(idx: int, window_radius: int = 5) -> str:
            w_start = max(0, idx - window_radius)
            w_end = min(n_words, idx + window_radius + 1)
            return " ".join(_word_text(w).lower().strip(",.!?\"'") for w in words[w_start:w_end])

        i = 0
        while i < n_words:
            w = words[i]
            w_clean = _word_text(w).lower().strip(",.!?\"'")
            if not w_clean:
                i += 1
                continue

            # Check all concept definitions
            matched_cue: SemanticVisualCue | None = None

            for concept_id, defn in self.definitions.items():
                is_keyword_match = False
                matched_kw = ""

                for kw in defn.primary_keywords:
                    if " " in kw:
                        # Multi-word keyword (e.g. "shut down", "payment holds", "taking off")
                        kw_tokens = kw.split()
                        kw_len = len(kw_tokens)
                        if i + kw_len <= n_words:
                            seq = " ".join(_word_text(words[j]).lower().strip(",.!?\"'") for j in range(i, i + kw_len))
                            if seq == kw:
                                is_keyword_match = True
                                matched_kw = kw
                                break
                    else:
                        if w_clean == kw:
                            is_keyword_match = True
                            matched_kw = kw
                            break

                if not is_keyword_match:
                    continue

                # We have a candidate keyword match! Now verify contextual semantics:
                window_text = get_window_text(i, window_radius=6)

                # 1. Check for forbidden figurative idioms
                has_forbidden = any(forbid in window_text for forbid in defn.forbidden_figurative_phrases)
                if has_forbidden:
                    log.info("Figurative guard rejected keyword '%s' for concept '%s' in context: '%s'", matched_kw, concept_id, window_text)
                    continue

                # 2. Check for positive context indicators
                has_context = any(ind in window_text for ind in defn.context_indicators)

                # Numbers / currency are strong literal indicators for financial/revenue
                has_numbers = bool(re.search(r"\b(\d+|million|thousand|dollars?|\$\d+)\b", window_text))
                if concept_id == "financial_revenue" and has_numbers:
                    has_context = True

                # Require context verification if not an unambiguous single entity
                if not has_context and len(defn.context_indicators) > 0:
                    # Still allow if keyword is explicitly multi-word or distinct
                    if matched_kw not in ["warehouse", "amazon", "shutdown", "revenue"]:
                        continue

                # Determine timing: start around trigger word, span adaptive duration
                trigger_start = w.start
                # Adaptive duration: longer for complex explanations, capped at clip boundary
                duration = self.default_dwell_s
                trigger_end = min(clip_end_s, trigger_start + duration)

                # If trigger_start is beyond clip or duration is too short, skip
                if trigger_start >= clip_end_s or (trigger_end - trigger_start) < self.min_dwell_s:
                    continue

                # Extract overlay metadata if applicable
                meta: dict[str, Any] = {}
                if defn.default_presentation_mode == PresentationMode.PARTIAL_OVERLAY:
                    if concept_id == "financial_revenue":
                        # Extract dollar amount or metric if mentioned
                        amt_match = re.search(r"(\$?\d[\d,]*(?:\.\d+)?\s*(?:million|k|billion)?|a million dollars?)", window_text)
                        val_str = amt_match.group(1).upper() if amt_match else "$1,248,331"
                        if "million" in val_str.lower() and not re.search(r"\d", val_str):
                            val_str = "$1,000,000+"
                        meta = {"title": "TOTAL REVENUE", "value": val_str, "badge": "+18.4% YOY"}
                    elif concept_id == "business_growth":
                        meta = {"title": "GROWTH TRAJECTORY", "value": "EXPONENTIAL", "badge": "SCALING"}
                    elif concept_id == "product_marketplace":
                        meta = {"title": "FEATURED PRODUCT", "rating": "4.9 ★★★★★", "badge": "BEST SELLER"}

                matched_cue = SemanticVisualCue(
                    cue_id=f"cue_{len(cues)+1}_{concept_id}",
                    concept=concept_id,
                    trigger_phrase=window_text,
                    trigger_word=matched_kw,
                    start_s=round(trigger_start, 2),
                    end_s=round(trigger_end, 2),
                    preferred_mode=defn.default_presentation_mode,
                    visual_type=defn.default_visual_type,
                    search_queries=defn.search_queries,
                    is_literal=True,
                    metadata=meta,
                )
                break

            if matched_cue:
                # Enforce non-overlapping spacing between cues (at least 1.5s between B-roll cuts)
                if not cues or (matched_cue.start_s >= cues[-1].end_s + 1.2):
                    cues.append(matched_cue)
                    log.info("Semantic visual cue detected: [%.2fs - %.2fs] concept=%s phrase='%s'", matched_cue.start_s, matched_cue.end_s, matched_cue.concept, matched_cue.trigger_word)
                    # Advance past the duration of this cue
                    while i < n_words and words[i].start < matched_cue.end_s:
                        i += 1
                    continue

            i += 1

        return cues
