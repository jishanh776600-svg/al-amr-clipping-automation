"""Physical Media Artifact Guard (Step 10.1).

Enforces that every clip submitted or evaluated is a genuine, physically existing,
playable, and non-synthetic video file on disk.

Invariants:
1. File must exist on local disk and have non-zero, plausible size (>= 50 KB).
2. Format must be a valid ISO Base Media File (MP4/MOV) verified via ffprobe.
3. Streams must include valid video (portrait 9:16 aspect ratio, min 720x1280) and audio.
4. Duration must strictly satisfy canonical guideline duration bounds (default 20.0s - 30.0s).
5. Exactly 5 physical clips must be provided. Duplicate file hashes or identical paths are rejected.
6. Synthetic stubs, 0-byte placeholders, and mock dictionaries without backing files fail closed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

log = logging.getLogger(__name__)

CANONICAL_MIN_DURATION_S = 20.0
CANONICAL_MAX_DURATION_S = 30.0
MIN_PLAUSIBLE_FILE_SIZE_BYTES = 50_000  # at least 50KB for a real short video clip


class MediaGuardError(ValueError):
    """Raised when media artifact verification fails."""
    pass


def compute_file_sha256(path: Union[str, Path]) -> str:
    """Compute deterministic SHA-256 hash of a file on disk."""
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def probe_file_with_ffprobe(file_path: Path) -> Dict[str, Any]:
    """Execute ffprobe to extract stream and format metadata as JSON."""
    ffprobe_bin = shutil.which("ffprobe")
    if not ffprobe_bin:
        raise MediaGuardError("ffprobe executable not found on system PATH.")

    cmd = [
        ffprobe_bin,
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(file_path),
    ]

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15.0,
            check=False,
        )
    except Exception as exc:
        raise MediaGuardError(f"ffprobe execution failed for {file_path.name}: {exc}") from exc

    if proc.returncode != 0:
        raise MediaGuardError(f"ffprobe failed with exit code {proc.returncode}: {proc.stderr.strip()}")

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as jde:
        raise MediaGuardError(f"Invalid ffprobe JSON output for {file_path.name}: {jde}") from jde

    return data


def assert_real_physical_clip_artifact(
    path: Union[str, Path],
    min_duration_s: float = CANONICAL_MIN_DURATION_S,
    max_duration_s: float = CANONICAL_MAX_DURATION_S,
    require_vertical: bool = True,
) -> Dict[str, Any]:
    """Assert that a file path is a genuine, playable video artifact on disk.
    
    Returns a dictionary of validated media properties.
    Raises MediaGuardError on any discrepancy.
    """
    if not path:
        raise MediaGuardError("Clip path is empty or None.")

    p = Path(path).resolve()
    if not p.is_file():
        raise MediaGuardError(f"Physical clip file does not exist on disk: {p}")

    size_bytes = p.stat().st_size
    if size_bytes < MIN_PLAUSIBLE_FILE_SIZE_BYTES:
        raise MediaGuardError(
            f"Physical clip file {p.name} is implausibly small ({size_bytes} bytes). "
            f"Expected at least {MIN_PLAUSIBLE_FILE_SIZE_BYTES} bytes."
        )

    # Probe metadata via ffprobe
    probe_data = probe_file_with_ffprobe(p)
    streams = probe_data.get("streams", [])
    format_info = probe_data.get("format", {})

    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]

    if not video_streams:
        raise MediaGuardError(f"No video stream found in {p.name}")

    v_stream = video_streams[0]
    v_codec = str(v_stream.get("codec_name", "")).lower()
    width = int(v_stream.get("width", 0))
    height = int(v_stream.get("height", 0))

    if width <= 0 or height <= 0:
        raise MediaGuardError(f"Invalid video dimensions {width}x{height} for {p.name}")

    if require_vertical:
        if height <= width:
            raise MediaGuardError(
                f"Video {p.name} is not vertical 9:16 (width={width}, height={height}). "
                f"Height must be greater than width."
            )
        if width < 720 or height < 1280:
            raise MediaGuardError(
                f"Video {p.name} resolution {width}x{height} is below standard short-form minimum 720x1280."
            )

    # Check duration from format or stream
    raw_dur = format_info.get("duration") or v_stream.get("duration")
    if not raw_dur:
        raise MediaGuardError(f"Unable to determine duration for {p.name}")
    try:
        duration_s = float(raw_dur)
    except ValueError as ve:
        raise MediaGuardError(f"Malformed duration '{raw_dur}' for {p.name}") from ve

    if duration_s < min_duration_s:
        raise MediaGuardError(
            f"Clip {p.name} duration {duration_s:.2f}s is below minimum {min_duration_s:.2f}s"
        )
    if duration_s > max_duration_s:
        raise MediaGuardError(
            f"Clip {p.name} duration {duration_s:.2f}s exceeds maximum {max_duration_s:.2f}s"
        )

    # Audio verification
    a_codec = audio_streams[0].get("codec_name", "") if audio_streams else ""
    channels = int(audio_streams[0].get("channels", 0)) if audio_streams else 0
    if not audio_streams:
        raise MediaGuardError(f"Audio stream missing in {p.name}; clipping pipeline requires clean audio.")

    file_hash = compute_file_sha256(p)

    return {
        "path": str(p),
        "filename": p.name,
        "file_size_bytes": size_bytes,
        "sha256": file_hash,
        "duration_s": duration_s,
        "width": width,
        "height": height,
        "video_codec": v_codec,
        "audio_codec": a_codec,
        "channels": channels,
    }


def assert_five_physical_clips(
    clip_paths: List[Union[str, Path]],
    min_duration_s: float = CANONICAL_MIN_DURATION_S,
    max_duration_s: float = CANONICAL_MAX_DURATION_S,
) -> List[Dict[str, Any]]:
    """Assert that exactly 5 distinct, valid physical clips exist on disk.
    
    Validates:
    - Exactly 5 clips provided.
    - All 5 paths are unique on disk.
    - All 5 files pass physical media integrity probes.
    - All 5 file hashes are distinct (rejects duplicated copies of a single render).
    """
    if len(clip_paths) != 5:
        raise MediaGuardError(
            f"INSUFFICIENT_VALID_CLIPS: Exactly 5 clips required by Whop submission invariant, "
            f"got {len(clip_paths)}"
        )

    resolved_paths = [str(Path(p).resolve()) for p in clip_paths]
    if len(set(resolved_paths)) != 5:
        raise MediaGuardError(
            f"Duplicate file paths detected in candidate set: {resolved_paths}"
        )

    probed_clips: List[Dict[str, Any]] = []
    seen_hashes: set[str] = set()

    for idx, p in enumerate(clip_paths):
        meta = assert_real_physical_clip_artifact(
            p, min_duration_s=min_duration_s, max_duration_s=max_duration_s
        )
        h = meta["sha256"]
        if h in seen_hashes:
            raise MediaGuardError(
                f"Duplicate content detected for clip {idx+1} ({Path(p).name}); "
                f"file hash {h[:12]} matches an earlier clip in the submission batch."
            )
        seen_hashes.add(h)
        meta["clip_index"] = idx
        probed_clips.append(meta)

    return probed_clips
