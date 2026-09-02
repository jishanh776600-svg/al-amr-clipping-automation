"""Candidate Window Generation from Transcript and Perception Boundaries."""

import re
from typing import List, Optional, Tuple
from clipping.contracts.clip import ClipCandidate, ClipBoundary
from clipping.contracts.perception import (
    SpeakerAttributedTranscript,
    SceneCut,
    WordTimestamp,
)
from clipping.discovery.config import ClipDiscoveryConfig
from clipping.discovery.exceptions import WindowGenerationError
from clipping.logging.logger import get_logger

logger = get_logger("clipping.discovery.windows")


class CandidateWindowGenerator:
    """
    Generates candidate clip windows based on sentence boundaries, speaker turns,
    and conversational pauses rather than arbitrary fixed-duration slicing.
    """

    def __init__(self, config: Optional[ClipDiscoveryConfig] = None):
        self.config = config or ClipDiscoveryConfig()

    def generate_windows(
        self,
        transcript: SpeakerAttributedTranscript,
        scene_cuts: Optional[List[SceneCut]] = None,
        campaign_id: str = "default_campaign",
    ) -> List[ClipCandidate]:
        words = transcript.words
        if not words or len(words) < 5:
            logger.info("Transcript has insufficient words for candidate windowing", word_count=len(words))
            return []

        # 1. Segment transcript into sentences / utterance blocks
        sentences: List[Tuple[int, int, str]] = []  # (start_word_idx, end_word_idx, text)
        cur_start = 0

        for i, w in enumerate(words):
            is_last = (i == len(words) - 1)
            has_punct = bool(re.search(r"[.!?]$", w.word))
            
            # Check pause gap to next word
            is_long_pause = False
            if not is_last and (words[i + 1].start - w.end) >= 1.5:
                is_long_pause = True

            # Check speaker change
            is_speaker_switch = False
            if not is_last and w.speaker_id != words[i + 1].speaker_id and w.speaker_id is not None:
                is_speaker_switch = True

            if has_punct or is_long_pause or is_speaker_switch or is_last:
                sentence_words = words[cur_start : i + 1]
                sentence_text = " ".join(sw.word for sw in sentence_words).strip()
                if sentence_text:
                    sentences.append((cur_start, i, sentence_text))
                cur_start = i + 1

        if not sentences:
            return []

        candidates: List[ClipCandidate] = []
        scene_list = scene_cuts or []

        # 2. Multi-sentence sliding window generation
        for s_idx in range(len(sentences)):
            start_word_idx = sentences[s_idx][0]
            hook_sentence = sentences[s_idx][2]

            for e_idx in range(s_idx, len(sentences)):
                end_word_idx = sentences[e_idx][1]
                
                win_words = words[start_word_idx : end_word_idx + 1]
                win_start = win_words[0].start
                win_end = win_words[-1].end
                duration = win_end - win_start

                if duration > self.config.max_duration_seconds:
                    break

                if duration >= self.config.min_duration_seconds:
                    full_text = " ".join(w.word for w in win_words).strip()
                    
                    # Speaker and scene attribution
                    speaker_counts = {}
                    for w in win_words:
                        if w.speaker_id:
                            speaker_counts[w.speaker_id] = speaker_counts.get(w.speaker_id, 0) + 1
                    
                    primary_spk = max(speaker_counts, key=speaker_counts.get) if speaker_counts else None
                    unique_spks = sorted(list(speaker_counts.keys()))

                    # Intersecting scene IDs
                    intersecting_scenes = [
                        sc.scene_id
                        for sc in scene_list
                        if max(win_start, sc.start_time) < min(win_end, sc.end_time)
                    ]

                    cand_id = f"cand_{transcript.source_video_id}_{win_start:.1f}_{win_end:.1f}".replace(".", "_")

                    candidate = ClipCandidate(
                        candidate_id=cand_id,
                        source_video_id=transcript.source_video_id,
                        campaign_id=campaign_id,
                        start_time=round(win_start, 3),
                        end_time=round(win_end, 3),
                        duration=round(duration, 3),
                        transcript_text=full_text,
                        words=win_words,
                        hook_sentence=hook_sentence,
                        primary_speaker_id=primary_spk,
                        speaker_ids=unique_spks,
                        scene_ids=intersecting_scenes,
                        boundary=ClipBoundary(
                            start_time=round(win_start, 3),
                            end_time=round(win_end, 3),
                            duration=round(duration, 3),
                            start_word_idx=start_word_idx,
                            end_word_idx=end_word_idx,
                        ),
                    )
                    candidates.append(candidate)

        logger.info(
            "Candidate window generation completed",
            source_video_id=transcript.source_video_id,
            total_candidates=len(candidates),
        )
        return candidates
