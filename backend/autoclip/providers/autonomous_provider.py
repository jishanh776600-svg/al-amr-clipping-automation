"""Autonomous Highlight Provider for AL AMR.

Provides deterministic, heuristic- and guideline-driven highlight detection
with zero external API key requirements, zero token costs, and 100% offline reliability.
"""

from __future__ import annotations

import json
import logging
import re

from .base import (
    ClipCandidate,
    ClipCandidates,
    DetectionConfig,
    LLMProvider,
    ProviderStatus,
    TranscriptWindow,
)

log = logging.getLogger(__name__)


class AutonomousProvider(LLMProvider):
    """Deterministic highlight detector driven by speech density, hook indicators, and guidelines."""

    name = "autonomous"
    requires_key = False

    def __init__(self, model: str = "al-amr-autonomous-v1", *, api_key: str | None = None, base_url: str | None = None):
        super().__init__(model=model or "al-amr-autonomous-v1", api_key=api_key, base_url=base_url)

    async def health_check(self) -> ProviderStatus:
        return ProviderStatus(
            name=self.name,
            available=True,
            detail="Ready (Autonomous Heuristic & Guideline Engine)",
            models=[self.model],
        )

    async def detect_highlights(
        self, window: TranscriptWindow, config: DetectionConfig
    ) -> ClipCandidates:
        """Detect candidate highlight ranges directly from the window text and indices."""
        # Find word token indices in window text: "[0]word [1]another"
        tokens = re.findall(r"\[(\d+)\]([^\s\[\]]+)", window.text)
        if not tokens:
            return ClipCandidates(clips=[])

        word_count = len(tokens)
        if word_count < 10:
            return ClipCandidates(clips=[])

        first_idx = int(tokens[0][0])
        last_idx = int(tokens[-1][0])

        candidates: list[ClipCandidate] = []
        
        # Estimate words per second (~2.5 wps)
        wps = 2.5
        min_words = max(8, int(config.min_duration_s * wps * 0.7))
        max_words = min(word_count, int(config.max_duration_s * wps * 1.3))
        step = max(min_words // 2, 10)

        for start_rel in range(0, word_count, step):
            end_rel = min(word_count - 1, start_rel + max_words - 1)
            rel_len = end_rel - start_rel + 1
            if rel_len < min_words:
                continue

            start_w_idx = int(tokens[start_rel][0])
            end_w_idx = int(tokens[end_rel][0])

            chunk_words = [t[1] for t in tokens[start_rel : end_rel + 1]]
            chunk_text = " ".join(chunk_words)

            hook = chunk_text[:60].strip()
            title = chunk_text[:40].strip() + "..."
            
            # Simple scoring heuristics: length balance + engagement markers
            score = 80
            lower = chunk_text.lower()
            if any(q in lower for q in ("how", "why", "what", "secret", "never", "always", "important", "critical", "best", "truth")):
                score += 10
            if any(c in chunk_text for c in ("!", "?", ":")):
                score += 5

            candidates.append(
                ClipCandidate(
                    start_word_index=start_w_idx,
                    end_word_index=end_w_idx,
                    title=title,
                    hook=hook,
                    score=min(95, score),
                    reason="Autonomous speech density & hook detection",
                )
            )
            if len(candidates) >= config.max_clips:
                break

        return ClipCandidates(clips=candidates)

    async def _complete(self, system: str, user: str, config: DetectionConfig) -> str:
        return json.dumps({"clips": []})
