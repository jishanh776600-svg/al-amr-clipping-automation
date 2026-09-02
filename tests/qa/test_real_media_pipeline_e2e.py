"""End-to-end real media rendering and QA validation test."""

import os
import tempfile
import cv2
import numpy as np
import pytest
from clipping.contracts.director import ReframePlan, ReframeCropKeyframe
from clipping.contracts.perception import WordTimestamp, SpeakerAttributedTranscript
from clipping.contracts.qa import QACheckStatus
from clipping.rendering.engine import RenderOrchestrationEngine
from clipping.rendering.subtitles import AssSubtitleGenerator
from clipping.rendering.filters import FFmpegFiltergraphBuilder
from clipping.rendering.ffmpeg import FFmpegRenderer
from clipping.qa.engine import QAEngine
from clipping.storage.local import LocalStorageDriver


def create_synthetic_test_video(output_path: str, width: int = 1920, height: int = 1080, duration_sec: int = 3, fps: int = 30):
    """Creates a real playable MP4 video file using OpenCV VideoWriter."""
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    out = cv2.VideoWriter(output_path, fourcc, fps, (width, height))

    num_frames = int(duration_sec * fps)
    for f in range(num_frames):
        # Create a frame with a colored rectangle in the center representing a speaker
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        frame[:] = (30, 30, 30)  # Dark background

        # Draw simulated face at center (x=960, y=400)
        cv2.circle(frame, (960, 400), 120, (180, 150, 100), -1)
        cv2.putText(frame, f"Frame {f}", (100, 100), cv2.FONT_HERSHEY_SIMPLEX, 2.0, (255, 255, 255), 3)

        out.write(frame)

    out.release()


@pytest.mark.asyncio
async def test_full_pipeline_real_media_render_and_qa(temp_vault_dir):
    storage = LocalStorageDriver(root_dir=temp_vault_dir)

    source_video_id = "REAL_MEDIA_01"
    clip_id = "REAL_CLIP_01"

    # 1. Create real test master video file
    with tempfile.TemporaryDirectory() as tmp_dir:
        master_mp4_path = os.path.join(tmp_dir, "master_source.mp4")
        create_synthetic_test_video(master_mp4_path, width=1920, height=1080, duration_sec=3, fps=30)
        assert os.path.isfile(master_mp4_path)
        assert os.path.getsize(master_mp4_path) > 0

        # Upload master video to canonical storage
        master_key = f"sources/{source_video_id}/master.mp4"
        await storage.upload(master_mp4_path, master_key, content_type="video/mp4")

    # 2. Setup Word Timestamps
    words = [
        WordTimestamp(word="Autonomous", start=0.2, end=1.0, probability=0.99, speaker_id="SPEAKER_00"),
        WordTimestamp(word="video", start=1.1, end=1.6, probability=0.98, speaker_id="SPEAKER_00"),
        WordTimestamp(word="clipping.", start=1.7, end=2.6, probability=0.99, speaker_id="SPEAKER_00"),
    ]

    # 3. Setup Reframe Plan (1080x1920 portrait crop from 1920x1080 landscape)
    reframe_plan = ReframePlan(
        clip_id=clip_id,
        source_width=1920,
        source_height=1080,
        target_width=1080,
        target_height=1920,
        keyframes=[
            ReframeCropKeyframe(timestamp=0.0, crop_x=656, crop_y=0, crop_w=608, crop_h=1080),
            ReframeCropKeyframe(timestamp=1.5, crop_x=656, crop_y=0, crop_w=608, crop_h=1080),
        ],
    )

    # 4. Render using real FFmpeg executable discovered by FFmpegRenderer
    render_engine = RenderOrchestrationEngine(
        subtitle_generator=AssSubtitleGenerator(),
        filtergraph_builder=FFmpegFiltergraphBuilder(),
        media_renderer=FFmpegRenderer(),
    )

    render_out = await render_engine.render(
        clip_id=clip_id,
        source_video_id=source_video_id,
        clip_start=0.0,
        clip_end=2.8,
        reframe_plan=reframe_plan,
        words=words,
        storage_driver=storage,
    )

    assert render_out.clip_id == clip_id
    assert render_out.file_size_bytes > 0

    # 5. Run QA Evaluation against the real rendered MP4
    qa_engine = QAEngine()
    report = await qa_engine.evaluate_rendered_clip(
        clip_id=clip_id,
        source_video_id=source_video_id,
        storage_driver=storage,
        expected_duration=2.8,
        reframe_plan=reframe_plan,
    )

    # Verify QA Result
    assert report.clip_id == clip_id
    assert report.can_publish is True
    assert report.overall_status in (QACheckStatus.PASS, QACheckStatus.WARN)
    assert report.media_validation is not None
    assert report.media_validation.width == 1080
    assert report.media_validation.height == 1920
    assert report.media_validation.file_size_bytes > 0

    # Verify canonical artifacts
    assert await storage.exists(f"clips/{clip_id}/final_1080x1920.mp4") is True
    assert await storage.exists(f"clips/{clip_id}/subtitles.ass") is True
    assert await storage.exists(f"clips/{clip_id}/render_output.json") is True
    assert await storage.exists(f"clips/{clip_id}/qa_report.json") is True
