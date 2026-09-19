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

    def discover_candidates(self, cue: SemanticVisualCue) -> list[VisualAsset]:
        """Returns candidate visual assets matching the semantic cue."""
        candidates: list[VisualAsset] = []

        # 1. Partial Overlay Mode: Generate or retrieve dynamic UI Evidence Cards
        if cue.preferred_mode == PresentationMode.PARTIAL_OVERLAY:
            card_path = self.card_generator.generate_card(
                concept=cue.concept,
                metadata=cue.metadata,
            )
            candidates.append(
                VisualAsset(
                    asset_id=f"card_{cue.concept}_{cue.cue_id}",
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

        # 4. Fallback bundled starter asset ONLY if no specific candidate exists from vault or Pexels
        if not candidates:
            fallback_file = self.vault_dir / f"starter_{cue.concept}.mp4"
            if fallback_file.is_file():
                candidates.append(
                    VisualAsset(
                        asset_id=f"starter_{cue.concept}",
                        file_path=fallback_file,
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

            fallback_img = self.vault_dir / f"starter_{cue.concept}.jpg"
            if fallback_img.is_file():
                candidates.append(
                    VisualAsset(
                        asset_id=f"starter_img_{cue.concept}",
                        file_path=fallback_img,
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

        return candidates

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
            concept_def = None
            try:
                from .semantic_parser import CONCEPT_DEFINITIONS
                concept_def = CONCEPT_DEFINITIONS.get(cue.concept)
            except Exception:
                pass

            if concept_def and concept_def.search_queries:
                query = concept_def.search_queries[0]
            elif cue.trigger_phrase:
                # Derive a contextual query from trigger phrase + concept
                query = f"{cue.trigger_phrase} {cue.concept.replace('_', ' ')}"
            else:
                query = cue.concept.replace("_", " ")

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
        """Ensures bundled visual starter assets exist on disk for the core reference concepts."""
        core_concepts = [
            ("nature_grass", "green grass nature outdoors", (34, 139, 34)),
            ("warehouse_shipping", "amazon logistics warehouse", (40, 44, 52)),
            ("fire_danger", "flames and smoke", (220, 38, 38)),
            ("money_cash", "hundred dollar bills cash payout", (22, 101, 52)),
            ("business_shutdown", "closed business storefront sign", (30, 30, 30)),
            ("team_office", "business team workplace office", (51, 65, 85)),
        ]

        from PIL import Image, ImageDraw, ImageFont

        for cid, label, bg_color in core_concepts:
            img_path = self.vault_dir / f"starter_{cid}.jpg"
            if not img_path.is_file():
                try:
                    img = Image.new("RGB", (1080, 1920), bg_color)
                    draw = ImageDraw.Draw(img)
                    # Draw a nice clean visual representation card
                    draw.rectangle([60, 200, 1020, 1720], outline=(255, 255, 255, 120), width=6)
                    # Add descriptive text
                    draw.text((120, 920), f"[ EVIDENCE: {cid.upper()} ]", fill=(255, 255, 255))
                    draw.text((120, 980), label.upper(), fill=(220, 220, 220))
                    img.save(img_path, format="JPEG", quality=95)
                    log.info("Created starter visual asset: %s", img_path)
                except Exception as exc:
                    log.warning("Could not generate starter visual for %s: %s", cid, exc)
