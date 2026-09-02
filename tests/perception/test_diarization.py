"""Unit tests for Diarization Engine and speaker normalization."""

import pytest
from unittest.mock import MagicMock
from clipping.perception.diarization import (
    PyannoteDiarizationEngine,
    FallbackDiarizationEngine,
)


@pytest.mark.asyncio
async def test_pyannote_deterministic_speaker_ids(tmp_path):
    dummy_audio = tmp_path / "interview.wav"
    dummy_audio.write_bytes(b"RIFF_AUDIO")

    # Mock pyannote diarization output
    # Speaker B appears first at t=0.0, Speaker A appears at t=2.0
    turn1 = MagicMock(start=0.0, end=1.8)
    turn2 = MagicMock(start=2.0, end=4.0)
    turn3 = MagicMock(start=4.2, end=5.5)

    mock_annotation = MagicMock()
    mock_annotation.itertracks.return_value = [
        (turn1, None, "GUEST_SPEAKER_B"),
        (turn2, None, "HOST_SPEAKER_A"),
        (turn3, None, "GUEST_SPEAKER_B"),
    ]

    mock_pipeline = MagicMock()
    mock_pipeline.return_value = mock_annotation

    engine = PyannoteDiarizationEngine(pipeline=mock_pipeline)
    result = await engine.diarize(str(dummy_audio), source_video_id="VID_DIAR_01")

    assert result.backend == "pyannote"
    assert result.num_speakers == 2
    assert len(result.segments) == 3

    # GUEST_SPEAKER_B appeared first -> SPEAKER_00
    assert result.segments[0].speaker_id == "SPEAKER_00"
    assert result.segments[0].start == 0.0
    assert result.segments[0].end == 1.8

    # HOST_SPEAKER_A appeared second -> SPEAKER_01
    assert result.segments[1].speaker_id == "SPEAKER_01"
    assert result.segments[1].start == 2.0
    assert result.segments[1].end == 4.0

    # Turn 3 back to SPEAKER_00
    assert result.segments[2].speaker_id == "SPEAKER_00"


@pytest.mark.asyncio
async def test_pyannote_overlapping_speech(tmp_path):
    dummy_audio = tmp_path / "overlap.wav"
    dummy_audio.write_bytes(b"RIFF_AUDIO")

    # Simultaneous speech: Speaker 1 (0.0 - 3.0), Speaker 2 (2.0 - 4.0)
    turn1 = MagicMock(start=0.0, end=3.0)
    turn2 = MagicMock(start=2.0, end=4.0)

    mock_annotation = MagicMock()
    mock_annotation.itertracks.return_value = [
        (turn1, None, "SPK_1"),
        (turn2, None, "SPK_2"),
    ]
    mock_pipeline = MagicMock(return_value=mock_annotation)

    engine = PyannoteDiarizationEngine(pipeline=mock_pipeline)
    result = await engine.diarize(str(dummy_audio), source_video_id="VID_OVERLAP")

    # Overlapping segment is preserved
    assert len(result.segments) == 2
    assert result.segments[0].start == 0.0
    assert result.segments[0].end == 3.0
    assert result.segments[1].start == 2.0
    assert result.segments[1].end == 4.0


@pytest.mark.asyncio
async def test_fallback_diarization(tmp_path):
    dummy_audio = tmp_path / "fallback.wav"
    dummy_audio.write_bytes(b"RIFF_AUDIO")

    engine = FallbackDiarizationEngine()
    result = await engine.diarize(str(dummy_audio), source_video_id="VID_FALLBACK")

    assert result.backend == "energy_vad_fallback"
    assert result.num_speakers == 1
    assert len(result.segments) == 1
    assert result.segments[0].speaker_id == "SPEAKER_00"
