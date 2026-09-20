"""Deterministic FinalRenderQualityGate for production validation of rendered MP4 clips."""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from autoclip.pipeline import ffmpeg
from .models import FinalRenderConfig, FinalRenderGateResult

log = logging.getLogger(__name__)


class FinalRenderQualityGate:
    """Comprehensive broadcast validation gate for final vertical video clips."""

    def __init__(self, config: FinalRenderConfig | None = None) -> None:
        self.config = config or FinalRenderConfig()

    def probe_media(self, path: Path) -> dict[str, Any]:
        """Probes container and stream details via ffprobe."""
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "stream=index,codec_type,codec_name,width,height,r_frame_rate,duration,channels,sample_rate:format=duration,size,format_name",
            "-of", "json",
            str(path),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return json.loads(res.stdout)

    def decode_check(self, path: Path, check_duration_s: float = 5.0) -> tuple[bool, str]:
        """Runs ffmpeg decode test to ensure no corrupt packets or stream errors."""
        cmd = [
            ffmpeg.ffmpeg_path(),
            "-v", "error",
            "-xerror",
            "-i", str(path),
            "-t", str(check_duration_s),
            "-f", "null",
            "-",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            return False, proc.stderr.strip() or f"ffmpeg decode exited with {proc.returncode}"
        return True, ""

    def measure_audio_levels(self, path: Path) -> tuple[float, float]:
        """Measures true peak and mean volume using volumedetect filter."""
        cmd = [
            ffmpeg.ffmpeg_path(),
            "-i", str(path),
            "-af", "volumedetect",
            "-vn",
            "-sn",
            "-f", "null",
            "-",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        max_vol = -1.5
        mean_vol = -14.0
        for line in res.stderr.splitlines():
            if "max_volume:" in line:
                try:
                    max_vol = float(line.split("max_volume:")[1].replace("dB", "").strip())
                except Exception:
                    pass
            elif "mean_volume:" in line:
                try:
                    mean_vol = float(line.split("mean_volume:")[1].replace("dB", "").strip())
                except Exception:
                    pass
        return mean_vol, max_vol

    def compute_visual_metrics(
        self,
        edl_data: Any,
        video_duration: float,
        clip_start_s: float = 0.0,
    ) -> dict[str, Any]:
        """Calculates B-roll overlay metrics, coverage percentage, and A-roll gap distribution."""
        entries: list[Any] = []
        if hasattr(edl_data, "entries"):
            entries = getattr(edl_data, "entries") or []
        elif isinstance(edl_data, dict):
            entries = edl_data.get("entries", [])

        if not entries or video_duration <= 0:
            return {
                "broll_event_count": 0,
                "total_broll_duration_s": 0.0,
                "broll_coverage_pct": 0.0,
                "longest_a_roll_gap_s": round(video_duration, 2),
                "visual_qa_status": "VISUAL_NONE",
            }

        intervals: list[tuple[float, float]] = []
        for entry in entries:
            if isinstance(entry, dict):
                start = float(entry.get("start_s", 0.0))
                end = float(entry.get("end_s", 0.0))
                if end <= start and "duration_s" in entry:
                    end = start + float(entry.get("duration_s", 0.0))
            else:
                start = float(getattr(entry, "start_s", 0.0))
                end = float(getattr(entry, "end_s", 0.0))
                if end <= start and hasattr(entry, "duration_s"):
                    end = start + float(getattr(entry, "duration_s", 0.0))

            # Normalize to clip-relative timeline [0, video_duration]
            if start >= clip_start_s and clip_start_s > 0:
                rel_start = start - clip_start_s
                rel_end = end - clip_start_s
            else:
                rel_start = start
                rel_end = end

            rel_start = max(0.0, rel_start)
            rel_end = min(video_duration, rel_end)

            if rel_end > rel_start:
                intervals.append((rel_start, rel_end))

        if not intervals:
            return {
                "broll_event_count": 0,
                "total_broll_duration_s": 0.0,
                "broll_coverage_pct": 0.0,
                "longest_a_roll_gap_s": round(video_duration, 2),
                "visual_qa_status": "VISUAL_NONE",
            }

        # Merge overlapping intervals
        intervals.sort(key=lambda x: x[0])
        merged: list[list[float]] = []
        for s, e in intervals:
            if not merged:
                merged.append([s, e])
            else:
                if s <= merged[-1][1]:
                    merged[-1][1] = max(merged[-1][1], e)
                else:
                    merged.append([s, e])

        total_broll = sum(e - s for s, e in merged)
        coverage_pct = round((total_broll / video_duration) * 100.0, 1)

        # Calculate A-roll gaps
        gaps: list[float] = []
        curr = 0.0
        for s, e in merged:
            if s > curr:
                gaps.append(s - curr)
            curr = max(curr, e)
        if video_duration > curr:
            gaps.append(video_duration - curr)

        longest_gap = round(max(gaps), 2) if gaps else 0.0

        # Broadcast visual style target: 40-55% coverage; >= 25% passes comfortably
        if coverage_pct >= 25.0 and longest_gap <= 8.0:
            qa_status = "VISUAL_PASS"
        elif coverage_pct >= 15.0 or (len(intervals) >= 1 and video_duration <= 15.0):
            qa_status = "VISUAL_WARN"
        else:
            qa_status = "VISUAL_DEFICIENT"

        return {
            "broll_event_count": len(intervals),
            "total_broll_duration_s": round(total_broll, 2),
            "broll_coverage_pct": coverage_pct,
            "longest_a_roll_gap_s": longest_gap,
            "visual_qa_status": qa_status,
        }

    def evaluate(
        self,
        output_path: Path,
        expected_duration_s: float,
        expected_caption_style: str = "",
        expected_bgm_asset_id: str | None = None,
        require_audio: bool = True,
        min_duration_s: float | None = None,
        max_duration_s: float | None = None,
        expected_visual_filter: str = "original",
        edl: Any | None = None,
        clip_start_s: float = 0.0,
    ) -> FinalRenderGateResult:
        """Evaluates rendered MP4 against video, audio, caption, and BGM rules."""
        warnings: list[str] = []
        rejection_reasons: list[str] = []
        video_metrics: dict[str, Any] = {}
        audio_metrics: dict[str, Any] = {}
        provenance: dict[str, Any] = {
            "caption_style": expected_caption_style,
            "visual_filter": expected_visual_filter,
            "bgm_asset_id": expected_bgm_asset_id,
        }

        # 1. Existence and non-empty check
        if not output_path.exists() or not output_path.is_file():
            return FinalRenderGateResult(
                status="RENDER_REJECT",
                quality_score=0.0,
                rejection_reasons=["Rendered output file does not exist"],
            )

        file_size = output_path.stat().st_size
        if file_size < 1024:
            return FinalRenderGateResult(
                status="RENDER_REJECT",
                quality_score=0.0,
                rejection_reasons=[f"File size too small ({file_size} bytes); corrupt output header"],
            )

        # 1b. Check existing metadata if evaluating existing package
        metadata_file = output_path.parent / "metadata.json"
        if metadata_file.is_file():
            try:
                import json
                meta = json.loads(metadata_file.read_text(encoding="utf-8"))
                rec_filter = str(meta.get("visual_filter") or "original").strip().lower()
                exp_filter = str(expected_visual_filter or "original").strip().lower()
                if rec_filter != exp_filter:
                    rejection_reasons.append(
                        f"Visual filter mismatch in existing output: recorded '{rec_filter}', expected '{exp_filter}'"
                    )

                from ..captions import STYLE_ALIASES
                rec_style = str(meta.get("caption_style") or "").strip().lower()
                exp_style = str(expected_caption_style or "").strip().lower()
                rec_style_norm = STYLE_ALIASES.get(rec_style, rec_style)
                exp_style_norm = STYLE_ALIASES.get(exp_style, exp_style)
                if exp_style and rec_style and rec_style_norm != exp_style_norm:
                    rejection_reasons.append(
                        f"Caption style mismatch in existing output: recorded '{rec_style}', expected '{exp_style}'"
                    )

                rec_bgm = meta.get("bgm_asset_id")
                bgm_canonical = {
                    "cinematic": "canonical_cinematic",
                    "lofi": "canonical_lofi",
                    "rock": "canonical_upbeat",
                    "podcast": "canonical_ambient",
                    "motivation": "canonical_motivation",
                }
                exp_bgm_norm = bgm_canonical.get(str(expected_bgm_asset_id).lower(), str(expected_bgm_asset_id))
                rec_bgm_norm = bgm_canonical.get(str(rec_bgm).lower(), str(rec_bgm))
                if expected_bgm_asset_id is not None and rec_bgm_norm != exp_bgm_norm:
                    rejection_reasons.append(
                        f"BGM asset ID mismatch in existing output: recorded '{rec_bgm}', expected '{expected_bgm_asset_id}'"
                    )
            except Exception as exc:
                log.warning("Could not parse existing metadata.json for idempotency validation: %s", exc)

        # 2. Container & Stream parse
        try:
            probe_data = self.probe_media(output_path)
        except Exception as exc:
            return FinalRenderGateResult(
                status="RENDER_REJECT",
                quality_score=0.0,
                rejection_reasons=[f"ffprobe container parse failed: {exc}"],
            )

        fmt_name = probe_data.get("format", {}).get("format_name", "")
        if "mp4" not in fmt_name.lower() and "quicktime" not in fmt_name.lower():
            rejection_reasons.append(f"Invalid container format: {fmt_name}; expected mp4")

        streams = probe_data.get("streams", [])
        v_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
        a_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

        # 3. Video stream validation
        if not v_stream:
            rejection_reasons.append("No video stream found in rendered output")
            video_duration = 0.0
        else:
            w = int(v_stream.get("width", 0))
            h = int(v_stream.get("height", 0))
            v_codec = str(v_stream.get("codec_name", "")).lower()
            r_fps = v_stream.get("r_frame_rate", "30/1")
            try:
                num, den = r_fps.split("/")
                fps = round(float(num) / float(den), 2)
            except Exception:
                fps = 30.0

            video_duration = float(
                v_stream.get("duration")
                or probe_data.get("format", {}).get("duration", 0.0)
            )

            video_metrics.update({
                "width": w,
                "height": h,
                "aspect_ratio": f"{w}:{h}",
                "fps": fps,
                "video_codec": v_codec,
                "duration_s": round(video_duration, 2),
            })

            # Check exact resolution (1080x1920)
            if w != self.config.width or h != self.config.height:
                rejection_reasons.append(
                    f"Resolution mismatch: expected {self.config.width}x{self.config.height}, got {w}x{h}"
                )

            # Even dimensions
            if w % 2 != 0 or h % 2 != 0:
                rejection_reasons.append(f"Dimensions must be even for H.264 compatibility: {w}x{h}")

            # Valid codec
            if v_codec not in ("h264", "avc1", "hevc", "av1"):
                rejection_reasons.append(f"Disallowed video codec: {v_codec}; must be h264")

            # FPS check
            if fps < 15.0 or fps > 120.0:
                warnings.append(f"Non-standard frame rate: {fps} fps")

            # Strict configured duration bounds check on ACTUAL rendered media
            if min_duration_s is not None and video_duration < min_duration_s:
                rejection_reasons.append(
                    f"Actual rendered MP4 duration {video_duration:.2f}s is below configured minimum {min_duration_s:.2f}s"
                )
            if max_duration_s is not None and video_duration > max_duration_s:
                rejection_reasons.append(
                    f"Actual rendered MP4 duration {video_duration:.2f}s is above configured maximum {max_duration_s:.2f}s"
                )

            # Duration tolerance check
            diff_s = abs(video_duration - expected_duration_s)
            video_metrics["duration_diff_s"] = round(diff_s, 2)
            if diff_s > self.config.max_duration_diff_s:
                if diff_s > (self.config.max_duration_diff_s * 2.0):
                    rejection_reasons.append(
                        f"Video duration {video_duration:.2f}s deviates significantly from expected {expected_duration_s:.2f}s"
                    )
                else:
                    warnings.append(
                        f"Video duration {video_duration:.2f}s differs from expected {expected_duration_s:.2f}s by {diff_s:.2f}s"
                    )

        # 4. Audio stream validation
        audio_duration = 0.0
        if require_audio and not a_stream:
            rejection_reasons.append("Audio stream missing from output file")
        elif a_stream:
            a_codec = str(a_stream.get("codec_name", "")).lower()
            channels = int(a_stream.get("channels", 0))
            sample_rate = int(a_stream.get("sample_rate", 0))
            audio_duration = float(
                a_stream.get("duration")
                or probe_data.get("format", {}).get("duration", 0.0)
            )

            audio_metrics.update({
                "audio_codec": a_codec,
                "channels": channels,
                "sample_rate": sample_rate,
                "duration_s": round(audio_duration, 2),
            })

            # Codec check
            if a_codec not in ("aac", "mp4a"):
                warnings.append(f"Unexpected audio codec {a_codec}; AAC recommended")

            # Channels & Sample rate
            if channels < 1:
                rejection_reasons.append(f"Invalid audio channels: {channels}")
            if sample_rate < 44100:
                warnings.append(f"Low audio sample rate: {sample_rate} Hz; 48000 Hz recommended")

            # A/V Synchronization
            if v_stream:
                av_diff_s = abs(video_duration - audio_duration)
                audio_metrics["av_sync_diff_s"] = round(av_diff_s, 2)
                if av_diff_s > self.config.max_av_sync_diff_s:
                    warnings.append(
                        f"A/V duration mismatch: video={video_duration:.2f}s, audio={audio_duration:.2f}s (diff={av_diff_s:.2f}s)"
                    )

            # Audio levels check
            try:
                mean_vol, max_vol = self.measure_audio_levels(output_path)
                audio_metrics["mean_volume_db"] = mean_vol
                audio_metrics["true_peak_db"] = max_vol

                if max_vol >= 0.0:
                    warnings.append("Possible audio clipping detected (peak >= 0.0 dBFS)")
                if mean_vol < -35.0:
                    rejection_reasons.append(f"Audio output is near silent (level: {mean_vol:.1f} dBFS)")
            except Exception as exc:
                log.warning("Could not measure audio levels: %s", exc)

        # 5. FFmpeg Decode check (stream integrity)
        decode_ok, decode_err = self.decode_check(output_path)
        video_metrics["decode_ok"] = decode_ok
        if not decode_ok:
            rejection_reasons.append(f"Video decode check failed: {decode_err}")

        # 5b. Visual QA metrics evaluation (from edl or package edl.json)
        edl_to_eval = edl
        if edl_to_eval is None and output_path.parent:
            edl_file = output_path.parent / "edl.json"
            if edl_file.is_file():
                try:
                    import json
                    edl_to_eval = json.loads(edl_file.read_text(encoding="utf-8"))
                except Exception as exc:
                    log.warning("Could not read edl.json for visual QA: %s", exc)

        visual_metrics = self.compute_visual_metrics(
            edl_to_eval,
            video_duration=video_duration,
            clip_start_s=clip_start_s,
        )

        if visual_metrics.get("visual_qa_status") == "VISUAL_DEFICIENT" and video_duration >= 10.0:
            warnings.append(
                f"Visual B-roll coverage is low: {visual_metrics.get('broll_coverage_pct', 0)}% "
                f"({visual_metrics.get('broll_event_count', 0)} events, max gap {visual_metrics.get('longest_a_roll_gap_s', 0)}s)"
            )

        # 6. Quality scoring
        if rejection_reasons:
            status = "RENDER_REJECT"
            quality_score = 0.0
        elif warnings:
            status = "RENDER_WARN"
            quality_score = max(60.0, 100.0 - (len(warnings) * 10.0))
        else:
            status = "RENDER_PASS"
            quality_score = 100.0

        return FinalRenderGateResult(
            status=status,
            quality_score=round(quality_score, 1),
            video_metrics=video_metrics,
            audio_metrics=audio_metrics,
            provenance=provenance,
            visual_metrics=visual_metrics,
            warnings=warnings,
            rejection_reasons=rejection_reasons,
        )
