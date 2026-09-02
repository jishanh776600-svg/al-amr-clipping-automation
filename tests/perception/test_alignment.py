"""Unit tests for Temporal Attribution & Word-to-Speaker Alignment."""

from clipping.contracts.perception import (
    RawTranscript,
    DiarizationResult,
    SpeakerSegment,
    WordTimestamp,
)
from clipping.perception.alignment import TemporalAttributionEngine


def test_clear_word_attribution():
    words = [
        WordTimestamp(word="Welcome", start=0.1, end=0.6, probability=0.99),
        WordTimestamp(word="everyone", start=0.7, end=1.2, probability=0.98),
        WordTimestamp(word="Thanks", start=2.1, end=2.6, probability=0.95),
        WordTimestamp(word="host", start=2.7, end=3.1, probability=0.96),
    ]
    raw = RawTranscript(source_video_id="VID_01", text="Welcome everyone Thanks host", words=words)

    segments = [
        SpeakerSegment(speaker_id="SPEAKER_00", start=0.0, end=1.5),
        SpeakerSegment(speaker_id="SPEAKER_01", start=2.0, end=3.5),
    ]
    diar = DiarizationResult(source_video_id="VID_01", backend="test", model_name="test", num_speakers=2, segments=segments)

    engine = TemporalAttributionEngine()
    result = engine.align(raw, diar)

    assert result.words[0].speaker_id == "SPEAKER_00"
    assert result.words[1].speaker_id == "SPEAKER_00"
    assert result.words[2].speaker_id == "SPEAKER_01"
    assert result.words[3].speaker_id == "SPEAKER_01"


def test_ambiguous_cross_talk_attribution():
    # Word spans 1.8 to 2.2s during a rapid overlapping turn switch
    words = [
        WordTimestamp(word="crosstalk", start=1.8, end=2.2, probability=0.9),
    ]
    raw = RawTranscript(source_video_id="VID_02", text="crosstalk", words=words)

    # Both speakers overlap the word by roughly equal duration (0.2s each)
    segments = [
        SpeakerSegment(speaker_id="SPEAKER_00", start=1.0, end=2.0),
        SpeakerSegment(speaker_id="SPEAKER_01", start=2.0, end=3.0),
    ]
    diar = DiarizationResult(source_video_id="VID_02", backend="test", model_name="test", num_speakers=2, segments=segments)

    engine = TemporalAttributionEngine()
    result = engine.align(raw, diar, overlap_threshold=0.6)

    # Overlap ratio for each is 0.2 / 0.4 = 0.5 < 0.6 threshold -> leaves speaker_id = None
    assert result.words[0].speaker_id is None


def test_silence_word_no_speaker():
    # Word during a gap between speaker segments
    words = [
        WordTimestamp(word="isolated", start=5.0, end=5.4, probability=0.85),
    ]
    raw = RawTranscript(source_video_id="VID_03", text="isolated", words=words)

    segments = [
        SpeakerSegment(speaker_id="SPEAKER_00", start=0.0, end=2.0),
        SpeakerSegment(speaker_id="SPEAKER_01", start=8.0, end=10.0),
    ]
    diar = DiarizationResult(source_video_id="VID_03", backend="test", model_name="test", num_speakers=2, segments=segments)

    engine = TemporalAttributionEngine()
    result = engine.align(raw, diar)

    assert result.words[0].speaker_id is None
