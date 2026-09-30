"""Deterministic sanitizer and validator for internal pipeline leakage in public SEO metadata."""

from __future__ import annotations

import re
from typing import Any

# Specific internal automation tokens that must NEVER appear in public metadata
_AUTOMATION_BRAND_PATTERNS = [
    re.compile(r"\bAL\s*AMR\s*Highlight\b", re.IGNORECASE),
    re.compile(r"\bAutoClip\b", re.IGNORECASE),
    re.compile(r"#AutoClip\b", re.IGNORECASE),
    re.compile(r"\bArchive Backup\b", re.IGNORECASE),
    re.compile(r"\bReconciled from Telegram\b", re.IGNORECASE),
]

# Pipeline, runtime, and internal debugging tokens
_INTERNAL_TERM_PATTERNS = [
    re.compile(r"\b(?:PreRenderQualityGate|ClipSpecification|ClipCandidate|MetadataQualityGate)\b", re.IGNORECASE),
    re.compile(r"\b(?:worker_runner|reconcile_remote_clip|public_clip_media)\b", re.IGNORECASE),
    re.compile(r"\bStep\s*2[0-9]\b", re.IGNORECASE),
    re.compile(r"\b(?:FAILED_PERMANENT|FAILED_RETRYABLE|quality_status|quality_score)\b", re.IGNORECASE),
]

# Internal URLs, local hostnames, and cloud storage links
_INTERNAL_URL_PATTERNS = [
    re.compile(r"https?://(?:localhost|127\.0\.0\.1)(?::\d+)?\S*", re.IGNORECASE),
    re.compile(r"https?://[a-zA-Z0-9_-]+\.onrender\.com\S*", re.IGNORECASE),
    re.compile(r"https?://drive\.google\.com/(?:file/d/|uc\?)\S*", re.IGNORECASE),
    re.compile(r"https?://github\.com/\S+/actions/runs/\S*", re.IGNORECASE),
    re.compile(r"https?://api\.telegram\.org/\S*", re.IGNORECASE),
]

# System file paths and credentials
_INTERNAL_SYSTEM_PATTERNS = [
    re.compile(r"[A-Za-z]:\\(?:Users|data|temp|\.autoclip)\S*", re.IGNORECASE),
    re.compile(r"(?:/data/|~?/\.autoclip/|/tmp/)\S*", re.IGNORECASE),
    re.compile(r"\b(?:client_secret|refresh_token|access_token|fbtrace_id)\b", re.IGNORECASE),
    re.compile(r"\b(?:clip_|job_|export_|appr_|spec_|cand_)[0-9a-f]{8,}\b", re.IGNORECASE),
    re.compile(r"\bclip_[0-9a-f]+\.mp4\b", re.IGNORECASE),
]

# Hexadecimal identifiers (8 to 32 chars containing at least one digit and one hex char)
_HEX_ID_PATTERN = re.compile(r"\b(?=[0-9a-f]{8,32}\b)(?=[a-f]*[0-9])[0-9a-f]{8,32}\b", re.IGNORECASE)
_UUID_PATTERN = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE)


def detect_internal_leakage(text: str) -> list[str]:
    """Deterministically detects any internal pipeline, debug, identifier, or infrastructure tokens.

    Returns a list of violation descriptions. If empty, the text is 100% clean.
    """
    if not text:
        return []

    violations: list[str] = []

    for pat in _AUTOMATION_BRAND_PATTERNS:
        m = pat.search(text)
        if m:
            violations.append(f"Automation brand/label token detected: '{m.group(0).strip()}'")

    for pat in _INTERNAL_TERM_PATTERNS:
        m = pat.search(text)
        if m:
            violations.append(f"Internal pipeline term detected: '{m.group(0).strip()}'")

    for pat in _INTERNAL_URL_PATTERNS:
        m = pat.search(text)
        if m:
            violations.append(f"Internal/infrastructure URL detected: '{m.group(0).strip()}'")

    for pat in _INTERNAL_SYSTEM_PATTERNS:
        m = pat.search(text)
        if m:
            violations.append(f"Internal system path/token detected: '{m.group(0).strip()}'")

    uuid_match = _UUID_PATTERN.search(text)
    if uuid_match:
        violations.append(f"Internal UUID identifier detected: '{uuid_match.group(0)}'")

    # Exclude authorized social handles like @black_boxvault from hex check
    words = re.findall(r"\b[A-Za-z0-9_-]+\b", text)
    for w in words:
        if w.lower() in ("black_boxvault", "futurefounders", "future_founders11"):
            continue
        if _HEX_ID_PATTERN.fullmatch(w):
            violations.append(f"Internal hexadecimal identifier detected: '{w}'")
            break

    return violations


def sanitize_public_text(text: str, is_title: bool = False) -> str:
    """Deterministically scrubs automation tokens, hex IDs, and internal labels from public metadata."""
    if not text:
        return ""

    cleaned = text

    # Remove automation prefixes and labels
    cleaned = re.sub(r"\bAL\s*AMR\s*Highlight\b", "Key Insight", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bAL\s*AMR\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bAutoClip\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"#ALAMR\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"#AutoClip\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bArchive Backup:[^\n\r]*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bReconciled from Telegram[^\n\r]*", "", cleaned, flags=re.IGNORECASE)

    # Remove internal URLs
    for pat in _INTERNAL_URL_PATTERNS:
        cleaned = pat.sub("", cleaned)

    # Remove internal UUIDs and hex IDs
    cleaned = _UUID_PATTERN.sub("", cleaned)
    cleaned = re.sub(r"\b(?:clip_|job_|export_|appr_|spec_|cand_)[0-9a-f]{8,}\b", "", cleaned, flags=re.IGNORECASE)

    # Scrub standalone hex tokens (with digits) while preserving line breaks
    new_lines = []
    for line in cleaned.splitlines():
        tokens = line.split()
        retained = []
        for t in tokens:
            stripped_t = t.strip(".,!?:;\"'()[]{}#@")
            if _HEX_ID_PATTERN.fullmatch(stripped_t) and stripped_t.lower() not in ("black_boxvault", "futurefounders"):
                continue
            retained.append(t)
        new_lines.append(" ".join(retained))
    cleaned = "\n".join(new_lines)

    # Clean multiple consecutive dots (e.g. '.....')
    cleaned = re.sub(r"\.{2,}", "", cleaned)

    # Normalize whitespace
    raw_lines = [re.sub(r"[ \t]+", " ", line).strip() for line in cleaned.split("\n")]
    if is_title:
        # Title specific cleaning: drop trailing dangling connectors and flatten
        words = (" ".join(line for line in raw_lines if line)).split()
        while words and words[-1].lower() in (
            "and", "or", "so", "the", "a", "an", "to", "of", "in", "for", "with", "is", "at", "by", "that", "fo", "we"
        ):
            words.pop()
        cleaned = " ".join(words).strip().rstrip(".,!?:; ")
        if cleaned.islower():
            cleaned = cleaned.title()
    else:
        # Preserve paragraph breaks for captions and descriptions (max 2 consecutive newlines)
        joined = "\n".join(raw_lines).strip()
        cleaned = re.sub(r"\n{3,}", "\n\n", joined)

    return cleaned


def validate_public_metadata_cleanliness(
    title: str = "",
    description: str = "",
    hashtags: list[str] | None = None,
    mentions: list[str] | None = None,
) -> tuple[bool, list[str]]:
    """Evaluates title, description, hashtags, and mentions for zero pipeline leakage.

    Returns (is_clean, violations_list).
    """
    corpus = f"{title}\n{description}\n{' '.join(hashtags or [])}\n{' '.join(mentions or [])}"
    violations = detect_internal_leakage(corpus)
    return (len(violations) == 0, violations)
