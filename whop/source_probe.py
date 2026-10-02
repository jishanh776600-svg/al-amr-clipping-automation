"""Safe Read-Only Source Probe and Capability Classifier (Step 7.4).

Performs deterministic capability classification and bounded read-only inspection
of media sources found in Whop campaigns:
- Direct MP4 / MOV / WebM media URLs
- Public CDN URLs (CloudFront, Cloudflare, Fastly, Bunny)
- Public S3 / object storage URLs
- Google Drive integrated files & folders
- Public Dropbox links (with dl=1 conversion)
- YouTube (classified as PUBLIC_YOUTUBE_RESTRICTED for cloud workers)
- Auth/login/cookie/captcha required sources (marked ineligible)
"""

from __future__ import annotations

import io
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, parse_qs

import httpx

from .models import (
    SourceCapability,
    SourceTier,
    SourceProbeResult,
)

log = logging.getLogger(__name__)

# Standard browser UA for public probe checks
PROBE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 AutoClip/1.0"
)

# Known direct video container extensions
VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi")

# Magic bytes signatures for media containers
MAGIC_MP4 = b"ftyp"
MAGIC_MATROSKA_WEBM = b"\x1a\x45\xdf\xa3"
MAGIC_RIFF = b"RIFF"


def classify_source_url(url: str) -> Tuple[SourceCapability, SourceTier]:
    """Classifies a URL deterministically into its SourceCapability and SourceTier."""
    if not url or not isinstance(url, str):
        return SourceCapability.UNSUPPORTED, SourceTier.TIER_5_AUTH_BLOCKED

    clean = url.strip()
    u = clean.lower()

    if not (u.startswith("http://") or u.startswith("https://")):
        return SourceCapability.UNSUPPORTED, SourceTier.TIER_5_AUTH_BLOCKED

    parsed = urlparse(clean)
    path = parsed.path.lower()
    netloc = parsed.netloc.lower()

    # 1. Google Drive (Integrated Service Authentication Exception)
    if "drive.google.com" in netloc or "drive.usercontent.google.com" in netloc:
        return SourceCapability.GOOGLE_DRIVE_INTEGRATED, SourceTier.TIER_1_GOOGLE_DRIVE

    # 2. Public S3 / Object Storage
    if "s3.amazonaws.com" in netloc or ".s3." in netloc or "storage.googleapis.com" in netloc or "blob.core.windows.net" in netloc:
        if any(path.endswith(ext) for ext in VIDEO_EXTENSIONS):
            return SourceCapability.PUBLIC_S3, SourceTier.TIER_0_DIRECT_CDN_S3
        return SourceCapability.PUBLIC_S3, SourceTier.TIER_0_DIRECT_CDN_S3

    # 3. Public CDN
    if any(cdn in netloc for cdn in ("cloudfront.net", "fastly.net", "b-cdn.net", "bunnycdn.com", "cloudflare.com")):
        if any(path.endswith(ext) for ext in VIDEO_EXTENSIONS):
            return SourceCapability.PUBLIC_CDN, SourceTier.TIER_0_DIRECT_CDN_S3
        return SourceCapability.PUBLIC_CDN, SourceTier.TIER_0_DIRECT_CDN_S3

    # 4. Dropbox (Public Media Sharing)
    if "dropbox.com" in netloc or "dropboxusercontent.com" in netloc:
        return SourceCapability.DROPBOX_PUBLIC, SourceTier.TIER_3_DROPBOX_PUBLIC

    # 5. Direct Media File
    if any(path.endswith(ext) for ext in VIDEO_EXTENSIONS):
        return SourceCapability.DIRECT_MEDIA, SourceTier.TIER_0_DIRECT_CDN_S3

    # 6. YouTube (Operationally Restricted on Cloud Workers)
    if "youtube.com" in netloc or "youtu.be" in netloc:
        return SourceCapability.PUBLIC_YOUTUBE_RESTRICTED, SourceTier.TIER_4_YOUTUBE_RESTRICTED

    # 7. Other Generic / Fallback
    return SourceCapability.PUBLIC_FILE_HOST, SourceTier.TIER_2_PUBLIC_FILE_HOST


def convert_dropbox_to_direct_url(url: str) -> str:
    """Converts a public Dropbox sharing link into an immediate streamable binary download URL."""
    clean = url.strip()
    if "dropbox.com" not in clean.lower():
        return clean

    if "dl=0" in clean:
        return clean.replace("dl=0", "dl=1")
    if "dl=1" not in clean:
        sep = "&" if "?" in clean else "?"
        return f"{clean}{sep}dl=1"
    return clean


class SourceProbe:
    """Safe, read-only HTTP probe that verifies source accessibility and media headers without full downloads."""

    def __init__(self, timeout_s: float = 12.0) -> None:
        self.timeout_s = timeout_s

    def probe_url(self, raw_url: str, probe_bytes: int = 1024) -> SourceProbeResult:
        """Probes a candidate media source with bounded HTTP Range requests."""
        start_time = time.monotonic()
        cap, tier = classify_source_url(raw_url)
        domain = urlparse(raw_url).netloc.lower()

        target_url = raw_url
        if cap == SourceCapability.DROPBOX_PUBLIC:
            target_url = convert_dropbox_to_direct_url(raw_url)

        # Handle Google Drive via integrated capability
        if cap == SourceCapability.GOOGLE_DRIVE_INTEGRATED:
            latency = (time.monotonic() - start_time) * 1000.0
            return SourceProbeResult(
                url=raw_url,
                final_url=raw_url,
                domain=domain,
                capability=cap,
                tier=tier,
                is_supported_no_login=True,
                requires_login=False,
                requires_integrated_auth=True,
                status_code=200,
                content_type="video/mp4",
                supports_range=True,
                is_valid_media=True,
                media_format="gdrive_object",
                probe_latency_ms=round(latency, 2),
                details={"auth_method": "integrated_google_drive_credentials"},
            )

        # Handle YouTube restricted classification
        if cap == SourceCapability.PUBLIC_YOUTUBE_RESTRICTED:
            latency = (time.monotonic() - start_time) * 1000.0
            return SourceProbeResult(
                url=raw_url,
                final_url=raw_url,
                domain=domain,
                capability=cap,
                tier=tier,
                is_supported_no_login=True,
                requires_login=False,
                status_code=200,
                content_type="video/youtube",
                supports_range=False,
                is_valid_media=True,
                media_format="youtube_stream",
                rejection_reason="REJECTED_CLOUD_SOURCE_UNRELIABLE",
                probe_latency_ms=round(latency, 2),
                details={
                    "policy": "Restricted on cloud workers due to IP throttling (>1h timeout on long videos)",
                    "technically_downloadable": True,
                    "production_reliable": False,
                },
            )

        # Perform bounded HTTP probe on direct / CDN / S3 / Dropbox URLs
        headers = {
            "User-Agent": PROBE_USER_AGENT,
            "Accept": "*/*",
            "Range": f"bytes=0-{probe_bytes - 1}",
        }

        try:
            with httpx.Client(follow_redirects=True, timeout=self.timeout_s) as client:
                resp = client.get(target_url, headers=headers)
                latency = (time.monotonic() - start_time) * 1000.0

                final_url = str(resp.url)
                status = resp.status_code
                c_type = resp.headers.get("content-type", "").lower()
                c_len_str = resp.headers.get("content-length")
                c_range = resp.headers.get("content-range")

                total_size = None
                if c_range and "/" in c_range:
                    try:
                        total_size = int(c_range.split("/")[-1])
                    except ValueError:
                        pass
                elif c_len_str and c_len_str.isdigit():
                    total_size = int(c_len_str)

                supports_range = (status == 206) or bool(c_range)

                # Check for login or auth redirect
                requires_login = False
                rejection_reason = None

                if status in (401, 403):
                    requires_login = True
                    cap = SourceCapability.AUTH_REQUIRED
                    tier = SourceTier.TIER_5_AUTH_BLOCKED
                    rejection_reason = f"HTTP_{status}_ACCESS_DENIED"
                elif status >= 400:
                    rejection_reason = f"HTTP_{status}_ERROR"
                elif "login" in final_url.lower() or "signin" in final_url.lower():
                    requires_login = True
                    cap = SourceCapability.LOGIN_REQUIRED
                    tier = SourceTier.TIER_5_AUTH_BLOCKED
                    rejection_reason = "REDIRECTED_TO_LOGIN_PAGE"

                # Media validation from header bytes
                header_bytes = resp.content[:64]
                is_valid_media = False
                media_fmt = None

                # Detect MP4
                if MAGIC_MP4 in header_bytes or "video/mp4" in c_type:
                    is_valid_media = True
                    media_fmt = "mp4"
                elif MAGIC_MATROSKA_WEBM in header_bytes or "webm" in c_type or "mkv" in c_type:
                    is_valid_media = True
                    media_fmt = "webm/mkv"
                elif header_bytes.startswith(MAGIC_RIFF) and b"AVI " in header_bytes:
                    is_valid_media = True
                    media_fmt = "avi"
                elif "video/" in c_type or "application/octet-stream" in c_type:
                    # Generic media container
                    if any(target_url.lower().split("?")[0].endswith(ext) for ext in VIDEO_EXTENSIONS):
                        is_valid_media = True
                        media_fmt = "direct_binary"

                # Check if response is HTML masquerading as video
                if not is_valid_media and ("text/html" in c_type or b"<html" in header_bytes.lower() or b"<!doctype" in header_bytes.lower()):
                    is_valid_media = False
                    if not rejection_reason:
                        rejection_reason = "HTML_PAGE_NOT_DIRECT_MEDIA"

                is_supported = (status in (200, 206)) and is_valid_media and not requires_login

                return SourceProbeResult(
                    url=raw_url,
                    final_url=final_url,
                    domain=domain,
                    capability=cap,
                    tier=tier,
                    is_supported_no_login=is_supported,
                    requires_login=requires_login,
                    requires_integrated_auth=False,
                    status_code=status,
                    content_type=c_type,
                    content_length=total_size,
                    supports_range=supports_range,
                    is_valid_media=is_valid_media,
                    media_format=media_fmt,
                    rejection_reason=rejection_reason,
                    probe_latency_ms=round(latency, 2),
                    details={
                        "bytes_probed": len(resp.content),
                        "header_hex": header_bytes[:16].hex(),
                    },
                )
        except Exception as exc:
            latency = (time.monotonic() - start_time) * 1000.0
            return SourceProbeResult(
                url=raw_url,
                final_url=raw_url,
                domain=domain,
                capability=cap,
                tier=tier,
                is_supported_no_login=False,
                requires_login=False,
                status_code=None,
                rejection_reason=f"PROBE_EXCEPTION: {exc}",
                probe_latency_ms=round(latency, 2),
            )
