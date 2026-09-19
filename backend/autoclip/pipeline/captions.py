"""Caption generation — word timings to burned-in ASS subtitles.

Step 19: Operator-Selectable Dynamic Caption Styles.
Supported primary styles:
- Classic Professional (default): Clean, modern, minimal captions with professional
  Inter typography, subtle word highlighting, and restrained transitions.
- Rich Dynamic: High-energy short-form captions with Anton typography, active-word
  pop animations, and heightened hook/climax/CTA treatment.

The engine dynamically coordinates:
- Whisper word-level timestamps & phrase-level grouping
- Narrative retention cues (Hook window, Climax, CTA)
- Visual safety & face avoidance via CropPath keyframes
- Pre-render CaptionQualityGate (CAPTION_PASS, CAPTION_WARN, CAPTION_REJECT)
- Deterministic fallback to safe basic captions preventing pipeline hangs
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pysubs2

from ..campaign.models_intelligence import CampaignSpecification
from ..db.models import (
    CaptionOptimizationRecord,
    Clip,
    ClipSpecificationRecord,
    RetentionOptimizationRecord,
    VisualCompositionRecord,
    new_id,
    utcnow,
)
from .reframe.croppath import CropPath
from .transcript import Word

log = logging.getLogger(__name__)

FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"

#: Default words per caption event
DEFAULT_MAX_WORDS = 5
#: Max inter-word pause ending a group
DEFAULT_MAX_GAP_S = 0.45

#: Reference canvas height against which style metrics scale
REFERENCE_HEIGHT = 1920


def hex_to_ass(colour: str, alpha: int = 0) -> pysubs2.Color:
    """Convert ``#RRGGBB`` to a pysubs2 colour."""
    r, g, b = _rgb(colour)
    return pysubs2.Color(r, g, b, alpha)


def _rgb(colour: str) -> tuple[int, int, int]:
    value = colour.lstrip("#")
    if len(value) != 6:
        raise ValueError(f"Expected #RRGGBB, got {colour!r}")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def ass_colour_override(colour: str) -> str:
    r"""Format ``#RRGGBB`` as an inline ASS colour value, e.g. ``&H00E5FF&``."""
    r, g, b = _rgb(colour)
    return f"&H{b:02X}{g:02X}{r:02X}&"


@dataclass
class CaptionStyle:
    """A caption style preset."""

    key: str
    label: str
    description: str

    font: str = "Inter"
    font_file: str = "Inter-Variable.ttf"
    size_ratio: float = 0.040
    primary: str = "#FFFFFF"
    accent: str | None = "#E2E8F0"
    outline: str = "#000000"
    outline_width: float = 2.0
    shadow: float = 1.0
    bold: bool = False
    all_caps: bool = False
    margin_v_ratio: float = 0.18
    max_words: int = DEFAULT_MAX_WORDS
    animation: str = "subtle"  # "none" | "subtle" | "scale" | "karaoke"
    scale_percent: int = 103
    boxed: bool = False
    box_colour: str = "#000000"
    box_alpha: int = 40

    # Narrative retention cues
    hook_accent: str | None = None
    hook_scale: int = 105
    climax_accent: str | None = None
    climax_scale: int = 108
    cta_accent: str | None = None
    cta_scale: int = 105

    # Visual safety bounds
    min_margin_v_ratio: float = 0.12
    max_margin_v_ratio: float = 0.38

    @property
    def font_path(self) -> Path:
        return FONT_DIR / self.font_file


# --------------------------------------------------------------------------
# Style Presets (Step 19)
# --------------------------------------------------------------------------

CLASSIC_PROFESSIONAL = CaptionStyle(
    key="classic_professional",
    label="Classic Professional",
    description="Clean, modern, minimal captions with professional typography, subtle word highlighting, and restrained transitions.",
    font="Inter",
    font_file="Inter-Variable.ttf",
    size_ratio=0.038,
    primary="#FFFFFF",
    accent="#E2E8F0",  # Soft slate/white active word tint
    outline="#000000",
    outline_width=2.0,
    shadow=1.0,
    bold=False,
    all_caps=False,
    margin_v_ratio=0.18,
    max_words=6,
    animation="subtle",
    scale_percent=102,
    hook_accent="#93C5FD",  # Soft sky tint
    hook_scale=103,
    climax_accent="#FCD34D",  # Soft warm amber tint
    climax_scale=104,
    cta_accent="#6EE7B7",  # Soft emerald tint
    cta_scale=103,
)

RICH_DYNAMIC = CaptionStyle(
    key="rich_dynamic",
    label="Rich Dynamic",
    description="High-energy short-form captions with word/phrase emphasis, controlled pop animations, and heightened hook/climax treatment.",
    font="Anton",
    font_file="Anton-Regular.ttf",
    size_ratio=0.058,
    primary="#FFFFFF",
    accent="#FFE500",  # Vibrant yellow active word
    outline="#000000",
    outline_width=4.5,
    shadow=0.0,
    bold=True,
    all_caps=True,
    margin_v_ratio=0.24,
    max_words=4,
    animation="scale",
    scale_percent=116,
    hook_accent="#38BDF8",  # Sky blue punch during hook
    hook_scale=118,
    climax_accent="#FB923C",  # Amber/coral pop during climax
    climax_scale=120,
    cta_accent="#34D399",  # Emerald pop during CTA
    cta_scale=116,
)

KYLE_KIRSHNER_CORE = CaptionStyle(
    key="kyle_kirshner_core",
    label="Kyle Kirshner Core",
    description="High-converting short-form style: Anton all-caps, 1-3 word phrase pop, semantic highlight coloring (neon green for money/numbers/growth, bright red for loss/danger/shutdown, yellow for hook/subject).",
    font="Anton",
    font_file="Anton-Regular.ttf",
    size_ratio=0.056,
    primary="#FFFFFF",
    accent="#FFE500",  # Vibrant yellow
    outline="#000000",
    outline_width=4.5,
    shadow=0.0,
    bold=True,
    all_caps=True,
    margin_v_ratio=0.22,
    max_words=3,
    animation="phrase_pop",
    scale_percent=115,
    hook_accent="#FFE500",
    hook_scale=118,
    climax_accent="#00FF00",
    climax_scale=120,
    cta_accent="#00FF00",
    cta_scale=115,
)

PRESETS: dict[str, CaptionStyle] = {
    "classic_professional": CLASSIC_PROFESSIONAL,
    "rich_dynamic": RICH_DYNAMIC,
    "kyle_kirshner_core": KYLE_KIRSHNER_CORE,
    "clean_lower": CaptionStyle(
        key="clean_lower",
        label="Clean Lower",
        description="Minimal lower third, no animation. For talks and professional cuts.",
        font="Inter",
        font_file="Inter-Variable.ttf",
        size_ratio=0.034,
        primary="#FFFFFF",
        accent=None,
        outline_width=2.0,
        shadow=1.0,
        bold=False,
        all_caps=False,
        margin_v_ratio=0.10,
        animation="none",
        max_words=8,
    ),
    "bold_pop": CaptionStyle(
        key="bold_pop",
        label="Bold Pop",
        description="Punchy, modern vertical video captions with active word highlighting.",
        font="Anton",
        font_file="Anton-Regular.ttf",
        size_ratio=0.056,
        primary="#FFFFFF",
        accent="#FFE600",
        outline_width=4.0,
        shadow=2.0,
        bold=False,
        all_caps=True,
        margin_v_ratio=0.22,
        animation="scale",
        max_words=4,
    ),
    "karaoke_fill": CaptionStyle(
        key="karaoke_fill",
        label="Karaoke Fill",
        description="Words fill with colour exactly as they're spoken.",
        font="Anton",
        font_file="Anton-Regular.ttf",
        size_ratio=0.058,
        primary="#FFFFFF",
        accent="#31E981",
        outline_width=4.0,
        all_caps=True,
        animation="karaoke",
        max_words=5,
    ),
    "boxed": CaptionStyle(
        key="boxed",
        label="Boxed",
        description="High-contrast text on a solid block. Readable on any footage.",
        font="Anton",
        font_file="Anton-Regular.ttf",
        size_ratio=0.052,
        primary="#FFFFFF",
        accent="#00F0FF",
        boxed=True,
        box_colour="#000000",
        box_alpha=180,
        outline_width=0.0,
        shadow=0.0,
        all_caps=True,
        animation="none",
        max_words=5,
    ),
    "neon_glow": CaptionStyle(
        key="neon_glow",
        label="Neon Glow",
        description="Vibrant cyberpunk aesthetic with electric cyan and magenta punch.",
        font="Archivo",
        font_file="Archivo-Variable.ttf",
        size_ratio=0.052,
        primary="#00F5FF",
        accent="#FF007F",
        outline="#000000",
        outline_width=3.5,
        shadow=1.5,
        bold=True,
        all_caps=True,
        animation="scale",
        scale_percent=114,
        max_words=4,
    ),
    "minimal_luxury": CaptionStyle(
        key="minimal_luxury",
        label="Minimal Luxury",
        description="Elegant editorial typography with warm champagne and gold accents.",
        font="Instrument Serif",
        font_file="InstrumentSerif-Italic.ttf",
        size_ratio=0.046,
        primary="#FAF8F5",
        accent="#D4AF37",
        outline="#1A1A1A",
        outline_width=1.5,
        shadow=1.0,
        bold=False,
        all_caps=False,
        animation="subtle",
        margin_v_ratio=0.16,
        max_words=6,
    ),
    "cyber_glitch": CaptionStyle(
        key="cyber_glitch",
        label="Cyber Glitch",
        description="High-energy techno style with radioactive green and bright yellow.",
        font="Anton",
        font_file="Anton-Regular.ttf",
        size_ratio=0.056,
        primary="#00FF66",
        accent="#FFFF00",
        outline="#000000",
        outline_width=4.5,
        shadow=2.0,
        bold=True,
        all_caps=True,
        animation="scale",
        scale_percent=118,
        max_words=3,
    ),
    "editorial_serif": CaptionStyle(
        key="editorial_serif",
        label="Editorial Serif",
        description="Refined publication-style serif captions with warm amber focus.",
        font="Instrument Serif",
        font_file="InstrumentSerif-Regular.ttf",
        size_ratio=0.044,
        primary="#FFFFFF",
        accent="#F59E0B",
        outline="#000000",
        outline_width=1.8,
        shadow=1.2,
        bold=False,
        all_caps=False,
        animation="subtle",
        margin_v_ratio=0.17,
        max_words=6,
    ),
    "fire_punch": CaptionStyle(
        key="fire_punch",
        label="Fire Punch",
        description="Intense energetic captions featuring fiery orange and red accents.",
        font="Anton",
        font_file="Anton-Regular.ttf",
        size_ratio=0.058,
        primary="#FFFFFF",
        accent="#FF4500",
        outline="#1E0500",
        outline_width=4.5,
        shadow=2.0,
        bold=True,
        all_caps=True,
        animation="scale",
        scale_percent=118,
        max_words=4,
    ),
    "sunset_warmth": CaptionStyle(
        key="sunset_warmth",
        label="Sunset Warmth",
        description="Warm dusk tones blending soft peach with bright coral pop.",
        font="Archivo",
        font_file="Archivo-Variable.ttf",
        size_ratio=0.050,
        primary="#FFF1F2",
        accent="#FB7185",
        outline="#4C0519",
        outline_width=3.2,
        shadow=1.5,
        bold=True,
        all_caps=True,
        animation="scale",
        scale_percent=112,
        max_words=5,
    ),
    "ocean_breeze": CaptionStyle(
        key="ocean_breeze",
        label="Ocean Breeze",
        description="Cool aquatic palette with ice-white text and vivid cyan highlights.",
        font="Inter",
        font_file="Inter-Variable.ttf",
        size_ratio=0.042,
        primary="#F0F9FF",
        accent="#06B6D4",
        outline="#082F49",
        outline_width=2.8,
        shadow=1.2,
        bold=True,
        all_caps=False,
        animation="subtle",
        scale_percent=108,
        max_words=5,
    ),
    "monochrome_chic": CaptionStyle(
        key="monochrome_chic",
        label="Monochrome Chic",
        description="Clean contemporary studio monochrome with semi-transparent backing.",
        font="Inter",
        font_file="Inter-Variable.ttf",
        size_ratio=0.040,
        primary="#FFFFFF",
        accent="#E2E8F0",
        boxed=True,
        box_colour="#000000",
        box_alpha=150,
        outline_width=0.0,
        shadow=0.0,
        bold=True,
        all_caps=True,
        animation="subtle",
        max_words=5,
    ),
    "retro_arcade": CaptionStyle(
        key="retro_arcade",
        label="Retro Arcade",
        description="Vibrant 1980s nostalgia with hot magenta and electric lime pop.",
        font="Anton",
        font_file="Anton-Regular.ttf",
        size_ratio=0.055,
        primary="#FF1493",
        accent="#39FF14",
        outline="#000000",
        outline_width=4.0,
        shadow=2.0,
        bold=True,
        all_caps=True,
        animation="scale",
        scale_percent=115,
        max_words=4,
    ),
    "podcast_subtle": CaptionStyle(
        key="podcast_subtle",
        label="Podcast Subtle",
        description="Balanced long-form captions designed for natural interview flow.",
        font="Inter",
        font_file="Inter-Variable.ttf",
        size_ratio=0.036,
        primary="#F9FAFB",
        accent="#D97706",
        outline="#111827",
        outline_width=2.2,
        shadow=1.0,
        bold=False,
        all_caps=False,
        animation="subtle",
        margin_v_ratio=0.15,
        max_words=6,
    ),
    "headline_impact": CaptionStyle(
        key="headline_impact",
        label="Headline Impact",
        description="High-visibility journalistic style with golden yellow and heavy stroke.",
        font="Archivo",
        font_file="Archivo-Variable.ttf",
        size_ratio=0.054,
        primary="#FACC15",
        accent="#FFFFFF",
        outline="#000000",
        outline_width=4.2,
        shadow=2.0,
        bold=True,
        all_caps=True,
        animation="scale",
        scale_percent=115,
        max_words=4,
    ),
    "midnight_blue": CaptionStyle(
        key="midnight_blue",
        label="Midnight Blue",
        description="Deep nocturnal aesthetics with pristine white text and neon sky accent.",
        font="Inter",
        font_file="Inter-Variable.ttf",
        size_ratio=0.042,
        primary="#FFFFFF",
        accent="#38BDF8",
        outline="#0B132B",
        outline_width=3.0,
        shadow=1.5,
        bold=True,
        all_caps=False,
        animation="subtle",
        max_words=5,
    ),
    "pastel_dream": CaptionStyle(
        key="pastel_dream",
        label="Pastel Dream",
        description="Soft dreamy aesthetic featuring lavender and mint green accents.",
        font="Archivo",
        font_file="Archivo-Variable.ttf",
        size_ratio=0.048,
        primary="#F3E8FF",
        accent="#6EE7B7",
        outline="#2E1065",
        outline_width=3.0,
        shadow=1.2,
        bold=True,
        all_caps=False,
        animation="subtle",
        max_words=5,
    ),
    "crimson_shadow": CaptionStyle(
        key="crimson_shadow",
        label="Crimson Shadow",
        description="Dramatic bold typography with deep dark red shading and fiery red pop.",
        font="Anton",
        font_file="Anton-Regular.ttf",
        size_ratio=0.056,
        primary="#FFFFFF",
        accent="#EF4444",
        outline="#450A0A",
        outline_width=4.5,
        shadow=2.5,
        bold=True,
        all_caps=True,
        animation="scale",
        scale_percent=116,
        max_words=4,
    ),
    "emerald_elite": CaptionStyle(
        key="emerald_elite",
        label="Emerald Elite",
        description="Prestige luxury look with rich emerald green highlights.",
        font="Inter",
        font_file="Inter-Variable.ttf",
        size_ratio=0.040,
        primary="#FFFFFF",
        accent="#10B981",
        outline="#064E3B",
        outline_width=2.5,
        shadow=1.2,
        bold=True,
        all_caps=False,
        animation="subtle",
        max_words=5,
    ),
    "golden_hour": CaptionStyle(
        key="golden_hour",
        label="Golden Hour",
        description="Warm sun-drenched palette with rich champagne and amber highlights.",
        font="Archivo",
        font_file="Archivo-Variable.ttf",
        size_ratio=0.052,
        primary="#FEF3C7",
        accent="#F59E0B",
        outline="#451A03",
        outline_width=3.5,
        shadow=1.8,
        bold=True,
        all_caps=True,
        animation="scale",
        scale_percent=114,
        max_words=4,
    ),
    "comic_action": CaptionStyle(
        key="comic_action",
        label="Comic Action",
        description="Action-packed comic book style with saturated yellow and heavy outline.",
        font="Anton",
        font_file="Anton-Regular.ttf",
        size_ratio=0.060,
        primary="#FFDE00",
        accent="#FF5722",
        outline="#000000",
        outline_width=5.0,
        shadow=3.0,
        bold=True,
        all_caps=True,
        animation="scale",
        scale_percent=120,
        max_words=3,
    ),
    "tech_clean": CaptionStyle(
        key="tech_clean",
        label="Tech Clean",
        description="Sharp developer and tech aesthetic with cobalt blue active highlight.",
        font="Inter",
        font_file="Inter-Variable.ttf",
        size_ratio=0.038,
        primary="#FFFFFF",
        accent="#3B82F6",
        outline="#0F172A",
        outline_width=2.2,
        shadow=1.0,
        bold=True,
        all_caps=False,
        animation="subtle",
        max_words=5,
    ),
    "slate_modern": CaptionStyle(
        key="slate_modern",
        label="Slate Modern",
        description="Contemporary sleek typography with cool slate contrast.",
        font="Archivo",
        font_file="Archivo-Variable.ttf",
        size_ratio=0.048,
        primary="#FFFFFF",
        accent="#94A3B8",
        outline="#0F172A",
        outline_width=3.0,
        shadow=1.2,
        bold=True,
        all_caps=False,
        animation="subtle",
        max_words=5,
    ),
}

DEFAULT_STYLE = "classic_professional"


def register_style(style: CaptionStyle) -> None:
    """Register a new caption style preset."""
    PRESETS[style.key] = style


STYLE_ALIASES: dict[str, str] = {
    "kinetic": "bold_pop",
    "dynamic": "rich_dynamic",
    "professional": "classic_professional",
    "classic": "classic_professional",
    "karaoke": "karaoke_fill",
    "minimal": "clean_lower",
    "luxury": "minimal_luxury",
    "glitch": "cyber_glitch",
    "neon": "neon_glow",
    "editorial": "editorial_serif",
    "arcade": "retro_arcade",
    "podcast": "podcast_subtle",
    "headline": "headline_impact",
    "kyle": "kyle_kirshner_core",
    "kyle_kirshner": "kyle_kirshner_core",
    "reference": "kyle_kirshner_core",
}


def get_style(key: str) -> CaptionStyle:
    """Return style by key; raises ValueError if not found (strictly preserves API)."""
    norm = key.strip().lower().replace("-", "_").replace(" ", "_") if key else ""
    norm = STYLE_ALIASES.get(norm, norm)
    style = PRESETS.get(norm)
    if style is None:
        raise ValueError(f"Unknown caption style {key!r}. Available: {', '.join(PRESETS)}")
    return style


def resolve_style(key: str | None) -> CaptionStyle:
    """Safely resolve style by key, defaulting to CLASSIC_PROFESSIONAL if unknown."""
    if not key:
        return CLASSIC_PROFESSIONAL
    norm = key.strip().lower().replace("-", "_").replace(" ", "_")
    norm = STYLE_ALIASES.get(norm, norm)
    return PRESETS.get(norm, CLASSIC_PROFESSIONAL)


# --------------------------------------------------------------------------
# Grouping & Word Handling
# --------------------------------------------------------------------------

@dataclass
class CaptionGroup:
    words: list[Word] = field(default_factory=list)

    @property
    def start(self) -> float:
        return self.words[0].start if self.words else 0.0

    @property
    def end(self) -> float:
        return self.words[-1].end if self.words else 0.0

    @property
    def text(self) -> str:
        return " ".join(w.text.strip() for w in self.words)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def group_words(
    words: list[Word],
    *,
    max_words: int = DEFAULT_MAX_WORDS,
    max_gap_s: float = DEFAULT_MAX_GAP_S,
) -> list[CaptionGroup]:
    """Split words into caption-sized groups with natural rhythm."""
    groups: list[CaptionGroup] = []
    current: list[Word] = []

    for index, word in enumerate(words):
        if current:
            gap = word.start - current[-1].end
            if gap > max_gap_s or len(current) >= max_words:
                groups.append(CaptionGroup(words=current))
                current = []

        current.append(word)

        is_last = index == len(words) - 1
        if word.ends_sentence and not is_last:
            groups.append(CaptionGroup(words=current))
            current = []

    if current:
        groups.append(CaptionGroup(words=current))

    return [g for g in groups if g.words]


# --------------------------------------------------------------------------
# Visual Safety & Face Avoidance (Step 19)
# --------------------------------------------------------------------------

def calculate_caption_safe_margin(
    crop_path: CropPath | None,
    height: int,
    style: CaptionStyle,
    composition_record: VisualCompositionRecord | None = None,
) -> int:
    """Calculate vertical margin that prevents captions from occluding the speaker's face.

    If face is in the lower third, adjusts the caption higher (up to max_margin_v_ratio).
    Otherwise retains standard lower-third margin.
    """
    default_margin = round(height * style.margin_v_ratio)

    if not crop_path or not crop_path.segments:
        return default_margin

    # Inspect crop segments & keyframes
    seg = crop_path.segments[0]
    if seg.fit:
        # Padded/blurred background, default lower-third safe margin is ideal
        return default_margin

    # If composition telemetry reports face lower in frame
    face_is_low = False
    if composition_record and composition_record.telemetry:
        face_center_y = composition_record.telemetry.get("face_center_y", 0.35)
        if face_center_y > 0.55:
            face_is_low = True

    # If keyframes indicate low crop window (bottom face positioning)
    for kf in seg.keyframes[:10]:
        if kf.y > (crop_path.source_height * 0.45):
            face_is_low = True
            break

    if face_is_low:
        safe_ratio = min(style.max_margin_v_ratio, max(0.30, style.margin_v_ratio + 0.12))
        return round(height * safe_ratio)

    return default_margin


# --------------------------------------------------------------------------
# ASS Generation
# --------------------------------------------------------------------------

STYLE_NAME = "AutoClip"


def _text_of(word: Word, style: CaptionStyle) -> str:
    text = word.text.strip()
    return text.upper() if style.all_caps else text


def _event(start_s: float, end_s: float, text: str, offset: float) -> pysubs2.SSAEvent:
    return pysubs2.SSAEvent(
        start=pysubs2.make_time(s=max(0.0, start_s - offset)),
        end=pysubs2.make_time(s=max(0.0, end_s - offset)),
        text=text,
        style=STYLE_NAME,
    )


def _static_event(group: CaptionGroup, style: CaptionStyle, offset: float) -> pysubs2.SSAEvent:
    text = " ".join(_text_of(w, style) for w in group.words)
    return _event(group.start, group.end, text, offset)


def _karaoke_event(group: CaptionGroup, style: CaptionStyle, offset: float) -> pysubs2.SSAEvent:
    parts: list[str] = []
    for index, word in enumerate(group.words):
        duration_cs = max(1, round(word.duration * 100))
        if index + 1 < len(group.words):
            gap = group.words[index + 1].start - word.end
            if 0 < gap < 0.5:
                duration_cs += round(gap * 100)
        parts.append(f"{{\kf{duration_cs}}}{_text_of(word, style)}")

    return _event(group.start, group.end, " ".join(parts), offset)


def _per_word_events(
    group: CaptionGroup,
    style: CaptionStyle,
    offset: float,
    hook_window: tuple[float, float] | None = None,
    climax_window: tuple[float, float] | None = None,
    cta_window: tuple[float, float] | None = None,
) -> list[pysubs2.SSAEvent]:
    """Emit one dialogue event per word, applying active word highlighting and narrative cues."""
    events: list[pysubs2.SSAEvent] = []

    for active, word in enumerate(group.words):
        # Determine narrative accent and scale for the active word
        w_time = word.start
        accent = style.accent or style.primary
        scale = style.scale_percent

        if hook_window and (hook_window[0] <= w_time <= hook_window[1]) and style.hook_accent:
            accent = style.hook_accent
            scale = max(scale, style.hook_scale)
        elif climax_window and (climax_window[0] <= w_time <= climax_window[1]) and style.climax_accent:
            accent = style.climax_accent
            scale = max(scale, style.climax_scale)
        elif cta_window and (cta_window[0] <= w_time <= cta_window[1]) and style.cta_accent:
            accent = style.cta_accent
            scale = max(scale, style.cta_scale)

        accent_tag = rf"\c{ass_colour_override(accent)}"
        scale_tag = rf"\fscx{scale}\fscy{scale}" if scale != 100 else ""

        rendered: list[str] = []
        for index, other in enumerate(group.words):
            text = _text_of(other, style)
            if index == active:
                rendered.append(f"{{{accent_tag}{scale_tag}}}{text}{{\\r}}")
            else:
                rendered.append(text)

        end = word.end if active + 1 < len(group.words) else group.end
        next_start = group.words[active + 1].start if active + 1 < len(group.words) else end
        events.append(_event(word.start, max(end, next_start), " ".join(rendered), offset))

    return events


# --------------------------------------------------------------------------
# Semantic Word Coloring & Phrase Pop (Reference Style)
# --------------------------------------------------------------------------

SEMANTIC_GREEN_REGEX = re.compile(
    r"^(\$?\d[\d,\.]*[%kKmMbB]?|\$\w+|money|revenue|profit|profits|dollars?|million|billion|thousand|sales|growth|income|scale|scaled|cash|rich|margin|roi)$",
    re.IGNORECASE,
)
SEMANTIC_RED_REGEX = re.compile(
    r"^(shutdown|shut|closed|close|closing|fire|flame|flames|burning|burned|loss|losses|lost|killed|broke|danger|banned|worst|fail|failed|failure|lawsuit|died|destroy|bankrupt)$",
    re.IGNORECASE,
)
SEMANTIC_YELLOW_REGEX = re.compile(
    r"^(grass|amazon|business|secret|secrets|truth|strategy|method|product|products|listing|client|clients|store|stores|company|work|job)$",
    re.IGNORECASE,
)


def get_semantic_word_color(text: str) -> str | None:
    """Return semantic highlight color for a word based on narrative concept."""
    clean = re.sub(r"[^\w\$%]", "", text.strip())
    if not clean:
        return None
    if SEMANTIC_GREEN_REGEX.search(clean):
        return "#00FF00"  # Neon Green
    if SEMANTIC_RED_REGEX.search(clean):
        return "#FF2B2B"  # Bright Red
    if SEMANTIC_YELLOW_REGEX.search(clean):
        return "#FFE500"  # Vibrant Yellow
    return None


def _phrase_pop_events(
    group: CaptionGroup,
    style: CaptionStyle,
    offset: float,
    hook_window: tuple[float, float] | None = None,
    climax_window: tuple[float, float] | None = None,
    cta_window: tuple[float, float] | None = None,
) -> list[pysubs2.SSAEvent]:
    """Emit high-impact rapid phrase pop dialogue events (Kyle Kirshner reference style).

    Each 1-3 word event highlights the active word with dynamic scaling and semantic coloring
    (Neon green for numbers/money/growth, bright red for loss/danger/shutdown, yellow for hook/subject).
    """
    events: list[pysubs2.SSAEvent] = []
    for active, word in enumerate(group.words):
        w_time = word.start
        active_clean = re.sub(r"[^\w\$%]", "", word.text.strip())
        sem_color = get_semantic_word_color(active_clean)

        if sem_color:
            accent = sem_color
        elif hook_window and (hook_window[0] <= w_time <= hook_window[1]) and style.hook_accent:
            accent = style.hook_accent
        elif climax_window and (climax_window[0] <= w_time <= climax_window[1]) and style.climax_accent:
            accent = style.climax_accent
        elif cta_window and (cta_window[0] <= w_time <= cta_window[1]) and style.cta_accent:
            accent = style.cta_accent
        else:
            accent = style.accent or "#FFE500"

        scale = style.scale_percent if style.scale_percent else 115
        accent_tag = rf"\c{ass_colour_override(accent)}"
        scale_tag = rf"\fscx{scale}\fscy{scale}"

        rendered: list[str] = []
        for index, other in enumerate(group.words):
            text = _text_of(other, style)
            if index == active:
                rendered.append(f"{{{accent_tag}{scale_tag}}}{text}{{\\r}}")
            else:
                other_sem = get_semantic_word_color(re.sub(r"[^\w\$%]", "", other.text.strip()))
                if other_sem:
                    rendered.append(f"{{\\c{ass_colour_override(other_sem)}}}{text}{{\\r}}")
                else:
                    rendered.append(text)

        end = word.end if active + 1 < len(group.words) else group.end
        next_start = group.words[active + 1].start if active + 1 < len(group.words) else end
        events.append(_event(word.start, max(end, next_start), " ".join(rendered), offset))

    return events


def create_hook_headline_event(
    headline: str,
    start_s: float = 0.0,
    end_s: float = 3.0,
    offset: float = 0.0,
    style: CaptionStyle | None = None,
) -> pysubs2.SSAEvent:
    """Creates a top-positioned high-impact 2-3 line hook headline card in ASS.

    Uses \\an8 (top-center) and stacked lines (\\N) with semantic highlighting.
    """
    lines = [line.strip() for line in headline.replace("\\N", "\n").splitlines() if line.strip()]
    if not lines:
        lines = [headline.strip()]

    formatted_lines: list[str] = []
    for line in lines:
        tokens = line.split()
        colored_tokens: list[str] = []
        for tok in tokens:
            clean = re.sub(r"[^\w\$%]", "", tok)
            sem_color = get_semantic_word_color(clean)
            if sem_color:
                colored_tokens.append(rf"{{\c{ass_colour_override(sem_color)}}}{tok.upper()}{{\r}}")
            else:
                colored_tokens.append(tok.upper())
        formatted_lines.append(" ".join(colored_tokens))

    stacked_text = r"{\an8\fs75\b1}" + r"\N".join(formatted_lines)
    return pysubs2.SSAEvent(
        start=pysubs2.make_time(s=max(0.0, start_s - offset)),
        end=pysubs2.make_time(s=max(0.0, end_s - offset)),
        text=stacked_text,
        style=STYLE_NAME,
    )


def _build_ass_style(
    style: CaptionStyle,
    *,
    height: int,
    scale: float,
    margin_v: int | None = None,
    width: int = 1080,
) -> pysubs2.SSAStyle:
    ass_style = pysubs2.SSAStyle()
    ass_style.fontname = style.font
    ass_style.fontsize = round(height * style.size_ratio)
    ass_style.primarycolor = hex_to_ass(style.primary)
    ass_style.secondarycolor = hex_to_ass(style.accent or style.primary)
    ass_style.outlinecolor = hex_to_ass(style.outline)
    ass_style.backcolor = hex_to_ass(style.box_colour, alpha=style.box_alpha)
    ass_style.bold = style.bold
    ass_style.outline = style.outline_width * scale
    ass_style.shadow = style.shadow * scale
    ass_style.borderstyle = 3 if style.boxed else 1
    ass_style.alignment = pysubs2.Alignment.BOTTOM_CENTER
    ass_style.marginv = margin_v if margin_v is not None else round(height * style.margin_v_ratio)
    # Safe margins: minimum 5% on left and right to prevent text edge clipping in 9:16
    ass_style.marginl = ass_style.marginr = max(40, round(width * 0.05))
    return ass_style


def build_ass(
    words: list[Word],
    style: CaptionStyle,
    *,
    width: int,
    height: int,
    time_offset_s: float = 0.0,
    crop_path: CropPath | None = None,
    composition_record: VisualCompositionRecord | None = None,
    hook_window: tuple[float, float] | None = None,
    climax_window: tuple[float, float] | None = None,
    cta_window: tuple[float, float] | None = None,
    hook_headline: str | None = None,
) -> pysubs2.SSAFile:
    """Build an ASS subtitle file for a clip with dynamic styling and safety margins."""
    subs = pysubs2.SSAFile()
    subs.info["PlayResX"] = str(width)
    subs.info["PlayResY"] = str(height)
    subs.info["ScaledBorderAndShadow"] = "yes"
    subs.info["WrapStyle"] = "0"

    scale = height / REFERENCE_HEIGHT
    margin_v = calculate_caption_safe_margin(
        crop_path=crop_path,
        height=height,
        style=style,
        composition_record=composition_record,
    )

    subs.styles[STYLE_NAME] = _build_ass_style(
        style, height=height, scale=scale, margin_v=margin_v, width=width
    )

    # Optional Hook Headline Card (0-3s, stacked lines with semantic colors)
    if hook_headline:
        headline_end = min(3.0, (words[-1].end - time_offset_s) if words else 3.0)
        subs.events.append(
            create_hook_headline_event(
                hook_headline,
                start_s=0.0,
                end_s=max(1.0, headline_end),
                offset=0.0,
                style=style,
            )
        )

    groups = group_words(words, max_words=style.max_words)

    for group in groups:
        if style.animation == "karaoke":
            subs.events.append(_karaoke_event(group, style, time_offset_s))
        elif style.animation == "phrase_pop":
            subs.events.extend(
                _phrase_pop_events(
                    group,
                    style,
                    time_offset_s,
                    hook_window=hook_window,
                    climax_window=climax_window,
                    cta_window=cta_window,
                )
            )
        elif style.animation in ("scale", "subtle") and style.accent:
            subs.events.extend(
                _per_word_events(
                    group,
                    style,
                    time_offset_s,
                    hook_window=hook_window,
                    climax_window=climax_window,
                    cta_window=cta_window,
                )
            )
        else:
            subs.events.append(_static_event(group, style, time_offset_s))

    return subs


def write_ass(
    path: Path,
    words: list[Word],
    style: CaptionStyle,
    *,
    width: int,
    height: int,
    time_offset_s: float = 0.0,
    crop_path: CropPath | None = None,
    composition_record: VisualCompositionRecord | None = None,
    hook_window: tuple[float, float] | None = None,
    climax_window: tuple[float, float] | None = None,
    cta_window: tuple[float, float] | None = None,
    hook_headline: str | None = None,
) -> Path:
    """Render captions to an .ass file and return its path."""
    subs = build_ass(
        words,
        style,
        width=width,
        height=height,
        time_offset_s=time_offset_s,
        crop_path=crop_path,
        composition_record=composition_record,
        hook_window=hook_window,
        climax_window=climax_window,
        cta_window=cta_window,
        hook_headline=hook_headline,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    subs.save(str(path), encoding="utf-8")
    return path


def write_srt(path: Path, words: list[Word], *, time_offset_s: float = 0.0) -> Path:
    """Write a plain .srt sidecar."""
    subs = pysubs2.SSAFile()
    for group in group_words(words, max_words=8):
        subs.events.append(
            pysubs2.SSAEvent(
                start=pysubs2.make_time(s=max(0.0, group.start - time_offset_s)),
                end=pysubs2.make_time(s=max(0.0, group.end - time_offset_s)),
                text=group.text,
            )
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    subs.save(str(path), encoding="utf-8", format_="srt")
    return path


# --------------------------------------------------------------------------
# Caption Quality Gate (Step 19)
# --------------------------------------------------------------------------

@dataclass
class CaptionGateResult:
    status: str  # CAPTION_PASS, CAPTION_WARN, CAPTION_REJECT
    quality_score: float
    rejection_reasons: list[str]
    warnings: list[str]
    rule_checks: list[dict[str, Any]]

    @property
    def is_approved(self) -> bool:
        return self.status in ("CAPTION_PASS", "CAPTION_WARN")


class CaptionQualityGate:
    """Evaluates subtitle events against synchronization, readability, visual safety, and campaign rules."""

    def __init__(self, campaign_spec: CampaignSpecification | None = None) -> None:
        self.campaign_spec = campaign_spec

    def evaluate(
        self,
        subs: pysubs2.SSAFile,
        words: list[Word],
        clip_duration_s: float,
        style: CaptionStyle,
        campaign_spec: CampaignSpecification | None = None,
    ) -> CaptionGateResult:
        spec = campaign_spec or self.campaign_spec
        warnings: list[str] = []
        rejections: list[str] = []
        rule_checks: list[dict[str, Any]] = []

        # 1. Transcript Presence
        if not words:
            rejections.append("no_words_in_transcript")
            rule_checks.append({"rule": "word_presence", "passed": False})
        else:
            rule_checks.append({"rule": "word_presence", "passed": True, "word_count": len(words)})

        # 2. Event Timestamps & Synchronization
        events = subs.events
        if not events:
            rejections.append("zero_subtitle_events_emitted")
            rule_checks.append({"rule": "event_count", "passed": False})
        else:
            has_negative = any(e.start < 0 for e in events)
            if has_negative:
                rejections.append("negative_event_timestamps_detected")

            # Check if events exceed clip duration by more than 1.5s
            duration_ms = clip_duration_s * 1000.0
            overshoot = [e for e in events if e.end > (duration_ms + 1500)]
            if overshoot:
                warnings.append(f"events_exceed_clip_duration({len(overshoot)}_events)")

            # Check inverted timestamps
            inverted = [e for e in events if e.end < e.start]
            if inverted:
                rejections.append("inverted_event_start_end_times")

            rule_checks.append({
                "rule": "timestamp_integrity",
                "passed": not has_negative and not inverted,
                "event_count": len(events),
            })

        # 3. Readability & Line Length
        long_lines = 0
        for e in events:
            # Strip tags for text length calculation
            clean_text = re.sub(r"\{.*?\}", "", e.text).strip()
            if len(clean_text) > 48:
                long_lines += 1

        if long_lines > 0:
            warnings.append(f"long_caption_lines_detected({long_lines}_lines)")
            rule_checks.append({"rule": "line_length", "passed": False, "long_line_count": long_lines})
        else:
            rule_checks.append({"rule": "line_length", "passed": True})

        # 4. Campaign Compliance
        if spec:
            spoken_text = " ".join(w.text.lower() for w in words)
            for bw in spec.banned_words:
                term = str(bw.value).lower().strip()
                if term and term in spoken_text:
                    rejections.append(f"banned_term_in_captions({term})")

            # Check mandatory CTA
            if spec.cta_required and spec.cta_required.value:
                tail_text = " ".join(w.text.lower() for w in words[-min(10, len(words)):])
                if not any(k in tail_text for k in ["follow", "link", "bio", "check", "subscribe", "join", "visit"]):
                    warnings.append("required_cta_missing_from_spoken_text")

        # 5. Font Availability
        if not style.font_path.exists():
            warnings.append(f"font_file_missing_on_disk({style.font_file})")
            rule_checks.append({"rule": "font_file_exists", "passed": False})
        else:
            rule_checks.append({"rule": "font_file_exists", "passed": True})

        # Compute Score & Status
        base_score = 95.0
        base_score -= len(warnings) * 8.0
        if rejections:
            base_score = min(35.0, base_score - len(rejections) * 25.0)

        quality_score = max(0.0, min(100.0, round(base_score, 1)))

        if rejections:
            status = "CAPTION_REJECT"
        elif quality_score >= 70.0:
            status = "CAPTION_PASS"
        else:
            status = "CAPTION_WARN"

        return CaptionGateResult(
            status=status,
            quality_score=quality_score,
            rejection_reasons=rejections,
            warnings=warnings,
            rule_checks=rule_checks,
        )


# --------------------------------------------------------------------------
# Dynamic Caption Engine (Step 19)
# --------------------------------------------------------------------------

class CaptionEngine:
    """Orchestrates operator-selected caption rendering, visual safety, narrative cues, and quality gating."""

    def __init__(self, campaign_spec: CampaignSpecification | None = None) -> None:
        self.campaign_spec = campaign_spec
        self.quality_gate = CaptionQualityGate(campaign_spec=campaign_spec)

    def generate_captions(
        self,
        clip: Clip,
        words: list[Word],
        style_key: str | None = None,
        crop_path: CropPath | None = None,
        composition_record: VisualCompositionRecord | None = None,
        clip_spec: ClipSpecificationRecord | None = None,
        retention_record: RetentionOptimizationRecord | None = None,
        width: int = 1080,
        height: int = 1920,
    ) -> tuple[pysubs2.SSAFile, CaptionOptimizationRecord]:
        """Generates synchronized, visually safe ASS subtitles for a clip."""
        start_time = time.time()
        style = resolve_style(style_key)

        # 1. Extract narrative windows
        hook_window: tuple[float, float] | None = None
        climax_window: tuple[float, float] | None = None
        cta_window: tuple[float, float] | None = None

        if clip_spec:
            if clip_spec.hook_start is not None and clip_spec.hook_end is not None:
                hook_window = (clip_spec.hook_start, clip_spec.hook_end)
            if clip_spec.climax_start is not None and clip_spec.climax_end is not None:
                climax_window = (clip_spec.climax_start, clip_spec.climax_end)
            if clip_spec.cta_start is not None and clip_spec.cta_end is not None:
                cta_window = (clip_spec.cta_start, clip_spec.cta_end)

        dur = (clip.end_s - clip.start_s) if hasattr(clip, "start_s") else 0.0
        # Fallback hook window if not explicitly tagged: first 3.0s of clip
        if not hook_window and dur > 3.0:
            hook_window = (clip.start_s, clip.start_s + 3.0)

        fallback_used = False
        fallback_reason = ""
        duration_s = max(0.1, dur)

        try:
            subs = build_ass(
                words=words,
                style=style,
                width=width,
                height=height,
                time_offset_s=clip.start_s,
                crop_path=crop_path,
                composition_record=composition_record,
                hook_window=hook_window,
                climax_window=climax_window,
                cta_window=cta_window,
            )
            gate_res = self.quality_gate.evaluate(
                subs=subs,
                words=words,
                clip_duration_s=duration_s,
                style=style,
                campaign_spec=self.campaign_spec,
            )

            if gate_res.status == "CAPTION_REJECT":
                log.warning("Clip %s caption rejected by quality gate: %s", clip.id, "; ".join(gate_res.rejection_reasons))

        except Exception as exc:
            log.error("Caption generation error for clip %s: %s", clip.id, exc)
            fallback_used = True
            fallback_reason = f"build_ass_failed: {str(exc)}"
            subs = pysubs2.SSAFile()
            subs.info["PlayResX"] = str(width)
            subs.info["PlayResY"] = str(height)
            gate_res = CaptionGateResult(
                status="CAPTION_REJECT",
                quality_score=0.0,
                rejection_reasons=[fallback_reason],
                warnings=[],
                rule_checks=[{"rule": "build_ass", "passed": False}],
            )

        elapsed_s = round(time.time() - start_time, 3)

        # Build segments summary for record
        segments_summary = [
            {
                "start_s": round(e.start / 1000.0, 2),
                "end_s": round(e.end / 1000.0, 2),
                "text": re.sub(r"\{.*?\}", "", e.text).strip(),
            }
            for e in subs.events[:15]
        ]

        record = CaptionOptimizationRecord(
            id=new_id(),
            clip_id=clip.id,
            job_id=clip.job_id,
            style_key=style.key,
            style_label=style.label,
            caption_segments=segments_summary,
            emphasis_metadata={
                "animation": style.animation,
                "accent": style.accent,
                "scale_percent": style.scale_percent,
                "font": style.font,
            },
            hook_treatment={"enabled": hook_window is not None, "accent": style.hook_accent, "window": hook_window},
            climax_treatment={"enabled": climax_window is not None, "accent": style.climax_accent, "window": climax_window},
            cta_treatment={"enabled": cta_window is not None, "accent": style.cta_accent, "window": cta_window},
            quality_score=gate_res.quality_score,
            quality_status=gate_res.status,
            rejection_reasons=gate_res.rejection_reasons,
            warnings=gate_res.warnings,
            fallback_used=fallback_used,
            fallback_reason=fallback_reason,
            render_time_s=elapsed_s,
            version=1,
            telemetry={
                "event_count": len(subs.events),
                "word_count": len(words),
                "rule_checks": gate_res.rule_checks,
                "margin_v": subs.styles[STYLE_NAME].marginv if STYLE_NAME in subs.styles else 0,
            },
            created_at=utcnow(),
            updated_at=utcnow(),
        )

        return subs, record
