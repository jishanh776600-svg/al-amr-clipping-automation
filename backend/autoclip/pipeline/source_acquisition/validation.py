"""Media Validation Gate & SHA-256 Provenance.

Ensures that every acquired media file passes the exact same rigorous validation
criteria regardless of which acquisition provider produced it.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .. import ffmpeg
from .base import SourceAcquisitionError, SourceErrorCode

#: Default maximum permitted download size (2 GiB)
DEFAULT_MAX_MEDIA_BYTES = 2 * 1024 * 1024 * 1024


def compute_sha256(path: Path) -> str:
    """Compute the SHA-256 digest of a local file in 64 KiB chunks."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def validate_media_gate(
    path: Path,
    *,
    expected_dir: Path | None = None,
    max_size_bytes: int = DEFAULT_MAX_MEDIA_BYTES,
) -> tuple[ffmpeg.MediaInfo, str]:
    """Execute unified media validation gate on acquired local file.

    Returns:
        (MediaInfo, sha256_hash) on success.

    Raises:
        SourceAcquisitionError with code SOURCE_MEDIA_INVALID if validation fails.
    """
    if not path.exists():
        raise SourceAcquisitionError(
            f"Acquired media file does not exist: {path.name}",
            code=SourceErrorCode.SOURCE_MEDIA_INVALID,
            hint="The acquisition provider completed without leaving a valid file on disk.",
        )

    if not path.is_file():
        raise SourceAcquisitionError(
            f"Target media path is not a regular file: {path.name}",
            code=SourceErrorCode.SOURCE_MEDIA_INVALID,
            hint="The downloaded item is a directory, socket, or special device.",
        )

    # Path containment check
    if expected_dir is not None:
        try:
            resolved_file = path.resolve()
            resolved_dir = expected_dir.resolve()
            if not str(resolved_file).startswith(str(resolved_dir)):
                raise SourceAcquisitionError(
                    f"Acquired file '{path.name}' resides outside the expected job workspace.",
                    code=SourceErrorCode.SOURCE_MEDIA_INVALID,
                    hint="Possible path traversal detected.",
                )
        except Exception as exc:
            raise SourceAcquisitionError(
                f"Path validation failed: {exc}",
                code=SourceErrorCode.SOURCE_MEDIA_INVALID,
            ) from exc

    size_bytes = path.stat().st_size
    if size_bytes == 0:
        raise SourceAcquisitionError(
            f"Acquired media file '{path.name}' is completely empty (0 bytes).",
            code=SourceErrorCode.SOURCE_MEDIA_INVALID,
            hint="The provider produced an empty payload.",
        )

    if size_bytes > max_size_bytes:
        raise SourceAcquisitionError(
            f"Acquired media file '{path.name}' ({size_bytes} bytes) exceeds the maximum allowed limit ({max_size_bytes} bytes).",
            code=SourceErrorCode.SOURCE_MEDIA_INVALID,
            hint="Please provide a shorter video or upload a smaller file.",
        )

    # Header inspection for disguised HTML / error documents
    try:
        with path.open("rb") as f:
            header_sample = f.read(1024).lower()
        if (
            b"<!doctype html" in header_sample
            or b"<html" in header_sample
            or b"<head" in header_sample
            or b"<body" in header_sample
        ):
            raise SourceAcquisitionError(
                f"Downloaded media file '{path.name}' is an HTML document, not a video stream.",
                code=SourceErrorCode.SOURCE_MEDIA_INVALID,
                hint="The remote server returned an HTML error page instead of media bytes.",
            )
    except OSError as exc:
        raise SourceAcquisitionError(
            f"Failed to read file header from '{path.name}': {exc}",
            code=SourceErrorCode.SOURCE_MEDIA_INVALID,
        ) from exc

    # FFprobe stream and container validation
    try:
        info = ffmpeg.probe(path)
    except ffmpeg.FFmpegError as exc:
        raise SourceAcquisitionError(
            f"Corrupt or unreadable media container in '{path.name}'.",
            code=SourceErrorCode.SOURCE_MEDIA_INVALID,
            hint=f"FFprobe could not parse media stream: {exc}",
        ) from exc

    if not info.has_video:
        raise SourceAcquisitionError(
            f"Acquired file '{path.name}' contains no valid video stream.",
            code=SourceErrorCode.SOURCE_MEDIA_INVALID,
            hint="AL AMR requires video media with both visual and audio streams for clipping.",
        )

    if not info.has_audio:
        raise SourceAcquisitionError(
            f"Acquired file '{path.name}' contains no audio track.",
            code=SourceErrorCode.SOURCE_MEDIA_INVALID,
            hint="AutoClip finds highlights by transcribing spoken audio. Audio track is missing.",
        )

    if info.duration_s <= 0:
        raise SourceAcquisitionError(
            f"Acquired file '{path.name}' reports invalid or zero duration ({info.duration_s}s).",
            code=SourceErrorCode.SOURCE_MEDIA_INVALID,
            hint="The video container is corrupted or truncated.",
        )

    # Compute provenance SHA-256
    sha256_hex = compute_sha256(path)
    return info, sha256_hex
