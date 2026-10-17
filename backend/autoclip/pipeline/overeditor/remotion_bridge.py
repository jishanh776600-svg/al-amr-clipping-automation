"""Remotion 60FPS Video Engine Bridge for Content Rewards Overediting."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

REMOTION_PROJECT_DIR = Path("/home/ubuntu/al-amr-clipping-automation/remotion_engine")
SFX_DIR = Path("/home/ubuntu/al-amr-clipping-automation/assets/sfx")


class RemotionBridge:
    def __init__(self, project_dir: Path | None = None) -> None:
        self.project_dir = project_dir or REMOTION_PROJECT_DIR
        self.sfx_dir = SFX_DIR

    def is_available(self) -> bool:
        """Check if Remotion engine is configured and CLI is callable."""
        return (self.project_dir / "package.json").is_file() and (self.project_dir / "src" / "index.ts").is_file()

    def render_content_rewards_short(
        self,
        base_video_path: Path,
        headline_text: str,
        handle: str,
        output_path: Path,
        duration_s: float = 30.0,
        broll_path: Path | None = None,
        broll_start_s: float = 8.0,
        broll_duration_s: float = 2.5,
        fps: int = 30,
    ) -> Path | None:
        """Render modern Content Rewards 1080x1920 short with spring physics & transitions via Remotion."""
        if not self.is_available():
            log.warning("Remotion engine not available at %s", self.project_dir)
            return None

        total_frames = max(30, int(duration_s * fps))
        broll_start_frame = int(broll_start_s * fps)
        broll_frames = int(broll_duration_s * fps)

        props: dict[str, Any] = {
            "videoSrc": str(base_video_path.resolve()),
            "headlineText": headline_text.strip(),
            "handle": handle.strip() if handle else "@ALPHA_FUTURES",
            "brollSrc": str(broll_path.resolve()) if (broll_path and broll_path.is_file()) else "",
            "brollStartFrame": broll_start_frame,
            "brollDurationFrames": broll_frames,
            "whooshSfxSrc": str(self.sfx_dir / "whoosh_cinematic.wav") if (self.sfx_dir / "whoosh_cinematic.wav").is_file() else "",
            "popSfxSrc": str(self.sfx_dir / "pop_punch.wav") if (self.sfx_dir / "pop_punch.wav").is_file() else "",
        }

        # Write props to temporary json file for safe CLI consumption
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(props, f)
            props_file = Path(f.name)

        try:
            cmd = [
                "npx",
                "remotion",
                "render",
                "src/index.ts",
                "ContentRewardsShort",
                str(output_path.resolve()),
                f"--props={props_file}",
                f"--frames=0-{total_frames - 1}",
                "--concurrency=2",
                "--log=warn",
            ]

            log.info("Executing Remotion render: %s", " ".join(cmd))
            res = subprocess.run(
                cmd,
                cwd=str(self.project_dir),
                capture_output=True,
                text=True,
                timeout=120,
            )

            if res.returncode != 0:
                log.warning("Remotion render failed (%d): %s | %s", res.returncode, res.stdout, res.stderr)
                return None

            if output_path.is_file() and output_path.stat().st_size > 10000:
                log.info("Remotion successfully produced high-fidelity short: %s (%d bytes)", output_path, output_path.stat().st_size)
                return output_path

            return None
        except Exception as exc:
            log.warning("Remotion execution encountered exception: %s", exc)
            return None
        finally:
            props_file.unlink(missing_ok=True)
