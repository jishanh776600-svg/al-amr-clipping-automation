"""Media Guard and MP4 binary validation for AL AMR Clipping Automation.

Guarantees that files passed to publishing platforms (YouTube Shorts, Instagram Reels)
and Telegram preview delivery are genuine, non-empty, binary MP4 video files rather than
HTML error pages, quota warnings, or truncated downloads.
"""

from __future__ import annotations

import logging
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from . import paths
from .db import store

log = logging.getLogger(__name__)


def is_valid_mp4(target: bytes | Path | str | None, min_size_bytes: int = 50_000) -> bool:
    """Validate whether the file or byte buffer is a genuine MP4 video.

    Checks:
    1. Size >= min_size_bytes (default 50KB; real rendered clips are >= 1MB).
    2. Header does NOT contain HTML error/warning tags or JSON errors.
    3. Header contains standard ISO Base Media File Format box signatures
       ('ftyp', 'moov', or 'mdat' within the first 64 bytes).
    """
    if target is None:
        return False

    try:
        if isinstance(target, (str, Path)):
            p = Path(target)
            if not p.is_file():
                return False
            size = p.stat().st_size
            if size < min_size_bytes:
                return False
            with open(p, "rb") as f:
                header = f.read(512)
        elif isinstance(target, (bytes, bytearray)):
            if len(target) < min_size_bytes:
                return False
            header = bytes(target[:512])
        else:
            return False

        lower = header.lower()
        # Reject HTML documents, error pages, and plain text
        if (
            b"<html" in lower
            or b"<!doctype" in lower
            or b"<head" in lower
            or b"<body" in lower
            or b"404 not found" in lower
            or b"google drive - " in lower
        ):
            return False

        # Reject JSON error payloads
        if lower.strip().startswith(b"{") and (b"error" in lower or b"message" in lower):
            return False

        # MP4 / ISO BMFF verification:
        # Standard MP4 files contain an 'ftyp', 'moov', or 'mdat' atom in the initial 64 bytes.
        first_64 = header[:64]
        has_mp4_box = (b"ftyp" in first_64) or (b"moov" in first_64) or (b"mdat" in first_64)
        return has_mp4_box

    except Exception as exc:
        log.debug("is_valid_mp4 check encountered exception: %s", exc)
        return False


def clean_invalid_cached_media(clip_id: str) -> None:
    """Removes any invalid or corrupted cached video files for the clip."""
    cache_dir = paths.root() / "media_cache"
    if not cache_dir.exists():
        return

    cached_file = cache_dir / f"clip_{clip_id}.mp4"
    if cached_file.exists() and not is_valid_mp4(cached_file):
        try:
            cached_file.unlink(missing_ok=True)
            log.info("Cleaned invalid cached media file: %s", cached_file)
        except Exception as exc:
            log.warning("Could not unlink invalid cached media %s: %s", cached_file, exc)


def materialize_valid_clip_media(
    clip_id: str,
    dest_path: Path | None = None,
    preferred_source: str | None = None,
    drive_file_id: str | None = None,
) -> Path | None:
    """Materialize a genuine, verified MP4 video file for clip_id.

    Resolution strategy:
    1. Check existing cached file in media_cache/clip_{clip_id}.mp4.
    2. Check local final render output_path or export path on disk.
    3. Download from Telegram Bot API if telegram_file_id is available.
    4. Download from Google Drive (authenticated GoogleDriveStorage first, then direct download).
    5. Clean up any invalid files and return None if no valid MP4 could be obtained.
    """
    cache_dir = paths.root() / "media_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    canonical_cached = cache_dir / f"clip_{clip_id}.mp4"

    # Step 1: Check existing cached file
    if canonical_cached.is_file():
        if is_valid_mp4(canonical_cached):
            if dest_path and dest_path != canonical_cached:
                shutil.copy2(canonical_cached, dest_path)
                return dest_path
            return canonical_cached
        else:
            log.warning("Found corrupted file in media cache for clip %s. Removing.", clip_id)
            canonical_cached.unlink(missing_ok=True)

    target_file = dest_path or canonical_cached

    # Step 2: Check local renders & exports
    final_render = store.get_final_render(clip_id)
    if final_render and final_render.output_path:
        local_p = Path(final_render.output_path)
        if local_p.is_file() and is_valid_mp4(local_p):
            log.info("Materializing clip %s media from local final render: %s", clip_id, local_p)
            if target_file != local_p:
                shutil.copy2(local_p, target_file)
            if target_file != canonical_cached and not canonical_cached.exists():
                shutil.copy2(local_p, canonical_cached)
            return target_file

    exports = store.list_exports(clip_id)
    for exp in exports:
        if exp.path:
            exp_p = Path(exp.path)
            if exp_p.is_file() and is_valid_mp4(exp_p):
                log.info("Materializing clip %s media from local export: %s", clip_id, exp_p)
                if target_file != exp_p:
                    shutil.copy2(exp_p, target_file)
                if target_file != canonical_cached and not canonical_cached.exists():
                    shutil.copy2(exp_p, canonical_cached)
                return target_file

    # Step 3: Check Telegram Bot API (telegram_file_id)
    tg_file_id = None
    if final_render and final_render.telemetry:
        tg_file_id = final_render.telemetry.get("telegram_file_id")
    if not tg_file_id:
        approval = store.get_clip_approval(clip_id)
        if approval and approval.telemetry:
            tg_file_id = approval.telemetry.get("telegram_file_id")

    if tg_file_id:
        try:
            from .telegram.review_bot import get_telegram_config
            tg_token, _, _ = get_telegram_config()
            if tg_token:
                import httpx
                log.info("Attempting to download clip %s media from Telegram API (file_id=%s)...", clip_id, tg_file_id)
                with httpx.Client(timeout=60.0) as dl_client:
                    info_resp = dl_client.get(
                        f"https://api.telegram.org/bot{tg_token}/getFile",
                        params={"file_id": tg_file_id},
                    )
                    if info_resp.status_code == 200 and info_resp.json().get("ok"):
                        rel_path = info_resp.json()["result"].get("file_path")
                        if rel_path:
                            file_url = f"https://api.telegram.org/file/bot{tg_token}/{rel_path}"
                            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tf:
                                tmp_download = Path(tf.name)
                            try:
                                with open(tmp_download, "wb") as f_out:
                                    with dl_client.stream("GET", file_url) as stream_resp:
                                        if stream_resp.status_code == 200:
                                            for chunk in stream_resp.iter_bytes(chunk_size=65536):
                                                f_out.write(chunk)
                                if is_valid_mp4(tmp_download):
                                    shutil.copy2(tmp_download, target_file)
                                    if target_file != canonical_cached:
                                        shutil.copy2(tmp_download, canonical_cached)
                                    log.info(
                                        "Successfully materialized clip %s from Telegram Bot API (%d bytes)",
                                        clip_id,
                                        target_file.stat().st_size,
                                    )
                                    return target_file
                            finally:
                                tmp_download.unlink(missing_ok=True)
        except Exception as tg_err:
            log.warning("Telegram media download attempt failed for clip %s: %s", clip_id, tg_err)

    # Step 4: Check Google Drive
    if not drive_file_id:
        for exp in exports:
            if exp.drive_file_id:
                drive_file_id = exp.drive_file_id
                break
        if not drive_file_id and final_render and final_render.telemetry:
            drive_file_id = final_render.telemetry.get("drive_file_id")
        if not drive_file_id:
            approval = store.get_clip_approval(clip_id)
            if approval and approval.telemetry:
                drive_file_id = approval.telemetry.get("drive_file_id")

    if drive_file_id:
        try:
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tf:
                tmp_drive = Path(tf.name)

            # 4a. Authenticated Google Drive storage
            from .storage.drive import GoogleDriveStorage
            drive_storage = GoogleDriveStorage()
            if drive_storage.is_configured:
                log.info("Downloading clip %s media via authenticated Google Drive API (%s)...", clip_id, drive_file_id)
                drive_storage.download_file(drive_file_id, tmp_drive)
                if is_valid_mp4(tmp_drive):
                    shutil.copy2(tmp_drive, target_file)
                    if target_file != canonical_cached:
                        shutil.copy2(tmp_drive, canonical_cached)
                    log.info("Successfully materialized clip %s from authenticated Google Drive", clip_id)
                    tmp_drive.unlink(missing_ok=True)
                    return target_file

            # 4b. Direct download with confirmation token handling
            log.info("Attempting direct HTTP download from Google Drive %s...", drive_file_id)
            import httpx
            direct_urls = [
                f"https://drive.google.com/uc?export=download&id={drive_file_id}&confirm=t",
                f"https://drive.usercontent.google.com/download?id={drive_file_id}&export=download&confirm=t",
                f"https://drive.google.com/uc?id={drive_file_id}&export=download",
            ]
            with httpx.Client(timeout=90.0, follow_redirects=True) as dl_client:
                for d_url in direct_urls:
                    resp = dl_client.get(d_url)
                    if resp.status_code == 200 and is_valid_mp4(resp.content):
                        tmp_drive.write_bytes(resp.content)
                        shutil.copy2(tmp_drive, target_file)
                        if target_file != canonical_cached:
                            shutil.copy2(tmp_drive, canonical_cached)
                        log.info("Successfully materialized clip %s from direct Google Drive URL", clip_id)
                        tmp_drive.unlink(missing_ok=True)
                        return target_file

            tmp_drive.unlink(missing_ok=True)
        except Exception as drive_err:
            log.warning("Google Drive download attempt failed for clip %s: %s", clip_id, drive_err)

    log.error("Could not materialize genuine MP4 video for clip %s from any source.", clip_id)
    return None
