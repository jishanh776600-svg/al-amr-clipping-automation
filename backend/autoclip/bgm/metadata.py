"""Metadata processing and normalization for BGM Vault."""

from __future__ import annotations

from pathlib import Path

SUPPORTED_AUDIO_EXTENSIONS: tuple[str, ...] = (
    ".mp3",
    ".wav",
    ".m4a",
    ".aac",
    ".flac",
)

MIME_TYPE_MAP: dict[str, str] = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
}


def clean_text(val: str | None) -> str:
    """Normalize whitespace and strip strings."""
    if not val:
        return ""
    return " ".join(val.strip().split())


def clean_tags(tags: list[str] | str | None) -> list[str]:
    """Normalize tags from a list or comma-separated string."""
    if not tags:
        return []
    if isinstance(tags, str):
        raw_items = tags.split(",")
    else:
        raw_items = tags

    cleaned: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        t = clean_text(str(item)).lower()
        if t and t not in seen:
            seen.add(t)
            cleaned.append(t)
    return cleaned


def infer_title(raw_name: str | None, filename: str) -> str:
    """Infer a human-readable title from name or filename."""
    name = clean_text(raw_name)
    if name:
        return name
    stem = Path(filename).stem
    formatted = stem.replace("_", " ").replace("-", " ")
    return clean_text(formatted).title() or "Untitled BGM"


def get_mime_type(path_or_name: str | Path) -> str:
    """Get canonical audio MIME type from extension."""
    suffix = Path(path_or_name).suffix.lower()
    return MIME_TYPE_MAP.get(suffix, "audio/mpeg")
