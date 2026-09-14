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

    def list_assets(
        self, enabled_only: bool = False, genre: str | None = None
    ) -> list[BGMAssetRecord]:
        """List assets from the vault."""
        return store.list_bgm_assets(enabled_only=enabled_only, genre=genre)

    def get_asset(self, asset_id: str) -> BGMAssetRecord | None:
        """Fetch an asset by its unique identifier."""
        return store.get_bgm_asset(asset_id)

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
        if not asset:
            return False

        file_path = Path(asset.file_path)
        if file_path.exists():
            try:
                file_path.unlink()
            except OSError as exc:
                log.warning("Could not delete audio file %s: %s", file_path, exc)

        return store.delete_bgm_asset(asset_id)

    def resolve_campaign_bgm(
        self, asset_id: str | None
    ) -> tuple[bool, BGMAssetRecord | None, Path | None]:
        """Resolve a campaign-level BGM selection.

        Returns (enabled, asset, path).
        If asset_id is empty, None, or 'none', returns (False, None, None).
        Raises BGMUnavailableError if a specified asset is missing, disabled, or unreadable.
        """
        if not asset_id:
            return False, None, None

        cleaned_id = asset_id.strip()
        if not cleaned_id or cleaned_id.lower() in ("none", "null", "false", "no"):
            return False, None, None

        asset = self.get_asset(cleaned_id)
        if not asset:
            raise BGMUnavailableError(
                f"Selected BGM asset '{cleaned_id}' does not exist in the BGM Vault."
            )

        if not asset.enabled:
            raise BGMUnavailableError(
                f"Selected BGM asset '{asset.name}' ({asset.id}) is currently disabled."
            )

        path = Path(asset.file_path)
        if not path.exists():
            raise BGMUnavailableError(
                f"BGM audio file for '{asset.name}' ({asset.id}) was not found on disk at {path}."
            )

        return True, asset, path
