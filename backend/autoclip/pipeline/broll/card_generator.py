"""High-Resolution Dynamic UI Evidence Card Generator.

Renders authentic-looking, non-cheesy data evidence overlays (revenue analytics dashboards,
upward growth curves, marketplace product cards, and business closure badges)
as transparent 1080x1920 PNG assets designed to float over the speaker's upper-mid torso.
"""
from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from .models import PresentationMode, VisualType

log = logging.getLogger(__name__)

CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1920

# Safe placement: centered horizontally, situated between Y=950 and Y=1450 (below speaker's chin/face)
CARD_WIDTH = 760
CARD_HEIGHT = 440
CARD_LEFT = (CANVAS_WIDTH - CARD_WIDTH) // 2  # 160
CARD_TOP = 980

# Distinct color themes for visual variety (no two cards look identical)
COLOR_THEMES = [
    {
        "name": "cyber_emerald",
        "bg": (15, 23, 20, 235),
        "border": (16, 185, 129, 220),
        "badge_bg": (16, 185, 129, 45),
        "badge_border": (16, 185, 129, 200),
        "badge_text": (52, 211, 153, 255),
        "accent": (16, 185, 129, 255),
    },
    {
        "name": "electric_blue",
        "bg": (15, 23, 42, 235),
        "border": (59, 130, 246, 220),
        "badge_bg": (59, 130, 246, 45),
        "badge_border": (59, 130, 246, 200),
        "badge_text": (96, 165, 250, 255),
        "accent": (59, 130, 246, 255),
    },
    {
        "name": "neon_purple",
        "bg": (24, 15, 36, 235),
        "border": (168, 85, 247, 220),
        "badge_bg": (168, 85, 247, 45),
        "badge_border": (168, 85, 247, 200),
        "badge_text": (216, 180, 254, 255),
        "accent": (168, 85, 247, 255),
    },
    {
        "name": "amber_gold",
        "bg": (28, 22, 14, 235),
        "border": (245, 158, 11, 220),
        "badge_bg": (245, 158, 11, 45),
        "badge_border": (245, 158, 11, 200),
        "badge_text": (251, 191, 36, 255),
        "accent": (245, 158, 11, 255),
    },
    {
        "name": "dark_graphite",
        "bg": (18, 22, 28, 238),
        "border": (75, 85, 99, 220),
        "badge_bg": (75, 85, 99, 50),
        "badge_border": (156, 163, 175, 200),
        "badge_text": (229, 231, 235, 255),
        "accent": (209, 213, 219, 255),
    },
]


def _get_font(size: int, bold: bool = False) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    """Safely loads a bold sans-serif font or falls back to system/default font."""
    font_paths = [
        Path(__file__).resolve().parent.parent.parent / "assets" / "fonts" / "Anton-Regular.ttf",
        Path(__file__).resolve().parent.parent.parent / "assets" / "fonts" / "Archivo-Variable.ttf",
        Path(__file__).resolve().parent.parent.parent / "assets" / "fonts" / "Inter-Variable.ttf",
        Path("C:/Windows/Fonts/Arialbd.ttf"),
        Path("C:/Windows/Fonts/Segoeui.ttf"),
    ]
    for p in font_paths:
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size)
            except Exception:
                pass
    return ImageFont.load_default()


class EvidenceCardGenerator:
    """Generates transparent 1080x1920 PNG evidence cards for partial overlay mode."""

    def __init__(self, output_dir: Path | None = None) -> None:
        if output_dir is None:
            default_dir = Path(__file__).resolve().parent.parent.parent / "assets" / "visuals" / "cards"
        else:
            default_dir = output_dir
        self.output_dir = default_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_card(
        self,
        concept: str,
        metadata: dict[str, Any] | None = None,
        out_path: Path | None = None,
        variant_idx: int = 0,
    ) -> Path:
        """Generates an evidence card based on the semantic concept and metadata with guaranteed visual variety."""
        meta = metadata or {}
        theme = COLOR_THEMES[variant_idx % len(COLOR_THEMES)]
        if out_path is None:
            meta_hash = abs(hash(str(meta) + str(variant_idx)))
            out_path = self.output_dir / f"card_{concept}_v{variant_idx}_{meta_hash}.png"

        if concept in ("financial_revenue", "money_cash", "profit_cash"):
            return self.render_revenue_dashboard(
                title=meta.get("title", "TOTAL REVENUE"),
                value=meta.get("value", "$1,248,331"),
                badge=meta.get("badge", "+18.4% YOY"),
                out_path=out_path,
                theme=theme,
            )
        elif concept in ("business_growth", "growth_scale", "scaling"):
            return self.render_growth_chart(
                title=meta.get("title", "EXPONENTIAL GROWTH"),
                badge=meta.get("badge", "SCALING"),
                out_path=out_path,
                theme=theme,
            )
        elif concept in ("business_shutdown", "fire_danger", "warning_risk"):
            return self.render_closed_badge(out_path=out_path)
        elif concept in ("product_marketplace", "ecommerce_shopping"):
            return self.render_product_card(
                title=meta.get("title", "FEATURED PRODUCT"),
                rating=meta.get("rating", "4.9 ★★★★★"),
                badge=meta.get("badge", "BEST SELLER"),
                out_path=out_path,
                theme=theme,
            )
        elif concept in ("digital_analytics", "competition_market", "strategy_planning"):
            return self.render_kpi_metric_card(
                title=meta.get("title", "MARKET PERFORMANCE"),
                metric=meta.get("value", "99.4% OPTIMAL"),
                badge=meta.get("badge", "#1 LEADER"),
                out_path=out_path,
                theme=theme,
            )
        else:
            return self.render_kpi_metric_card(
                title=meta.get("title", concept.replace("_", " ").upper()),
                metric=meta.get("value", "VERIFIED"),
                badge=meta.get("badge", "KEY INSIGHT"),
                out_path=out_path,
                theme=theme,
            )

    def render_revenue_dashboard(
        self,
        title: str = "TOTAL REVENUE",
        value: str = "$1,248,331",
        badge: str = "+18.4% YOY",
        out_path: Path | None = None,
        theme: dict[str, Any] | None = None,
    ) -> Path:
        """Renders an authentic dark-mode analytics revenue dashboard card."""
        t = theme or COLOR_THEMES[0]
        img = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Card geometry
        x0, y0 = CARD_LEFT, CARD_TOP
        x1, y1 = x0 + CARD_WIDTH, y0 + CARD_HEIGHT
        radius = 24

        # 1. Subtle drop shadow
        shadow_offset = 12
        draw.rounded_rectangle(
            [x0 + shadow_offset, y0 + shadow_offset, x1 + shadow_offset, y1 + shadow_offset],
            radius=radius,
            fill=(0, 0, 0, 110),
        )

        # 2. Main card background
        draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=t["bg"], outline=t["border"], width=3)

        # 3. Header badge & title
        font_header = _get_font(28, bold=True)
        font_val = _get_font(72, bold=True)
        font_badge = _get_font(24, bold=True)
        font_label = _get_font(20, bold=False)

        # Title: "TOTAL REVENUE" in soft slate
        draw.text((x0 + 40, y0 + 36), title.upper(), fill=(156, 163, 175, 255), font=font_header)

        # Green Pill Badge: "+18.4% YOY" in top right
        badge_w = 160
        badge_h = 36
        badge_x0 = x1 - badge_w - 40
        badge_y0 = y0 + 34
        draw.rounded_rectangle([badge_x0, badge_y0, badge_x0 + badge_w, badge_y0 + badge_h], radius=18, fill=(16, 185, 129, 45), outline=(16, 185, 129, 200), width=2)
        draw.text((badge_x0 + 16, badge_y0 + 5), f"▲ {badge}", fill=(52, 211, 153, 255), font=font_badge)

        # 4. Large Big Value: e.g. "$1,248,331" in crisp White / Neon Accent
        draw.text((x0 + 40, y0 + 85), value, fill=(255, 255, 255, 255), font=font_val)

        # 5. Glowing Green Sparkline Graph
        graph_x0 = x0 + 40
        graph_y0 = y0 + 200
        graph_w = CARD_WIDTH - 80
        graph_h = 170

        # Subtle gridlines
        for line_y in [graph_y0 + 40, graph_y0 + 90, graph_y0 + 140]:
            draw.line([(graph_x0, line_y), (graph_x0 + graph_w, line_y)], fill=(38, 45, 56, 180), width=1)

        # Sparkline points (realistic upward business curve with mild volatility)
        points_rel = [(0.0, 0.85), (0.15, 0.72), (0.30, 0.78), (0.45, 0.55), (0.60, 0.60), (0.75, 0.32), (0.90, 0.38), (1.0, 0.12)]
        pts = [(graph_x0 + int(rx * graph_w), graph_y0 + int(ry * graph_h)) for rx, ry in points_rel]

        # Draw smooth neon green line
        for i in range(len(pts) - 1):
            draw.line([pts[i], pts[i + 1]], fill=(16, 185, 129, 255), width=5)

        # End node glowing dot
        end_x, end_y = pts[-1]
        draw.ellipse([end_x - 7, end_y - 7, end_x + 7, end_y + 7], fill=(52, 211, 153, 255), outline=(255, 255, 255, 255), width=2)

        # Subtle bottom labels
        draw.text((graph_x0, y1 - 38), "Q1", fill=(107, 114, 128, 255), font=font_label)
        draw.text((graph_x0 + int(graph_w * 0.33), y1 - 38), "Q2", fill=(107, 114, 128, 255), font=font_label)
        draw.text((graph_x0 + int(graph_w * 0.66), y1 - 38), "Q3", fill=(107, 114, 128, 255), font=font_label)
        draw.text((graph_x0 + graph_w - 25, y1 - 38), "Q4", fill=(107, 114, 128, 255), font=font_label)

        target = out_path or self.output_dir / "revenue_dashboard.png"
        img.save(target, format="PNG")
        return target

    def render_growth_chart(
        self,
        title: str = "EXPONENTIAL GROWTH",
        badge: str = "SCALING",
        out_path: Path | None = None,
        theme: dict[str, Any] | None = None,
    ) -> Path:
        """Renders an upward growth chart card."""
        t = theme or COLOR_THEMES[1]
        img = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        x0, y0 = CARD_LEFT, CARD_TOP
        x1, y1 = x0 + CARD_WIDTH, y0 + CARD_HEIGHT
        radius = 24

        # Shadow & Background
        draw.rounded_rectangle([x0 + 12, y0 + 12, x1 + 12, y1 + 12], radius=radius, fill=(0, 0, 0, 110))
        draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=t["bg"], outline=t["border"], width=3)

        font_header = _get_font(28, bold=True)
        font_val = _get_font(56, bold=True)
        font_badge = _get_font(24, bold=True)

        draw.text((x0 + 40, y0 + 36), title, fill=(148, 163, 184, 255), font=font_header)

        badge_w = 140
        badge_h = 36
        draw.rounded_rectangle([x1 - badge_w - 40, y0 + 34, x1 - 40, y0 + 34 + badge_h], radius=18, fill=t["badge_bg"], outline=t["badge_border"], width=2)
        draw.text((x1 - badge_w - 20, y0 + 39), f"★ {badge}", fill=t["badge_text"], font=font_badge)

        draw.text((x0 + 40, y0 + 82), "10X TRAJECTORY", fill=(255, 255, 255, 255), font=font_val)

        # Exponential curve
        graph_x0, graph_y0 = x0 + 40, y0 + 175
        graph_w, graph_h = CARD_WIDTH - 80, 210

        pts = []
        for step in range(30):
            rx = step / 29.0
            ry = 1.0 - math.pow(rx, 2.2)  # Exponential upward acceleration
            pts.append((graph_x0 + int(rx * graph_w), graph_y0 + int(ry * graph_h)))

        for i in range(len(pts) - 1):
            draw.line([pts[i], pts[i + 1]], fill=t["accent"], width=6)

        end_x, end_y = pts[-1]
        draw.ellipse([end_x - 8, end_y - 8, end_x + 8, end_y + 8], fill=(255, 255, 255, 255), outline=t["accent"], width=3)

        target = out_path or self.output_dir / "growth_chart.png"
        img.save(target, format="PNG")
        return target

    def render_closed_badge(self, out_path: Path | None = None) -> Path:
        """Renders an authentic CLOSED storefront notification card."""
        img = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        x0, y0 = CARD_LEFT, CARD_TOP + 40
        x1, y1 = x0 + CARD_WIDTH, y0 + 340
        radius = 20

        # Shadow & Warning Card Background
        draw.rounded_rectangle([x0 + 12, y0 + 12, x1 + 12, y1 + 12], radius=radius, fill=(0, 0, 0, 110))
        draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=(24, 18, 18, 240), outline=(239, 68, 68, 220), width=4)

        font_alert = _get_font(28, bold=True)
        font_closed = _get_font(84, bold=True)
        font_sub = _get_font(26, bold=False)

        draw.text((x0 + 40, y0 + 36), "⚠️ ACCOUNT STATUS NOTICE", fill=(248, 113, 113, 255), font=font_alert)
        draw.text((x0 + 40, y0 + 90), "CLOSED!", fill=(255, 255, 255, 255), font=font_closed)
        draw.text((x0 + 40, y0 + 225), "OPERATIONS TEMPORARILY SUSPENDED", fill=(209, 213, 219, 255), font=font_sub)

        target = out_path or self.output_dir / "closed_badge.png"
        img.save(target, format="PNG")
        return target

    def render_product_card(
        self,
        title: str = "PRIVATE LABEL BRAND",
        rating: str = "4.9 ★★★★★",
        badge: str = "BEST SELLER",
        out_path: Path | None = None,
        theme: dict[str, Any] | None = None,
    ) -> Path:
        """Renders a clean marketplace product card."""
        t = theme or COLOR_THEMES[3]
        img = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        x0, y0 = CARD_LEFT, CARD_TOP
        x1, y1 = x0 + CARD_WIDTH, y0 + CARD_HEIGHT
        radius = 24

        draw.rounded_rectangle([x0 + 12, y0 + 12, x1 + 12, y1 + 12], radius=radius, fill=(0, 0, 0, 110))
        draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=t["bg"], outline=t["border"], width=3)

        font_header = _get_font(26, bold=True)
        font_val = _get_font(52, bold=True)
        font_rating = _get_font(34, bold=True)
        font_badge = _get_font(24, bold=True)

        draw.text((x0 + 40, y0 + 36), "MARKETPLACE LISTING", fill=(163, 163, 163, 255), font=font_header)

        badge_w = 170
        badge_h = 36
        draw.rounded_rectangle([x1 - badge_w - 40, y0 + 34, x1 - 40, y0 + 34 + badge_h], radius=18, fill=t["badge_bg"], outline=t["badge_border"], width=2)
        draw.text((x1 - badge_w - 20, y0 + 39), f"🏆 {badge}", fill=t["badge_text"], font=font_badge)

        draw.text((x0 + 40, y0 + 88), title, fill=(255, 255, 255, 255), font=font_val)
        draw.text((x0 + 40, y0 + 190), f"RATING: {rating}", fill=(250, 204, 21, 255), font=font_rating)
        draw.text((x0 + 40, y0 + 260), "10,000+ UNITS SOLD THIS MONTH", fill=(229, 231, 235, 255), font=_get_font(26))

        target = out_path or self.output_dir / "product_card.png"
        img.save(target, format="PNG")
        return target

    def render_kpi_metric_card(
        self,
        title: str = "KEY PERFORMANCE METRIC",
        metric: str = "99.4% OPTIMAL",
        badge: str = "VERIFIED",
        out_path: Path | None = None,
        theme: dict[str, Any] | None = None,
    ) -> Path:
        """Renders a dynamic KPI metric insight card."""
        t = theme or COLOR_THEMES[2]
        img = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        x0, y0 = CARD_LEFT, CARD_TOP
        x1, y1 = x0 + CARD_WIDTH, y0 + CARD_HEIGHT
        radius = 24

        draw.rounded_rectangle([x0 + 12, y0 + 12, x1 + 12, y1 + 12], radius=radius, fill=(0, 0, 0, 110))
        draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=t["bg"], outline=t["border"], width=3)

        font_header = _get_font(26, bold=True)
        font_metric = _get_font(60, bold=True)
        font_badge = _get_font(24, bold=True)
        font_sub = _get_font(26, bold=False)

        draw.text((x0 + 40, y0 + 36), title.upper(), fill=(156, 163, 175, 255), font=font_header)

        badge_w = 160
        badge_h = 36
        draw.rounded_rectangle([x1 - badge_w - 40, y0 + 34, x1 - 40, y0 + 34 + badge_h], radius=18, fill=t["badge_bg"], outline=t["badge_border"], width=2)
        draw.text((x1 - badge_w - 20, y0 + 39), f"⚡ {badge}", fill=t["badge_text"], font=font_badge)

        draw.text((x0 + 40, y0 + 95), metric, fill=(255, 255, 255, 255), font=font_metric)

        # Decorative progress accent bar
        bar_x0 = x0 + 40
        bar_y0 = y0 + 205
        bar_w = CARD_WIDTH - 80
        draw.rounded_rectangle([bar_x0, bar_y0, bar_x0 + bar_w, bar_y0 + 12], radius=6, fill=(38, 45, 56, 200))
        draw.rounded_rectangle([bar_x0, bar_y0, bar_x0 + int(bar_w * 0.85), bar_y0 + 12], radius=6, fill=t["accent"])

        draw.text((x0 + 40, y0 + 250), "EXCEEDS TOP 1% BENCHMARK ACROSS INDUSTRY", fill=(209, 213, 219, 255), font=font_sub)

        target = out_path or self.output_dir / "kpi_metric_card.png"
        img.save(target, format="PNG")
        return target
