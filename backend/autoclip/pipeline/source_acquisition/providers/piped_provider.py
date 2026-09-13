"""Piped API Acquisition Provider for AL AMR.

Uses the official open-source Piped architecture (https://github.com/TeamPiped/Piped)
to query federated Piped API instances for video stream extraction, completely bypassing
datacenter IP blocks on the cloud worker.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from ..base import (
    AcquisitionResult,
    JobContext,
    SourceAcquisitionError,
    SourceAcquisitionProvider,
    SourceErrorCode,
)
from ..security import safe_target_path, validate_remote_url
from ..validation import validate_media_gate
from ..youtube_utils import extract_youtube_id, is_youtube_url, merge_video_audio

log = logging.getLogger(__name__)

DEFAULT_PIPED_INSTANCES = [
    "https://pipedapi.kavin.rocks",
    "https://api.piped.privacydev.net",
    "https://piped-api.lunar.icu",
    "https://pipedapi.leptons.xyz",
    "https://pipedapi.tokhmi.xyz",
    "https://pipedapi.r4fo.com",
]


class PipedAcquisitionProvider(SourceAcquisitionProvider):
    """Acquires media via federated Piped API backend instances."""

    def __init__(
        self,
        *,
        instances: list[str] | None = None,
        connect_timeout_s: float = 10.0,
        read_timeout_s: float = 60.0,
        max_size_bytes: int = 2 * 1024 * 1024 * 1024,  # 2 GiB
    ) -> None:
        if instances is not None:
            self._instances = list(instances)
        else:
            env_instances = os.environ.get("AUTOCLIP_PIPED_INSTANCES")
            if env_instances:
                self._instances = [u.strip() for u in env_instances.split(",") if u.strip()]
            else:
                self._instances = list(DEFAULT_PIPED_INSTANCES)

        self.connect_timeout_s = connect_timeout_s
        self.read_timeout_s = read_timeout_s
        self.max_size_bytes = max_size_bytes

    @property
    def provider_name(self) -> str:
        return "piped"

    def is_configured(self) -> bool:
        return bool(self._instances)

    @property
    def instances(self) -> list[str]:
        return list(self._instances)

    def acquire(
        self,
        source_url: str,
        target_dir: Path,
        job_context: JobContext | None = None,
        on_progress: Callable[[float], None] | None = None,
    ) -> AcquisitionResult:
        """Query Piped instances in failover order and stream media to target_dir."""
        video_id = extract_youtube_id(source_url)
        if not video_id:
            raise SourceAcquisitionError(
                f"Source URL is not a recognized YouTube URL: {source_url}",
                code=SourceErrorCode.SOURCE_PROVIDER_UNAVAILABLE,
                provider_name=self.provider_name,
                hint="Piped provider only handles YouTube URLs; passing to next provider.",
            )

        target_dir = Path(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        final_media_path = target_dir / "source.mp4"

        instance_errors: list[dict[str, Any]] = []
        start_time = time.time()

        for instance in self._instances:
            clean_instance = instance.rstrip("/")
            try:
                validate_remote_url(clean_instance, require_https=True)
            except Exception as exc:
                log.warning("Piped instance '%s' rejected by security check: %s", clean_instance, exc)
                continue

            endpoint = f"{clean_instance}/streams/{video_id}"
            log.info("Querying Piped instance: %s for video ID %s", clean_instance, video_id)

            try:
                with httpx.Client(
                    timeout=httpx.Timeout(self.read_timeout_s, connect=self.connect_timeout_s),
                    follow_redirects=True,
                    headers={"User-Agent": "ALAMR-CloudWorker/0.1.0"},
                ) as client:
                    resp = client.get(endpoint)
                    if resp.status_code == 404:
                        raise SourceAcquisitionError(
                            f"Video {video_id} not found on Piped instance {clean_instance}",
                            code=SourceErrorCode.SOURCE_UNAVAILABLE,
                            provider_name=self.provider_name,
                        )
                    if resp.status_code != 200:
                        raise RuntimeError(f"Piped instance returned HTTP {resp.status_code}")

                    stream_data = resp.json()
                    v_streams = stream_data.get("videoStreams", [])
                    a_streams = stream_data.get("audioStreams", [])

                    if not v_streams:
                        raise RuntimeError("No video streams found in Piped response")

                    def is_progressive(s: dict[str, Any]) -> bool:
                        if not s.get("url"):
                            return False
                        if s.get("videoOnly") is True:
                            return False
                        mime = (s.get("mimeType") or "").lower()
                        fmt = (s.get("format") or "").lower().replace("-", "_")
                        return "mp4" in mime or "mpeg_4" in fmt or "mp4" in fmt

                    progressive_streams = [s for s in v_streams if is_progressive(s)]

                    if progressive_streams:
                        # Pick highest quality / resolution
                        chosen = progressive_streams[0]
                        stream_url = chosen["url"]
                        log.info("Piped: found progressive stream at %s", clean_instance)
                        self._download_stream(client, stream_url, final_media_path, on_progress)
                    else:
                        # 2. Select compatible video and audio streams to merge
                        mp4_video = [
                            s for s in v_streams
                            if s.get("url") and (
                                "mp4" in (s.get("mimeType") or "").lower()
                                or "mpeg" in (s.get("format") or "").lower()
                                or "mp4" in (s.get("format") or "").lower()
                            )
                        ]
                        if not mp4_video:
                            mp4_video = [s for s in v_streams if s.get("url")]

                        m4a_audio = [
                            s for s in a_streams
                            if s.get("url") and (
                                "m4a" in (s.get("mimeType") or "").lower()
                                or "mp4" in (s.get("mimeType") or "").lower()
                                or "m4a" in (s.get("format") or "").lower()
                                or "aac" in (s.get("format") or "").lower()
                            )
                        ]
                        if not m4a_audio:
                            m4a_audio = [s for s in a_streams if s.get("url")]

                        if not m4a_audio:
                            raise RuntimeError("No compatible audio stream available in Piped response")

                        video_stream = mp4_video[0]
                        audio_stream = m4a_audio[0]

                        temp_v = target_dir / "temp_video.mp4"
                        temp_a = target_dir / "temp_audio.m4a"

                        try:
                            log.info("Piped: downloading separate video & audio streams to merge")
                            self._download_stream(client, video_stream["url"], temp_v, None)
                            self._download_stream(client, audio_stream["url"], temp_a, None)
                            merge_video_audio(temp_v, temp_a, final_media_path)
                        finally:
                            temp_v.unlink(missing_ok=True)
                            temp_a.unlink(missing_ok=True)

                    # 3. Validate media gate
                    media_info, sha256_hash = validate_media_gate(
                        final_media_path,
                        expected_dir=target_dir,
                        max_size_bytes=self.max_size_bytes,
                    )

                    duration_s = time.time() - start_time
                    file_size = final_media_path.stat().st_size

                    log.info(
                        "Piped acquisition successful from %s: size=%d bytes, duration=%.2fs, sha256=%s",
                        clean_instance,
                        file_size,
                        media_info.duration_s,
                        sha256_hash[:16],
                    )

                    return AcquisitionResult(
                        success=True,
                        local_media_path=final_media_path,
                        provider_name=self.provider_name,
                        source_url=source_url,
                        detected_media_type="video",
                        duration=media_info.duration_s,
                        file_size=file_size,
                        sha256=sha256_hash,
                        acquisition_attempts=len(instance_errors) + 1,
                        diagnostic_code="SUCCESS",
                        diagnostic_message=f"Acquired via Piped instance: {clean_instance}",
                        media_info=media_info,
                        provider_metadata={
                            "instance": clean_instance,
                            "video_id": video_id,
                            "telemetry": {
                                "provider": self.provider_name,
                                "instance": clean_instance,
                                "duration_s": round(duration_s, 2),
                                "bytes": file_size,
                                "sha256": sha256_hash,
                                "media_duration": media_info.duration_s,
                            },
                        },
                    )

            except SourceAcquisitionError as exc:
                if exc.code == SourceErrorCode.SOURCE_UNAVAILABLE:
                    raise exc
                instance_errors.append({"instance": clean_instance, "error": exc.message})
                log.warning("Piped instance %s failed: %s", clean_instance, exc.message)
            except Exception as exc:
                instance_errors.append({"instance": clean_instance, "error": str(exc)})
                log.warning("Piped instance %s failed: %s", clean_instance, exc)

        # If all Piped instances failed
        raise SourceAcquisitionError(
            f"All {len(self._instances)} configured Piped instances failed to acquire video {video_id}.",
            code=SourceErrorCode.SOURCE_PROVIDER_UNAVAILABLE,
            provider_name=self.provider_name,
            hint="Piped backend instances were unavailable or rate-limited.",
            attempts=len(instance_errors),
        )

    def _download_stream(
        self,
        client: httpx.Client,
        url: str,
        dest_path: Path,
        on_progress: Callable[[float], None] | None,
    ) -> None:
        """Download remote media stream to local destination file."""
        validate_remote_url(url, require_https=True)
        downloaded = 0
        with client.stream("GET", url, headers={"User-Agent": "ALAMR-CloudWorker/0.1.0"}) as stream_resp:
            if stream_resp.status_code != 200:
                raise RuntimeError(f"Stream URL returned HTTP status {stream_resp.status_code}")

            total_size = int(stream_resp.headers.get("Content-Length", 0))
            with dest_path.open("wb") as f:
                for chunk in stream_resp.iter_bytes(chunk_size=65536):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if downloaded > self.max_size_bytes:
                        raise SourceAcquisitionError(
                            f"Downloaded media exceeded limit of {self.max_size_bytes} bytes",
                            code=SourceErrorCode.SOURCE_MEDIA_INVALID,
                            provider_name=self.provider_name,
                        )
                    if on_progress and total_size > 0:
                        on_progress(min(0.95, downloaded / total_size))
