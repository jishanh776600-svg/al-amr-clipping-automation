"""Pexels Stock Video Acquisition Layer.

Fetches portrait stock video clips from the Pexels API matching semantic
concepts, filters by quality criteria, downloads and caches them for use in
the B-roll engine.

API key is loaded from PEXELS_API_KEY env var or the secure credential vault.
Secret is NEVER logged or returned in API responses.

Usage:
    client = PexelsVideoClient()
    assets = client.search_and_acquire(
        query="ecommerce warehouse fulfillment logistics",
        concept="warehouse_shipping",
        cue_duration_s=3.0,
    )
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from .models import PresentationMode, VisualAsset, VisualType

log = logging.getLogger(__name__)

# ── Cache directory ────────────────────────────────────────────────────────────
_CACHE_DIR: Path | None = None


def _cache_dir() -> Path:
    global _CACHE_DIR
    if _CACHE_DIR is None:
        try:
            from autoclip import paths
            _CACHE_DIR = paths.root() / "cache" / "b_roll"
        except Exception:
            _CACHE_DIR = Path.home() / ".autoclip" / "cache" / "b_roll"
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR


# ── API key resolution ─────────────────────────────────────────────────────────

def _get_api_key() -> str | None:
    """Resolves PEXELS_API_KEY from environment or vault. Returns None if unavailable."""
    key = os.environ.get("PEXELS_API_KEY", "").strip()
    if key:
        return key
    # Try secure vault
    try:
        from autoclip.security.vault import get_vault
        vault = get_vault()
        stored = vault.retrieve_secret("pexels_api_key")
        if stored and stored.strip():
            return stored.strip()
    except Exception:
        pass
    return None


# ── Pexels API client ──────────────────────────────────────────────────────────

PEXELS_VIDEO_SEARCH_URL = "https://api.pexels.com/videos/search"

# Acceptable video clip duration window (seconds) for B-roll segments
MIN_VIDEO_DURATION_S: float = 1.5
MAX_VIDEO_DURATION_S: float = 120.0   # We trim to cue duration in ffmpeg

# Minimum resolution width/height for portrait mode
MIN_WIDTH: int = 720
MIN_HEIGHT: int = 1080

# Confidence threshold (must match broll/scorer.py DEFAULT_CONFIDENCE_THRESHOLD)
CONFIDENCE_THRESHOLD: float = 0.70


def _relevance_score(video: dict[str, Any], query: str, concept: str) -> float:
    """Heuristic relevance score [0.0, 1.0] for a Pexels video record.

    Factors:
    - Orientation: portrait gets 1.0, landscape 0.3
    - Duration: ideal 2-60s gets 1.0, outside range degrades
    - Resolution quality: >=1080 wide gets full score
    - Tags/description keyword overlap with query
    """
    # Orientation score
    w = video.get("width", 0)
    h = video.get("height", 0)
    if h > 0 and h >= w:
        orientation_score = 1.0
    elif w > 0 and h > 0:
        ratio = h / w
        orientation_score = max(0.0, ratio - 0.5) if ratio < 1.0 else 1.0
    else:
        orientation_score = 0.5

    # Duration score: Pexels stock clips are trimmed to cue duration during ffmpeg reframe
    dur = video.get("duration", 0)
    if 2.0 <= dur <= 60.0:
        dur_score = 1.0
    elif 1.0 <= dur <= 120.0:
        dur_score = 0.8
    else:
        dur_score = 0.4

    # Quality score
    quality_score = 1.0 if (min(w, h) >= 1080) else (0.7 if (min(w, h) >= 720) else 0.4)

    # Keyword overlap
    query_tokens = set(query.lower().split())
    concept_tokens = set(concept.lower().replace("_", " ").split())
    all_tokens = query_tokens | concept_tokens
    description = (video.get("url", "") + " " + video.get("user", {}).get("name", "")).lower()
    tag_overlap = sum(1 for t in all_tokens if t in description) / max(1, len(all_tokens))
    keyword_score = min(1.0, 0.5 + tag_overlap * 0.5)

    # Weighted average
    return (
        orientation_score * 0.35
        + dur_score * 0.25
        + quality_score * 0.20
        + keyword_score * 0.20
    )


def _best_video_file(video: dict[str, Any]) -> dict[str, Any] | None:
    """Selects the best video file from Pexels video_files list (portrait preferred)."""
    files: list[dict[str, Any]] = video.get("video_files", [])
    if not files:
        return None

    # Prefer portrait HD files
    portrait_files = [
        f for f in files
        if f.get("height", 0) >= f.get("width", 0) and f.get("width", 0) >= MIN_WIDTH
    ]
    if not portrait_files:
        # Fall back to any HD file
        portrait_files = [f for f in files if f.get("width", 0) >= MIN_WIDTH]
    if not portrait_files:
        portrait_files = files

    # Sort: portrait first, then by resolution
    portrait_files.sort(key=lambda f: (
        -int(f.get("height", 0) >= f.get("width", 0)),
        -f.get("width", 0),
    ))
    return portrait_files[0] if portrait_files else None


def _cache_key(query: str, per_page: int) -> str:
    return hashlib.sha1(f"{query}|{per_page}".encode()).hexdigest()[:16]


class PexelsVideoClient:
    """Fetches, scores, downloads and caches Pexels portrait stock video clips."""

    def __init__(self, cache_dir: Path | None = None) -> None:
        self._cache = cache_dir or _cache_dir()
        self._api_key: str | None = None
        self._available: bool | None = None

    def _key(self) -> str | None:
        if self._api_key is None:
            self._api_key = _get_api_key() or ""
        return self._api_key or None

    def is_available(self) -> bool:
        """Returns True if a PEXELS_API_KEY is configured."""
        if self._available is None:
            self._available = bool(self._key())
        return self._available

    def search(
        self,
        query: str,
        concept: str,
        per_page: int = 5,
    ) -> list[dict[str, Any]]:
        """Searches Pexels API for portrait videos matching the query.

        Returns a list of Pexels video records sorted by relevance score descending.
        Returns empty list on API failure (graceful degradation).
        """
        key = self._key()
        if not key:
            log.debug("Pexels API key not configured; skipping Pexels search for '%s'", query)
            return []

        # Check JSON cache first (avoid redundant API calls)
        cache_key_str = _cache_key(query, per_page)
        cache_file = self._cache / "api_cache" / f"{cache_key_str}.json"
        cache_file.parent.mkdir(parents=True, exist_ok=True)

        if cache_file.is_file():
            try:
                age_s = time.time() - cache_file.stat().st_mtime
                if age_s < 86400 * 7:  # 7-day cache
                    data = json.loads(cache_file.read_text(encoding="utf-8"))
                    log.debug("Pexels API cache hit for query '%s'", query)
                    return data.get("results", [])
            except Exception:
                pass

        try:
            import urllib.parse
            import urllib.request

            url = (
                f"{PEXELS_VIDEO_SEARCH_URL}"
                f"?query={urllib.parse.quote(query)}"
                f"&per_page={per_page}"
                f"&orientation=portrait"
            )

            req = urllib.request.Request(
                url,
                headers={
                    "Authorization": key,
                    "User-Agent": "AL-AMR-AutoClip/1.0",
                },
            )

            with urllib.request.urlopen(req, timeout=15) as resp:
                payload = json.loads(resp.read().decode("utf-8"))

        except Exception as exc:
            log.warning("Pexels API search failed for '%s': %s", query, exc)
            return []

        videos = payload.get("videos", [])
        # Score and sort
        scored = []
        for v in videos:
            v["_relevance"] = _relevance_score(v, query, concept)
            scored.append(v)
        scored.sort(key=lambda v: v["_relevance"], reverse=True)

        # Cache result
        try:
            cache_file.write_text(
                json.dumps({"query": query, "results": scored}, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

        return scored

    def _download_video(
        self,
        video: dict[str, Any],
        concept: str,
        cue_duration_s: float,
    ) -> Path | None:
        """Downloads and reframes a Pexels video to 9:16 (1080×1920), trimmed to cue_duration_s.

        Returns the local Path to the processed file, or None on failure.
        """
        file_info = _best_video_file(video)
        if not file_info:
            return None

        download_url = file_info.get("link", "")
        if not download_url:
            return None

        video_id = video.get("id", "unknown")
        asset_key = f"pexels_{video_id}"
        processed_path = self._cache / concept / f"{asset_key}_9x16.mp4"
        processed_path.parent.mkdir(parents=True, exist_ok=True)

        # Already cached and processed
        if processed_path.is_file() and processed_path.stat().st_size > 10_000:
            log.debug("Pexels cache hit for video %s → %s", video_id, processed_path)
            return processed_path

        # Download raw
        raw_path = self._cache / concept / f"{asset_key}_raw.mp4"
        try:
            import urllib.request
            key = self._key()
            req = urllib.request.Request(
                download_url,
                headers={
                    "Authorization": key or "",
                    "User-Agent": "AL-AMR-AutoClip/1.0",
                },
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw_path.write_bytes(resp.read())
            log.info("Downloaded Pexels video %s → %s", video_id, raw_path)
        except Exception as exc:
            log.warning("Failed to download Pexels video %s: %s", video_id, exc)
            raw_path.unlink(missing_ok=True)
            return None

        # Reframe to 9:16 (1080x1920) and trim
        trim_dur = min(cue_duration_s + 1.0, video.get("duration", cue_duration_s))
        cmd = [
            "ffmpeg", "-y",
            "-ss", "0",
            "-t", f"{trim_dur:.3f}",
            "-i", str(raw_path),
            "-vf", (
                "scale=1080:1920:force_original_aspect_ratio=increase,"
                "crop=1080:1920"
            ),
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-an",
            "-movflags", "+faststart",
            str(processed_path),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if proc.returncode != 0:
                log.warning(
                    "FFmpeg reframe failed for Pexels video %s: %s",
                    video_id, proc.stderr[-400:],
                )
                processed_path.unlink(missing_ok=True)
                return None
        except Exception as exc:
            log.warning("FFmpeg reframe exception for Pexels video %s: %s", video_id, exc)
            processed_path.unlink(missing_ok=True)
            return None
        finally:
            # Remove raw file to save disk space
            raw_path.unlink(missing_ok=True)

        log.info(
            "Pexels video %s processed to 9:16 → %s (%.1fs)",
            video_id, processed_path, trim_dur,
        )
        return processed_path

    def search_and_acquire(
        self,
        query: str,
        concept: str,
        cue_duration_s: float = 3.0,
        per_page: int = 5,
        confidence_threshold: float = CONFIDENCE_THRESHOLD,
    ) -> list[VisualAsset]:
        """Searches Pexels, downloads qualifying clips, and returns VisualAsset objects.

        Only clips with relevance score >= confidence_threshold are downloaded.
        Returns empty list if Pexels is unavailable or no qualifying clips found.
        """
        if not self.is_available():
            return []

        candidates = self.search(query, concept, per_page=per_page)
        if not candidates:
            return []

        assets: list[VisualAsset] = []
        for video in candidates:
            score = video.get("_relevance", 0.0)
            if score < confidence_threshold:
                log.debug(
                    "Pexels video %s score %.2f below threshold %.2f; skipping",
                    video.get("id"), score, confidence_threshold,
                )
                continue

            dur = video.get("duration", 0)
            if dur < MIN_VIDEO_DURATION_S or dur > MAX_VIDEO_DURATION_S:
                log.debug(
                    "Pexels video %s duration %.1fs out of range [%.1f, %.1f]; skipping",
                    video.get("id"), dur, MIN_VIDEO_DURATION_S, MAX_VIDEO_DURATION_S,
                )
                continue

            processed = self._download_video(video, concept, cue_duration_s)
            if processed is None:
                continue

            w = video.get("width", 1080)
            h = video.get("height", 1920)
            asset = VisualAsset(
                asset_id=f"pexels_{video.get('id')}",
                file_path=processed,
                visual_type=VisualType.STOCK_VIDEO,
                concept=concept,
                presentation_mode=PresentationMode.FULL_SCREEN,
                width=1080,
                height=1920,
                duration_s=min(cue_duration_s + 1.0, float(dur)),
                tags=[concept, "pexels", "stock_video"],
                source_provider="pexels",
                is_video=True,
                metadata={
                    "pexels_id": video.get("id"),
                    "pexels_url": video.get("url", ""),
                    "original_width": w,
                    "original_height": h,
                    "duration_s": dur,
                    "relevance_score": round(score, 4),
                },
            )
            assets.append(asset)

        log.info(
            "Pexels acquisition for concept '%s' (query='%s'): "
            "%d videos fetched, %d qualified, %d downloaded",
            concept, query, len(candidates),
            sum(1 for v in candidates if v.get("_relevance", 0) >= confidence_threshold),
            len(assets),
        )
        return assets
