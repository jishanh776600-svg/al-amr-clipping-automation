"""Remote Video Ingestion Implementation with yt-dlp adapter."""

import os
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from clipping.contracts.perception import SourceVideoMetadata
from clipping.ingestion.base import VideoIngestor
from clipping.ingestion.source import SourceReference, SourceType
from clipping.ingestion.exceptions import (
    IngestionNetworkError,
    InvalidSourceError,
    UnsupportedMediaError,
)
from clipping.storage.base import StorageDriver
from clipping.storage.keys import StorageKeyBuilder
from clipping.logging.logger import get_logger

logger = get_logger("clipping.ingestion")


class RemoteVideoIngestor(VideoIngestor):
    """
    Ingests video from remote sources (YouTube, direct URLs, Google Drive)
    and persists them into canonical StorageDriver without keeping local copies.
    """

    def __init__(self, ydl_client: Optional[Any] = None):
        self._ydl_client = ydl_client

    async def ingest(
        self,
        source_ref: SourceReference,
        storage_driver: StorageDriver,
        source_video_id: str,
        force_reingest: bool = False
    ) -> SourceVideoMetadata:
        master_key = StorageKeyBuilder.source_master_video(source_video_id, ext="mp4")
        meta_key = f"sources/{source_video_id}/metadata.json"
        audio_key = StorageKeyBuilder.source_audio_wav(source_video_id)

        # 1. Idempotency Check
        if not force_reingest and await storage_driver.exists(master_key) and await storage_driver.exists(meta_key):
            logger.info("Source video already ingested, skipping download", source_video_id=source_video_id)
            meta_bytes = await storage_driver.download_bytes(meta_key)
            return SourceVideoMetadata.model_validate_json(meta_bytes.decode("utf-8"))

        # 2. Ingestion Routing
        if source_ref.source_type in [SourceType.YOUTUBE, SourceType.DIRECT_URL]:
            return await self._ingest_yt_dlp(
                source_ref=source_ref,
                storage_driver=storage_driver,
                source_video_id=source_video_id,
                master_key=master_key,
                audio_key=audio_key,
                meta_key=meta_key,
            )
        elif source_ref.source_type == SourceType.GDRIVE:
            return await self._ingest_gdrive(
                source_ref=source_ref,
                storage_driver=storage_driver,
                source_video_id=source_video_id,
                master_key=master_key,
                audio_key=audio_key,
                meta_key=meta_key,
            )
        else:
            raise UnsupportedMediaError(f"Unsupported source type: {source_ref.source_type}")

    async def extract_metadata(self, source_ref: SourceReference) -> SourceVideoMetadata:
        if source_ref.source_type in [SourceType.YOUTUBE, SourceType.DIRECT_URL]:
            info = self._get_info_dict(source_ref.uri, download=False)
            return SourceVideoMetadata(
                video_id=info.get("id", "unknown"),
                title=info.get("title", "Unknown Title"),
                duration_seconds=float(info.get("duration", 0.0)),
                width=int(info.get("width", 1920) or 1920),
                height=int(info.get("height", 1080) or 1080),
                fps=float(info.get("fps", 30.0) or 30.0),
                source_url=source_ref.uri,
                master_video_storage_key="",
                audio_storage_key="",
            )
        raise UnsupportedMediaError(f"Cannot extract metadata without download for {source_ref.source_type}")

    def _get_info_dict(self, uri: str, download: bool = False, outtmpl: Optional[str] = None) -> Dict[str, Any]:
        """Runs yt-dlp extraction in an isolated configuration."""
        if self._ydl_client:
            return self._ydl_client.extract_info(uri, download=download)

        import yt_dlp

        ydl_opts = {
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "quiet": True,
            "no_warnings": True,
            "nocheckcertificate": True,
        }
        if outtmpl:
            ydl_opts["outtmpl"] = outtmpl

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(uri, download=download)
                return info or {}
        except Exception as e:
            raise IngestionNetworkError(f"Failed to fetch video stream from {uri}: {e}") from e

    async def _ingest_yt_dlp(
        self,
        source_ref: SourceReference,
        storage_driver: StorageDriver,
        source_video_id: str,
        master_key: str,
        audio_key: str,
        meta_key: str,
    ) -> SourceVideoMetadata:
        # Use ephemeral worker temporary directory
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_pattern = os.path.join(tmp_dir, f"{source_video_id}.%(ext)s")
            info = self._get_info_dict(source_ref.uri, download=True, outtmpl=out_pattern)

            # Locate downloaded file in tempdir
            downloaded_files = list(Path(tmp_dir).glob(f"{source_video_id}.*"))
            if not downloaded_files:
                # If mocked or direct download, fallback to finding any file
                downloaded_files = list(Path(tmp_dir).glob("*.*"))

            if not downloaded_files:
                raise IngestionNetworkError("No video file was written by ingestion engine")

            downloaded_path = str(downloaded_files[0])

            # Upload master video stream to storage vault
            logger.info("Uploading ingested master video to storage vault", master_key=master_key)
            await storage_driver.upload(
                local_path=downloaded_path,
                storage_key=master_key,
                content_type="video/mp4",
            )

            # Build metadata
            metadata = SourceVideoMetadata(
                video_id=source_video_id,
                title=info.get("title", source_ref.title_hint or f"Video {source_video_id}"),
                duration_seconds=float(info.get("duration", 0.0) or 60.0),
                width=int(info.get("width", 1920) or 1920),
                height=int(info.get("height", 1080) or 1080),
                fps=float(info.get("fps", 30.0) or 30.0),
                source_url=source_ref.uri,
                master_video_storage_key=master_key,
                audio_storage_key=audio_key,
            )

            # Persist metadata JSON in storage
            meta_json = metadata.model_dump_json(indent=2).encode("utf-8")
            await storage_driver.upload_bytes(
                data=meta_json,
                storage_key=meta_key,
                content_type="application/json",
            )

            return metadata

    async def _ingest_gdrive(
        self,
        source_ref: SourceReference,
        storage_driver: StorageDriver,
        source_video_id: str,
        master_key: str,
        audio_key: str,
        meta_key: str,
    ) -> SourceVideoMetadata:
        # If source is already in storage, copy to canonical path
        gdrive_key = source_ref.uri.replace("gdrive://", "").lstrip("/")
        if await storage_driver.exists(gdrive_key):
            await storage_driver.copy(gdrive_key, master_key)
        else:
            raise InvalidSourceError(f"Google Drive source file not found at: {source_ref.uri}")

        metadata = SourceVideoMetadata(
            video_id=source_video_id,
            title=source_ref.title_hint or f"GDrive Video {source_video_id}",
            duration_seconds=float(source_ref.extra_params.get("duration", 60.0)),
            width=int(source_ref.extra_params.get("width", 1920)),
            height=int(source_ref.extra_params.get("height", 1080)),
            fps=float(source_ref.extra_params.get("fps", 30.0)),
            source_url=source_ref.uri,
            master_video_storage_key=master_key,
            audio_storage_key=audio_key,
        )

        meta_json = metadata.model_dump_json(indent=2).encode("utf-8")
        await storage_driver.upload_bytes(
            data=meta_json,
            storage_key=meta_key,
            content_type="application/json",
        )

        return metadata
