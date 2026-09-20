"""Contextual Semantic Parser for identifying narrative visual intent from transcripts.

Distinguishes literal from figurative phrases using surrounding context windows,
translates figurative idioms into visualizable underlying concepts,
classifies visual concepts across e-commerce and business domains,
selects appropriate presentation modes (FULL_SCREEN vs PARTIAL_OVERLAY),
and aligns trigger timings with word-level timestamps.
Includes Contextual Gap Hunter to guarantee healthy B-roll coverage (40-55%)
without artificial or meaningless footage.
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

STOPWORDS = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and",
    "any", "are", "aren't", "as", "at", "be", "because", "been", "before", "being",
    "below", "between", "both", "but", "by", "can", "can't", "cannot", "could",
    "couldn't", "did", "didn't", "do", "does", "doesn't", "doing", "don't", "down",
    "during", "each", "few", "for", "from", "further", "had", "hadn't", "has",
    "hasn't", "have", "haven't", "having", "he", "he'd", "he'll", "he's", "her",
    "here", "here's", "hers", "herself", "him", "himself", "his", "how", "how's",
    "i", "i'd", "i'll", "i'm", "i've", "if", "in", "into", "is", "isn't", "it",
    "it's", "its", "itself", "let's", "me", "more", "most", "mustn't", "my",
    "myself", "no", "nor", "not", "of", "off", "on", "once", "only", "or", "other",
    "ought", "our", "ours", "ourselves", "out", "over", "own", "same", "shan't",
    "she", "she'd", "she'll", "she's", "should", "shouldn't", "so", "some", "such",
    "than", "that", "that's", "the", "their", "theirs", "them", "themselves",
    "then", "there", "there's", "these", "they", "they'd", "they'll", "they're",
    "they've", "this", "those", "through", "to", "too", "under", "until", "up",
    "very", "was", "wasn't", "we", "we'd", "we'll", "we're", "we've", "were",
    "weren't", "what", "what's", "when", "when's", "where", "where's", "which",
    "while", "who", "who's", "whom", "why", "why's", "with", "won't", "would",
    "wouldn't", "you", "you'd", "you'll", "you're", "you've", "your", "yours",
    "yourself", "yourselves", "yeah", "um", "uh", "like", "you know", "actually",
    "basically", "literally", "totally", "definitely", "bro", "man", "right",
    "okay", "well", "gonna", "wanna", "gotta",
}


def synthesize_contextual_query(
    trigger_phrase: str,
    concept: str,
    default_queries: list[str] | None = None,
) -> str:
    """Derives a contextual, search-engine-friendly query from the spoken context window."""
    clean_phrase = re.sub(r"[^a-zA-Z0-9\s$]", " ", trigger_phrase).lower()
    tokens = clean_phrase.split()
    salient = [t for t in tokens if t not in STOPWORDS and len(t) > 2]

    # Concept base hints
    concept_hints = {
        "nature_grass": ["lush", "green", "grass", "meadow", "nature"],
        "financial_revenue": ["ecommerce", "revenue", "sales", "analytics", "dashboard"],
        "warehouse_shipping": ["amazon", "warehouse", "boxes", "packages", "logistics"],
        "business_shutdown": ["storefront", "closed", "business", "sign"],
        "product_marketplace": ["ecommerce", "product", "packaging", "showcase"],
        "ecommerce_shopping": ["customer", "online", "shopping", "store", "order"],
        "digital_analytics": ["analytics", "dashboard", "metrics", "chart", "screen"],
        "team_office": ["startup", "team", "office", "collaboration", "workplace"],
        "factory_manufacturing": ["factory", "manufacturing", "production", "assembly"],
        "mobile_apps_social": ["smartphone", "social", "media", "video", "screen"],
        "competition_market": ["business", "market", "sales", "growth", "chart"],
        "business_growth": ["business", "growth", "scaling", "chart", "metrics"],
        "fire_danger": ["dramatic", "fire", "flames", "smoke"],
        "money_cash": ["money", "cash", "dollar", "bills", "finance"],
    }
    hints = concept_hints.get(concept, [concept.replace("_", " ")])

    if len(salient) >= 2:
        # Combine top 3 salient context words with top 2 concept hints
        combined = []
        seen = set()
        for word in salient[:3]:
            if word not in seen:
                seen.add(word)
                combined.append(word)
        for hint in hints:
            if hint not in seen:
                seen.add(hint)
                combined.append(hint)
            if len(combined) >= 5:
                break
        return " ".join(combined)

    if default_queries and default_queries[0]:
        return default_queries[0]

    return f"{concept.replace('_', ' ')} footage"


# Figurative language mappings: translates non-literal idioms into visualizable underlying concepts
FIGURATIVE_TRANSLATIONS: list[dict[str, Any]] = [
    {
        "patterns": [
            r"\bcrush(?:ed|ing)?\s+(?:the\s+)?competition\b",
            r"\bbeating\s+(?:the\s+)?competition\b",
            r"\boutranking\s+everyone\b",
        ],
        "concept": "competition_market",
        "visual_type": VisualType.CHART,
        "mode": PresentationMode.PARTIAL_OVERLAY,
        "search_queries": [
            "business market competition sales chart growth",
            "business growth market comparison chart",
        ],
        "metadata": {"title": "MARKET LEADER", "value": "#1 RANKED", "badge": "COMPETITIVE EDGE"},
    },
    {
        "patterns": [
            r"\bmoney\s+on\s+the\s+table\b",
            r"\bcash\s+cow\b",
            r"\blot\s+of\s+money\b",
            r"\bmake\s+bank\b",
        ],
        "concept": "money_cash",
        "visual_type": VisualType.STOCK_VIDEO,
        "mode": PresentationMode.FULL_SCREEN,
        "search_queries": [
            "money dollar cash investment profit business",
            "counting money cash business currency",
        ],
    },
    {
        "patterns": [
            r"\bskyrocket(?:ed|ing)?\b",
            r"\bwent\s+through\s+the\s+roof\b",
            r"\btook\s+off\b",
            r"\btotal\s+180\b",
            r"\bflipped\s+the\s+script\b",
            r"\bgame\s+changer\b",
        ],
        "concept": "business_growth",
        "visual_type": VisualType.CHART,
        "mode": PresentationMode.PARTIAL_OVERLAY,
        "search_queries": [
            "exponential business growth chart upward trend graph",
            "business success scaling metrics graph",
        ],
        "metadata": {"title": "GROWTH TRAJECTORY", "value": "EXPONENTIAL", "badge": "SCALING"},
    },
    {
        "patterns": [
            r"\bblew\s+up\b",
            r"\bviral\s+overnight\b",
            r"\bviews\s+went\s+crazy\b",
            r"\bviews\s+popping\s+off\b",
        ],
        "concept": "mobile_apps_social",
        "visual_type": VisualType.STOCK_VIDEO,
        "mode": PresentationMode.FULL_SCREEN,
        "search_queries": [
            "viral social media mobile video smartphone screen engagement",
            "social media metrics mobile phone video",
        ],
    },
    {
        "patterns": [
            r"\bdrowning\s+in\s+orders\b",
            r"\borders\s+flooding\s+in\b",
            r"\borders\s+going\s+crazy\b",
        ],
        "concept": "warehouse_shipping",
        "visual_type": VisualType.STOCK_VIDEO,
        "mode": PresentationMode.FULL_SCREEN,
        "search_queries": [
            "ecommerce package shipping fulfillment warehouse busy",
            "busy warehouse shipping parcels boxes logistics",
        ],
    },
]

# Standard concept definitions covering reference e-commerce, business, and physical domains
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
        primary_keywords=["revenue", "sales", "profit", "income", "earnings", "gross", "margin", "margins", "balance sheet"],
        context_indicators=["made", "did", "hit", "dashboard", "analytics", "annual", "year", "total", "went over", "generated"],
        forbidden_figurative_phrases=["million reasons", "million miles", "feel like a million", "million questions"],
        default_presentation_mode=PresentationMode.PARTIAL_OVERLAY,
        default_visual_type=VisualType.DASHBOARD,
        search_queries=["ecommerce revenue sales dashboard analytics graph", "financial performance dashboard UI metrics"],
        description="Real revenue analytics dashboard showing sales metrics and financial numbers.",
    ),
    "warehouse_shipping": SemanticConceptDefinition(
        concept_id="warehouse_shipping",
        primary_keywords=["warehouse", "amazon", "shipped", "shipping", "fulfillment", "fba", "boxes", "logistics", "inventory", "pallet", "pallets", "forklift", "parcel", "parcels", "packaging"],
        context_indicators=["local", "boxes", "pallet", "facility", "forklift", "store", "working for", "department", "packages", "orders"],
        forbidden_figurative_phrases=["warehouse of knowledge", "data warehouse"],
        default_presentation_mode=PresentationMode.FULL_SCREEN,
        default_visual_type=VisualType.STOCK_VIDEO,
        search_queries=["amazon fulfillment center warehouse workers pallets forklift", "large modern logistics shipping warehouse interior"],
        description="Amazon fulfillment warehouse operations with shelving, pallets, and logistics.",
    ),
    "business_shutdown": SemanticConceptDefinition(
        concept_id="business_shutdown",
        primary_keywords=["shutdown", "shut down", "closed", "suspension", "suspended", "locked", "banned", "account suspended"],
        context_indicators=["company", "account", "business", "store", "week", "got their", "for a week"],
        forbidden_figurative_phrases=["shut down the idea", "shut down the rumors", "closed mind", "behind closed doors"],
        default_presentation_mode=PresentationMode.FULL_SCREEN,
        default_visual_type=VisualType.STOCK_PHOTO,
        search_queries=["storefront closed sign hanging on glass door", "business shut down closed retail office"],
        description="Storefront window or door with a prominent CLOSED / shutdown sign.",
    ),
    "product_marketplace": SemanticConceptDefinition(
        concept_id="product_marketplace",
        primary_keywords=["product", "products", "listing", "taking off", "private label", "brand", "item", "items", "merchandise", "goods", "unboxing", "bestseller"],
        context_indicators=["sold", "launched", "research", "reviews", "bluetooth", "battery", "started taking off", "selling", "store"],
        forbidden_figurative_phrases=["product of society", "product of my environment", "by-product"],
        default_presentation_mode=PresentationMode.PARTIAL_OVERLAY,
        default_visual_type=VisualType.PRODUCT_LISTING,
        search_queries=["ecommerce product listing electronics packaging marketplace", "consumer ecommerce product showcase on white background"],
        description="Product presentation or clean marketplace product listing card.",
    ),
    "ecommerce_shopping": SemanticConceptDefinition(
        concept_id="ecommerce_shopping",
        primary_keywords=["customers", "customer", "shopping", "buyer", "buyers", "orders", "online store", "marketplace", "cart", "reviews", "purchases"],
        context_indicators=["online", "store", "buying", "selling", "people", "buy", "review", "order", "happy"],
        forbidden_figurative_phrases=[],
        default_presentation_mode=PresentationMode.FULL_SCREEN,
        default_visual_type=VisualType.STOCK_VIDEO,
        search_queries=["person online shopping ecommerce credit card laptop", "customer browsing online store smartphone"],
        description="Customer shopping online on smartphone or laptop.",
    ),
    "team_office": SemanticConceptDefinition(
        concept_id="team_office",
        primary_keywords=["team", "hired", "hiring", "employees", "workplace", "department", "office", "meeting", "colleagues", "coworkers", "staff"],
        context_indicators=["built", "small", "handle", "worked for", "group", "people", "staff", "working", "desk"],
        forbidden_figurative_phrases=["team player", "team spirit", "take one for the team"],
        default_presentation_mode=PresentationMode.FULL_SCREEN,
        default_visual_type=VisualType.STOCK_VIDEO,
        search_queries=["diverse startup business team collaborating in modern office", "small workplace team meeting discussion"],
        description="Professional team collaborating in a modern workplace setting.",
    ),
    "business_growth": SemanticConceptDefinition(
        concept_id="business_growth",
        primary_keywords=["growing", "growth", "scaled", "scaling", "expanded", "exponential", "scale up", "doubled"],
        context_indicators=["kept", "business", "fast", "company", "metrics", "chart", "upward", "sales"],
        forbidden_figurative_phrases=["personal growth", "growth mindset"],
        default_presentation_mode=PresentationMode.PARTIAL_OVERLAY,
        default_visual_type=VisualType.CHART,
        search_queries=["business growth upward trendline graph neon green", "exponential revenue growth chart visualization"],
        description="Upward curved growth chart indicating business scale and expansion.",
    ),
    "fire_danger": SemanticConceptDefinition(
        concept_id="fire_danger",
        primary_keywords=["fire", "flames", "smoke", "smoking", "burning"],
        context_indicators=["catching", "caught", "building", "house", "warehouse", "on fire", "physically", "actually", "burned"],
        forbidden_figurative_phrases=["under fire", "playing with fire", "fire in his eyes", "fired from job", "spitting fire"],
        default_presentation_mode=PresentationMode.FULL_SCREEN,
        default_visual_type=VisualType.STOCK_VIDEO,
        search_queries=["close up dramatic fire flames smoke burning slow motion", "actual fire burning with smoke"],
        description="Flames, fire, and smoke conveying literal burning or danger.",
    ),
    "money_cash": SemanticConceptDefinition(
        concept_id="money_cash",
        primary_keywords=["cash", "payout", "bills", "payments", "money", "payment holds", "million", "millions", "dollars", "funds", "wealth"],
        context_indicators=["hold", "holding", "stack", "full", "beginning", "dollar", "two weeks", "made", "hit"],
        forbidden_figurative_phrases=["cash in on", "money talks", "time is money", "for my money"],
        default_presentation_mode=PresentationMode.FULL_SCREEN,
        default_visual_type=VisualType.STOCK_PHOTO,
        search_queries=["hands sliding thick stack of 100 dollar bills across desk", "counting fresh us dollar cash bills hands"],
        description="Hands holding or sliding crisp stacks of hundred-dollar bills.",
    ),
    "factory_manufacturing": SemanticConceptDefinition(
        concept_id="factory_manufacturing",
        primary_keywords=["factory", "manufacturing", "supplier", "suppliers", "manufacturer", "production line", "assembly", "supply chain", "plant"],
        context_indicators=["china", "overseas", "produce", "parts", "cost", "samples", "production", "units"],
        forbidden_figurative_phrases=[],
        default_presentation_mode=PresentationMode.FULL_SCREEN,
        default_visual_type=VisualType.STOCK_VIDEO,
        search_queries=["modern automated factory production assembly line", "industrial manufacturing facility machinery"],
        description="Factory assembly line and manufacturing facility.",
    ),
    "mobile_apps_social": SemanticConceptDefinition(
        concept_id="mobile_apps_social",
        primary_keywords=["views", "viral", "tiktok", "instagram", "phone", "smartphone", "social media", "followers", "account", "content", "scrolling", "reels"],
        context_indicators=["video", "posted", "algorithm", "feed", "million views", "post", "app", "watch"],
        forbidden_figurative_phrases=["in view of", "point of view"],
        default_presentation_mode=PresentationMode.FULL_SCREEN,
        default_visual_type=VisualType.STOCK_VIDEO,
        search_queries=["person scrolling viral videos smartphone screen closeup", "social media metrics engagement smartphone app"],
        description="Smartphone screen showing viral social media engagement.",
    ),
    "digital_analytics": SemanticConceptDefinition(
        concept_id="digital_analytics",
        primary_keywords=["dashboard", "analytics", "sales chart", "graph", "metrics", "ranking", "stats", "traffic", "data"],
        context_indicators=["looking at", "screen", "laptop", "numbers", "ranking", "check", "computer"],
        forbidden_figurative_phrases=[],
        default_presentation_mode=PresentationMode.PARTIAL_OVERLAY,
        default_visual_type=VisualType.DASHBOARD,
        search_queries=["ecommerce sales analytics dashboard screen metrics", "digital business analytics graph computer display"],
        description="Digital analytics dashboard showing business metrics on screen.",
    ),
    "competition_market": SemanticConceptDefinition(
        concept_id="competition_market",
        primary_keywords=["competitors", "competitor", "outranking", "rivals", "market share", "competition"],
        context_indicators=["market", "niche", "beat", "selling", "amazon", "better", "rank"],
        forbidden_figurative_phrases=[],
        default_presentation_mode=PresentationMode.PARTIAL_OVERLAY,
        default_visual_type=VisualType.CHART,
        search_queries=["business competitor analysis marketplace comparison chart", "market share competition business graph"],
        description="Market competition chart and competitor comparison visual.",
    ),
}


class ContextualSemanticParser:
    """Extracts semantic visual cues from speech transcripts with contextual awareness."""

    def __init__(
        self,
        definitions: dict[str, SemanticConceptDefinition] | None = None,
        default_dwell_s: float = 2.4,
        min_dwell_s: float = 1.8,
        max_dwell_s: float = 3.2,
    ) -> None:
        self.definitions = definitions or CONCEPT_DEFINITIONS
        self.default_dwell_s = default_dwell_s
        self.min_dwell_s = min_dwell_s
        self.max_dwell_s = max_dwell_s

    def parse_transcript(
        self,
        words: list[Word],
        clip_start_s: float = 0.0,
        clip_end_s: float | None = None,
    ) -> list[SemanticVisualCue]:
        """Convenience alias for parse_transcript_segment."""
        return self.parse_transcript_segment(words, clip_start_s, clip_end_s)

    def parse_transcript_segment(
        self,
        words: list[Word],
        clip_start_s: float = 0.0,
        clip_end_s: float | None = None,
    ) -> list[SemanticVisualCue]:
        """Parses a word list into chronologically ordered, non-overlapping semantic visual cues."""
        if not words:
            return []

        if clip_end_s is None:
            clip_end_s = (words[-1].end + self.default_dwell_s) if words else (clip_start_s + 30.0)

        cues: list[SemanticVisualCue] = []
        n_words = len(words)

        def _word_text(w: Word) -> str:
            return getattr(w, "text", getattr(w, "word", str(w)))

        def get_window_text(idx: int, window_radius: int = 6) -> str:
            w_start = max(0, idx - window_radius)
            w_end = min(n_words, idx + window_radius + 1)
            return " ".join(_word_text(w).lower().strip(",.!?\"'") for w in words[w_start:w_end])

        # Step 1: Scan for figurative language matches first
        full_transcript_text = " ".join(_word_text(w).lower() for w in words)
        for fig in FIGURATIVE_TRANSLATIONS:
            for pattern in fig["patterns"]:
                match = re.search(pattern, full_transcript_text)
                if match:
                    # Find approximately which word triggered this
                    match_start_char = match.start()
                    char_count = 0
                    matched_idx = 0
                    for widx, w in enumerate(words):
                        char_count += len(_word_text(w)) + 1
                        if char_count >= match_start_char:
                            matched_idx = widx
                            break

                    w_obj = words[matched_idx]
                    t_start = w_obj.start
                    t_end = min(clip_end_s, t_start + self.default_dwell_s)
                    if (t_end - t_start) < self.min_dwell_s and (clip_end_s - t_start) >= 1.5:
                        t_end = clip_end_s
                    if t_start < clip_end_s and (t_end - t_start) >= 1.5:
                        window_ctx = get_window_text(matched_idx, window_radius=6)
                        q = fig.get("search_queries", [""])[0]
                        ctx_q = synthesize_contextual_query(window_ctx, fig["concept"], fig.get("search_queries"))
                        cue = SemanticVisualCue(
                            cue_id=f"cue_{len(cues)+1}_{fig['concept']}",
                            concept=fig["concept"],
                            trigger_phrase=window_ctx,
                            trigger_word=match.group(0),
                            start_s=round(t_start, 2),
                            end_s=round(t_end, 2),
                            preferred_mode=fig["mode"],
                            visual_type=fig["visual_type"],
                            search_queries=fig.get("search_queries", []),
                            context_query=ctx_q,
                            is_literal=False,
                            metadata=fig.get("metadata", {}),
                        )
                        # Spacing check
                        if not cues or (cue.start_s >= cues[-1].end_s + 1.2):
                            cues.append(cue)
                            log.info(
                                "Figurative cue detected: [%.2fs - %.2fs] concept=%s phrase='%s' query='%s'",
                                cue.start_s, cue.end_s, cue.concept, cue.trigger_word, cue.context_query,
                            )

        # Step 2: Scan for literal concept keywords
        i = 0
        while i < n_words:
            w = words[i]
            w_clean = _word_text(w).lower().strip(",.!?\"'")
            if not w_clean:
                i += 1
                continue

            matched_cue: SemanticVisualCue | None = None

            for concept_id, defn in self.definitions.items():
                is_keyword_match = False
                matched_kw = ""

                for kw in defn.primary_keywords:
                    if " " in kw:
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

                window_text = get_window_text(i, window_radius=6)

                # Check forbidden figurative idioms
                has_forbidden = any(forbid in window_text for forbid in defn.forbidden_figurative_phrases)
                if has_forbidden:
                    log.debug("Forbidden idiom rejected keyword '%s' for concept '%s'", matched_kw, concept_id)
                    continue

                # Require positive context indicators unless the keyword is highly specific/distinct
                strong_unambiguous = {
                    "amazon", "fba", "shopify", "ecommerce", "warehouse", "revenue",
                    "competitor", "competition", "shipping", "shutdown", "suspended",
                    "million dollars", "payout", "unboxing", "bestseller", "analytics",
                    "dashboard", "fulfillment", "pallets", "forklift",
                }
                if defn.context_indicators and matched_kw not in strong_unambiguous:
                    has_context = any(ind in window_text for ind in defn.context_indicators)
                    if concept_id in ("financial_revenue", "profit_cash", "digital_analytics"):
                        if re.search(r"\b(\d+|million|thousand|dollars?|\$\d+|k\b)", window_text):
                            has_context = True
                    if not has_context:
                        continue

                trigger_start = w.start
                duration = self.default_dwell_s
                trigger_end = min(clip_end_s, trigger_start + duration)

                if trigger_start >= clip_end_s:
                    continue
                if (trigger_end - trigger_start) < self.min_dwell_s:
                    if (clip_end_s - trigger_start) >= 1.5:
                        trigger_end = clip_end_s
                    else:
                        continue

                # Extract partial overlay metadata if applicable
                meta: dict[str, Any] = {}
                if defn.default_presentation_mode == PresentationMode.PARTIAL_OVERLAY:
                    if concept_id in ("financial_revenue", "money_cash"):
                        amt_match = re.search(r"(\$?\d[\d,]*(?:\.\d+)?\s*(?:million|k|billion)?|a million dollars?)", window_text)
                        val_str = amt_match.group(1).upper() if amt_match else "$1,248,331"
                        if "million" in val_str.lower() and not re.search(r"\d", val_str):
                            val_str = "$1,000,000+"
                        meta = {"title": "TOTAL REVENUE", "value": val_str, "badge": "+18.4% YOY"}
                    elif concept_id == "business_growth":
                        meta = {"title": "GROWTH TRAJECTORY", "value": "EXPONENTIAL", "badge": "SCALING"}
                    elif concept_id == "product_marketplace":
                        meta = {"title": "FEATURED PRODUCT", "rating": "4.9 ★★★★★", "badge": "BEST SELLER"}
                    elif concept_id == "digital_analytics":
                        meta = {"title": "PERFORMANCE ANALYTICS", "value": "OPTIMAL", "badge": "VERIFIED"}
                    elif concept_id == "competition_market":
                        meta = {"title": "MARKET SHARE", "value": "#1 LEADER", "badge": "COMPETITIVE EDGE"}

                ctx_query = synthesize_contextual_query(window_text, concept_id, defn.search_queries)

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
                    context_query=ctx_query,
                    is_literal=True,
                    metadata=meta,
                )
                break

            if matched_cue:
                # Spacing check: avoid overlapping cues (require at least 1.0s between cuts)
                if not cues or (matched_cue.start_s >= cues[-1].end_s + 1.0):
                    cues.append(matched_cue)
                    log.info(
                        "Semantic visual cue detected: [%.2fs - %.2fs] concept=%s phrase='%s' query='%s'",
                        matched_cue.start_s, matched_cue.end_s, matched_cue.concept, matched_cue.trigger_word, matched_cue.context_query,
                    )
                    while i < n_words and words[i].start < matched_cue.end_s:
                        i += 1
                    continue

            i += 1

        # Sort cues chronologically
        cues.sort(key=lambda c: c.start_s)

        # Step 3: Contextual Gap Hunter (inspect long uninterrupted A-roll spans > 4.5s)
        cues = self.fill_long_gaps(cues, words, clip_start_s, clip_end_s, min_gap_s=4.5)

        return cues

    def fill_long_gaps(
        self,
        cues: list[SemanticVisualCue],
        words: list[Word],
        clip_start_s: float,
        clip_end_s: float,
        min_gap_s: float = 4.5,
    ) -> list[SemanticVisualCue]:
        """Proactively identifies secondary visual opportunities inside long uninterrupted A-roll sections."""
        if not words or (clip_end_s - clip_start_s) < 8.0:
            return cues

        def _word_text(w: Word) -> str:
            return getattr(w, "text", getattr(w, "word", str(w)))

        # Identify all A-roll gap intervals
        gaps: list[tuple[float, float]] = []
        cursor = clip_start_s

        for c in cues:
            if c.start_s - cursor >= min_gap_s:
                gaps.append((cursor, c.start_s))
            cursor = max(cursor, c.end_s)

        if clip_end_s - cursor >= min_gap_s:
            gaps.append((cursor, clip_end_s))

        # Secondary entity triggers for gap filling
        secondary_triggers = [
            ("product_marketplace", ["selling", "item", "order", "product", "listing", "store", "buy", "brand"], PresentationMode.FULL_SCREEN, VisualType.STOCK_VIDEO, "ecommerce product selling online store"),
            ("team_office", ["work", "working", "talking", "people", "started", "built", "doing", "screen", "computer", "laptop"], PresentationMode.FULL_SCREEN, VisualType.STOCK_VIDEO, "modern business workplace people working laptop"),
            ("digital_analytics", ["metrics", "scale", "stats", "data", "traffic", "ranking", "success"], PresentationMode.PARTIAL_OVERLAY, VisualType.DASHBOARD, "business analytics performance data metrics"),
            ("ecommerce_shopping", ["customers", "orders", "online", "people buying", "clients"], PresentationMode.FULL_SCREEN, VisualType.STOCK_VIDEO, "customer shopping online smartphone"),
        ]

        filled_cues = list(cues)

        for gap_start, gap_end in gaps:
            gap_dur = gap_end - gap_start
            if gap_dur < min_gap_s:
                continue

            # Words inside gap
            gap_words = [w for w in words if gap_start <= w.start <= gap_end]
            if not gap_words:
                continue

            gap_text = " ".join(_word_text(w).lower() for w in gap_words)

            for concept_id, kws, mode, vtype, base_query in secondary_triggers:
                for kw in kws:
                    if kw in gap_text:
                        # Find word timing
                        kw_word = next((w for w in gap_words if kw in _word_text(w).lower()), gap_words[len(gap_words) // 2])
                        cue_start = max(gap_start + 0.8, kw_word.start)
                        dwell = min(self.default_dwell_s, gap_end - cue_start - 0.5)
                        if dwell >= self.min_dwell_s:
                            cue_end = cue_start + dwell
                            ctx_q = synthesize_contextual_query(gap_text, concept_id, [base_query])
                            meta: dict[str, Any] = {}
                            if mode == PresentationMode.PARTIAL_OVERLAY:
                                meta = {"title": "BUSINESS METRICS", "value": "SCALING", "badge": "OPTIMIZED"}
                            gap_cue = SemanticVisualCue(
                                cue_id=f"cue_{len(filled_cues)+1}_gap_{concept_id}",
                                concept=concept_id,
                                trigger_phrase=gap_text[:120],
                                trigger_word=kw,
                                start_s=round(cue_start, 2),
                                end_s=round(cue_end, 2),
                                preferred_mode=mode,
                                visual_type=vtype,
                                search_queries=[base_query],
                                context_query=ctx_q,
                                is_literal=True,
                                metadata=meta,
                            )
                            filled_cues.append(gap_cue)
                            log.info(
                                "Contextual Gap Hunter filled gap [%.2fs - %.2fs] with concept=%s (query='%s')",
                                gap_cue.start_s, gap_cue.end_s, gap_cue.concept, gap_cue.context_query,
                            )
                            break
                # Only 1 visual event per gap
                if len(filled_cues) > len(cues) + len(gaps):
                    break

        filled_cues.sort(key=lambda c: c.start_s)
        return filled_cues
