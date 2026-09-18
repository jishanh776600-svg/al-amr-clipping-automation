"""High-level BGM Vault manager."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import BinaryIO

from autoclip import paths
from autoclip.db import store
from autoclip.db.models import BGMAssetRecord, new_id
from .metadata import clean_tags, clean_text, get_mime_type, infer_title
from .models import BGMUploadMetadata
from .validation import (
    BGMUnavailableError,
    validate_audio_stream,
    validate_extension,
    validate_file_size,
)

log = logging.getLogger(__name__)

BUNDLED_ASSETS_DIR = Path(__file__).resolve().parent / "assets"


class BGMVault:
    """Manages persistent audio assets for the AL AMR BGM Vault."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = (base_dir or paths.bgm_dir()).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def register_asset(
        self,
        source: Path | bytes | BinaryIO,
        filename: str,
        metadata: BGMUploadMetadata | None = None,
    ) -> BGMAssetRecord:
        """Validate, store, and index a new background music track into the vault."""
        suffix = validate_extension(filename)
        asset_id = new_id()
        dest_path = self.base_dir / f"{asset_id}{suffix}"

        try:
            if isinstance(source, (str, Path)):
                shutil.copy2(source, dest_path)
            elif isinstance(source, bytes):
                validate_file_size(len(source))
                dest_path.write_bytes(source)
            else:
                content = source.read()
                validate_file_size(len(content))
                dest_path.write_bytes(content)

            media_info = validate_audio_stream(dest_path)
            file_size = dest_path.stat().st_size
            title = infer_title(metadata.name if metadata else None, filename)
            genre = clean_text(metadata.genre) if metadata else ""
            mood = clean_text(metadata.mood) if metadata else ""
            tags = clean_tags(metadata.tags) if metadata else []
            mime = get_mime_type(dest_path)
            duration_s = round(media_info.duration_s, 2)

            record = BGMAssetRecord(
                id=asset_id,
                name=title,
                file_path=str(dest_path.resolve()),
                genre=genre,
                mood=mood,
                tags=tags,
                mime_type=mime,
                duration_s=duration_s,
                file_size_bytes=file_size,
                enabled=True,
            )
            store.create_bgm_asset(record)

            # Persist metadata sidecar JSON alongside the audio file for persistent continuity
            sidecar_path = self.base_dir / f"{asset_id}.json"
            try:
                import json
                sidecar_data = {
                    "id": asset_id,
                    "name": title,
                    "original_filename": filename,
                    "genre": genre,
                    "mood": mood,
                    "tags": tags,
                    "mime_type": mime,
                    "duration_s": duration_s,
                    "file_size_bytes": file_size,
                }
                sidecar_path.write_text(json.dumps(sidecar_data, indent=2), encoding="utf-8")
            except Exception as exc:
                log.warning("Could not write sidecar metadata for %s: %s", asset_id, exc)

            log.info("Registered BGM asset %s ('%s', %.1fs) in vault", asset_id, title, duration_s)
            return record

        except Exception:
            if dest_path.exists():
                try:
                    dest_path.unlink()
                except OSError:
                    pass
            sidecar_err = self.base_dir / f"{asset_id}.json"
            if sidecar_err.exists():
                try:
                    sidecar_err.unlink()
                except OSError:
                    pass
            raise

    SUPPORTED_EXTENSIONS = (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg")

    def reconcile_vault(self) -> list[BGMAssetRecord]:
        """Scan persistent storage for audio files missing from the database and register them idempotently."""
        reconciled: list[BGMAssetRecord] = []
        if not self.base_dir.exists():
            self.base_dir.mkdir(parents=True, exist_ok=True)

        # 0. Seed bundled canonical assets into vault directory if missing
        if BUNDLED_ASSETS_DIR.exists():
            for p in BUNDLED_ASSETS_DIR.iterdir():
                if p.is_file() and (p.suffix.lower() in self.SUPPORTED_EXTENSIONS or p.suffix.lower() == ".json"):
                    dest = self.base_dir / p.name
                    if not dest.exists():
                        try:
                            shutil.copy2(p, dest)
                            log.info("Seeded canonical BGM asset %s into vault persistent storage", p.name)
                        except Exception as exc:
                            log.warning("Could not seed canonical asset %s: %s", p.name, exc)

        try:
            existing_assets = {a.id: a for a in store.list_bgm_assets()}
            existing_paths = {Path(a.file_path).resolve() for a in existing_assets.values()}
        except Exception as exc:
            log.warning("Could not query existing BGM assets for reconciliation: %s", exc)
            return reconciled

        try:
            for p in self.base_dir.iterdir():
                if not p.is_file():
                    continue
                if p.suffix.lower() not in self.SUPPORTED_EXTENSIONS:
                    continue

                asset_id = p.stem
                sidecar = self.base_dir / f"{asset_id}.json"
                sidecar_meta: dict[str, Any] = {}
                if sidecar.is_file():
                    try:
                        import json
                        sidecar_meta = json.loads(sidecar.read_text(encoding="utf-8"))
                    except Exception:
                        sidecar_meta = {}

                if sidecar_meta.get("id"):
                    asset_id = str(sidecar_meta["id"])

                if asset_id in existing_assets or p.resolve() in existing_paths:
                    # Self-heal: If existing record name is corrupted (matches raw UUID/filename) but sidecar has real name, heal it
                    if sidecar_meta.get("name"):
                        rec = existing_assets.get(asset_id)
                        if rec and (rec.name == p.stem or rec.name == p.name or rec.name == asset_id):
                            store.update_bgm_asset(
                                asset_id,
                                name=sidecar_meta["name"],
                                genre=sidecar_meta.get("genre") or rec.genre,
                                mood=sidecar_meta.get("mood") or rec.mood,
                                tags=sidecar_meta.get("tags") or rec.tags,
                            )
                    continue

                try:
                    media_info = validate_audio_stream(p)
                    title = sidecar_meta.get("name") or infer_title(None, p.name)
                    genre = clean_text(sidecar_meta.get("genre", ""))
                    mood = clean_text(sidecar_meta.get("mood", ""))
                    tags = clean_tags(sidecar_meta.get("tags", []))
                    mime = get_mime_type(p)
                    file_size = p.stat().st_size
                    duration_s = round(media_info.duration_s, 2)

                    record = BGMAssetRecord(
                        id=asset_id,
                        name=title,
                        file_path=str(p.resolve()),
                        genre=genre,
                        mood=mood,
                        tags=tags,
                        mime_type=mime,
                        duration_s=duration_s,
                        file_size_bytes=file_size,
                        enabled=True,
                    )
                    store.create_bgm_asset(record)
                    # If sidecar was missing, generate it now
                    if not sidecar.is_file():
                        try:
                            import json
                            sidecar_data = {
                                "id": asset_id,
                                "name": title,
                                "original_filename": p.name,
                                "genre": genre,
                                "mood": mood,
                                "tags": tags,
                            }
                            sidecar.write_text(json.dumps(sidecar_data, indent=2), encoding="utf-8")
                        except Exception:
                            pass
                    existing_assets[asset_id] = record
                    reconciled.append(record)
                    log.info(
                        "Reconciled unindexed BGM asset %s ('%s', %.1fs) from %s into database",
                        asset_id,
                        title,
                        duration_s,
                        p.name,
                    )
                except Exception as exc:
                    log.warning("Skipping unindexable audio file %s during reconciliation: %s", p.name, exc)
        except Exception as exc:
            log.warning("Error reading BGM base_dir %s: %s", self.base_dir, exc)

        return reconciled

    def list_assets(
        self, enabled_only: bool = False, genre: str | None = None
    ) -> list[BGMAssetRecord]:
        """List assets from the vault, reconciling unindexed persistent files first."""
        self.reconcile_vault()
        return store.list_bgm_assets(enabled_only=enabled_only, genre=genre)

    def get_asset(self, asset_id: str) -> BGMAssetRecord | None:
        """Fetch an asset by its unique identifier or name, reconciling from disk on-demand if missing in DB."""
        if not asset_id:
            return None

        # 1. Try exact match by ID in DB
        asset = store.get_bgm_asset(asset_id)
        if asset is not None:
            return asset

        # 2. Canonical alias mapping
        target = asset_id.strip().lower()
        canonical_map = {
            "cinematic": "canonical_cinematic",
            "lofi": "canonical_lofi",
            "lo-fi": "canonical_lofi",
            "rock": "canonical_upbeat",
            "upbeat": "canonical_upbeat",
            "podcast": "canonical_ambient",
            "ambient": "canonical_ambient",
            "motivation": "canonical_motivation",
        }
        mapped_id = canonical_map.get(target)
        if mapped_id:
            m = store.get_bgm_asset(mapped_id)
            if m is not None:
                return m

        # 3. Check existing assets in DB by name or tags
        all_assets = store.list_bgm_assets()
        for a in all_assets:
            if a.id.strip().lower() == target:
                return a
            if a.name.strip().lower() == target:
                return a

        # 4. Reconcile vault from disk so unindexed audio files are registered
        self.reconcile_vault()

        asset = store.get_bgm_asset(asset_id)
        if asset is not None:
            return asset

        if mapped_id:
            m = store.get_bgm_asset(mapped_id)
            if m is not None:
                return m

        all_assets = store.list_bgm_assets()
        for a in all_assets:
            if a.id.strip().lower() == target:
                return a
            if a.name.strip().lower() == target:
                return a
            if target in a.name.strip().lower() or (a.genre and target in a.genre.strip().lower()):
                return a
            if a.tags and any(target in t.lower() for t in a.tags):
                return a

        # 5. If not found in DB, check if the audio file exists on disk in persistent storage
        if self.base_dir.exists():
            for ext in self.SUPPORTED_EXTENSIONS:
                candidate = self.base_dir / f"{asset_id}{ext}"
                if candidate.is_file():
                    try:
                        sidecar = self.base_dir / f"{asset_id}.json"
                        sidecar_meta: dict[str, Any] = {}
                        if sidecar.is_file():
                            try:
                                import json
                                sidecar_meta = json.loads(sidecar.read_text(encoding="utf-8"))
                            except Exception:
                                pass

                        media_info = validate_audio_stream(candidate)
                        title = sidecar_meta.get("name") or infer_title(None, candidate.name)
                        genre = clean_text(sidecar_meta.get("genre", ""))
                        mood = clean_text(sidecar_meta.get("mood", ""))
                        tags = clean_tags(sidecar_meta.get("tags", []))
                        mime = get_mime_type(candidate)
                        file_size = candidate.stat().st_size
                        duration_s = round(media_info.duration_s, 2)

                        record = BGMAssetRecord(
                            id=asset_id,
                            name=title,
                            file_path=str(candidate.resolve()),
                            genre=genre,
                            mood=mood,
                            tags=tags,
                            mime_type=mime,
                            duration_s=duration_s,
                            file_size_bytes=file_size,
                            enabled=True,
                        )
                        store.create_bgm_asset(record)
                        log.info(
                            "Reconciled on-demand audio asset %s ('%s') from persistent disk into database",
                            asset_id,
                            title,
                        )
                        return record
                    except Exception as exc:
                        log.warning("Failed to reconcile on-demand audio asset %s: %s", candidate.name, exc)

        return None

    def update_asset(
        self,
        asset_id: str,
        name: str | None = None,
        genre: str | None = None,
        mood: str | None = None,
        tags: list[str] | str | None = None,
        enabled: bool | None = None,
    ) -> BGMAssetRecord | None:
        """Update editable metadata or enabled status."""
        cleaned_tags = clean_tags(tags) if tags is not None else None
        res = store.update_bgm_asset(
            asset_id=asset_id,
            name=clean_text(name) if name is not None else None,
            genre=clean_text(genre) if genre is not None else None,
            mood=clean_text(mood) if mood is not None else None,
            tags=cleaned_tags,
            enabled=enabled,
        )
        if res:
            sidecar = self.base_dir / f"{asset_id}.json"
            try:
                import json
                cur_data: dict[str, Any] = {}
                if sidecar.is_file():
                    cur_data = json.loads(sidecar.read_text(encoding="utf-8"))
                cur_data.update({
                    "id": res.id,
                    "name": res.name,
                    "genre": res.genre,
                    "mood": res.mood,
                    "tags": res.tags,
                })
                sidecar.write_text(json.dumps(cur_data, indent=2), encoding="utf-8")
            except Exception as exc:
                log.warning("Failed to update sidecar JSON for %s: %s", asset_id, exc)
        return res

    def delete_asset(self, asset_id: str) -> bool:
        """Remove an asset from disk and delete its database record."""
        asset = self.get_asset(asset_id)
        deleted_file = False

        if asset:
            file_path = Path(asset.file_path)
            if file_path.exists():
                try:
                    file_path.unlink()
                    deleted_file = True
                except OSError as exc:
                    log.warning("Could not delete audio file %s: %s", file_path, exc)
        else:
            # Check if an unindexed audio file exists on disk with this ID
            if self.base_dir.exists():
                for ext in self.SUPPORTED_EXTENSIONS:
                    candidate = self.base_dir / f"{asset_id}{ext}"
                    if candidate.exists():
                        try:
                            candidate.unlink()
                            deleted_file = True
                        except OSError as exc:
                            log.warning("Could not delete orphan audio file %s: %s", candidate, exc)

        # Also remove sidecar JSON if it exists
        sidecar = self.base_dir / f"{asset_id}.json"
        if sidecar.exists():
            try:
                sidecar.unlink()
            except OSError:
                pass

        db_deleted = store.delete_bgm_asset(asset_id)
        return db_deleted or deleted_file

    def resolve_campaign_bgm(
        self, asset_id: str | None, allow_fallback: bool = False
    ) -> tuple[bool, BGMAssetRecord | None, Path | None]:
        """Resolve a campaign-level BGM selection.

        Returns (enabled, asset, path).
        - Explicitly disabled ('none', 'disabled', 'no', 'false'): returns (False, None, None).
        - Explicit selection: returns exact asset; if missing and allow_fallback=True, recovers with default.
        - Default (empty/None): enables canonical BGM asset if available in the vault.
        """
        self.reconcile_vault()

        if asset_id is not None:
            cleaned_id = str(asset_id).strip()
            if cleaned_id.lower() in ("none", "null", "false", "no", "off", "disabled", "__none__"):
                return False, None, None

            if cleaned_id:
                asset = self.get_asset(cleaned_id)
                if not asset:
                    if allow_fallback:
                        log.warning(
                            "Selected BGM asset '%s' does not exist in the BGM Vault. Recovering with default BGM.",
                            cleaned_id,
                        )
                        fallback_assets = self.list_assets(enabled_only=True)
                        if fallback_assets:
                            fb = fallback_assets[0]
                            return True, fb, Path(fb.file_path)
                        return False, None, None
                    raise BGMUnavailableError(
                        f"Selected BGM asset '{cleaned_id}' does not exist in the BGM Vault."
                    )

                if not asset.enabled:
                    if allow_fallback:
                        log.warning(
                            "Selected BGM asset '%s' (%s) is disabled. Recovering with default BGM.",
                            asset.name,
                            asset.id,
                        )
                        fallback_assets = [a for a in self.list_assets(enabled_only=True) if a.id != asset.id]
                        if fallback_assets:
                            fb = fallback_assets[0]
                            return True, fb, Path(fb.file_path)
                        return False, None, None
                    raise BGMUnavailableError(
                        f"Selected BGM asset '{asset.name}' ({asset.id}) is currently disabled."
                    )

                path = Path(asset.file_path)
                if not path.exists():
                    if allow_fallback:
                        log.warning(
                            "BGM audio file for '%s' (%s) missing on disk at %s. Recovering with default BGM.",
                            asset.name,
                            asset.id,
                            path,
                        )
                        fallback_assets = [a for a in self.list_assets(enabled_only=True) if a.id != asset.id and Path(a.file_path).exists()]
                        if fallback_assets:
                            fb = fallback_assets[0]
                            return True, fb, Path(fb.file_path)
                        return False, None, None
                    raise BGMUnavailableError(
                        f"BGM audio file for '{asset.name}' ({asset.id}) was not found on disk at {path}."
                    )

                return True, asset, path

        # DEFAULT: If user has not explicitly disabled BGM and a valid BGM asset exists -> BGM enabled
        available = self.list_assets(enabled_only=True)
        for a in available:
            p = Path(a.file_path)
            if p.exists():
                return True, a, p

        return False, None, None
