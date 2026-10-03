"""Public Social Post URL Guard (Step 10.1).

Validates that published social media URLs represent genuine, publicly accessible
posts on YouTube, Instagram, or TikTok, strictly rejecting hardcoded stubs,
mock templates, and dead links.

Invariants:
1. Rejects known synthetic placeholders ('live_verified_clip', 'test_clip', 'example.com', etc.).
2. Matches platform-specific URL regex (e.g. YouTube 11-char video ID, Instagram reel shortcode, TikTok video ID).
3. Optionally queries remote platform oEmbed / HTTP endpoints to ensure public accessibility.
"""

from __future__ import annotations

import logging
import re
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# Platform URL patterns
YOUTUBE_SHORTS_REGEX = re.compile(
    r"^https?://(?:www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]{11})(?:\?.*)?$", re.IGNORECASE
)
YOUTUBE_WATCH_REGEX = re.compile(
    r"^https?://(?:www\.)?youtube\.com/watch\?.*v=([a-zA-Z0-9_-]{11})(?:&.*)?$", re.IGNORECASE
)
YOUTUBE_SHORTLINK_REGEX = re.compile(
    r"^https?://youtu\.be/([a-zA-Z0-9_-]{11})(?:\?.*)?$", re.IGNORECASE
)

INSTAGRAM_REEL_REGEX = re.compile(
    r"^https?://(?:www\.)?instagram\.com/reel/([a-zA-Z0-9_-]{5,30})/?(?:\?.*)?$", re.IGNORECASE
)
INSTAGRAM_POST_REGEX = re.compile(
    r"^https?://(?:www\.)?instagram\.com/p/([a-zA-Z0-9_-]{5,30})/?(?:\?.*)?$", re.IGNORECASE
)

TIKTOK_VIDEO_REGEX = re.compile(
    r"^https?://(?:www\.)?tiktok\.com/@[\w\.-]+/video/(\d{15,25})/?(?:\?.*)?$", re.IGNORECASE
)
TIKTOK_SHORTLINK_REGEX = re.compile(
    r"^https?://(?:vm|vt)\.tiktok\.com/([a-zA-Z0-9]{5,20})/?(?:\?.*)?$", re.IGNORECASE
)

# Synthetic stubs and placeholders to permanently reject
SYNTHETIC_URL_MARKERS = [
    "live_verified_clip",
    "test_clip",
    "example.com",
    "mock_clip",
    "dummy_clip",
    "placeholder",
    "fake_post",
    "stub_url",
    "sample_post",
]


class UrlGuardError(ValueError):
    """Raised when a social media post URL fails validation."""
    pass


def parse_and_validate_platform_url(platform: str, url: str) -> tuple[str, str]:
    """Parse a URL against platform-specific syntax and return (canonical_platform, external_id)."""
    if not url or not isinstance(url, str):
        raise UrlGuardError("URL is empty or not a string.")

    clean_url = url.strip()

    # Reject synthetic markers
    for marker in SYNTHETIC_URL_MARKERS:
        if marker.lower() in clean_url.lower():
            raise UrlGuardError(
                f"Synthetic URL placeholder detected: '{clean_url}'. "
                f"Production requires a genuine, published social media post URL."
            )

    norm_plat = platform.lower().strip()
    if "youtube" in norm_plat or "shorts" in norm_plat:
        m = (
            YOUTUBE_SHORTS_REGEX.match(clean_url)
            or YOUTUBE_WATCH_REGEX.match(clean_url)
            or YOUTUBE_SHORTLINK_REGEX.match(clean_url)
        )
        if not m:
            raise UrlGuardError(f"Invalid YouTube URL format or video ID length: '{clean_url}'")
        video_id = m.group(1)
        if len(video_id) != 11:
            raise UrlGuardError(f"YouTube video ID must be exactly 11 characters, got '{video_id}'")
        return "youtube", video_id

    if "instagram" in norm_plat or "reel" in norm_plat:
        m = INSTAGRAM_REEL_REGEX.match(clean_url) or INSTAGRAM_POST_REGEX.match(clean_url)
        if not m:
            raise UrlGuardError(f"Invalid Instagram post/reel URL format: '{clean_url}'")
        return "instagram", m.group(1)

    if "tiktok" in norm_plat:
        m = TIKTOK_VIDEO_REGEX.match(clean_url) or TIKTOK_SHORTLINK_REGEX.match(clean_url)
        if not m:
            raise UrlGuardError(f"Invalid TikTok video URL format: '{clean_url}'")
        return "tiktok", m.group(1)

    raise UrlGuardError(f"Unsupported social publishing platform: '{platform}'")


def verify_remote_url_accessibility(platform: str, url: str, external_id: str, timeout_s: float = 10.0) -> bool:
    """Remotely verify that the URL is public and returns HTTP 200."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
    }

    # For YouTube, oEmbed is the most reliable public check
    check_url = url
    if platform == "youtube":
        check_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={external_id}&format=json"

    req = urllib.request.Request(check_url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return resp.status in (200, 201)
    except urllib.error.HTTPError as he:
        log.warning("Remote URL check returned HTTP %s for %s", he.code, url)
        return False
    except Exception as exc:
        log.warning("Remote URL check connection failed for %s: %s", url, exc)
        return False


def verify_real_public_post_url(
    platform: str,
    url: str,
    check_remote: bool = False,
    timeout_s: float = 10.0,
) -> Dict[str, Any]:
    """Verify that a URL is a genuine public social media post.
    
    Raises UrlGuardError on format failure or synthetic placeholder.
    """
    canonical_plat, external_id = parse_and_validate_platform_url(platform, url)

    if check_remote:
        is_accessible = verify_remote_url_accessibility(
            canonical_plat, url, external_id, timeout_s=timeout_s
        )
        if not is_accessible:
            raise UrlGuardError(
                f"Public post URL '{url}' is not accessible remotely (HTTP error or private video)."
            )

    return {
        "platform": canonical_plat,
        "url": url,
        "external_id": external_id,
        "is_verified": True,
    }
