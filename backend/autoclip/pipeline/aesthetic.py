"""Aesthetic Vertical Card Layout Engine for AL AMR AutoClip.

Transforms horizontal/standard videos into modern TikTok/Reels aesthetic vertical clips:
1. Floating center card with anti-aliased rounded corners (e.g. 44px radius).
2. Ambient motion-blurred, darkened background filling the 9:16 vertical canvas (zero black dead space).
3. Persistent aesthetic channel branding header (e.g. "⚡ FUTURE FOUNDERS") at the top.
4. Dynamic kinetic subtitles placed right beneath the floating card.
5. High-CTR footer call-to-action (e.g. "SUBSCRIBE FOR DAILY DROPS") near the bottom.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Tuple
from PIL import Image, ImageDraw

try:
    import pysubs2
except ImportError:
    pysubs2 = None

from .ffmpeg import escape_filter_path

log = logging.getLogger("autoclip.aesthetic")

DEFAULT_CANVAS_WIDTH = 1080
DEFAULT_CANVAS_HEIGHT = 1920
DEFAULT_CORNER_RADIUS = 44
DEFAULT_CHANNEL_HEADER = "⚡ FUTURE FOUNDERS"
DEFAULT_CTA_FOOTER = "SUBSCRIBE FOR DAILY DROPS"


def generate_rounded_mask(
    width: int,
    height: int,
    radius: int = DEFAULT_CORNER_RADIUS,
    cache_dir: Optional[Path] = None,
) -> Path:
    """Generates and caches an anti-aliased grayscale alpha mask for rounded video cards."""
    if cache_dir is None:
        cache_dir = Path("data/masks")
    cache_dir.mkdir(parents=True, exist_ok=True)

    mask_filename = f"rounded_mask_{width}x{height}_r{radius}.png"
    mask_path = cache_dir / mask_filename

    if mask_path.exists() and mask_path.stat().st_size > 500:
        return mask_path

    # Ensure even dimensions
    width = int(width) & ~1
    height = int(height) & ~1
    radius = max(0, min(radius, min(width, height) // 2))

    if radius == 0:
        # Fully rectangular white mask
        mask = Image.new("L", (width, height), 255)
    else:
        # Supersample 2x for smooth anti-aliased edges
        ss_w, ss_h, ss_r = width * 2, height * 2, radius * 2
        mask_ss = Image.new("L", (ss_w, ss_h), 0)
        draw = ImageDraw.Draw(mask_ss)
        draw.rounded_rectangle([(0, 0), (ss_w - 1, ss_h - 1)], radius=ss_r, fill=255)
        mask = mask_ss.resize((width, height), Image.Resampling.LANCZOS)

    mask.save(mask_path, "PNG")
    log.info("Generated aesthetic rounded corner mask: %s (%dx%d, r=%d)", mask_path, width, height, radius)
    return mask_path


def compute_card_geometry(
    src_width: int = 1920,
    src_height: int = 1080,
    canvas_w: int = DEFAULT_CANVAS_WIDTH,
    canvas_h: int = DEFAULT_CANVAS_HEIGHT,
) -> Tuple[int, int, int, int]:
    """Computes optimal (card_w, card_h, card_x, card_y) for the floating card.
    
    Leaves appropriate headroom for the top header branding and lower region
    for dynamic subtitles and subscribe CTA.
    """
    card_w = min(980, canvas_w - 100)
    card_w = card_w & ~1  # Must be even

    aspect = (src_height / src_width) if (src_width and src_height) else (9 / 16)
    card_h = int(card_w * aspect)
    # Clamp card height so it doesn't take too much or too little vertical real estate
    card_h = max(420, min(800, card_h))
    card_h = card_h & ~1  # Must be even

    card_x = (canvas_w - card_w) // 2
    # Place card slightly above the exact geometric center to leave ample room for subtitles
    card_y = 560

    return card_w, card_h, card_x, card_y


def build_aesthetic_filter_complex(
    card_w: int,
    card_h: int,
    card_x: int,
    card_y: int,
    mask_input_idx: int = 1,
    canvas_w: int = DEFAULT_CANVAS_WIDTH,
    canvas_h: int = DEFAULT_CANVAS_HEIGHT,
    ass_path: Optional[Path] = None,
    visual_filter_expr: Optional[str] = None,
) -> str:
    """Builds the FFmpeg -filter_complex expression for the dual-layer aesthetic layout.
    
    Layer 1: Motion-blurred & darkened ambient video background.
    Layer 2: Alpha-masked rounded card foreground centered cleanly.
    Layer 3: Optional burned-in ASS subtitles & aesthetic branding.
    """
    parts = [
        "[0:v]split=2[fg][bg]",
        (
            f"[bg]scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=increase,"
            f"crop={canvas_w}:{canvas_h},boxblur=30:5,eq=brightness=-0.32:contrast=1.12[bg_dark]"
        ),
        (
            f"[fg]scale={card_w}:{card_h}:force_original_aspect_ratio=increase,"
            f"crop={card_w}:{card_h},format=yuva420p[fg_crop]"
        ),
        f"[fg_crop][{mask_input_idx}:v]alphamerge[rounded_card]",
        f"[bg_dark][rounded_card]overlay={card_x}:{card_y}[v_comp]",
    ]

    current_label = "[v_comp]"

    if visual_filter_expr and visual_filter_expr != "null":
        parts.append(f"{current_label}{visual_filter_expr}[v_graded]")
        current_label = "[v_graded]"

    if ass_path and Path(ass_path).exists():
        esc_ass = escape_filter_path(Path(ass_path).resolve())
        parts.append(f"{current_label}ass=filename={esc_ass}[v_out]")
    else:
        parts.append(f"{current_label}null[v_out]")

    return ";".join(parts)


def inject_aesthetic_branding_to_ass(
    ass_path: Path,
    channel_header: str = DEFAULT_CHANNEL_HEADER,
    cta_footer: str = DEFAULT_CTA_FOOTER,
    canvas_w: int = DEFAULT_CANVAS_WIDTH,
    canvas_h: int = DEFAULT_CANVAS_HEIGHT,
) -> None:
    """Appends high-aesthetic header branding and footer CTA to an existing ASS file."""
    if not ass_path.exists():
        return

    content = ass_path.read_text(encoding="utf-8", errors="replace")

    # If already injected, skip
    if "BrandHeader" in content or "BrandFooter" in content:
        return

    header_clean = (channel_header or "").strip()
    footer_clean = (cta_footer or "").strip()

    if not header_clean and not footer_clean:
        return

    lines = content.splitlines()
    new_lines = []
    in_styles = False
    in_events = False

    header_style_def = (
        "Style: BrandHeader,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
        "-1,0,0,0,100,100,4,0,1,3,2,8,20,20,440,1"
    )
    footer_style_def = (
        "Style: BrandFooter,Arial,36,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
        "-1,0,0,0,100,100,2,0,1,2,2,2,20,20,280,1"
    )

    for line in lines:
        new_lines.append(line)
        if line.strip().lower() == "[v4+ styles]":
            in_styles = True
        elif line.startswith("[") and in_styles:
            # End of styles, inject brand styles
            if header_clean:
                new_lines.insert(-1, header_style_def)
            if footer_clean:
                new_lines.insert(-1, footer_style_def)
            in_styles = False

        if line.strip().lower() == "[events]":
            in_events = True
        elif line.startswith("Format:") and in_events:
            # Inject persistent brand events
            if header_clean:
                new_lines.append(f"Dialogue: 0,0:00:00.00,0:10:00.00,BrandHeader,,0,0,0,,{{\\b1}}{header_clean}")
            if footer_clean:
                new_lines.append(f"Dialogue: 0,0:00:00.00,0:10:00.00,BrandFooter,,0,0,0,,{footer_clean}")

    if in_styles:
        if header_clean:
            new_lines.append(header_style_def)
        if footer_clean:
            new_lines.append(footer_style_def)

    ass_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    log.info("Injected aesthetic branding into ASS subtitle file: %s", ass_path.name)
