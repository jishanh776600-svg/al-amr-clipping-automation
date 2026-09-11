"""Output validation for rendered media files.

Guarantees that a rendered clip is actually valid, non-corrupted, contains the
expected video and audio streams, satisfies aspect ratio requirements, and can
be decoded by FFmpeg without stream errors.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import ffmpeg

log = logging.getLogger(__name__)


class OutputValidationError(RuntimeError):
    """Rendered output failed validation checks."""

    def __init__(self, message: str, *, errors: list[str] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or [message]


@dataclass
class ValidationResult:
    """Validation outcome and probed stream metadata."""

    valid: bool
    path: Path
    file_size_bytes: int = 0
    duration_s: float = 0.0
    width: int = 0
    height: int = 0
    aspect_ratio: float = 0.0
    fps: float = 0.0
    video_codec: str = ""
    audio_codec: str = ""
    has_video: bool = False
    has_audio: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return self.valid

    @property
    def issues(self) -> list[str]:
        return self.errors


def validate_media_output(
    path: Path,
    *,
    expected_duration_s: float | None = None,
    expected_ratio: str | None = "9:16",
    require_audio: bool = True,
    decode_check: bool = True,
    decode_check_duration_s: float = 5.0,
    strict: bool = False,
) -> ValidationResult:
    """Validate a rendered media file.

    Checks:
    1. File exists and is non-empty (> 1024 bytes).
    2. ffprobe can parse container and streams.
    3. Video stream exists, has positive dimensions and valid video codec.
    4. Aspect ratio matches expected ratio (e.g. 9:16 vertical).
    5. Audio stream exists and has valid audio codec (if require_audio).
    6. Duration is within reasonable margin of expected_duration_s.
    7. FFmpeg decode check verifies stream integrity without corrupt packet errors.

    Raises :class:`OutputValidationError` if ``strict=True`` and validation fails.
    """
    path = Path(path)
    errors: list[str] = []

    if not path.exists():
        errors.append(f"Output file does not exist: {path}")
        res = ValidationResult(valid=False, path=path, errors=errors)
        if strict:
            raise OutputValidationError(f"File {path.name} does not exist.", errors=errors)
        return res

    if not path.is_file():
        errors.append(f"Output path is not a file: {path}")
        res = ValidationResult(valid=False, path=path, errors=errors)
        if strict:
            raise OutputValidationError(f"{path.name} is not a file.", errors=errors)
        return res

    size = path.stat().st_size
    if size < 1024:
        errors.append(f"File size too small ({size} bytes); likely corrupted or empty header.")
        res = ValidationResult(valid=False, path=path, file_size_bytes=size, errors=errors)
        if strict:
            raise OutputValidationError(f"{path.name} is too small ({size} bytes).", errors=errors)
        return res

    try:
        info = ffmpeg.probe(path)
    except ffmpeg.FFmpegError as exc:
        errors.append(f"ffprobe failed to parse media container: {exc}")
        res = ValidationResult(valid=False, path=path, file_size_bytes=size, errors=errors)
        if strict:
            raise OutputValidationError(f"{path.name} is not readable by ffprobe.", errors=errors)
        return res

    # Stream checks
    if not info.has_video:
        errors.append("No video stream found in rendered output.")
    if require_audio and not info.has_audio:
        errors.append("No audio stream found in rendered output.")

    width = info.width or 0
    height = info.height or 0
    aspect = (width / height) if (width > 0 and height > 0) else 0.0

    if width <= 0 or height <= 0:
        errors.append(f"Invalid dimensions: {width}x{height}.")

    # Aspect ratio checks
    if expected_ratio == "9:16":
        expected_ar = 9.0 / 16.0  # 0.5625
        if aspect <= 0 or abs(aspect - expected_ar) > 0.05:
            errors.append(
                f"Aspect ratio mismatch for 9:16: expected ~{expected_ar:.4f}, got {aspect:.4f} ({width}x{height})."
            )
    elif expected_ratio == "1:1":
        if aspect <= 0 or abs(aspect - 1.0) > 0.02:
            errors.append(f"Aspect ratio mismatch for 1:1: expected 1.0, got {aspect:.4f} ({width}x{height}).")
    elif expected_ratio == "16:9":
        expected_ar = 16.0 / 9.0  # 1.7778
        if aspect <= 0 or abs(aspect - expected_ar) > 0.05:
            errors.append(
                f"Aspect ratio mismatch for 16:9: expected ~{expected_ar:.4f}, got {aspect:.4f} ({width}x{height})."
            )

    # Duration check
    if expected_duration_s is not None and expected_duration_s > 0:
        if abs(info.duration_s - expected_duration_s) > 2.5:
            errors.append(
                f"Duration mismatch: expected ~{expected_duration_s:.2f}s, got {info.duration_s:.2f}s."
            )

    # Codec checks
    if info.video_codec and info.video_codec.lower() not in ("h264", "hevc", "av1", "vp9", "mpeg4"):
        errors.append(f"Unexpected video codec: {info.video_codec}")
    if require_audio and info.audio_codec and info.audio_codec.lower() not in ("aac", "mp3", "opus", "flac"):
        errors.append(f"Unexpected audio codec: {info.audio_codec}")

    # Lightweight decode check
    if decode_check and not errors:
        try:
            decode_cmd = [
                ffmpeg.ffmpeg_path(),
                "-v",
                "error",
                "-xerror",
                "-i",
                str(path),
                "-t",
                str(decode_check_duration_s),
                "-f",
                "null",
                "-",
            ]
            proc = subprocess.run(
                decode_cmd,
                capture_output=True,
                text=True,
                check=False,
                encoding="utf-8",
                timeout=30,
            )
            if proc.returncode != 0:
                err_msg = proc.stderr.strip() or f"ffmpeg decode returned code {proc.returncode}"
                errors.append(f"Decode integrity check failed: {err_msg}")
        except Exception as exc:
            errors.append(f"Decode check could not run: {exc}")

    is_valid = len(errors) == 0
    result = ValidationResult(
        valid=is_valid,
        path=path,
        file_size_bytes=size,
        duration_s=info.duration_s,
        width=width,
        height=height,
        aspect_ratio=aspect,
        fps=info.fps or 0.0,
        video_codec=info.video_codec or "",
        audio_codec=info.audio_codec or "",
        has_video=info.has_video,
        has_audio=info.has_audio,
        errors=errors,
    )

    if not is_valid and strict:
        raise OutputValidationError(
            f"Validation failed for {path.name}: {'; '.join(errors)}", errors=errors
        )

    return result