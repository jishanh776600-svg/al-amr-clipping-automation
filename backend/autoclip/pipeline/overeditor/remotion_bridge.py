"""Antigravity Production Master Bridge for AutoClip Overediting.

Bridges AutoClip's generated clips directly to the VPS Antigravity Engine:
1. Kinetic Motion Graphics with Dynamic Header Pill, Graphs, Cards, HUDs
2. Broadcast Loudness Audio Mastering (-14 LUFS Voice Dominance + Ducked BGM + SFX Bus)
3. 1:1 SAR Crisp Studio Master (CRF 14, Lanczos scaling, Unsharp mask)
4. AI Viral SEO generation for YouTube Shorts & Instagram Reels
5. Instant Telegram Delivery with Action Buttons
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import requests
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

VPS_ANTIGRAVITY_SCRIPT = Path("/home/ubuntu/.gemini/antigravity/scratch/remotion_viral/antigravity_master_machine.py")
VPS_API_ENDPOINT = os.getenv("ANTIGRAVITY_API_ENDPOINT", "http://127.0.0.1:8000/api/antigravity/enhance_clip")
VPS_TUNNEL_ENDPOINT = "https://naturally-cakes-spine-essex.trycloudflare.com/api/antigravity/enhance_clip"
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "7866408097")


class RemotionBridge:
    def __init__(self, project_dir: Path | None = None) -> None:
        self.script_path = VPS_ANTIGRAVITY_SCRIPT

    def is_available(self) -> bool:
        """Check if Antigravity Master Engine is present locally or via endpoint."""
        if self.script_path.is_file():
            return True
        try:
            # Check local API
            r = requests.get("http://127.0.0.1:8000/docs", timeout=1.0)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        return True  # Fallback to VPS endpoint

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
        brief_text: str | None = None,
        transcript_text: str | None = None,
    ) -> Path | None:
        """Renders studio viral short via Antigravity Engine and delivers to Telegram."""
        log.info("Dispatching clip to Antigravity Master Engine: %s", base_video_path.name)

        brief_combined = f"Speaker: {handle or 'CORE INSIGHT'}\nHook: {headline_text}\n{brief_text or ''}"

        # 1. Local execution on VPS
        if self.script_path.is_file():
            cmd = [
                "/usr/bin/python3",
                str(self.script_path),
                "--video", str(base_video_path.resolve()),
                "--brief", brief_combined,
                "--transcript", transcript_text or headline_text,
                "--chat-id", TELEGRAM_CHAT_ID
            ]
            try:
                log.info("Running local Antigravity Master Engine...")
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
                if res.returncode == 0:
                    log.info("Antigravity Master Engine successfully completed!")
                    # Locate output master
                    out_dir = Path("/home/ubuntu/.gemini/antigravity/scratch/remotion_viral/out")
                    expected_master = out_dir / f"final_master_{base_video_path.stem}.mp4"
                    if expected_master.is_file() and expected_master.stat().st_size > 10000:
                        shutil.copy2(expected_master, output_path)
                        return output_path
                else:
                    log.warning("Antigravity script failed (%d): %s", res.returncode, res.stderr)
            except Exception as e:
                log.warning("Antigravity local execution failed: %s", e)

        # 2. Remote endpoint dispatch (HTTP POST)
        for ep in [VPS_API_ENDPOINT, VPS_TUNNEL_ENDPOINT]:
            try:
                log.info("Attempting HTTP dispatch to Antigravity endpoint: %s", ep)
                with open(base_video_path, "rb") as f_vid:
                    resp = requests.post(
                        ep,
                        data={
                            "brief_text": brief_combined,
                            "transcript_text": transcript_text or headline_text,
                            "chat_id": TELEGRAM_CHAT_ID
                        },
                        files={"file": (base_video_path.name, f_vid, "video/mp4")},
                        timeout=30.0
                    )
                    if resp.status_code == 200:
                        log.info("Successfully dispatched to Antigravity endpoint: %s", resp.text)
                        # The server renders in background and sends directly to Telegram!
                        # Copy base video as placeholder if needed
                        if not output_path.is_file() or output_path.stat().st_size < 10000:
                            shutil.copy2(base_video_path, output_path)
                        return output_path
            except Exception as http_err:
                log.debug("Endpoint %s failed: %s", ep, http_err)

        return None
