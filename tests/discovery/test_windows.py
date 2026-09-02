"""Unit tests for Candidate Window Generation."""

import pytest
from clipping.contracts.perception import SpeakerAttributedTranscript, WordTimestamp
from clipping.discovery.windows import CandidateWindowGenerator
from clipping.discovery.config import ClipDiscoveryConfig


def make_words_from_sentences(sentences: list[str], words_per_sec: float = 2.5) -> list[WordTimestamp]:
    words: list[WordTimestamp] = []
    t = 0.0
    spk_id = "SPEAKER_00"

    for s_idx, s in enumerate(sentences):
        tokens = s.split()
        if s_idx % 2 == 1:
            spk_id = "SPEAKER_01"
        else:
            spk_id = "SPEAKER_00"

        for tok in tokens:
            dur = 1.0 / words_per_sec
            words.append(
                WordTimestamp(
                    word=tok,
                    start=round(t, 2),
                    end=round(t + dur, 2),
                    probability=0.98,
                    speaker_id=spk_id,
                )
            )
            t += dur
        # Add pause after sentence
        t += 0.5

    return words


def test_window_generation_sentence_boundaries():
    sentences = [
        "The biggest mistake founders make is building without talking to users.",
        "They spend six months writing code in complete isolation.",
        "When they finally launch nobody cares and they run out of money.",
        "Here is what you should do instead.",
        "Talk to at least fifty potential customers before writing a single line of code.",
        "And that will save you years of wasted effort.",
    ]
    words = make_words_from_sentences(sentences)
    transcript = SpeakerAttributedTranscript(
        source_video_id="VID_TEST_01",
        text=" ".join(sentences),
        words=words,
    )

    config = ClipDiscoveryConfig(min_duration_seconds=15.0, max_duration_seconds=40.0)
    generator = CandidateWindowGenerator(config=config)
    candidates = generator.generate_windows(transcript)

    assert len(candidates) > 0
    for cand in candidates:
        assert cand.duration >= 15.0
        assert cand.duration <= 40.0
        assert len(cand.words) >= 5
        assert cand.hook_sentence != ""


def test_window_generation_empty_transcript():
    transcript = SpeakerAttributedTranscript(
        source_video_id="VID_EMPTY",
        text="",
        words=[],
    )
    generator = CandidateWindowGenerator()
    candidates = generator.generate_windows(transcript)
    assert candidates == []
