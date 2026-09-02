"""Secure Subprocess-Based FFmpeg Rendering Engine."""

import asyncio
import os
import shutil
from typing import List, Optional
from clipping.rendering.base import MediaRenderer
from clipping.rendering.exceptions import FFmpegExecutionError
from clipping.logging.logger import get_logger

logger = get_logger("clipping.rendering.ffmpeg")


class FFmpegRenderer(MediaRenderer):
    """
    CPU-first FFmpeg execution engine.
    Executes isolated external FFmpeg subprocesses with argument vectors (zero shell injection).
    """

    def __init__(self, ffmpeg_path: Optional[str] = None):
        self.ffmpeg_path = ffmpeg_path or self._discover_ffmpeg_binary()

    def _discover_ffmpeg_binary(self) -> str:
        # 1. Try imageio_ffmpeg
        try:
            import imageio_ffmpeg
            exe = imageio_ffmpeg.get_ffmpeg_exe()
            if exe and os.path.exists(exe):
                return exe
        except Exception:
            pass

        # 2. Try system PATH
        system_exe = shutil.which("ffmpeg")
        if system_exe:
            return system_exe

        return "ffmpeg"

    async def render_clip(
        self,
        source_video_path: str,
        filtergraph: str,
        output_path: str,
        clip_start: float,
        clip_end: float,
    ) -> str:
        if not os.path.isfile(source_video_path):
            raise FileNotFoundError(f"Source video file not found: {source_video_path}")

        duration = clip_end - clip_start
        if duration <= 0:
            raise ValueError(f"Invalid render duration: {duration}s")

        cmd: List[str] = [
            self.ffmpeg_path,
            "-y",
            "-ss", f"{clip_start:.3f}",
            "-to", f"{clip_end:.3f}",
            "-i", source_video_path,
            "-vf", filtergraph,
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "22",
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "48000",
            "-movflags", "+faststart",
            output_path,
        ]

        logger.info(
            "Executing FFmpeg render subprocess",
            clip_start=clip_start,
            clip_end=clip_end,
            output_path=output_path,
        )

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                err_msg = stderr.decode("utf-8", errors="replace") if stderr else "Unknown FFmpeg error"
                logger.error("FFmpeg render failed", returncode=proc.returncode, stderr=err_msg)
                # Clean corrupted partial output if created
                if os.path.isfile(output_path):
                    try:
                        os.remove(output_path)
                    except Exception:
                        pass
                raise FFmpegExecutionError(f"FFmpeg render process failed (exit {proc.returncode}): {err_msg}")

            if not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
                raise FFmpegExecutionError("FFmpeg reported success but output video file is missing or 0 bytes")

            logger.info("FFmpeg render completed successfully", output_path=output_path, size_bytes=os.path.getsize(output_path))
            return output_path

        except Exception as e:
            if not isinstance(e, FFmpegExecutionError):
                raise FFmpegExecutionError(f"Failed to spawn FFmpeg process: {e}") from e
            raise
