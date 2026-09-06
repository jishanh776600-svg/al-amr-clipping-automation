"""Hardened media downloader with integrity verification, streaming, and anti-spoofing."""

import hashlib
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
import httpx

from clipping.ingestion.exceptions import (
    IngestionNetworkError,
    InvalidSourceError,
    UnsupportedMediaError,
)
from clipping.logging.logger import get_logger
from clipping.qa.prober import MediaProber

logger = get_logger("clipping.ingestion.robust_downloader")


class RobustMediaDownloader:
    """
    Hardened HTTP downloader for external video assets.
    Protects against HTML/login-gate spoofing, oversized files, corrupted payloads,
    and calculates stream SHA-256 checksums with media stream validation.
    """

    MAX_DOWNLOAD_SIZE_BYTES = 500 * 1024 * 1024  # 500 MB
    DEFAULT_TIMEOUT_SECONDS = 60.0
    DISALLOWED_CONTENT_TYPES = {
        "text/html",
        "text/plain",
        "application/json",
        "text/xml",
        "application/xml",
        "application/javascript",
        "text/css",
    }

    def __init__(
        self,
        max_size_bytes: int = MAX_DOWNLOAD_SIZE_BYTES,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        prober: Optional[MediaProber] = None,
    ):
        self.max_size_bytes = max_size_bytes
        self.timeout_seconds = timeout_seconds
        self.prober = prober or MediaProber()

    async def download_and_verify(
        self,
        url: str,
        destination_path: str,
    ) -> Dict[str, Any]:
        """
        Streams a remote video URL directly to destination_path while validating:
        - HTTP 200/206 status
        - Non-HTML Content-Type
        - Size bounds
        - SHA-256 checksum
        - Container readability via MediaProber (FFprobe/cv2)

        Returns dict of {checksum, file_size, mime_type, duration, width, height, fps}.
        """
        logger.info("Initiating robust remote media download", url=url, destination=destination_path)
        os.makedirs(os.path.dirname(os.path.abspath(destination_path)), exist_ok=True)

        sha256 = hashlib.sha256()
        total_downloaded = 0
        detected_mime = "application/octet-stream"
        final_url = url

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                follow_redirects=True,
                headers={"User-Agent": "AlAmrClippingBot/2.0 (Video Ingest Engine)"},
            ) as client:
                async with client.stream("GET", url) as response:
                    final_url = str(response.url)
                    if response.status_code not in (200, 206):
                        raise IngestionNetworkError(
                            f"Remote server returned HTTP {response.status_code} for URL: {url}"
                        )

                    # 1. Inspect Content-Type
                    content_type = response.headers.get("content-type", "").lower().split(";")[0].strip()
                    if content_type:
                        detected_mime = content_type
                        if content_type in self.DISALLOWED_CONTENT_TYPES:
                            raise InvalidSourceError(
                                f"Remote URL returned non-video content type '{content_type}' (HTML/error page pretending to be video)"
                            )

                    # 2. Inspect Content-Length
                    content_length = response.headers.get("content-length")
                    if content_length:
                        try:
                            expected_size = int(content_length)
                            if expected_size > self.max_size_bytes:
                                raise UnsupportedMediaError(
                                    f"Remote video size ({expected_size} bytes) exceeds maximum limit ({self.max_size_bytes} bytes)"
                                )
                        except ValueError:
                            pass

                    # 3. Stream download with chunk inspection
                    first_chunk = True
                    with open(destination_path, "wb") as f:
                        async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                            if not chunk:
                                continue

                            if first_chunk:
                                first_chunk = False
                                # Inspect leading bytes for HTML tags
                                sample = chunk[:512].decode("utf-8", errors="ignore").lower()
                                if "<!doctype html" in sample or "<html" in sample or "<head" in sample:
                                    f.close()
                                    if os.path.exists(destination_path):
                                        os.remove(destination_path)
                                    raise InvalidSourceError(
                                        "Downloaded payload begins with HTML markup instead of binary media container"
                                    )

                            total_downloaded += len(chunk)
                            if total_downloaded > self.max_size_bytes:
                                f.close()
                                if os.path.exists(destination_path):
                                    os.remove(destination_path)
                                raise UnsupportedMediaError(
                                    f"Downloaded media stream exceeded maximum limit of {self.max_size_bytes} bytes"
                                )

                            sha256.update(chunk)
                            f.write(chunk)

        except (InvalidSourceError, UnsupportedMediaError, IngestionNetworkError):
            if os.path.exists(destination_path):
                try:
                    os.remove(destination_path)
                except Exception:
                    pass
            raise
        except httpx.HTTPError as e:
            if os.path.exists(destination_path):
                try:
                    os.remove(destination_path)
                except Exception:
                    pass
            raise IngestionNetworkError(f"Network error downloading video stream from {url}: {str(e)}") from e
        except Exception as e:
            if os.path.exists(destination_path):
                try:
                    os.remove(destination_path)
                except Exception:
                    pass
            raise IngestionNetworkError(f"Unexpected error downloading media: {str(e)}") from e

        if total_downloaded == 0:
            if os.path.exists(destination_path):
                os.remove(destination_path)
            raise InvalidSourceError("Remote media download resulted in zero bytes")

        digest = sha256.hexdigest()

        # 4. Probe media validity
        try:
            probe_result = await self.prober.probe_media(destination_path)
            if not probe_result.is_valid:
                if os.path.exists(destination_path):
                    os.remove(destination_path)
                raise UnsupportedMediaError(
                    f"Downloaded media failed container integrity check: {probe_result.video_codec}"
                )

            return {
                "local_path": destination_path,
                "file_size": total_downloaded,
                "checksum": digest,
                "mime_type": detected_mime,
                "final_url": final_url,
                "duration": probe_result.duration_seconds,
                "width": probe_result.width,
                "height": probe_result.height,
                "fps": probe_result.fps,
                "video_codec": probe_result.video_codec,
                "audio_codec": probe_result.audio_codec,
            }
        except (InvalidSourceError, UnsupportedMediaError):
            raise
        except Exception as e:
            if os.path.exists(destination_path):
                try:
                    os.remove(destination_path)
                except Exception:
                    pass
            raise UnsupportedMediaError(f"Corrupted or invalid media container: {str(e)}") from e
