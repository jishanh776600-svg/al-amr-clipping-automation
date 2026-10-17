"""100% AI Generated B-Roll Factory (Zero Stock Footage).

Generates photorealistic, hyper-contextual 9:16 vertical visuals using AI image generation,
and animates them into 3D Ken Burns camera push-in video cutaways using FFmpeg.
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

BROLL_CACHE_DIR = Path("/home/ubuntu/al-amr-clipping-automation/data/ai_broll_cache")


class AIBrollAsset:
    def __init__(self, asset_id: str, image_path: Path, video_path: Path, duration_s: float, prompt: str):
        self.asset_id = asset_id
        self.image_path = image_path
        self.video_path = video_path
        self.duration_s = duration_s
        self.prompt = prompt


class AIBrollFactory:
    """Orchestrates 100% AI B-roll image generation and 3D Ken-Burns animation."""

    def __init__(self, cache_dir: Path | None = None):
        self.cache_dir = cache_dir or BROLL_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def generate_broll_clip(
        self,
        prompt: str,
        duration_s: float = 2.5,
        fps: int = 30,
    ) -> AIBrollAsset | None:
        """Generate a 9:16 vertical AI image and convert to a Ken Burns motion video."""
        prompt_clean = prompt.strip()
        h = hashlib.md5(f"{prompt_clean}_{duration_s}".encode()).hexdigest()[:12]
        img_path = self.cache_dir / f"broll_{h}.jpg"
        video_path = self.cache_dir / f"broll_{h}.mp4"

        # 1. Check if motion video already cached
        if video_path.is_file() and video_path.stat().st_size > 10000:
            return AIBrollAsset(h, img_path, video_path, duration_s, prompt_clean)

        # 2. Generate 9:16 Image if not present
        if not img_path.is_file() or img_path.stat().st_size < 5000:
            success = self._fetch_ai_image(prompt_clean, img_path)
            if not success:
                log.warning("Could not generate AI image for prompt: %s", prompt_clean)
                return None

        # 3. Animate static image into 3D Ken-Burns 9:16 video clip via FFmpeg
        try:
            total_frames = int(fps * duration_s)
            # Zoom from 1.0x to 1.14x smoothly over the duration
            zoom_filter = (
                f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
                f"zoompan=z='min(zoom+0.0012,1.14)':d={total_frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920:fps={fps}"
            )
            cmd = [
                "ffmpeg",
                "-y",
                "-loop",
                "1",
                "-i",
                str(img_path),
                "-vf",
                zoom_filter,
                "-t",
                f"{duration_s:.2f}",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-pix_fmt",
                "yuv420p",
                str(video_path),
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True, timeout=30)
            log.info("Rendered AI B-roll motion clip: %s (duration: %.1fs)", video_path.name, duration_s)
            return AIBrollAsset(h, img_path, video_path, duration_s, prompt_clean)
        except Exception as exc:
            log.error("Failed to animate AI B-roll with FFmpeg: %s", exc)
            return None

    def _fetch_ai_image(self, prompt: str, target_path: Path) -> bool:
        """Download high-res 9:16 vertical AI visual using high-speed generation."""
        # Style conditioning for hyper-realistic cinematic look
        styled_prompt = (
            f"cinematic 9:16 vertical, photorealistic, {prompt}, "
            "dramatic lighting, shallow depth of field, 35mm film grain, 8k resolution, documentary realism"
        )
        encoded_prompt = urllib.parse.quote(styled_prompt)
        url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1080&height=1920&model=flux&nologo=true"

        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "AutoClip-Overeditor/1.0"},
            )
            with urllib.request.urlopen(req, timeout=18) as response:
                content = response.read()
                if len(content) > 10000:
                    target_path.write_bytes(content)
                    log.info("Generated 1080x1920 AI image saved to %s", target_path.name)
                    return True
        except Exception as exc:
            log.warning("Pollinations image fetch failed: %s; trying fallback", exc)

        # Fallback to local procedural high-contrast cinematic card if offline
        try:
            from PIL import Image, ImageDraw, ImageFont
            img = Image.new("RGB", (1080, 1920), color=(15, 17, 23))
            draw = ImageDraw.Draw(img)
            draw.rectangle([60, 60, 1020, 1860], outline=(40, 50, 70), width=4)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            img.save(target_path, quality=90)
            return True
        except Exception as fallback_exc:
            log.error("Fallback image generation failed: %s", fallback_exc)
            return False
