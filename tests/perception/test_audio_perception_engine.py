"""Integration tests for AudioPerceptionEngine and Canonical Artifact Persistence."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from clipping.contracts.perception import (
    RawTranscript,
    DiarizationResult,
    SpeakerSegment,
    WordTimestamp,
)
from clipping.perception.engine import AudioPerceptionEngine
from clipping.perception.alignment import TemporalAttributionEngine
from clipping.storage.local import LocalStorageDriver


@pytest.mark.asyncio
async def test_audio_perception_pipeline_lifecycle(temp_vault_dir):
    storage = LocalStorageDriver(root_dir=temp_vault_dir)

    # 1. Seed audio file in storage vault
    audio_key = "sources/VID_INTEGRATION_01/audio.wav"
    await storage.upload_bytes(b"RIFF_AUDIO_TEST_PAYLOAD", audio_key, content_type="audio/wav")

    # 2. Mock Transcription Engine
    mock_transcriber = MagicMock()
    mock_transcriber.transcribe = AsyncMock(return_value=RawTranscript(
        source_video_id="VID_INTEGRATION_01",
        language="en",
        language_probability=0.98,
        text="Hello and welcome to the automation podcast",
        words=[
            WordTimestamp(word="Hello", start=0.1, end=0.5, probability=0.99),
            WordTimestamp(word="and", start=0.6, end=0.8, probability=0.98),
            WordTimestamp(word="welcome", start=0.9, end=1.4, probability=0.97),
            WordTimestamp(word="to", start=1.5, end=1.7, probability=0.99),
            WordTimestamp(word="the", start=1.8, end=2.0, probability=0.99),
            WordTimestamp(word="automation", start=2.1, end=2.7, probability=0.96),
            WordTimestamp(word="podcast", start=2.8, end=3.4, probability=0.95),
        ],
    ))

    # 3. Mock Diarization Engine (2 speakers)
    mock_diarizer = MagicMock()
    mock_diarizer.diarize = AsyncMock(return_value=DiarizationResult(
        source_video_id="VID_INTEGRATION_01",
        backend="pyannote",
        model_name="pyannote/speaker-diarization-3.1",
        num_speakers=2,
        segments=[
            SpeakerSegment(speaker_id="SPEAKER_00", start=0.0, end=1.6),
            SpeakerSegment(speaker_id="SPEAKER_01", start=1.7, end=3.5),
        ],
    ))

    engine = AudioPerceptionEngine(
        transcription_engine=mock_transcriber,
        diarization_engine=mock_diarizer,
        alignment_engine=TemporalAttributionEngine(),
    )

    # 4. Run Process
    transcript, metadata = await engine.process(
        source_video_id="VID_INTEGRATION_01",
        storage_driver=storage,
    )

    # Verify transcript and attribution
    assert len(transcript.words) == 7
    assert transcript.words[0].speaker_id == "SPEAKER_00"
    assert transcript.words[6].speaker_id == "SPEAKER_01"
    assert metadata.num_speakers == 2
    assert metadata.total_words == 7
    assert metadata.detected_language == "en"

    # 5. Verify all 4 canonical artifacts are stored in the storage vault
    assert await storage.exists("sources/VID_INTEGRATION_01/transcript_raw.json") is True
    assert await storage.exists("sources/VID_INTEGRATION_01/diarization.json") is True
    assert await storage.exists("sources/VID_INTEGRATION_01/speaker_transcript.json") is True
    assert await storage.exists("sources/VID_INTEGRATION_01/perception_metadata.json") is True

    # 6. Idempotency Check: Re-running should skip inference
    mock_transcriber.transcribe.side_effect = RuntimeError("Should not be called on cached run")
    cached_transcript, cached_meta = await engine.process(
        source_video_id="VID_INTEGRATION_01",
        storage_driver=storage,
        force_recompute=False,
    )
    assert len(cached_transcript.words) == 7
    assert cached_meta.total_words == 7


@pytest.mark.asyncio
async def test_diarization_failure_fallback(temp_vault_dir):
    storage = LocalStorageDriver(root_dir=temp_vault_dir)
    audio_key = "sources/VID_FALLBACK_TEST/audio.wav"
    await storage.upload_bytes(b"RIFF_AUDIO_TEST_PAYLOAD", audio_key, content_type="audio/wav")

    # Mock transcriber
    mock_transcriber = MagicMock()
    mock_transcriber.transcribe = AsyncMock(return_value=RawTranscript(
        source_video_id="VID_FALLBACK_TEST",
        language="en",
        text="Single speaker podcast test",
        words=[WordTimestamp(word="Test", start=0.0, end=1.0, probability=0.99)],
    ))

    # Failing diarization engine
    mock_failing_diarizer = MagicMock()
    mock_failing_diarizer.diarize = AsyncMock(side_effect=RuntimeError("Hugging Face API token missing"))

    engine = AudioPerceptionEngine(
        transcription_engine=mock_transcriber,
        diarization_engine=mock_failing_diarizer,
    )

    transcript, metadata = await engine.process(
        source_video_id="VID_FALLBACK_TEST",
        storage_driver=storage,
    )

    assert metadata.diarization_backend == "energy_vad_fallback"
    assert len(metadata.warnings) > 0
    assert "FallbackDiarizationEngine" in metadata.warnings[0]
    assert transcript.words[0].speaker_id == "SPEAKER_00"
