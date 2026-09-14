"""Audio validation routines for the BGM Vault."""

from __future__ import annotations

import logging
from pathlib import Path

from autoclip.pipeline import ffmpeg
from autoclip.pipeline.ffmpeg import FFmpegError, MediaInfo
from .metadata import SUPPORTED_AUDIO_EXTENSIONS

log = logging.getLogger(__name__)

MAX_FILE_SIZE_BYTES: int = 100 * 1024 * 1024  # 100 MB
MIN_FILE_SIZE_BYTES: int = 256                 # 256 bytes minimum


class BGMValidationError(ValueError):
    """Base exception for BGM validation failures."""


class BGMUnsupportedFormatError(BGMValidationError):
    """Raised when an audio format is not in the supported extensions list."""


class BGMFileSizeError(BGMValidationError):
    """Raised when a BGM file is empty or exceeds the maximum allowed size."""


class BGMCorruptAudioError(BGMValidationError):
    """Raised when ffprobe fails to parse a valid audio stream from the file."""


class BGMUnavailableError(BGMValidationError):
    """Raised when a selected campaign BGM asset is disabled or missing from disk."""


def validate_extension(filename: str) -> str:
    """Ensure the file extension is one of the supported audio formats."""
    suffix = Path(filename).suffix.lower()
    if not suffix or suffix not in SUPPORTED_AUDIO_EXTENSIONS:
        supported_str = ", ".join(SUPPORTED_AUDIO_EXTENSIONS)
        raise BGMUnsupportedFormatError(
            f"Unsupported audio extension '{suffix}'. Supported formats are: {supported_str}."
        )
    return suffix


def validate_file_size(size_bytes: int) -> None:
    """Ensure the file size is within acceptable limits."""
    if size_bytes < MIN_FILE_SIZE_BYTES:
        raise BGMFileSizeError(
            f"File is empty or too small ({size_bytes} bytes). Minimum required is {MIN_FILE_SIZE_BYTES} bytes."
        )
    if size_bytes > MAX_FILE_SIZE_BYTES:
        max_mb = MAX_FILE_SIZE_BYTES // (1024 * 1024)
        raise BGMFileSizeError(
            f"File size ({size_bytes / (1024 * 1024):.1f} MB) exceeds the maximum limit of {max_mb} MB."
        )


def validate_audio_stream(file_path: Path, timeout_s: float = 15.0) -> MediaInfo:
    """Inspect the file with ffprobe to ensure it contains a valid, readable audio stream."""
    if not file_path.exists():
        raise BGMValidationError(f"Audio file does not exist at {file_path}")

    try:
        info = ffmpeg.probe(file_path, timeout_s=timeout_s)
    except FFmpegError as exc:
        raise BGMCorruptAudioError(
            f"Could not read audio stream from {file_path.name}: {exc}"
        ) from exc
    except Exception as exc:
        raise BGMCorruptAudioError(
            f"Unexpected probe error while validating {file_path.name}: {exc}"
        ) from exc

    if not info.has_audio:
        raise BGMCorruptAudioError(
            f"The file {file_path.name} does not contain an audio stream."
        )

    if info.duration_s <= 0.05:
        raise BGMCorruptAudioError(
            f"Invalid audio duration ({info.duration_s}s) in {file_path.name}."
        )

    return info
