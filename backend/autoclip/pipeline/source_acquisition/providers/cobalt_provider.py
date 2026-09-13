"""Cobalt API Acquisition Provider for AL AMR.

Uses the official open-source Cobalt architecture (https://github.com/imputnet/cobalt, AGPL-3.0)
to query self-hosted or federated Cobalt API instances for video stream extraction.
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

log = logging.getLogger(__name__)

DEFAULT_COBALT_INSTANCES = [
    "https://api.cobalt.tools",
]


class CobaltAcquisitionProvider(SourceAcquisitionProvider):
    """Acquires media via open-source Cobalt API gateway instances."""

    def __init__(
        self,
        *,
        instances: list[str] | None = None,
        api_key: str | None = None,
        connect_timeout_s: float = 5.0,
        read_timeout_s: float = 60.0,
        max_size_bytes: int = 2 * 1024 * 1024 * 1024,  # 2 GiB
    ) -> None:
        if instances is not None:
            self._instances = list(instances)
        else:
            env_single = os.environ.get("AUTOCLIP_COBALT_ENDPOINT", "").strip()
            env_instances = os.environ.get("AUTOCLIP_COBALT_INSTANCES", "").strip()
            if env_single:
                self._instances = [env_single]
            elif env_instances:
                self._instances = [u.strip() for u in env_instances.split(",") if u.strip()]
            else:
                self._instances = list(DEFAULT_COBALT_INSTANCES)

        self._api_key = api_key or os.environ.get("AUTOCLIP_COBALT_API_KEY", "").strip()
        self.connect_timeout_s = connect_timeout_s
        self.read_timeout_s = read_timeout_s
        self.max_size_bytes = max_size_bytes

    @property
    def provider_name(self) -> str:
        return "cobalt"

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
        """Query Cobalt instances in failover order and stream media to target_dir."""
        if not self.is_configured():
            raise SourceAcquisitionError(
                "Cobalt acquisition provider is not configured.",
                code=SourceErrorCode.SOURCE_PROVIDER_UNAVAILABLE,
                provider_name=self.provider_name,
                hint="AUTOCLIP_COBALT_ENDPOINT is not configured in the environment.",
            )

        validate_remote_url(source_url)
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
                log.warning("Cobalt instance '%s' rejected by security check: %s", clean_instance, exc)
                continue

            endpoint = f"{clean_instance}/"
            log.info("Querying Cobalt API instance: %s for URL %s", clean_instance, source_url)

            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "ALAMR-CloudWorker/0.1.0",
            }
            if self._api_key:
                headers["Authorization"] = f"Api-Key {self._api_key}"

            payload = {
                "url": source_url,
                "videoQuality": "1080",
                "audioFormat": "mp3",
            }

            try:
                with httpx.Client(
                    timeout=httpx.Timeout(self.read_timeout_s, connect=self.connect_timeout_s),
                    follow_redirects=True,
                    headers=headers,
                ) as client:
                    resp = client.post(endpoint, json=payload)
                    resp_data: dict[str, Any] = {}
                    try:
                        resp_data = resp.json()
                    except Exception:
                        pass

                    if resp.status_code != 200:
                        err_code = resp_data.get("error", {}).get("code", "") if isinstance(resp_data.get("error"), dict) else ""
                        msg = f"HTTP {resp.status_code} ({err_code})" if err_code else f"HTTP {resp.status_code}"
                        raise RuntimeError(f"Cobalt instance returned {msg}")

                    status = resp_data.get("status")
                    media_stream_url = ""

                    if status in ("tunnel", "redirect"):
                        media_stream_url = resp_data.get("url", "")
                    elif status == "picker":
                        picker_items = resp_data.get("picker", [])
                        if picker_items:
                            media_stream_url = picker_items[0].get("url", "")
                    elif status == "error":
                        err_info = resp_data.get("error", {})
                        code_str = err_info.get("code", "unknown_error") if isinstance(err_info, dict) else "unknown_error"
                        raise RuntimeError(f"Cobalt returned error: {code_str}")

                    if not media_stream_url:
                        raise RuntimeError(f"Cobalt response did not provide a stream URL: {resp_data}")

                    # Validate the stream URL before connecting
                    validate_remote_url(media_stream_url, require_https=True)
                    log.info("Streaming media bytes from Cobalt resolved URL: %s", media_stream_url)

                    # Stream media bytes to disk
                    downloaded_bytes = 0
                    with client.stream("GET", media_stream_url) as stream_resp:
                        if stream_resp.status_code != 200:
                            raise RuntimeError(f"Cobalt media stream endpoint returned HTTP {stream_resp.status_code}")

                        total_bytes_header = stream_resp.headers.get("Content-Length")
                        total_bytes = int(total_bytes_header) if total_bytes_header and total_bytes_header.isdigit() else 0

                        with final_media_path.open("wb") as f_out:
                            for chunk in stream_resp.iter_bytes(chunk_size=65536):
                                downloaded_bytes += len(chunk)
                                if downloaded_bytes > self.max_size_bytes:
                                    raise SourceAcquisitionError(
                                        f"Cobalt stream exceeded maximum size of {self.max_size_bytes} bytes",
                                        code=SourceErrorCode.SOURCE_MEDIA_INVALID,
                                        provider_name=self.provider_name,
                                    )
                                f_out.write(chunk)
                                if on_progress and total_bytes > 0:
                                    on_progress(min(0.95, downloaded_bytes / total_bytes))

                # Media stream acquired; run validation gate
                gate = validate_media_gate(final_media_path)
                log.info(
                    "Cobalt acquisition succeeded via %s: size=%d bytes, duration=%.2fs",
                    clean_instance,
                    gate.file_size,
                    gate.duration,
                )

                return AcquisitionResult(
                    success=True,
                    local_media_path=final_media_path,
                    provider_name=self.provider_name,
                    source_url=source_url,
                    detected_media_type=gate.detected_media_type,
                    duration=gate.duration,
                    file_size=gate.file_size,
                    sha256=gate.sha256,
                    media_info=gate.media_info,
                    provider_metadata={
                        "provider": self.provider_name,
                        "instance": clean_instance,
                        "elapsed_s": time.time() - start_time,
                    },
                )

            except Exception as exc:
                instance_errors.append({"instance": clean_instance, "error": str(exc)})
                log.warning("Cobalt instance '%s' failed: %s", clean_instance, exc)
                if final_media_path.exists():
                    try:
                        final_media_path.unlink()
                    except OSError:
                        pass
                continue

        # All instances failed
        shutil.rmtree(target_dir, ignore_errors=True)
        all_msgs = "; ".join(f"{e['instance']} ({e['error']})" for e in instance_errors)
        raise SourceAcquisitionError(
            f"All {len(self._instances)} configured Cobalt instances failed to acquire video: {all_msgs}",
            code=SourceErrorCode.SOURCE_PROVIDER_UNAVAILABLE,
            provider_name=self.provider_name,
            hint="Cobalt instances were unavailable, timed out, or blocked.",
        )
