"""Invidious API Acquisition Provider for AL AMR.

Uses the official open-source Invidious architecture (https://github.com/iv-org/invidious)
to query federated Invidious instances for video stream extraction, bypassing cloud datacenter blocks.
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

DEFAULT_INVIDIOUS_INSTANCES = [
    "https://inv.tux.pizza",
    "https://invidious.nerdvpn.de",
    "https://invidious.slipfox.xyz",
    "https://yewtu.be",
    "https://invidious.jing.rocks",
    "https://iv.melmac.space",
    "https://invidious.io.lol",
]


class InvidiousAcquisitionProvider(SourceAcquisitionProvider):
    """Acquires media via federated Invidious API instances."""

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
            env_instances = os.environ.get("AUTOCLIP_INVIDIOUS_INSTANCES")
            if env_instances:
                self._instances = [u.strip() for u in env_instances.split(",") if u.strip()]
            else:
                self._instances = list(DEFAULT_INVIDIOUS_INSTANCES)

        self.connect_timeout_s = connect_timeout_s
        self.read_timeout_s = read_timeout_s
        self.max_size_bytes = max_size_bytes

    @property
    def provider_name(self) -> str:
        return "invidious"

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
        """Query Invidious instances in failover order and stream media to target_dir."""
        video_id = extract_youtube_id(source_url)
        if not video_id:
            raise SourceAcquisitionError(
                f"Source URL is not a recognized YouTube URL: {source_url}",
                code=SourceErrorCode.SOURCE_PROVIDER_UNAVAILABLE,
                provider_name=self.provider_name,
                hint="Invidious provider only handles YouTube URLs; passing to next provider.",
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
                log.warning("Invidious instance '%s' rejected by security check: %s", clean_instance, exc)
                continue

            endpoint = f"{clean_instance}/api/v1/videos/{video_id}"
            log.info("Querying Invidious instance: %s for video ID %s", clean_instance, video_id)

            try:
                with httpx.Client(
                    timeout=httpx.Timeout(self.read_timeout_s, connect=self.connect_timeout_s),
                    follow_redirects=True,
                    headers={"User-Agent": "ALAMR-CloudWorker/0.1.0"},
                ) as client:
                    resp = client.get(endpoint)
                    if resp.status_code == 404:
                        raise SourceAcquisitionError(
                            f"Video {video_id} not found on Invidious instance {clean_instance}",
                            code=SourceErrorCode.SOURCE_UNAVAILABLE,
                            provider_name=self.provider_name,
                        )
                    if resp.status_code != 200:
                        raise RuntimeError(f"Invidious instance returned HTTP {resp.status_code}")

                    video_data = resp.json()
                    fmt_streams = video_data.get("formatStreams", [])
                    adapt_streams = video_data.get("adaptiveFormats", [])

                    # 1. Prefer progressive MP4 formatStream
                    mp4_prog = [
                        s for s in fmt_streams
                        if s.get("url") and (s.get("container") == "mp4" or "mp4" in s.get("type", ""))
                    ]

                    if mp4_prog:
                        # Highest resolution first
                        chosen = mp4_prog[0]
                        stream_url = chosen["url"]
                        log.info("Invidious: found progressive MP4 stream at %s", clean_instance)
                        self._download_stream(client, stream_url, final_media_path, on_progress)
                    else:
                        # 2. Select compatible adaptive video and audio streams
                        video_adapt = [
                            s for s in adapt_streams
                            if s.get("url") and "video/mp4" in s.get("type", "")
                        ]
                        audio_adapt = [
                            s for s in adapt_streams
                            if s.get("url") and ("audio/mp4" in s.get("type", "") or "audio/m4a" in s.get("type", ""))
                        ]

                        if not video_adapt:
                            video_adapt = [s for s in adapt_streams if s.get("url") and "video" in s.get("type", "")]
                        if not audio_adapt:
                            audio_adapt = [s for s in adapt_streams if s.get("url") and "audio" in s.get("type", "")]

                        if not video_adapt or not audio_adapt:
                            raise RuntimeError("No compatible separate video/audio streams found in Invidious response")

                        temp_v = target_dir / "temp_inv_video.mp4"
                        temp_a = target_dir / "temp_inv_audio.m4a"

                        try:
                            log.info("Invidious: downloading adaptive video & audio streams to merge")
                            self._download_stream(client, video_adapt[0]["url"], temp_v, None)
                            self._download_stream(client, audio_adapt[0]["url"], temp_a, None)
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
                        "Invidious acquisition successful from %s: size=%d bytes, duration=%.2fs, sha256=%s",
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
                        diagnostic_message=f"Acquired via Invidious instance: {clean_instance}",
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
                log.warning("Invidious instance %s failed: %s", clean_instance, exc.message)
            except Exception as exc:
                instance_errors.append({"instance": clean_instance, "error": str(exc)})
                log.warning("Invidious instance %s failed: %s", clean_instance, exc)

        # If all Invidious instances failed
        raise SourceAcquisitionError(
            f"All {len(self._instances)} configured Invidious instances failed to acquire video {video_id}.",
            code=SourceErrorCode.SOURCE_PROVIDER_UNAVAILABLE,
            provider_name=self.provider_name,
            hint="Invidious backend instances were unavailable or rate-limited.",
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
