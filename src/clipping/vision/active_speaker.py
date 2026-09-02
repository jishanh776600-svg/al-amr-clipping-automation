"""Deterministic CPU-First Active Speaker Resolution Engine."""

from typing import Dict, List, Optional, Tuple
from clipping.contracts.perception import (
    ActiveSpeakerSegment,
    FaceTrack,
    SceneCut,
    SpeakerAttributedTranscript,
    SpeakerSegment,
)
from clipping.vision.base import ActiveSpeakerResolver
from clipping.logging.logger import get_logger

logger = get_logger("clipping.vision.active_speaker")


class DeterministicActiveSpeakerResolver(ActiveSpeakerResolver):
    """
    Lightweight, CPU-first Active Speaker Resolver.
    Fuses acoustic speaker diarization intervals with visual face tracks
    using temporal co-occurrence and spatial layout heuristics without neural ASD models.
    """

    def __init__(self, mock_active_speakers: Optional[List[ActiveSpeakerSegment]] = None):
        self._mock_active_speakers = mock_active_speakers

    async def resolve_active_speakers(
        self,
        source_video_id: str,
        face_tracks: List[FaceTrack],
        speaker_transcript: SpeakerAttributedTranscript,
        scene_cuts: List[SceneCut],
    ) -> List[ActiveSpeakerSegment]:
        if self._mock_active_speakers is not None:
            return self._mock_active_speakers

        results: List[ActiveSpeakerSegment] = []
        speaker_segments = speaker_transcript.speaker_segments

        # If no speaker segments in transcript, fallback to empty list
        if not speaker_segments:
            return results

        # 1. Map tracks by their temporal life spans: (track_id, min_time, max_time, mean_x)
        track_spans: List[Tuple[int, float, float, float]] = []
        for track in face_tracks:
            if track.boxes:
                t_min = min(b.timestamp for b in track.boxes)
                t_max = max(b.timestamp for b in track.boxes)
                mean_x = sum(b.x + b.w / 2.0 for b in track.boxes) / len(track.boxes)
                track_spans.append((track.track_id, t_min, t_max, mean_x))

        # 2. Iterate through acoustic speaker segments
        for seg in speaker_segments:
            s_start = seg.start
            s_end = seg.end
            spk_id = seg.speaker_id

            # Find active tracks during this segment
            overlapping_tracks = [
                (t_id, mean_x)
                for (t_id, t_min, t_max, mean_x) in track_spans
                if max(s_start, t_min) < min(s_end, t_max)
            ]

            if not overlapping_tracks:
                # Case A: Voiceover / No visible face in frame
                results.append(
                    ActiveSpeakerSegment(
                        speaker_id=spk_id,
                        start_time=s_start,
                        end_time=s_end,
                        track_id=None,
                        speaking_confidence=0.0,
                    )
                )
            elif len(overlapping_tracks) == 1:
                # Case B: Clear single visible speaker
                t_id, _ = overlapping_tracks[0]
                results.append(
                    ActiveSpeakerSegment(
                        speaker_id=spk_id,
                        start_time=s_start,
                        end_time=s_end,
                        track_id=t_id,
                        speaking_confidence=0.95,
                    )
                )
            else:
                # Case C: Multi-person visible (e.g. 2-person interview / panel)
                # Sort overlapping tracks horizontally (left-to-right)
                overlapping_tracks.sort(key=lambda x: x[1])

                # Heuristic mapping: SPEAKER_00 -> Track 0 (Left), SPEAKER_01 -> Track 1 (Right)
                assigned_track = None
                conf = 0.70  # Explicit uncertainty due to multi-face co-occurrence

                try:
                    spk_num = int(spk_id.replace("SPEAKER_", ""))
                    if spk_num < len(overlapping_tracks):
                        assigned_track = overlapping_tracks[spk_num][0]
                    else:
                        assigned_track = overlapping_tracks[0][0]
                except Exception:
                    assigned_track = overlapping_tracks[0][0]

                results.append(
                    ActiveSpeakerSegment(
                        speaker_id=spk_id,
                        start_time=s_start,
                        end_time=s_end,
                        track_id=assigned_track,
                        speaking_confidence=conf,
                    )
                )

        logger.info(
            "Active speaker resolution completed",
            source_video_id=source_video_id,
            total_active_segments=len(results),
        )

        return results
