"""SFX Engine for viral streamer and retention audio enhancement."""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import Any

from autoclip.pipeline.transcript import Word
from autoclip.pipeline.reframe.croppath import CropPath
from .models import SFXEvent

log = logging.getLogger(__name__)

SFX_DIR = Path(__file__).resolve().parent.parent.parent / "assets" / "sfx"

# Semantic keywords for contextual sound cues
SUCCESS_REGEX = re.compile(
    r"^(\$?\d[\d,\.]*[%kKmMbB]?|\$\w+|money|revenue|profit|dollars?|million|billion|thousand|sales|growth|easy|win|winner|won|golden|gold|champion|hero|goat)$",
    re.IGNORECASE,
)
SHOCK_REGEX = re.compile(
    r"^(crazy|insane|impossible|shocking|died|dead|killed|shut|danger|banned|worst|fail|failed|failure|lawsuit|destroy|bankrupt|definitely|staring|eyes)$",
    re.IGNORECASE,
)


class SFXEngine:
    """Detects and renders synchronized sound effects for viral short-form clips."""

    def __init__(self, sfx_dir: Path | None = None, use_extracted_ref: bool = False) -> None:
        self.sfx_dir = sfx_dir or SFX_DIR
        self.use_extracted_ref = use_extracted_ref

    def get_asset_path(self, sound_type: str) -> Path | None:
        candidates = []
        if self.use_extracted_ref:
            ref_dir = self.sfx_dir / "extracted_ref"
            ref_map = {
                "boom": "ref_vine_boom.wav",
                "whoosh": "ref_whoosh_cut.wav",
                "pop": "ref_impact_thud.wav",
                "riser": "ref_riser_whoosh.wav",
                "swoosh": "ref_swoosh.wav",
            }
            if sound_type in ref_map:
                candidates.append(ref_dir / ref_map[sound_type])

        candidates.extend([
            self.sfx_dir / f"{sound_type}.wav",
            self.sfx_dir / f"{sound_type}.mp3",
        ])
        for c in candidates:
            if c.is_file():
                return c
        return None

    def plan_sfx_events(
        self,
        words: list[Word],
        crop_path: CropPath | None = None,
        duration_s: float = 30.0,
        hook_window: tuple[float, float] | None = None,
        climax_window: tuple[float, float] | None = None,
        max_sfx_per_clip: int = 6,
    ) -> list[SFXEvent]:
        """Detects high-impact visual and spoken moments and schedules appropriate SFX."""
        events: list[SFXEvent] = []

        # 1. Visual cuts & transitions -> whoosh
        if crop_path and len(crop_path.segments) > 1:
            for seg in crop_path.segments[1:]:
                if 0.5 <= seg.start_s <= (duration_s - 0.5):
                    # Avoid overlapping with another SFX within 0.8s
                    if not any(abs(e.timestamp_s - seg.start_s) < 0.8 for e in events):
                        events.append(
                            SFXEvent(
                                sound_type="whoosh",
                                timestamp_s=seg.start_s,
                                volume=0.75,
                                reason="camera_cut_transition",
                                asset_path=self.get_asset_path("whoosh"),
                            )
                        )

        # 2. Hook Delivery -> pop
        hook_t = 0.5
        if hook_window:
            hook_t = max(0.3, hook_window[0])
        elif words and len(words) > 0:
            hook_t = max(0.3, words[0].start)

        if not any(abs(e.timestamp_s - hook_t) < 0.8 for e in events):
            events.append(
                SFXEvent(
                    sound_type="pop",
                    timestamp_s=hook_t,
                    volume=0.85,
                    reason="hook_delivery_cue",
                    asset_path=self.get_asset_path("pop"),
                )
            )

        # 3. Climax & Tension -> riser + vine boom
        if climax_window:
            c_start = climax_window[0]
            if c_start > 1.5:
                # Add riser 1.2s before climax
                riser_t = max(0.2, c_start - 1.2)
                if not any(abs(e.timestamp_s - riser_t) < 0.8 for e in events):
                    events.append(
                        SFXEvent(
                            sound_type="riser",
                            timestamp_s=riser_t,
                            volume=0.70,
                            reason="climax_tension_riser",
                            asset_path=self.get_asset_path("riser"),
                        )
                    )

            if not any(abs(e.timestamp_s - c_start) < 0.8 for e in events):
                events.append(
                    SFXEvent(
                        sound_type="boom",
                        timestamp_s=c_start,
                        volume=0.95,
                        reason="climax_impact_boom",
                        asset_path=self.get_asset_path("boom"),
                    )
                )

        # 4. Spoken keyword cues -> ding / boom
        for w in words:
            if len(events) >= max_sfx_per_clip:
                break
            w_time = w.start
            clean = re.sub(r"[^\w\$%]", "", w.text.strip())
            if not clean:
                continue

            if any(abs(e.timestamp_s - w_time) < 1.0 for e in events):
                continue

            if SUCCESS_REGEX.search(clean):
                events.append(
                    SFXEvent(
                        sound_type="ding",
                        timestamp_s=w_time,
                        volume=0.80,
                        reason=f"success_word({clean})",
                        asset_path=self.get_asset_path("ding"),
                    )
                )
            elif SHOCK_REGEX.search(clean):
                events.append(
                    SFXEvent(
                        sound_type="boom",
                        timestamp_s=w_time,
                        volume=0.90,
                        reason=f"shock_word({clean})",
                        asset_path=self.get_asset_path("boom"),
                    )
                )

        # Sort chronologically and filter to existing assets
        valid_events = [e for e in sorted(events, key=lambda x: x.timestamp_s) if e.asset_path and e.asset_path.is_file()]
        return valid_events[:max_sfx_per_clip]

    def render_sfx_track(
        self,
        events: list[SFXEvent],
        duration_s: float,
        output_path: Path,
    ) -> Path:
        """Renders an audio track containing all scheduled SFX mixed at their exact timestamps."""
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if not events:
            # Generate silent audio track
            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", "anullsrc=r=48000:cl=stereo",
                "-t", f"{duration_s:.3f}",
                "-c:a", "aac",
                "-b:a", "192k",
                str(output_path),
            ]
            subprocess.run(cmd, capture_output=True, check=True)
            return output_path

        cmd = ["ffmpeg", "-y"]
        filter_inputs = []

        for idx, ev in enumerate(events):
            cmd.extend(["-i", str(ev.asset_path)])
            delay_ms = max(0, int(ev.timestamp_s * 1000))
            filter_inputs.append(
                f"[{idx}:a]volume={ev.volume:.2f},adelay={delay_ms}|{delay_ms},apad=whole_dur={duration_s:.3f}[a{idx}]"
            )

        in_labels = "".join(f"[a{i}]" for i in range(len(events)))
        filter_complex = (
            f"{';'.join(filter_inputs)};"
            f"{in_labels}amix=inputs={len(events)}:duration=first:dropout_transition=0:normalize=0,"
            f"atrim=0:{duration_s:.3f},asetpts=PTS-STARTPTS[out_sfx]"
        )

        cmd.extend([
            "-filter_complex", filter_complex,
            "-map", "[out_sfx]",
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "48000",
            "-t", f"{duration_s:.3f}",
            str(output_path),
        ])

        subprocess.run(cmd, capture_output=True, text=True, check=True)
        return output_path
