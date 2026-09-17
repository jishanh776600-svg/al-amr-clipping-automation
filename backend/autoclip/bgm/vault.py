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
            if isinstance(source, bytes):
                validate_file_size(len(source))
                dest_path.write_bytes(source)
            elif isinstance(source, Path):
                validate_file_size(source.stat().st_size)
                shutil.copy2(source, dest_path)
            else:
                data = source.read()
                validate_file_size(len(data))
                dest_path.write_bytes(data)

            media_info = validate_audio_stream(dest_path)

            title = infer_title(metadata.name if metadata else None, filename)
            genre = clean_text(metadata.genre if metadata else "")
            mood = clean_text(metadata.mood if metadata else "")
            tags = clean_tags(metadata.tags if metadata else [])
            mime = get_mime_type(dest_path)
            file_size = dest_path.stat().st_size
            duration_s = round(media_info.duration_s, 2)

            record = BGMAssetRecord(
                id=asset_id,
                name=title,
                file_path=str(dest_path),
                genre=genre,
                mood=mood,
                tags=tags,
                mime_type=mime,
                duration_s=duration_s,
                file_size_bytes=file_size,
                enabled=True,
            )
            store.create_bgm_asset(record)
            log.info("Registered BGM asset %s ('%s', %.1fs) in vault", asset_id, title, duration_s)
            return record

        except Exception:
            if dest_path.exists():
                try:
                    dest_path.unlink()
                except OSError:
                    pass
            raise

    SUPPORTED_EXTENSIONS = (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg")

    def reconcile_vault(self) -> list[BGMAssetRecord]:
        """Scan persistent storage for audio files missing from the database and register them idempotently."""
        reconciled: list[BGMAssetRecord] = []
        if not self.base_dir.exists():
            return reconciled

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
                if asset_id in existing_assets or p.resolve() in existing_paths:
                    continue

                try:
                    media_info = validate_audio_stream(p)
                    title = infer_title(None, p.name)
                    mime = get_mime_type(p)
                    file_size = p.stat().st_size
                    duration_s = round(media_info.duration_s, 2)

                    record = BGMAssetRecord(
                        id=asset_id,
                        name=title,
                        file_path=str(p.resolve()),
                        genre="",
                        mood="",
                        tags=[],
                        mime_type=mime,
                        duration_s=duration_s,
                        file_size_bytes=file_size,
                        enabled=True,
                    )
                    store.create_bgm_asset(record)
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
        """Fetch an asset by its unique identifier, reconciling from disk on-demand if missing in DB."""
        asset = store.get_bgm_asset(asset_id)
        if asset is not None:
            return asset

        # If not found in DB, check if the audio file exists on disk in persistent storage
        if self.base_dir.exists():
            for ext in self.SUPPORTED_EXTENSIONS:
                candidate = self.base_dir / f"{asset_id}{ext}"
                if candidate.is_file():
                    try:
                        media_info = validate_audio_stream(candidate)
                        title = infer_title(None, candidate.name)
                        mime = get_mime_type(candidate)
                        file_size = candidate.stat().st_size
                        duration_s = round(media_info.duration_s, 2)

                        record = BGMAssetRecord(
                            id=asset_id,
                            name=title,
                            file_path=str(candidate.resolve()),
                            genre="",
                            mood="",
                            tags=[],
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
        return store.update_bgm_asset(
            asset_id=asset_id,
            name=clean_text(name) if name is not None else None,
            genre=clean_text(genre) if genre is not None else None,
            mood=clean_text(mood) if mood is not None else None,
            tags=cleaned_tags,
            enabled=enabled,
        )

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

        db_deleted = store.delete_bgm_asset(asset_id)
        return db_deleted or deleted_file

    def resolve_campaign_bgm(
        self, asset_id: str | None, allow_fallback: bool = False
    ) -> tuple[bool, BGMAssetRecord | None, Path | None]:
        """Resolve a campaign-level BGM selection.

        Returns (enabled, asset, path).
        If asset_id is empty, None, or 'none', returns (False, None, None).
        Raises BGMUnavailableError if a specified asset is missing, disabled, or unreadable (unless allow_fallback is True).
        """
        if not asset_id:
            return False, None, None

        cleaned_id = asset_id.strip()
        if not cleaned_id or cleaned_id.lower() in ("none", "null", "false", "no"):
            return False, None, None

        asset = self.get_asset(cleaned_id)
        if not asset:
            if allow_fallback:
                log.warning(
                    "Selected BGM asset '%s' does not exist in the BGM Vault. Proceeding with No BGM.",
                    cleaned_id,
                )
                return False, None, None
            raise BGMUnavailableError(
                f"Selected BGM asset '{cleaned_id}' does not exist in the BGM Vault."
            )

        if not asset.enabled:
            if allow_fallback:
                log.warning(
                    "Selected BGM asset '%s' (%s) is currently disabled. Proceeding with No BGM.",
                    asset.name,
                    asset.id,
                )
                return False, None, None
            raise BGMUnavailableError(
                f"Selected BGM asset '{asset.name}' ({asset.id}) is currently disabled."
            )

        path = Path(asset.file_path)
        if not path.exists():
            if allow_fallback:
                log.warning(
                    "BGM audio file for '%s' (%s) missing on disk at %s. Proceeding with No BGM.",
                    asset.name,
                    asset.id,
                    path,
                )
                return False, None, None
            raise BGMUnavailableError(
                f"BGM audio file for '{asset.name}' ({asset.id}) was not found on disk at {path}."
            )

        return True, asset, path
