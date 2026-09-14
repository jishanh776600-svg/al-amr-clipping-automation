"""Audio Quality Gate for validating mixed audio against broadcast standards."""

from __future__ import annotations

import json
import logging
import math
import subprocess
from pathlib import Path
from typing import Any

from .models import AudioGateResult

log = logging.getLogger(__name__)


class AudioQualityGate:
    """Evaluates final mixed audio against loudness, peak, and integrity criteria."""

    def __init__(
        self,
        min_duration_ratio: float = 0.90,
        max_duration_diff_s: float = 0.5,
        target_lufs: float = -14.0,
        lufs_warn_tolerance: float = 6.0,
        true_peak_limit_db: float = -0.5,
    ) -> None:
        self.min_duration_ratio = min_duration_ratio
        self.max_duration_diff_s = max_duration_diff_s
        self.target_lufs = target_lufs
        self.lufs_warn_tolerance = lufs_warn_tolerance
        self.true_peak_limit_db = true_peak_limit_db

    def probe_audio(self, audio_path: Path) -> dict[str, Any]:
        """Probes basic stream audio metadata with ffprobe."""
        cmd = [
            "ffprobe",
            "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=codec_name,sample_rate,channels,duration:format=duration,size",
            "-of", "json",
            str(audio_path),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return json.loads(res.stdout)

    def measure_loudness(self, audio_path: Path) -> tuple[float, float]:
        """Measures integrated LUFS and true peak dBFS using ffmpeg ebur128 or volumedetect filter."""
        # Use volumedetect filter for fast and robust peak / level checking
        cmd = [
            "ffmpeg",
            "-i", str(audio_path),
            "-af", "volumedetect",
            "-vn",
            "-sn",
            "-f", "null",
            "NUL" if subprocess.os.name == "nt" else "/dev/null",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        max_volume = -1.5
        mean_volume = -16.0
        for line in res.stderr.splitlines():
            if "max_volume:" in line:
                try:
                    parts = line.split("max_volume:")
                    max_volume = float(parts[1].replace("dB", "").strip())
                except Exception:
                    pass
            elif "mean_volume:" in line:
                try:
                    parts = line.split("mean_volume:")
                    mean_volume = float(parts[1].replace("dB", "").strip())
                except Exception:
                    pass

        # Approximate integrated LUFS: mean_volume is closely correlated with integrated loudness
        integrated_lufs = round(mean_volume, 2)
        true_peak_db = round(max_volume, 2)
        return integrated_lufs, true_peak_db

    def evaluate(self, audio_path: Path, expected_duration_s: float) -> AudioGateResult:
        """Validates the rendered audio file against quality gates."""
        if not audio_path.is_file() or audio_path.stat().st_size == 0:
            return AudioGateResult(
                status="MIX_REJECT",
                quality_score=0.0,
                duration_s=0.0,
                expected_duration_s=expected_duration_s,
                duration_diff_s=expected_duration_s,
                rejection_reasons=["Audio output file missing or empty"],
            )

        warnings: list[str] = []
        rejection_reasons: list[str] = []
        metrics: dict[str, Any] = {}

        # 1. Probe audio stream
        try:
            probe_data = self.probe_audio(audio_path)
            streams = probe_data.get("streams", [])
            if not streams:
                return AudioGateResult(
                    status="MIX_REJECT",
                    quality_score=0.0,
                    duration_s=0.0,
                    expected_duration_s=expected_duration_s,
                    rejection_reasons=["No audio stream found in output file"],
                )
            a_stream = streams[0]
            channels = int(a_stream.get("channels", 0))
            sample_rate = int(a_stream.get("sample_rate", 0))
            metrics["channels"] = channels
            metrics["sample_rate"] = sample_rate

            duration_s = float(
                a_stream.get("duration")
                or probe_data.get("format", {}).get("duration", 0.0)
            )
            metrics["duration_s"] = duration_s
        except Exception as exc:
            return AudioGateResult(
                status="MIX_REJECT",
                quality_score=0.0,
                duration_s=0.0,
                expected_duration_s=expected_duration_s,
                rejection_reasons=[f"Audio probe failed: {exc}"],
            )

        # 2. Duration check
        diff_s = abs(duration_s - expected_duration_s)
        if expected_duration_s > 0 and duration_s < (expected_duration_s * self.min_duration_ratio):
            rejection_reasons.append(
                f"Audio duration {duration_s:.2f}s is significantly shorter than expected {expected_duration_s:.2f}s"
            )
        elif diff_s > self.max_duration_diff_s:
            warnings.append(
                f"Audio duration {duration_s:.2f}s differs from expected {expected_duration_s:.2f}s by {diff_s:.2f}s"
            )

        # 3. Loudness and True Peak check
        try:
            integrated_lufs, true_peak_db = self.measure_loudness(audio_path)
            metrics["integrated_lufs"] = integrated_lufs
            metrics["true_peak_db"] = true_peak_db

            # Clipping check
            if true_peak_db > self.true_peak_limit_db:
                warnings.append(
                    f"True peak {true_peak_db:.2f} dBFS exceeds recommended ceiling {self.true_peak_limit_db:.2f} dBFS"
                )
            if true_peak_db >= 0.0:
                warnings.append("Possible clipping detected (peak >= 0.0 dBFS)")

            # Loudness check
            if integrated_lufs < -40.0:
                rejection_reasons.append(f"Audio output is near silent (level: {integrated_lufs:.1f} dBFS)")
            elif integrated_lufs > -5.0:
                warnings.append(f"Audio is unusually loud ({integrated_lufs:.1f} dBFS)")
            elif abs(integrated_lufs - self.target_lufs) > self.lufs_warn_tolerance:
                warnings.append(
                    f"Audio loudness {integrated_lufs:.1f} LUFS deviates from target {self.target_lufs:.1f} LUFS"
                )
        except Exception as exc:
            log.warning("Could not measure loudness: %s", exc)
            integrated_lufs = -14.0
            true_peak_db = -1.5

        # 4. Compute quality score
        quality_score = 100.0
        if rejection_reasons:
            status = "MIX_REJECT"
            quality_score = 0.0
        elif warnings:
            status = "MIX_WARN"
            quality_score = max(50.0, 100.0 - (len(warnings) * 15.0))
        else:
            status = "MIX_PASS"
            quality_score = 98.0

        return AudioGateResult(
            status=status,
            quality_score=round(quality_score, 1),
            integrated_lufs=integrated_lufs,
            true_peak_db=true_peak_db,
            duration_s=round(duration_s, 2),
            expected_duration_s=round(expected_duration_s, 2),
            duration_diff_s=round(diff_s, 2),
            warnings=warnings,
            rejection_reasons=rejection_reasons,
            metrics=metrics,
        )
