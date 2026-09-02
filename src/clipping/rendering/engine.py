"""End-to-End Rendering Orchestration Engine."""

import os
import tempfile
import time
from typing import List, Optional
from clipping.contracts.director import ReframePlan
from clipping.contracts.perception import WordTimestamp
from clipping.contracts.rendering import RenderOutput
from clipping.rendering.base import (
    SubtitleGenerator,
    FiltergraphBuilder,
    MediaRenderer,
)
from clipping.rendering.subtitles import AssSubtitleGenerator
from clipping.rendering.filters import FFmpegFiltergraphBuilder
from clipping.rendering.ffmpeg import FFmpegRenderer
from clipping.rendering.styles import SubtitleStyleConfig
from clipping.storage.base import StorageDriver
from clipping.storage.keys import StorageKeyBuilder
from clipping.logging.logger import get_logger

logger = get_logger("clipping.rendering.engine")


class RenderOrchestrationEngine:
    """
    Orchestrates subtitle generation, virtual camera filtergraphs,
    FFmpeg video encoding, and StorageDriver artifact persistence.
    """

    def __init__(
        self,
        subtitle_generator: Optional[SubtitleGenerator] = None,
        filtergraph_builder: Optional[FiltergraphBuilder] = None,
        media_renderer: Optional[MediaRenderer] = None,
    ):
        self.subtitle_generator = subtitle_generator or AssSubtitleGenerator()
        self.filtergraph_builder = filtergraph_builder or FFmpegFiltergraphBuilder()
        self.media_renderer = media_renderer or FFmpegRenderer()

    async def render(
        self,
        clip_id: str,
        source_video_id: str,
        clip_start: float,
        clip_end: float,
        reframe_plan: ReframePlan,
        words: List[WordTimestamp],
        storage_driver: StorageDriver,
        style: Optional[SubtitleStyleConfig] = None,
        force_recompute: bool = False,
    ) -> RenderOutput:
        final_video_key = f"clips/{clip_id}/final_1080x1920.mp4"
        subtitles_key = f"clips/{clip_id}/subtitles.ass"
        output_meta_key = f"clips/{clip_id}/render_output.json"

        # 1. Idempotency Check
        if not force_recompute and (
            await storage_driver.exists(final_video_key)
            and await storage_driver.exists(output_meta_key)
        ):
            logger.info("Rendered clip already exists in vault, skipping render", clip_id=clip_id)
            meta_bytes = await storage_driver.download_bytes(output_meta_key)
            return RenderOutput.model_validate_json(meta_bytes.decode("utf-8"))

        master_video_key = StorageKeyBuilder.source_master_video(source_video_id)
        if not await storage_driver.exists(master_video_key):
            raise FileNotFoundError(f"Source master video not found in storage: {master_video_key}")

        start_time_wall = time.time()

        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_source_path = os.path.join(tmp_dir, "source_master.mp4")
            temp_ass_path = os.path.join(tmp_dir, "subtitles.ass")
            temp_output_path = os.path.join(tmp_dir, "rendered_output.mp4")

            # 2. Download master video into ephemeral scratch
            logger.info("Downloading master video to ephemeral worker scratch", master_key=master_video_key)
            await storage_driver.download(master_video_key, temp_source_path)

            # 3. Generate ASS Subtitles
            ass_content = self.subtitle_generator.generate_subtitles(
                words=words,
                clip_start=clip_start,
                clip_end=clip_end,
                style=style,
            )
            with open(temp_ass_path, "w", encoding="utf-8") as f:
                f.write(ass_content)

            # Upload ASS script to storage
            await storage_driver.upload_bytes(
                data=ass_content.encode("utf-8"),
                storage_key=subtitles_key,
                content_type="text/x-ssa",
            )

            # 4. Build Filtergraph with Virtual Camera & Subtitles
            filtergraph = self.filtergraph_builder.build_filtergraph(
                reframe_plan=reframe_plan,
                subtitle_ass_path=temp_ass_path,
                target_width=1080,
                target_height=1920,
            )

            # 5. Execute FFmpeg Rendering
            await self.media_renderer.render_clip(
                source_video_path=temp_source_path,
                filtergraph=filtergraph,
                output_path=temp_output_path,
                clip_start=clip_start,
                clip_end=clip_end,
            )

            render_elapsed = time.time() - start_time_wall
            file_size = os.path.getsize(temp_output_path)
            duration_sec = clip_end - clip_start

            # 6. Upload Rendered Video to Canonical Storage Vault
            logger.info("Uploading rendered vertical short to storage vault", destination_key=final_video_key)
            await storage_driver.upload(
                local_path=temp_output_path,
                storage_key=final_video_key,
                content_type="video/mp4",
            )

            # 7. Create and Persist RenderOutput Metadata
            render_output = RenderOutput(
                clip_id=clip_id,
                output_storage_key=final_video_key,
                duration_seconds=round(duration_sec, 3),
                file_size_bytes=file_size,
                render_time_seconds=round(render_elapsed, 3),
            )

            await storage_driver.upload_bytes(
                data=render_output.model_dump_json(indent=2).encode("utf-8"),
                storage_key=output_meta_key,
                content_type="application/json",
            )

            return render_output
