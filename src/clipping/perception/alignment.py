"""Word-to-Speaker Alignment and Temporal Attribution Engine."""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional
from clipping.contracts.perception import (
    RawTranscript,
    DiarizationResult,
    SpeakerSegment,
    SpeakerAttributedTranscript,
    WordTimestamp,
)
from clipping.logging.logger import get_logger

logger = get_logger("clipping.perception.alignment")


class AlignmentEngine(ABC):
    """Abstract interface for assigning speaker IDs to word timestamps."""

    @abstractmethod
    def align(
        self,
        raw_transcript: RawTranscript,
        diarization: DiarizationResult,
        overlap_threshold: float = 0.5,
    ) -> SpeakerAttributedTranscript:
        """Assigns speaker labels to words based on temporal overlap."""
        pass


class TemporalAttributionEngine(AlignmentEngine):
    """
    Deterministic temporal attribution engine.
    Calculates exact overlap duration between each word and speaker segments.
    """

    def align(
        self,
        raw_transcript: RawTranscript,
        diarization: DiarizationResult,
        overlap_threshold: float = 0.5,
    ) -> SpeakerAttributedTranscript:
        attributed_words: List[WordTimestamp] = []
        segments = diarization.segments

        for w in raw_transcript.words:
            w_start = w.start
            w_end = w.end
            w_duration = max(0.001, w_end - w_start)

            speaker_overlaps: Dict[str, float] = {}

            for s in segments:
                # Calculate intersection between word [w_start, w_end] and segment [s.start, s.end]
                overlap = max(0.0, min(w_end, s.end) - max(w_start, s.start))
                if overlap > 0:
                    speaker_overlaps[s.speaker_id] = speaker_overlaps.get(s.speaker_id, 0.0) + overlap

            assigned_speaker: Optional[str] = None

            if speaker_overlaps:
                # Sort speakers by overlap duration descending
                sorted_overlaps = sorted(
                    speaker_overlaps.items(), key=lambda item: item[1], reverse=True
                )
                best_speaker, best_overlap = sorted_overlaps[0]
                overlap_ratio = best_overlap / w_duration

                # Check if overlap exceeds required threshold
                if overlap_ratio >= overlap_threshold:
                    # Check for ambiguity: if second speaker is too close, keep None to avoid false attribution
                    if len(sorted_overlaps) > 1:
                        second_speaker, second_overlap = sorted_overlaps[1]
                        second_ratio = second_overlap / w_duration
                        if (overlap_ratio - second_ratio) < 0.15:
                            # High ambiguity between speakers during cross-talk
                            assigned_speaker = None
                        else:
                            assigned_speaker = best_speaker
                    else:
                        assigned_speaker = best_speaker

            attributed_words.append(
                WordTimestamp(
                    word=w.word,
                    start=w.start,
                    end=w.end,
                    probability=w.probability,
                    speaker_id=assigned_speaker,
                )
            )

        logger.info(
            "Temporal speaker attribution completed",
            source_video_id=raw_transcript.source_video_id,
            total_words=len(attributed_words),
            attributed_count=sum(1 for w in attributed_words if w.speaker_id is not None),
        )

        return SpeakerAttributedTranscript(
            source_video_id=raw_transcript.source_video_id,
            text=raw_transcript.text,
            words=attributed_words,
            speaker_segments=segments,
        )
