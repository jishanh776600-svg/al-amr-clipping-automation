"""Curated Local Asset Vault & Visual Asset Discovery Provider.

Manages curated visual evidence assets (stock video, stock photos, product listings,
and dynamic UI evidence cards) indexed by semantic concept.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

from .card_generator import EvidenceCardGenerator
from .models import (
    PresentationMode,
    SemanticVisualCue,
    VisualAsset,
    VisualType,
)

log = logging.getLogger(__name__)

VAULT_DIR = Path(__file__).resolve().parent.parent.parent / "assets" / "visuals"


class VisualAssetVault:
    """Discovers and retrieves visual evidence assets matching semantic cues."""

    def __init__(self, vault_dir: Path | None = None) -> None:
        self.vault_dir = vault_dir or VAULT_DIR
        self.vault_dir.mkdir(parents=True, exist_ok=True)
        self.card_generator = EvidenceCardGenerator(output_dir=self.vault_dir / "cards")
        self._ensure_starter_assets()

    def discover_candidates(
        self,
        cue: SemanticVisualCue,
        recent_asset_ids: list[str] | None = None,
    ) -> list[VisualAsset]:
        """Returns candidate visual assets matching the semantic cue with strict anti-repetition ordering."""
        candidates: list[VisualAsset] = []
        recent_ids = set(recent_asset_ids or [])
        variant_idx = len(recent_ids)

        # 1. Partial Overlay Mode: Generate or retrieve dynamic UI Evidence Cards
        if cue.preferred_mode == PresentationMode.PARTIAL_OVERLAY:
            card_path = self.card_generator.generate_card(
                concept=cue.concept,
                metadata=cue.metadata,
                variant_idx=variant_idx,
            )
            card_asset_id = f"card_{cue.concept}_{cue.cue_id}_v{variant_idx}"
            candidates.append(
                VisualAsset(
                    asset_id=card_asset_id,
                    file_path=card_path,
                    visual_type=cue.visual_type,
                    concept=cue.concept,
                    presentation_mode=PresentationMode.PARTIAL_OVERLAY,
                    width=1080,
                    height=1920,
                    duration_s=cue.duration_s,
                    tags=[cue.concept, cue.trigger_word, "evidence_card", "ui_overlay"],
                    source_provider="generated_ui",
                    is_video=False,
                    metadata=cue.metadata,
                )
            )

        # 2. Local Curated Asset Vault: Find indexed files matching concept or tags
        concept_dir = self.vault_dir / cue.concept
        if concept_dir.is_dir():
            for f in concept_dir.glob("*.*"):
                if f.suffix.lower() in [".mp4", ".mov", ".jpg", ".jpeg", ".png", ".webp"]:
                    is_vid = f.suffix.lower() in [".mp4", ".mov"]
                    candidates.append(
                        VisualAsset(
                            asset_id=f.stem,
                            file_path=f,
                            visual_type=VisualType.STOCK_VIDEO if is_vid else VisualType.STOCK_PHOTO,
                            concept=cue.concept,
                            presentation_mode=cue.preferred_mode,
                            width=1080,
                            height=1920,
                            duration_s=cue.duration_s,
                            tags=[cue.concept, f.stem],
                            source_provider="local_vault",
                            is_video=is_vid,
                        )
                    )

        # 3. Pexels Dynamic Stock Video Acquisition (portrait, 9:16, cached locally)
        try:
            pexels_assets = self._discover_pexels_candidates(cue)
            candidates.extend(pexels_assets)
        except Exception as exc:
            log.warning(
                "Pexels discovery failed non-fatally for concept '%s': %s",
                cue.concept, exc,
            )

        # 4. Multi-variant bundled starter assets
        for variant_num in range(1, 5):
            var_file_mp4 = self.vault_dir / f"starter_{cue.concept}_v{variant_num}.mp4"
            if var_file_mp4.is_file():
                candidates.append(
                    VisualAsset(
                        asset_id=f"starter_{cue.concept}_v{variant_num}",
                        file_path=var_file_mp4,
                        visual_type=VisualType.STOCK_VIDEO,
                        concept=cue.concept,
                        presentation_mode=cue.preferred_mode,
                        width=1080,
                        height=1920,
                        duration_s=cue.duration_s or 5.0,
                        tags=[cue.concept, f"variant_{variant_num}"],
                        source_provider="local_vault",
                        is_video=True,
                    )
                )

            var_file_jpg = self.vault_dir / f"starter_{cue.concept}_v{variant_num}.jpg"
            if var_file_jpg.is_file():
                candidates.append(
                    VisualAsset(
                        asset_id=f"starter_{cue.concept}_v{variant_num}",
                        file_path=var_file_jpg,
                        visual_type=VisualType.STOCK_PHOTO,
                        concept=cue.concept,
                        presentation_mode=cue.preferred_mode,
                        width=1080,
                        height=1920,
                        duration_s=cue.duration_s,
                        tags=[cue.concept, f"variant_{variant_num}"],
                        source_provider="local_vault",
                        is_video=False,
                    )
                )

        # Legacy fallback bundled starter asset
        legacy_mp4 = self.vault_dir / f"starter_{cue.concept}.mp4"
        if legacy_mp4.is_file():
            candidates.append(
                VisualAsset(
                    asset_id=f"starter_{cue.concept}",
                    file_path=legacy_mp4,
                    visual_type=VisualType.STOCK_VIDEO,
                    concept=cue.concept,
                    presentation_mode=cue.preferred_mode,
                    width=1080,
                    height=1920,
                    duration_s=5.0,
                    tags=[cue.concept, "starter"],
                    source_provider="local_vault",
                    is_video=True,
                )
            )

        legacy_jpg = self.vault_dir / f"starter_{cue.concept}.jpg"
        if legacy_jpg.is_file():
            candidates.append(
                VisualAsset(
                    asset_id=f"starter_img_{cue.concept}",
                    file_path=legacy_jpg,
                    visual_type=VisualType.STOCK_PHOTO,
                    concept=cue.concept,
                    presentation_mode=cue.preferred_mode,
                    width=1080,
                    height=1920,
                    duration_s=cue.duration_s,
                    tags=[cue.concept, "starter_image"],
                    source_provider="local_vault",
                    is_video=False,
                )
            )

        # 5. Anti-Repetition Guarantee: Sort unused assets first, or generate dynamic procedural card
        unused_candidates = [c for c in candidates if c.asset_id not in recent_ids]
        if unused_candidates:
            return unused_candidates

        # If every candidate has already been used, dynamically generate a fresh procedural card!
        fresh_card_path = self.card_generator.generate_card(
            concept=cue.concept,
            metadata=cue.metadata,
            variant_idx=variant_idx + 1,
        )
        fresh_asset_id = f"proc_card_{cue.concept}_{cue.cue_id}_v{variant_idx+1}"
        fresh_asset = VisualAsset(
            asset_id=fresh_asset_id,
            file_path=fresh_card_path,
            visual_type=cue.visual_type,
            concept=cue.concept,
            presentation_mode=PresentationMode.PARTIAL_OVERLAY,
            width=1080,
            height=1920,
            duration_s=cue.duration_s,
            tags=[cue.concept, "procedural_evidence_card"],
            source_provider="generated_ui",
            is_video=False,
            metadata=cue.metadata,
        )
        return [fresh_asset] + candidates

    def _discover_pexels_candidates(self, cue: SemanticVisualCue) -> list[VisualAsset]:
        """Attempts to acquire portrait stock video clips from Pexels matching the cue.

        Returns an empty list if PEXELS_API_KEY is not configured or on any failure.
        Results are cached locally; downloaded videos are reframed to 9:16.
        """
        try:
            from .pexels_client import PexelsVideoClient
            client = PexelsVideoClient()
            if not client.is_available():
                return []

            # Build contextual search query from cue
            query = getattr(cue, "context_query", "") or ""
            if not query:
                from .semantic_parser import synthesize_contextual_query, CONCEPT_DEFINITIONS
                concept_def = CONCEPT_DEFINITIONS.get(cue.concept)
                defaults = concept_def.search_queries if concept_def else cue.search_queries
                query = synthesize_contextual_query(cue.trigger_phrase, cue.concept, defaults)

            return client.search_and_acquire(
                query=query,
                concept=cue.concept,
                cue_duration_s=cue.duration_s,
                per_page=5,
            )
        except Exception as exc:
            log.warning(
                "Pexels acquisition failed for concept '%s' (non-fatal): %s",
                cue.concept, exc,
            )
            return []

    def _ensure_starter_assets(self) -> None:
        """Ensures bundled visual starter assets exist on disk with multi-variant visual diversity."""
        core_concepts = [
            ("nature_grass", "green grass nature outdoors", (34, 139, 34)),
            ("warehouse_shipping", "amazon logistics warehouse", (40, 44, 52)),
            ("fire_danger", "flames and smoke", (220, 38, 38)),
            ("money_cash", "hundred dollar bills cash payout", (22, 101, 52)),
            ("business_shutdown", "closed business storefront sign", (30, 30, 30)),
            ("team_office", "business team workplace office", (51, 65, 85)),
            ("digital_analytics", "performance analytics dashboard", (15, 23, 42)),
            ("business_growth", "exponential scaling upward trajectory", (16, 185, 129)),
            ("competition_market", "market competition leader graph", (245, 158, 11)),
            ("product_marketplace", "top rated marketplace product", (79, 70, 229)),
            ("mobile_apps_social", "viral mobile video audience engagement", (236, 72, 153)),
        ]

        from PIL import Image, ImageDraw, ImageFont

        variant_palettes = [
            # v1: Clean Dark Frame
            {"bg_mul": 0.8, "frame_color": (255, 255, 255, 160), "badge_color": (16, 185, 129)},
            # v2: High Contrast Neon
            {"bg_mul": 0.5, "frame_color": (59, 130, 246, 200), "badge_color": (59, 130, 246)},
            # v3: Warm Amber Accent
            {"bg_mul": 0.6, "frame_color": (245, 158, 11, 200), "badge_color": (245, 158, 11)},
            # v4: Deep Graphite Minimalist
            {"bg_mul": 0.4, "frame_color": (148, 163, 184, 180), "badge_color": (148, 163, 184)},
        ]

        for cid, label, base_rgb in core_concepts:
            # Generate legacy starter if missing
            legacy_path = self.vault_dir / f"starter_{cid}.jpg"
            if not legacy_path.is_file():
                try:
                    img = Image.new("RGB", (1080, 1920), base_rgb)
                    draw = ImageDraw.Draw(img)
                    draw.rectangle([60, 200, 1020, 1720], outline=(255, 255, 255, 120), width=6)
                    draw.text((120, 920), f"[ EVIDENCE: {cid.upper()} ]", fill=(255, 255, 255))
                    draw.text((120, 980), label.upper(), fill=(220, 220, 220))
                    img.save(legacy_path, format="JPEG", quality=95)
                except Exception as exc:
                    log.warning("Could not generate starter visual for %s: %s", cid, exc)

            # Generate multi-variant visuals
            for v_idx, palette in enumerate(variant_palettes, start=1):
                var_path = self.vault_dir / f"starter_{cid}_v{v_idx}.jpg"
                if not var_path.is_file():
                    try:
                        r = int(min(255, base_rgb[0] * palette["bg_mul"] + (v_idx * 12)))
                        g = int(min(255, base_rgb[1] * palette["bg_mul"] + (v_idx * 8)))
                        b = int(min(255, base_rgb[2] * palette["bg_mul"] + (v_idx * 16)))
                        img = Image.new("RGB", (1080, 1920), (r, g, b))
                        draw = ImageDraw.Draw(img)

                        # Geometric modern framing
                        margin = 60 + (v_idx * 15)
                        draw.rectangle([margin, 180 + margin, 1080 - margin, 1740 - margin], outline=palette["frame_color"], width=5)

                        # Top badge
                        badge_bg = palette["badge_color"]
                        draw.rectangle([100, 300, 480, 370], fill=badge_bg)
                        draw.text((120, 320), f"EVIDENCE #{v_idx} • VERIFIED", fill=(255, 255, 255))

                        # Center concept typography
                        draw.text((120, 900), f"[ {cid.replace('_', ' ').upper()} ]", fill=(255, 255, 255))
                        draw.text((120, 970), f"SEQUENCE VARIANT {v_idx}: {label.upper()}", fill=(200, 210, 225))

                        # Bottom visual indicator
                        draw.rectangle([100, 1500, 1080 - 100, 1512], fill=badge_bg)

                        img.save(var_path, format="JPEG", quality=95)
                        log.info("Created multi-variant visual asset: %s", var_path)
                    except Exception as exc:
                        log.warning("Could not generate starter variant %d for %s: %s", v_idx, cid, exc)
