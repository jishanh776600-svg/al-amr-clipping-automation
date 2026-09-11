"""Unit tests for Step 2 enhancements: robust ingestion, multi-speaker split framing, and output validator."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from autoclip.pipeline import ffmpeg
from autoclip.pipeline.export import (
    ExportRequest,
    build_video_filtergraph,
)
from autoclip.pipeline.ingest import is_direct_media_url, is_youtube_url
from autoclip.pipeline.reframe import ReframeConfig, _split_segment
from autoclip.pipeline.reframe.croppath import (
    CropKeyframe,
    CropPath,
    CropSegment,
    Strategy,
    segment_crop_filter,
    segment_secondary_crop_filter,
)
from autoclip.pipeline.reframe.faces import FaceObservation
from autoclip.pipeline.reframe.speaker import _assignment_for_two_speakers
from autoclip.pipeline.reframe.tracker import FaceTrack
from autoclip.pipeline.validator import validate_media_output


class TestIngestionEnhancements:
    def test_is_direct_media_url(self) -> None:
        assert is_direct_media_url("https://example.com/media/clip.mp4")
        assert is_direct_media_url("http://cdn.site.org/videos/interview.webm?token=123")
        assert is_direct_media_url("https://s3.amazonaws.com/bucket/raw.MOV#t=10")
        assert not is_direct_media_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        assert not is_direct_media_url("https://vimeo.com/12345678")
        assert not is_direct_media_url("not a url")

    def test_is_youtube_url_still_works(self) -> None:
        assert is_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        assert is_youtube_url("https://youtu.be/dQw4w9WgXcQ")
        assert not is_youtube_url("https://example.com/video.mp4")


class TestMultiSpeakerConversationSupport:
    def _make_track(self, track_id: int, cx: float, cy: float, mar_values: list[float]) -> FaceTrack:
        obs = [
            FaceObservation(
                t=float(i) * 0.2,
                cx=cx,
                cy=cy,
                width=200.0,
                height=250.0,
                eye_y=cy - 40.0,
                mar=mar,
            )
            for i, mar in enumerate(mar_values)
        ]
        return FaceTrack(id=track_id, observations=obs)

    def test_dominant_speaker_is_tracked(self) -> None:
        # Track 1 has active talking mouth variance, Track 2 is silent
        track1 = self._make_track(1, cx=400.0, cy=500.0, mar_values=[0.01, 0.08, 0.02, 0.09, 0.03, 0.07])
        track2 = self._make_track(2, cx=1400.0, cy=500.0, mar_values=[0.02, 0.02, 0.021, 0.019, 0.02, 0.02])

        assignment = _assignment_for_two_speakers([track1, track2], 0.0, 1.2)
        assert assignment.layout == "track"
        assert assignment.track is track1

    def test_conversational_speakers_get_split_layout(self) -> None:
        # Both speakers are talking actively
        track1 = self._make_track(1, cx=400.0, cy=500.0, mar_values=[0.02, 0.08, 0.03, 0.07, 0.04, 0.06])
        track2 = self._make_track(2, cx=1500.0, cy=500.0, mar_values=[0.03, 0.07, 0.02, 0.08, 0.03, 0.07])

        assignment = _assignment_for_two_speakers([track1, track2], 0.0, 1.2)
        assert assignment.layout == "split"
        assert assignment.track is track1  # Left speaker (mean_cx=400)
        assert assignment.secondary_track is track2  # Right speaker (mean_cx=1500)

    def test_split_segment_geometry(self) -> None:
        track1 = self._make_track(1, cx=400.0, cy=500.0, mar_values=[0.05] * 5)
        track2 = self._make_track(2, cx=1500.0, cy=500.0, mar_values=[0.05] * 5)

        segment = _split_segment(
            track1,
            track2,
            start_s=0.0,
            end_s=1.0,
            source_w=1920,
            source_h=1080,
            config=ReframeConfig(),
        )
        assert segment.strategy == Strategy.SPLIT
        # 9:8 ratio from 1920x1080 gives 1214x1080 (even)
        assert segment.width == 1214
        assert segment.height == 1080
        assert len(segment.keyframes) >= 1
        assert len(segment.secondary_keyframes) >= 1
        # Primary is centered near x=400, secondary near x=1500
        assert segment.keyframes[0].x < segment.secondary_keyframes[0].x

    def test_split_filtergraph_construction(self) -> None:
        segment = CropSegment(
            start_s=0.0,
            end_s=5.0,
            width=1214,
            height=1080,
            keyframes=[CropKeyframe(t=0.0, x=100.0, y=50.0)],
            secondary_keyframes=[CropKeyframe(t=0.0, x=800.0, y=50.0)],
            strategy=Strategy.SPLIT,
        )
        crop_path = CropPath(source_width=1920, source_height=1080, segments=[segment])
        from autoclip.pipeline.captions import get_style

        req = ExportRequest(
            source=Path("test.mp4"),
            destination=Path("out.mp4"),
            start_s=0.0,
            end_s=5.0,
            crop_path=crop_path,
            words=[],
            style=get_style("bold_pop"),
            ratio="9:16",
            burn_captions=False,
        )
        filtergraph = build_video_filtergraph(req, subtitle_name=None)
        assert "split=2" in filtergraph
        assert "vstack=inputs=2" in filtergraph
        assert "scale=1080:960" in filtergraph


class TestOutputValidator:
    @pytest.fixture(scope="module")
    def synthetic_mp4(self, tmp_path_factory) -> Path:
        path = tmp_path_factory.mktemp("val_media") / "test_9x16.mp4"
        cmd = [
            ffmpeg.ffmpeg_path(),
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1080x1920:rate=30:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        return path

    def test_validator_accepts_valid_mp4(self, synthetic_mp4: Path) -> None:
        result = validate_media_output(
            synthetic_mp4,
            expected_duration_s=2.0,
            expected_ratio="9:16",
            require_audio=True,
            decode_check=True,
        )
        assert result.valid
        assert result.has_video
        assert result.has_audio
        assert result.width == 1080
        assert result.height == 1920
        assert len(result.errors) == 0

    def test_validator_rejects_empty_file(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty.mp4"
        empty.write_bytes(b"")
        result = validate_media_output(empty, strict=False)
        assert not result.valid
        assert any("too small" in err for err in result.errors)

    def test_validator_rejects_corrupted_file(self, tmp_path: Path) -> None:
        corrupt = tmp_path / "corrupt.mp4"
        corrupt.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"garbage" * 300)
        result = validate_media_output(corrupt, strict=False)
        assert not result.valid

    def test_validator_rejects_ratio_mismatch(self, synthetic_mp4: Path) -> None:
        # synthetic_mp4 is 1080x1920 (9:16), so asking for 16:9 should fail validation
        result = validate_media_output(synthetic_mp4, expected_ratio="16:9", strict=False)
        assert not result.valid
        assert any("Aspect ratio mismatch" in err for err in result.errors)
