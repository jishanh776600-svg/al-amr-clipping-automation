"""Word-Level Kinetic ASS Subtitle Generation Engine."""

import re
from typing import List, Optional
from clipping.contracts.perception import WordTimestamp
from clipping.rendering.base import SubtitleGenerator
from clipping.rendering.styles import SubtitleStyleConfig
from clipping.rendering.exceptions import SubtitleGenerationError
from clipping.logging.logger import get_logger

logger = get_logger("clipping.rendering.subtitles")


def format_ass_time(seconds: float) -> str:
    """Formats seconds into ASS timestamp format H:MM:SS.cc (centiseconds)."""
    sec_clamped = max(0.0, seconds)
    hrs = int(sec_clamped // 3600)
    mins = int((sec_clamped % 3600) // 60)
    secs = int(sec_clamped % 60)
    centis = int(round((sec_clamped - int(sec_clamped)) * 100))
    if centis >= 100:
        centis = 99
    return f"{hrs}:{mins:02d}:{secs:02d}.{centis:02d}"


def escape_ass_text(text: str) -> str:
    """Escapes special characters in ASS script lines."""
    # Replace backslashes and brackets
    cleaned = text.replace("\\", "\\\\").replace("{", "(").replace("}", ")")
    return cleaned.strip()


class AssSubtitleGenerator(SubtitleGenerator):
    """
    Generates ASS format subtitles with word-level karaoke timing and safe-zone styling.
    Normalizes timestamps to clip-local coordinates.
    """

    def generate_subtitles(
        self,
        words: List[WordTimestamp],
        clip_start: float,
        clip_end: float,
        style: Optional[SubtitleStyleConfig] = None,
    ) -> str:
        if clip_end <= clip_start:
            raise SubtitleGenerationError(
                f"Invalid clip boundaries: clip_end ({clip_end}) must be > clip_start ({clip_start})"
            )

        active_style = style or SubtitleStyleConfig()

        # 1. Normalize word timestamps to clip-local time coordinates
        normalized_words: List[WordTimestamp] = []
        for w in words:
            if w.end <= clip_start or w.start >= clip_end:
                continue

            t_start = max(0.0, w.start - clip_start)
            t_end = min(clip_end - clip_start, w.end - clip_start)

            if t_end > t_start:
                word_clean = escape_ass_text(w.word)
                if word_clean:
                    normalized_words.append(
                        WordTimestamp(
                            word=word_clean,
                            start=round(t_start, 3),
                            end=round(t_end, 3),
                            probability=w.probability,
                            speaker_id=w.speaker_id,
                        )
                    )

        if not normalized_words:
            logger.info("No words found in clip interval, generating empty subtitle script")
            return self._build_ass_header(active_style) + "\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"

        # 2. Group normalized words into compact subtitle cards (3-4 words)
        cards: List[List[WordTimestamp]] = []
        current_card: List[WordTimestamp] = []

        for w in normalized_words:
            current_card.append(w)
            # Break card if word count reached or punctuation encountered
            has_ending_punct = bool(re.search(r"[.!?]$", w.word))
            if len(current_card) >= active_style.words_per_card or has_ending_punct:
                cards.append(current_card)
                current_card = []

        if current_card:
            cards.append(current_card)

        # 3. Generate ASS Events for each card with karaoke highlighting
        events_lines: List[str] = [
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]

        for card in cards:
            card_start = card[0].start
            card_end = card[-1].end
            if card_end <= card_start:
                card_end = card_start + 0.5

            # For each word in the card, create a highlighted dialogue event
            for active_idx, active_word in enumerate(card):
                w_start_str = format_ass_time(active_word.start)
                w_end_str = format_ass_time(active_word.end)

                # Format words: active word in highlight color, other words in primary color
                word_parts: List[str] = []
                for idx, w in enumerate(card):
                    if idx == active_idx:
                        # Highlight active spoken word
                        word_parts.append(f"{{\\c{active_style.highlight_color}}}{w.word}{{\\c{active_style.primary_color}}}")
                    else:
                        word_parts.append(w.word)

                dialogue_text = " ".join(word_parts)
                events_lines.append(
                    f"Dialogue: 0,{w_start_str},{w_end_str},Default,,0,0,0,,{dialogue_text}"
                )

        header = self._build_ass_header(active_style)
        return header + "\n" + "\n".join(events_lines) + "\n"

    def _build_ass_header(self, style: SubtitleStyleConfig) -> str:
        """Constructs ASS v4.00+ Script Info and Styles header."""
        return f"""[Script Info]
Title: Clipping Automation Vertical Subtitles
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{style.font_name},{style.font_size},{style.primary_color},&H000000FF,{style.outline_color},{style.shadow_color},-1,0,0,0,100,100,0,0,1,{style.outline_width},{style.shadow_depth},{style.alignment},{style.margin_l},{style.margin_r},{style.margin_v},1
"""
