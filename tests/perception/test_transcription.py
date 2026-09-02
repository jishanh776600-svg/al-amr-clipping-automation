"""Unit tests for Transcription Engine and faster-whisper integration."""

import pytest
from unittest.mock import MagicMock
from clipping.perception.transcription import FasterWhisperTranscriptionEngine
from clipping.contracts.perception import RawTranscript


def test_device_and_compute_type_resolution():
    engine_cpu = FasterWhisperTranscriptionEngine(model_size="small", device="cpu", compute_type="auto")
    assert engine_cpu.device == "cpu"
    assert engine_cpu.compute_type == "int8"

    engine_cuda = FasterWhisperTranscriptionEngine(model_size="large-v3", device="cuda", compute_type="auto")
    assert engine_cuda.device == "cuda"
    assert engine_cuda.compute_type == "float16"


@pytest.mark.asyncio
async def test_transcribe_with_mock_whisper(tmp_path):
    # Create a dummy audio file
    dummy_audio = tmp_path / "audio.wav"
    dummy_audio.write_bytes(b"RIFF_DUMMY_AUDIO_BYTES")

    # Mock whisper output
    mock_word1 = MagicMock()
    mock_word1.word = "Hello"
    mock_word1.start = 0.0
    mock_word1.end = 0.5
    mock_word1.probability = 0.98

    mock_word2 = MagicMock()
    mock_word2.word = "world"
    mock_word2.start = 0.6
    mock_word2.end = 1.0
    mock_word2.probability = 0.95

    mock_seg = MagicMock()
    mock_seg.text = "Hello world"
    mock_seg.words = [mock_word1, mock_word2]

    mock_info = MagicMock()
    mock_info.language = "en"
    mock_info.language_probability = 0.99

    mock_whisper_model = MagicMock()
    mock_whisper_model.transcribe.return_value = ([mock_seg], mock_info)

    engine = FasterWhisperTranscriptionEngine(
        model_size="base",
        whisper_model=mock_whisper_model,
    )

    result = await engine.transcribe(str(dummy_audio), source_video_id="VID_TEST_01")

    assert isinstance(result, RawTranscript)
    assert result.source_video_id == "VID_TEST_01"
    assert result.language == "en"
    assert result.text == "Hello world"
    assert len(result.words) == 2
    assert result.words[0].word == "Hello"
    assert result.words[0].start == 0.0
    assert result.words[0].end == 0.5
    assert result.words[1].word == "world"
    assert result.words[1].end == 1.0


@pytest.mark.asyncio
async def test_transcribe_file_not_found():
    engine = FasterWhisperTranscriptionEngine(model_size="base")
    with pytest.raises(FileNotFoundError):
        await engine.transcribe("/non/existent/audio.wav", source_video_id="VID_NON_EXISTENT")
