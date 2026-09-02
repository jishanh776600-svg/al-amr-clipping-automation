"""Unit tests for Active Speaker Resolution."""

import pytest
from clipping.contracts.perception import (
    ActiveSpeakerSegment,
    FaceBoundingBox,
    FaceTrack,
    SceneCut,
    SpeakerAttributedTranscript,
    SpeakerSegment,
)
from clipping.vision.active_speaker import DeterministicActiveSpeakerResolver


@pytest.mark.asyncio
async def test_single_speaker_face_association():
    face_tracks = [
        FaceTrack(
            track_id=0,
            boxes=[
                FaceBoundingBox(frame_idx=0, timestamp=0.0, x=0.4, y=0.2, w=0.2, h=0.25),
                FaceBoundingBox(frame_idx=5, timestamp=1.0, x=0.4, y=0.2, w=0.2, h=0.25),
                FaceBoundingBox(frame_idx=10, timestamp=2.0, x=0.4, y=0.2, w=0.2, h=0.25),
            ],
        )
    ]

    speaker_transcript = SpeakerAttributedTranscript(
        source_video_id="VID_SOLO",
        text="Solo speaker monologue",
        words=[],
        speaker_segments=[
            SpeakerSegment(speaker_id="SPEAKER_00", start=0.0, end=2.0),
        ],
    )

    scenes = [SceneCut(scene_id=0, start_frame=0, end_frame=60, start_time=0.0, end_time=2.0)]

    resolver = DeterministicActiveSpeakerResolver()
    results = await resolver.resolve_active_speakers(
        source_video_id="VID_SOLO",
        face_tracks=face_tracks,
        speaker_transcript=speaker_transcript,
        scene_cuts=scenes,
    )

    assert len(results) == 1
    assert results[0].speaker_id == "SPEAKER_00"
    assert results[0].track_id == 0
    assert results[0].speaking_confidence >= 0.90


@pytest.mark.asyncio
async def test_two_person_interview_association():
    face_tracks = [
        # Track 0: Host on the left (x=0.2)
        FaceTrack(
            track_id=0,
            boxes=[
                FaceBoundingBox(frame_idx=0, timestamp=0.0, x=0.2, y=0.2, w=0.15, h=0.2),
                FaceBoundingBox(frame_idx=20, timestamp=4.0, x=0.2, y=0.2, w=0.15, h=0.2),
            ],
        ),
        # Track 1: Guest on the right (x=0.7)
        FaceTrack(
            track_id=1,
            boxes=[
                FaceBoundingBox(frame_idx=0, timestamp=0.0, x=0.7, y=0.2, w=0.15, h=0.2),
                FaceBoundingBox(frame_idx=20, timestamp=4.0, x=0.7, y=0.2, w=0.15, h=0.2),
            ],
        ),
    ]

    speaker_transcript = SpeakerAttributedTranscript(
        source_video_id="VID_DUO",
        text="Dialogue",
        words=[],
        speaker_segments=[
            SpeakerSegment(speaker_id="SPEAKER_00", start=0.0, end=2.0),
            SpeakerSegment(speaker_id="SPEAKER_01", start=2.1, end=4.0),
        ],
    )

    scenes = [SceneCut(scene_id=0, start_frame=0, end_frame=120, start_time=0.0, end_time=4.0)]

    resolver = DeterministicActiveSpeakerResolver()
    results = await resolver.resolve_active_speakers(
        source_video_id="VID_DUO",
        face_tracks=face_tracks,
        speaker_transcript=speaker_transcript,
        scene_cuts=scenes,
    )

    assert len(results) == 2
    assert results[0].speaker_id == "SPEAKER_00"
    assert results[0].track_id == 0
    assert results[1].speaker_id == "SPEAKER_01"
    assert results[1].track_id == 1


@pytest.mark.asyncio
async def test_voiceover_no_face():
    # Zero face tracks in video
    face_tracks = []
    speaker_transcript = SpeakerAttributedTranscript(
        source_video_id="VID_VOICEOVER",
        text="Voiceover",
        words=[],
        speaker_segments=[
            SpeakerSegment(speaker_id="SPEAKER_00", start=0.0, end=5.0),
        ],
    )

    scenes = [SceneCut(scene_id=0, start_frame=0, end_frame=150, start_time=0.0, end_time=5.0)]

    resolver = DeterministicActiveSpeakerResolver()
    results = await resolver.resolve_active_speakers(
        source_video_id="VID_VOICEOVER",
        face_tracks=face_tracks,
        speaker_transcript=speaker_transcript,
        scene_cuts=scenes,
    )

    assert len(results) == 1
    assert results[0].track_id is None
    assert results[0].speaking_confidence == 0.0
