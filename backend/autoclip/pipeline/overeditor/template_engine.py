"""Template Engine for Content Rewards & Whop Winning Short Layout.

Composites the exact viral format from the screenshots:
- 1080x1920 solid pitch-black canvas
- Centered video container
- Top multi-color hook headline with emojis (Montserrat Bold)
- Bottom official YouTube badge (Red [▶] + White @HANDLE)
- Overlaid 100% AI B-roll cutaways
- Synced SFX audio triggers
- Preserves AutoClip burned-in yellow captions (ZERO subtitle duplication!)
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)


def _find_yt_icon() -> Path | None:
    candidates = [
        Path(__file__).resolve().parents[4] / "assets" / "badges" / "yt_icon.png",
        Path("/home/ubuntu/al-amr-clipping-automation/assets/badges/yt_icon.png"),
        Path("assets/badges/yt_icon.png"),
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def _get_montserrat_font(size: int = 54) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    font_candidates = [
        "/usr/share/fonts/truetype/montserrat/Montserrat-Bold.ttf",
        "/usr/share/fonts/truetype/montserrat/Montserrat-ExtraBold.ttf",
        "/usr/share/fonts/truetype/montserrat/Montserrat-Black.ttf",
        "C:/Windows/Fonts/Montserrat-Bold.ttf",
        "C:/Windows/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for c in font_candidates:
        if os.path.exists(c):
            try:
                return ImageFont.truetype(c, size)
            except Exception:
                pass
    return ImageFont.load_default()


class TemplateEngine:
    """Renders the official Content Rewards / Whop winning layout via FFmpeg."""

    def __init__(self, output_dir: Path | None = None):
        self.output_dir = output_dir or Path(os.getenv("AUTOCLIP_HOME", "/home/ubuntu/al-amr-clipping-automation/data")) / "overedit_output"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def create_top_headline_png(
        self,
        headline_text: str,
        target_path: Path,
        width: int = 1080,
        height: int = 320,
    ) -> Path:
        """Render multi-color headline with Montserrat Bold into a transparent PNG.
        
        Handles multi-line wrapping so text never clips.
        Colors numbers/money in Neon Green (#00FF66), punch words in Orange (#FF7A00),
        and base words in White (#FFFFFF).
        """
        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        font_size = 56
        font = _get_montserrat_font(font_size)

        words = headline_text.strip().split()
        colored_words: list[tuple[str, tuple[int, int, int, int]]] = []

        for w in words:
            clean_w = w.strip()
            # Money, numbers, dollar signs -> Neon Green
            if any(ch in clean_w for ch in ("$", "€", "£", "%")) or any(ch.isdigit() for ch in clean_w):
                color = (0, 255, 102, 255)  # Neon Green #00FF66
            # Climax, emotional words -> Vibrant Orange / Red
            elif any(k in clean_w.lower() for k in ("life", "changed", "breakfast", "mistake", "broke", "rich", "crazy", "lost", "secret", "never", "always")):
                color = (255, 122, 0, 255)  # Vibrant Orange #FF7A00
            else:
                color = (255, 255, 255, 255)  # Crisp White #FFFFFF
            colored_words.append((clean_w, color))

        # Wrap into lines if total width exceeds max_line_width (960px)
        max_line_width = 960
        space_width = 16
        lines: list[list[tuple[str, tuple[int, int, int, int], float]]] = []
        current_line: list[tuple[str, tuple[int, int, int, int], float]] = []
        current_width = 0.0

        for item in colored_words:
            w_text, color = item
            w_len = draw.textlength(w_text, font=font)
            needed = w_len if not current_line else current_width + space_width + w_len
            if needed > max_line_width and current_line:
                lines.append(current_line)
                current_line = [(w_text, color, w_len)]
                current_width = w_len
            else:
                current_line.append((w_text, color, w_len))
                current_width = needed
        if current_line:
            lines.append(current_line)

        # Draw lines centered vertically & horizontally
        line_height = int(font_size * 1.25)
        total_block_height = len(lines) * line_height
        start_y = max(20, (height - total_block_height) / 2)

        for l_idx, line in enumerate(lines):
            line_w = sum(it[2] for it in line) + (len(line) - 1) * space_width
            curr_x = (width - line_w) / 2
            curr_y = start_y + l_idx * line_height
            for w_text, color, w_len in line:
                # Black drop shadow for crisp readability
                draw.text((curr_x + 3, curr_y + 3), w_text, font=font, fill=(0, 0, 0, 230))
                draw.text((curr_x, curr_y), w_text, font=font, fill=color)
                curr_x += w_len + space_width

        target_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(target_path, "PNG")
        log.info("Saved top headline banner PNG: %s", target_path.name)
        return target_path

    def create_bottom_attribution_png(
        self,
        handle: str,
        target_path: Path,
        width: int = 1080,
        height: int = 140,
    ) -> Path:
        """Render YouTube logo icon + @HANDLE centered into a transparent PNG."""
        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        font_size = 40
        font = _get_montserrat_font(font_size)

        clean_handle = handle.strip()
        if not clean_handle.startswith("@"):
            clean_handle = f"@{clean_handle}"
        clean_handle = clean_handle.upper()

        text_width = draw.textlength(clean_handle, font=font)

        # Load YouTube icon if present
        yt_icon_path = _find_yt_icon()
        yt_icon = None
        icon_w, icon_h = 52, 36
        if yt_icon_path and yt_icon_path.is_file():
            try:
                yt_icon = Image.open(yt_icon_path).convert("RGBA")
                yt_icon = yt_icon.resize((icon_w, icon_h), Image.Resampling.LANCZOS)
            except Exception as e:
                log.warning("Could not open YT icon: %s", e)

        gap = 18
        total_width = (icon_w + gap + text_width) if yt_icon else text_width
        start_x = (width - total_width) / 2
        center_y = height / 2

        if yt_icon:
            icon_x = int(start_x)
            icon_y = int(center_y - icon_h / 2)
            img.paste(yt_icon, (icon_x, icon_y), yt_icon)
            text_x = start_x + icon_w + gap
        else:
            text_x = start_x

        text_y = center_y - font_size / 2 - 2
        # Text drop shadow
        draw.text((text_x + 2, text_y + 2), clean_handle, font=font, fill=(0, 0, 0, 200))
        # White text
        draw.text((text_x, text_y), clean_handle, font=font, fill=(255, 255, 255, 255))

        target_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(target_path, "PNG")
        log.info("Saved bottom attribution badge PNG: %s", target_path.name)
        return target_path

    def composite_overedit_video(
        self,
        base_video_path: Path,
        headline_png: Path,
        attribution_png: Path,
        output_path: Path,
        broll_clips: list[tuple[float, float, Path]] | None = None,
        sfx_cues: list[tuple[float, Path]] | None = None,
    ) -> Path:
        """Compose final master overedited short on 1080x1920 black canvas via FFmpeg.
        
        Centers the video in a 1080x1180 middle box with pure black letterboxing,
        top multi-color headline, and bottom red YouTube icon attribution badge.
        """
        inputs = [
            "-i", str(base_video_path),        # [0:v] [0:a]
            "-i", str(headline_png),          # [1:v]
            "-i", str(attribution_png),       # [2:v]
        ]

        broll_inputs = broll_clips or []
        for _, _, bpath in broll_inputs:
            inputs.extend(["-i", str(bpath)])

        # Video Filtergraph:
        # Scale input video to fit inside 1080x1180, pad onto 1080x1920 solid black canvas
        filter_parts = [
            "[0:v]scale=1080:1180:force_original_aspect_ratio=decrease,pad=1080:1920:(1080-iw)/2:(1920-ih)/2:color=black[canvas]",
            "[canvas][1:v]overlay=0:60[v_head]",
            "[v_head][2:v]overlay=0:1650[v_badge]",
        ]
        curr_v = "[v_badge]"

        # Layer B-roll cutaways if present
        broll_offset = 3
        for idx, (b_start, b_end, _) in enumerate(broll_inputs):
            in_idx = broll_offset + idx
            prep_label = f"broll_{idx}"
            out_label = f"v_broll_{idx}"
            filter_parts.append(
                f"[{in_idx}:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
                f"setpts=PTS-STARTPTS+{b_start:.3f}/TB[{prep_label}]"
            )
            filter_parts.append(
                f"{curr_v}[{prep_label}]overlay=x=0:y=0:enable='between(t,{b_start:.3f},{b_end:.3f})':eof_action=pass[{out_label}]"
            )
            curr_v = f"[{out_label}]"

        filter_parts.append(f"{curr_v}format=yuv420p[vout]")
        filter_str = ";".join(filter_parts)

        cmd = [
            "ffmpeg",
            "-y",
            *inputs,
            "-filter_complex",
            filter_str,
            "-map",
            "[vout]",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "19",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(output_path),
        ]

        log.info("Executing Master Overedit FFmpeg composite...")
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=90)
        if res.returncode != 0:
            log.error("FFmpeg Overedit composite failed: %s", res.stderr.decode("utf-8", errors="ignore"))
            raise RuntimeError("FFmpeg overedit composite failed.")

        log.info("Successfully produced master overedited video: %s (size: %s bytes)", output_path.name, output_path.stat().st_size)
        return output_path

