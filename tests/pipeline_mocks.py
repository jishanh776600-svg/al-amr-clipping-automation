"""Mock pipeline engines for fast, offline unit testing without external calls or ML models."""

from typing import Dict, List, Optional
from clipping.contracts.clip import (
    ClipCandidate,
    ClipScore,
    ClipSelectionResult,
    RankedCandidate,
)
from clipping.contracts.director import ReframeCropKeyframe, ReframePlan
from clipping.contracts.perception import (
    ActiveSpeakerSegment,
    FaceTrack,
    PerceptionMetadata,
    SceneCut,
    SourceVideoMetadata,
    SpeakerAttributedTranscript,
    WordTimestamp,
)
from clipping.contracts.qa import QACheckStatus, QAReport
from clipping.contracts.rendering import RenderOutput
from clipping.approval.models import ApprovalRequest, ApprovalStatus
from clipping.ingestion.base import VideoIngestor
from clipping.ingestion.source import SourceReference
from clipping.storage.base import StorageDriver
from clipping.vision.base import VirtualCameraDirector


class MockVideoIngestor(VideoIngestor):
    def __init__(self, storage: Optional[StorageDriver] = None):
        self.storage = storage

    async def ingest(
        self,
        source_ref: SourceReference,
        storage_driver: StorageDriver,
        source_video_id: str,
        force_reingest: bool = False,
    ) -> SourceVideoMetadata:
        meta = SourceVideoMetadata(
            video_id=source_video_id,
            title="Mock Video Title",
            duration_seconds=60.0,
            width=1920,
            height=1080,
            fps=30.0,
            source_url=source_ref.uri,
            master_video_storage_key=f"sources/{source_video_id}/master.mp4",
            audio_storage_key=f"sources/{source_video_id}/audio.wav",
        )
        await storage_driver.upload_bytes(
            b"fake_mp4_bytes",
            f"sources/{source_video_id}/master.mp4",
            content_type="video/mp4",
        )
        await storage_driver.upload_bytes(
            meta.model_dump_json(indent=2).encode("utf-8"),
            f"sources/{source_video_id}/metadata.json",
            content_type="application/json",
        )
        return meta

    async def extract_metadata(self, source_ref: SourceReference) -> SourceVideoMetadata:
        return SourceVideoMetadata(
            video_id="mock_id",
            title="Mock Video",
            duration_seconds=60.0,
            width=1920,
            height=1080,
            fps=30.0,
            source_url=source_ref.uri,
            master_video_storage_key="",
            audio_storage_key="",
        )


class MockAudioPerceptionEngine:
    async def process(
        self,
        source_video_id: str,
        storage_driver: StorageDriver,
        force_recompute: bool = False,
    ):
        words = [
            WordTimestamp(word="Welcome", start=1.0, end=1.5, probability=0.99, speaker_id="SPEAKER_00"),
            WordTimestamp(word="to", start=1.5, end=1.8, probability=0.99, speaker_id="SPEAKER_00"),
            WordTimestamp(word="the", start=1.8, end=2.0, probability=0.99, speaker_id="SPEAKER_00"),
            WordTimestamp(word="future.", start=2.0, end=2.8, probability=0.99, speaker_id="SPEAKER_00"),
        ]
        transcript = SpeakerAttributedTranscript(
            source_video_id=source_video_id,
            language="en",
            language_probability=0.99,
            text="Welcome to the future.",
            words=words,
            speaker_segments=[],
        )
        meta = PerceptionMetadata(
            source_video_id=source_video_id,
            asr_backend="mock",
            asr_model="mock",
            asr_device="cpu",
            asr_compute_type="int8",
            asr_vad_enabled=True,
            diarization_backend="mock",
            diarization_model="mock",
            detected_language="en",
            num_speakers=1,
            total_words=4,
            audio_duration_seconds=3.0,
            source_checksum="mock_sha",
            warnings=[],
        )
        await storage_driver.upload_bytes(
            transcript.model_dump_json(indent=2).encode("utf-8"),
            f"sources/{source_video_id}/speaker_transcript.json",
            content_type="application/json",
        )
        await storage_driver.upload_bytes(
            meta.model_dump_json(indent=2).encode("utf-8"),
            f"sources/{source_video_id}/perception_metadata.json",
            content_type="application/json",
        )
        return transcript, meta


class MockVideoUnderstandingEngine:
    async def process(
        self,
        source_video_id: str,
        storage_driver: StorageDriver,
        speaker_transcript: Optional[SpeakerAttributedTranscript] = None,
        force_recompute: bool = False,
    ):
        from pydantic import TypeAdapter
        scene_cuts = [SceneCut(scene_id=0, start_time=0.0, end_time=3.0, start_frame=0, end_frame=90)]
        face_tracks: List[FaceTrack] = []
        active_speakers = [
            ActiveSpeakerSegment(segment_id=0, speaker_id="SPEAKER_00", start_time=0.0, end_time=3.0, confidence=0.95)
        ]

        await storage_driver.upload_bytes(
            TypeAdapter(List[SceneCut]).dump_json(scene_cuts, indent=2),
            f"sources/{source_video_id}/scenes.json",
            content_type="application/json",
        )
        await storage_driver.upload_bytes(
            TypeAdapter(List[FaceTrack]).dump_json(face_tracks, indent=2),
            f"sources/{source_video_id}/face_tracks.json",
            content_type="application/json",
        )
        await storage_driver.upload_bytes(
            TypeAdapter(List[ActiveSpeakerSegment]).dump_json(active_speakers, indent=2),
            f"sources/{source_video_id}/active_speaker.json",
            content_type="application/json",
        )
        return scene_cuts, face_tracks, active_speakers


class MockClipDiscoveryEngine:
    def __init__(self, produce_candidates: bool = True):
        self.produce_candidates = produce_candidates

    async def process(
        self,
        source_video_id: str,
        storage_driver: StorageDriver,
        transcript: Optional[SpeakerAttributedTranscript] = None,
        campaign_id: str = "default_campaign",
        force_recompute: bool = False,
    ) -> ClipSelectionResult:
        if not self.produce_candidates:
            result = ClipSelectionResult(
                source_video_id=source_video_id,
                total_candidates_generated=0,
                quality_threshold=70.0,
                selected_clips=[],
            )
            await storage_driver.upload_bytes(
                result.model_dump_json(indent=2).encode("utf-8"),
                f"sources/{source_video_id}/selected_clips.json",
                content_type="application/json",
            )
            return result

        cand = ClipCandidate(
            candidate_id=f"clip_{source_video_id}_01",
            source_video_id=source_video_id,
            campaign_id=campaign_id,
            start_time=1.0,
            end_time=2.8,
            duration=1.8,
            transcript_text="Welcome to the future.",
            hook_sentence="Welcome to the future.",
            words=[
                WordTimestamp(word="Welcome", start=1.0, end=1.5, probability=0.99),
                WordTimestamp(word="to", start=1.5, end=1.8, probability=0.99),
                WordTimestamp(word="the", start=1.8, end=2.0, probability=0.99),
                WordTimestamp(word="future.", start=2.0, end=2.8, probability=0.99),
            ],
        )
        score = ClipScore(
            candidate_id=cand.candidate_id,
            hook_strength=95.0,
            narrative_completeness=90.0,
            curiosity_factor=85.0,
            campaign_relevance=100.0,
            overall_virality_score=92.0,
        )
        ranked = RankedCandidate(
            candidate=cand,
            score=score,
            rank=1,
            selection_reason="High hook score",
            is_selected=True,
        )
        result = ClipSelectionResult(
            source_video_id=source_video_id,
            total_candidates_generated=1,
            quality_threshold=70.0,
            selected_clips=[ranked],
        )
        await storage_driver.upload_bytes(
            result.model_dump_json(indent=2).encode("utf-8"),
            f"sources/{source_video_id}/selected_clips.json",
            content_type="application/json",
        )
        return result


class MockVirtualCameraDirector(VirtualCameraDirector):
    def generate_reframe_plan(
        self,
        clip_id: str,
        source_width: int,
        source_height: int,
        clip_start: float,
        clip_end: float,
        scene_cuts: List[SceneCut],
        face_tracks: List[FaceTrack],
        active_speakers: List[ActiveSpeakerSegment],
        speaker_transcript: Optional[SpeakerAttributedTranscript] = None,
    ) -> ReframePlan:
        return ReframePlan(
            clip_id=clip_id,
            source_width=source_width,
            source_height=source_height,
            target_width=1080,
            target_height=1920,
            keyframes=[
                ReframeCropKeyframe(timestamp=0.0, crop_x=656, crop_y=0, crop_w=608, crop_h=1080)
            ],
        )


class MockRenderOrchestrationEngine:
    async def render(
        self,
        clip_id: str,
        source_video_id: str,
        clip_start: float,
        clip_end: float,
        reframe_plan: ReframePlan,
        words: List[WordTimestamp],
        storage_driver: StorageDriver,
        style=None,
        force_recompute: bool = False,
    ) -> RenderOutput:
        final_video_key = f"clips/{clip_id}/final_1080x1920.mp4"
        await storage_driver.upload_bytes(b"mock_mp4_bytes", final_video_key, content_type="video/mp4")
        out = RenderOutput(
            clip_id=clip_id,
            output_storage_key=final_video_key,
            duration_seconds=round(clip_end - clip_start, 3),
            file_size_bytes=1024,
            render_time_seconds=0.1,
        )
        await storage_driver.upload_bytes(
            out.model_dump_json(indent=2).encode("utf-8"),
            f"clips/{clip_id}/render_output.json",
            content_type="application/json",
        )
        return out


class MockQAEngine:
    def __init__(self, can_publish: bool = True):
        self.can_publish = can_publish

    async def evaluate_rendered_clip(
        self,
        clip_id: str,
        source_video_id: str,
        storage_driver: StorageDriver,
        expected_duration: float,
        reframe_plan: Optional[ReframePlan] = None,
        selected_clip: Optional[RankedCandidate] = None,
    ) -> QAReport:
        report = QAReport(
            clip_id=clip_id,
            source_video_id=source_video_id,
            overall_status=QACheckStatus.PASS if self.can_publish else QACheckStatus.FAIL,
            can_publish=self.can_publish,
            checks=[],
            summary="Mock QA pass" if self.can_publish else "Mock QA fail",
        )
        await storage_driver.upload_bytes(
            report.model_dump_json(indent=2).encode("utf-8"),
            f"clips/{clip_id}/qa_report.json",
            content_type="application/json",
        )
        return report


class MockTelegramApprovalGateway:
    def __init__(self):
        self.dispatched_requests: List[ApprovalRequest] = []

    async def dispatch_candidate_clips(
        self,
        job_id: str,
        source_video_id: str,
        ranked_candidates: List[RankedCandidate],
        render_outputs: Dict[str, RenderOutput],
        chat_id: int,
    ) -> List[ApprovalRequest]:
        requests = []
        for idx, cand in enumerate(ranked_candidates, start=1):
            req = ApprovalRequest(
                approval_request_id=f"req_{cand.candidate.candidate_id[:16]}",
                job_id=job_id,
                source_video_id=source_video_id,
                clip_id=cand.candidate.candidate_id,
                clip_index=idx,
                title=cand.candidate.hook_sentence,
                hook_sentence=cand.candidate.hook_sentence,
                start_time=cand.candidate.start_time,
                end_time=cand.candidate.end_time,
                duration=cand.candidate.duration,
                score=cand.score.overall_virality_score,
                video_storage_key=render_outputs[cand.candidate.candidate_id].output_storage_key,
                status=ApprovalStatus.AWAITING_APPROVAL,
            )
            requests.append(req)
        self.dispatched_requests.extend(requests)
        return requests
