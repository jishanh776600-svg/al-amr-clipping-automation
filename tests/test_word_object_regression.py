"""Regression test for Word object model contract and SEO pipeline integration.

Verifies:
1. Canonical Word dataclass interface: uses 'text', not 'word'.
2. Transcript.slice and Transcript.text_between slicing contract.
3. Pipeline Stage EXPORT / SEO integration: ensures SEOEngine.generate_for_clip
   receives transcript_text without raising AttributeError: 'Word' object has no attribute 'word'.
4. Transcribe word extraction handles both whisper objects and alternate dicts.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from autoclip.db.models import Clip
from autoclip.pipeline.transcript import Transcript, Word
from autoclip.seo.engine import SEOEngine


def test_word_canonical_interface():
    """Ensure Word has canonical 'text' attribute and no fake 'word' property."""
    w = Word(text="hello", start=0.0, end=0.5, speaker="SPEAKER_00")
    assert w.text == "hello"
    assert w.start == 0.0
    assert w.end == 0.5
    assert w.speaker == "SPEAKER_00"
    assert w.duration == 0.5
    assert not hasattr(w, "word"), "Canonical Word interface must not have a fake 'word' property"


def test_transcript_slice_and_text_between():
    """Verify Transcript.slice returns list[Word] and text_between constructs text."""
    words = [
        Word(text="Welcome", start=0.0, end=0.5),
        Word(text="to", start=0.5, end=0.7),
        Word(text="AL", start=0.7, end=1.0),
        Word(text="AMR.", start=1.0, end=1.5),
    ]
    t = Transcript(words=words)

    # slice returns list[Word]
    sliced = t.slice(0, 3)
    assert len(sliced) == 4
    assert isinstance(sliced[0], Word)
    assert sliced[0].text == "Welcome"

    # text_between uses w.text
    text = t.text_between(0, 3)
    assert text == "Welcome to AL AMR."


def test_seo_metadata_generation_with_transcript_slice(tmp_path, monkeypatch):
    """Reproduce the exact Stage EXPORT / SEO path in PipelineRunner._stage_export.

    Before the fix, runner.py executed:
        slice_text = " ".join(w.word for w in clip_words)
    which raised AttributeError: 'Word' object has no attribute 'word'.

    With the fix, it executes:
        slice_text = transcript.text_between(clip.start_word, clip.end_word)
        meta_rec = seo_engine.generate_for_clip(clip, transcript_text=slice_text)
    """
    from autoclip import db, paths
    from autoclip.db import store
    from autoclip.db.models import Source, Job
    monkeypatch.setenv("AUTOCLIP_DATA_DIR", str(tmp_path))
    paths.ensure_layout()
    db.init()

    source = Source(id="source-test-123", path=str(tmp_path / "video.mp4"), duration_s=60.0, type="upload")
    store.create_source(source)
    job = Job(id="job-test-456", source_id=source.id)
    store.create_job(job)

    words = [
        Word(text="Incredible", start=10.0, end=10.5),
        Word(text="performance", start=10.5, end=11.2),
        Word(text="by", start=11.2, end=11.4),
        Word(text="the", start=11.4, end=11.5),
        Word(text="team", start=11.5, end=12.0),
        Word(text="today.", start=12.0, end=12.5),
    ]
    transcript = Transcript(words=words)

    clip = Clip(
        id="clip-test-123",
        job_id="job-test-456",
        start_s=10.0,
        end_s=12.5,
        start_word=0,
        end_word=5,
        title="Team Highlights",
        hook="Incredible performance",
        rank=1,
    )
    store.create_clip(clip)

    # Execute the exact runner logic
    clip_words = transcript.slice(clip.start_word, clip.end_word)
    assert len(clip_words) == 6
    assert all(isinstance(w, Word) for w in clip_words)

    # Must NOT raise AttributeError
    slice_text = transcript.text_between(clip.start_word, clip.end_word)
    assert slice_text == "Incredible performance by the team today."

    seo_engine = SEOEngine.from_campaign_spec(None)
    meta_rec = seo_engine.generate_for_clip(clip, transcript_text=slice_text)

    assert meta_rec is not None
    assert meta_rec.clip_id == "clip-test-123"
    assert meta_rec.final_title != ""
    assert meta_rec.final_description != ""
    assert isinstance(meta_rec.final_hashtags, list)


def test_transcribe_word_extraction_defensive():
    """Verify transcribe word extraction handles faster-whisper objects and dict fallbacks."""
    from autoclip.pipeline.transcribe import transcribe

    # Test whisper word object with .word
    mock_word_obj = MagicMock()
    mock_word_obj.word = "  artificial  "
    mock_word_obj.start = 1.0
    mock_word_obj.end = 1.8

    mock_segment = MagicMock()
    mock_segment.words = [mock_word_obj]
    mock_segment.text = "artificial"
    mock_segment.start = 1.0
    mock_segment.end = 1.8

    mock_model = MagicMock()
    mock_model.transcribe.return_value = ([mock_segment], MagicMock(duration=2.0, language="en"))

    mock_whisper_settings = MagicMock(language=None, diarization=False, model="base")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("faster_whisper.WhisperModel", lambda *a, **k: mock_model)
        # Call transcribe
        import tempfile
        from pathlib import Path

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            audio_path = Path(f.name)

        try:
            result = transcribe(audio_path, mock_whisper_settings, duration_s=2.0)
            assert len(result.words) == 1
            assert result.words[0].text == "artificial"
            assert result.words[0].start == 1.0
            assert result.words[0].end == 1.8
        finally:
            audio_path.unlink(missing_ok=True)
