"""BGM Mixing Engine with speech-aware sidechain ducking, seamless looping, and limiter."""

from __future__ import annotations

import logging
import math
import subprocess
import time
from pathlib import Path
from typing import Any

from autoclip.db.models import BGMAssetRecord, BGMMixRecord, Clip, new_id, utcnow
from .models import AudioGateResult, DuckingConfig
from .quality_gate import AudioQualityGate

log = logging.getLogger(__name__)


class BGMMixingEngine:
    """Production-grade BGM mixing engine with speech ducking and loudness compliance."""

    def __init__(
        self,
        config: DuckingConfig | None = None,
        quality_gate: AudioQualityGate | None = None,
    ) -> None:
        self.config = config or DuckingConfig()
        self.quality_gate = quality_gate or AudioQualityGate(
            target_lufs=self.config.target_lufs,
            true_peak_limit_db=self.config.true_peak_limit,
        )

    def probe_duration(self, file_path: Path) -> float:
        """Returns the duration of an audio or video file in seconds."""
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(file_path),
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return float(res.stdout.strip())
        except Exception:
            return 0.0

    def mix_clip(
        self,
        clip: Clip,
        speech_input_path: Path,
        speech_start_offset_s: float,
        duration_s: float,
        bgm_asset: BGMAssetRecord | None,
        output_path: Path,
    ) -> BGMMixRecord:
        """Mixes selected BGM asset under speech audio for a clip.

        If bgm_asset is None, extracts/copies speech audio untouched (No BGM mode).
        If bgm_asset is specified but does not exist, raises FileNotFoundError.
        """
        start_time = time.time()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # 1. No BGM Mode: Preserve original speech cleanly
        if bgm_asset is None:
            log.info("No BGM selected for clip %s; extracting speech audio directly", clip.id)
            cmd = [
                "ffmpeg",
                "-y",
                "-ss", str(speech_start_offset_s),
                "-t", str(duration_s),
                "-i", str(speech_input_path),
                "-vn",
                "-sn",
                "-c:a", "aac",
                "-b:a", "192k",
                "-ar", "48000",
                str(output_path),
            ]
            subprocess.run(cmd, capture_output=True, text=True, check=True)
            elapsed_s = round(time.time() - start_time, 3)

            gate_res = self.quality_gate.evaluate(output_path, expected_duration_s=duration_s)

            return BGMMixRecord(
                id=new_id(),
                clip_id=clip.id,
                job_id=clip.job_id,
                bgm_asset_id=None,
                bgm_asset_name="",
                bgm_applied=False,
                clip_duration_s=round(duration_s, 2),
                bgm_duration_s=0.0,
                loop_trim_decision="none",
                ducking_applied=False,
                ducking_parameters={},
                normalization_applied=False,
                integrated_lufs=gate_res.integrated_lufs,
                true_peak_db=gate_res.true_peak_db,
                quality_score=gate_res.quality_score,
                quality_status=gate_res.status,
                warnings=gate_res.warnings,
                rejection_reasons=gate_res.rejection_reasons,
                processing_time_s=elapsed_s,
                mixed_audio_path=str(output_path),
                telemetry={"mode": "no_bgm", "gate_metrics": gate_res.metrics},
                created_at=utcnow(),
                updated_at=utcnow(),
            )

        # 2. Operator BGM specified: Validate asset existence
        bgm_path = Path(bgm_asset.file_path)
        if not bgm_path.is_file():
            raise FileNotFoundError(
                f"Authoritative BGM asset '{bgm_asset.name}' (ID: {bgm_asset.id}) not found at {bgm_path}"
            )

        bgm_duration_s = bgm_asset.duration_s
        if bgm_duration_s <= 0.0:
            bgm_duration_s = self.probe_duration(bgm_path)

        # Determine loop vs trim
        if duration_s > (bgm_duration_s + 0.5):
            loop_trim_decision = "loop"
        else:
            loop_trim_decision = "trim"

        # Calculate fade out start
        fade_out_st = max(0.0, duration_s - self.config.fade_out_s)

        # Build FFmpeg command with sidechain compression ducking
        # Input 0: Speech input (sliced to clip window)
        # Input 1: BGM input (stream-looped if clip > bgm)
        cmd = ["ffmpeg", "-y"]

        # Speech input
        cmd.extend([
            "-ss", str(speech_start_offset_s),
            "-t", str(duration_s),
            "-i", str(speech_input_path),
        ])

        # BGM input
        if loop_trim_decision == "loop":
            cmd.extend(["-stream_loop", "-1", "-i", str(bgm_path)])
        else:
            cmd.extend(["-i", str(bgm_path)])

        filter_complex = (
            f"[1:a]atrim=0:{duration_s:.3f},asetpts=PTS-STARTPTS,"
            f"afade=t=in:st=0:d={self.config.fade_in_s},"
            f"afade=t=out:st={fade_out_st:.3f}:d={self.config.fade_out_s}[bgm_faded];"
            f"[0:a]asplit=2[speech_main][speech_sc];"
            f"[bgm_faded][speech_sc]sidechaincompress="
            f"threshold={self.config.threshold}:"
            f"ratio={self.config.ratio}:"
            f"attack={self.config.attack_ms}:"
            f"release={self.config.release_ms}[bgm_ducked];"
            f"[speech_main][bgm_ducked]amix="
            f"inputs=2:duration=first:dropout_transition=2:"
            f"weights={self.config.speech_weight} {self.config.bgm_weight},"
            f"alimiter=limit={self.config.limiter_limit}:attack=5:release=50:asc=1,"
            f"loudnorm=I={self.config.target_lufs}:TP={self.config.true_peak_limit}:LRA={self.config.lra}[out_a]"
        )

        cmd.extend([
            "-filter_complex", filter_complex,
            "-map", "[out_a]",
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "48000",
            str(output_path),
        ])

        log.info(
            "Mixing clip %s with BGM '%s' (decision=%s, duration=%.2fs)",
            clip.id, bgm_asset.name, loop_trim_decision, duration_s
        )

        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            log.error("FFmpeg mixing failed for clip %s: %s", clip.id, proc.stderr)
            raise RuntimeError(f"FFmpeg mixing failed: {proc.stderr[:400]}")

        elapsed_s = round(time.time() - start_time, 3)

        # Evaluate quality gate
        gate_res = self.quality_gate.evaluate(output_path, expected_duration_s=duration_s)

        record = BGMMixRecord(
            id=new_id(),
            clip_id=clip.id,
            job_id=clip.job_id,
            bgm_asset_id=bgm_asset.id,
            bgm_asset_name=bgm_asset.name,
            bgm_applied=True,
            clip_duration_s=round(duration_s, 2),
            bgm_duration_s=round(bgm_duration_s, 2),
            loop_trim_decision=loop_trim_decision,
            ducking_applied=True,
            ducking_parameters=self.config.to_dict(),
            normalization_applied=True,
            integrated_lufs=gate_res.integrated_lufs,
            true_peak_db=gate_res.true_peak_db,
            quality_score=gate_res.quality_score,
            quality_status=gate_res.status,
            warnings=gate_res.warnings,
            rejection_reasons=gate_res.rejection_reasons,
            processing_time_s=elapsed_s,
            mixed_audio_path=str(output_path),
            telemetry={
                "loop_trim_decision": loop_trim_decision,
                "gate_metrics": gate_res.metrics,
                "ducking_config": self.config.to_dict(),
            },
            created_at=utcnow(),
            updated_at=utcnow(),
        )

        return record
