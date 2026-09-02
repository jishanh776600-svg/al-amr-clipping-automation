"""Integration tests for RenderOrchestrationEngine."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from clipping.contracts.director import ReframePlan, ReframeCropKeyframe
from clipping.contracts.perception import WordTimestamp
from clipping.contracts.rendering import RenderOutput
from clipping.rendering.engine import RenderOrchestrationEngine
from clipping.rendering.subtitles import AssSubtitleGenerator
from clipping.rendering.filters import FFmpegFiltergraphBuilder
from clipping.rendering.exceptions import FFmpegExecutionError
from clipping.storage.local import LocalStorageDriver


@pytest.mark.asyncio
async def test_render_orchestration_lifecycle(temp_vault_dir):
    storage = LocalStorageDriver(root_dir=temp_vault_dir)

    # 1. Seed master video in storage vault
    master_key = "sources/VID_RENDER_01/master.mp4"
    await storage.upload_bytes(b"MOCK_MASTER_MP4_BYTES", master_key, content_type="video/mp4")

    # 2. Mock Media Renderer
    mock_renderer = MagicMock()

    async def fake_render(source_video_path, filtergraph, output_path, clip_start, clip_end):
        with open(output_path, "wb") as f:
            f.write(b"MOCK_RENDERED_1080X1920_SHORT_VIDEO_BYTES")
        return output_path

    mock_renderer.render_clip = AsyncMock(side_effect=fake_render)

    engine = RenderOrchestrationEngine(
        subtitle_generator=AssSubtitleGenerator(),
        filtergraph_builder=FFmpegFiltergraphBuilder(),
        media_renderer=mock_renderer,
    )

    reframe_plan = ReframePlan(
        clip_id="CLIP_RENDER_01",
        source_width=1920,
        source_height=1080,
        target_width=1080,
        target_height=1920,
        keyframes=[
            ReframeCropKeyframe(timestamp=0.0, crop_x=656, crop_y=0, crop_w=608, crop_h=1080)
        ],
    )

    words = [
        WordTimestamp(word="AI", start=0.1, end=0.5, probability=0.99),
        WordTimestamp(word="Video", start=0.6, end=1.0, probability=0.98),
        WordTimestamp(word="Clipping", start=1.1, end=1.8, probability=0.97),
    ]

    # 3. Execute render
    output = await engine.render(
        clip_id="CLIP_RENDER_01",
        source_video_id="VID_RENDER_01",
        clip_start=0.0,
        clip_end=2.0,
        reframe_plan=reframe_plan,
        words=words,
        storage_driver=storage,
    )

    # 4. Verify returned RenderOutput
    assert isinstance(output, RenderOutput)
    assert output.clip_id == "CLIP_RENDER_01"
    assert output.duration_seconds == 2.0
    assert output.output_storage_key == "clips/CLIP_RENDER_01/final_1080x1920.mp4"
    assert output.file_size_bytes > 0

    # 5. Verify artifacts in StorageDriver vault
    assert await storage.exists("clips/CLIP_RENDER_01/subtitles.ass") is True
    assert await storage.exists("clips/CLIP_RENDER_01/final_1080x1920.mp4") is True
    assert await storage.exists("clips/CLIP_RENDER_01/render_output.json") is True

    # 6. Idempotency Check: Re-running skips rendering
    mock_renderer.render_clip = AsyncMock(side_effect=RuntimeError("Should not render on cached run!"))
    cached_output = await engine.render(
        clip_id="CLIP_RENDER_01",
        source_video_id="VID_RENDER_01",
        clip_start=0.0,
        clip_end=2.0,
        reframe_plan=reframe_plan,
        words=words,
        storage_driver=storage,
        force_recompute=False,
    )
    assert cached_output.clip_id == "CLIP_RENDER_01"
    assert cached_output.duration_seconds == 2.0


@pytest.mark.asyncio
async def test_render_failure_handling(temp_vault_dir):
    storage = LocalStorageDriver(root_dir=temp_vault_dir)
    master_key = "sources/VID_FAIL/master.mp4"
    await storage.upload_bytes(b"MOCK_MASTER_MP4_BYTES", master_key, content_type="video/mp4")

    mock_failing_renderer = MagicMock()
    mock_failing_renderer.render_clip = AsyncMock(side_effect=FFmpegExecutionError("FFmpeg encoding failed"))

    engine = RenderOrchestrationEngine(media_renderer=mock_failing_renderer)

    reframe_plan = ReframePlan(
        clip_id="CLIP_FAIL",
        source_width=1920,
        source_height=1080,
        target_width=1080,
        target_height=1920,
        keyframes=[ReframeCropKeyframe(timestamp=0.0, crop_x=656, crop_y=0, crop_w=608, crop_h=1080)],
    )

    with pytest.raises(FFmpegExecutionError):
        await engine.render(
            clip_id="CLIP_FAIL",
            source_video_id="VID_FAIL",
            clip_start=0.0,
            clip_end=3.0,
            reframe_plan=reframe_plan,
            words=[],
            storage_driver=storage,
        )

    # Ensure no false-success output was persisted
    assert await storage.exists("clips/CLIP_FAIL/final_1080x1920.mp4") is False
    assert await storage.exists("clips/CLIP_FAIL/render_output.json") is False
