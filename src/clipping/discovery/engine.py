"""Clip Discovery Orchestration Engine."""

from typing import List, Optional
from pydantic import TypeAdapter
from clipping.contracts.clip import (
    ClipCandidate,
    RankedCandidate,
    ClipSelectionResult,
)
from clipping.contracts.perception import (
    ActiveSpeakerSegment,
    SceneCut,
    SpeakerAttributedTranscript,
)
from clipping.discovery.config import ClipDiscoveryConfig
from clipping.discovery.windows import CandidateWindowGenerator
from clipping.discovery.scoring import DeterministicClipScorer
from clipping.discovery.dedup import CandidateDeduplicator
from clipping.discovery.selection import ClipSelector
from clipping.storage.base import StorageDriver
from clipping.logging.logger import get_logger

logger = get_logger("clipping.discovery.engine")

_candidate_list_adapter = TypeAdapter(List[ClipCandidate])
_ranked_candidate_list_adapter = TypeAdapter(List[RankedCandidate])
_scene_list_adapter = TypeAdapter(List[SceneCut])
_active_speaker_list_adapter = TypeAdapter(List[ActiveSpeakerSegment])


class ClipDiscoveryEngine:
    """
    Orchestrates candidate generation, deterministic heuristic scoring,
    deduplication, and yield selection (5-10 clips).
    """

    def __init__(
        self,
        config: Optional[ClipDiscoveryConfig] = None,
        window_generator: Optional[CandidateWindowGenerator] = None,
        scorer: Optional[DeterministicClipScorer] = None,
        deduplicator: Optional[CandidateDeduplicator] = None,
        selector: Optional[ClipSelector] = None,
    ):
        self.config = config or ClipDiscoveryConfig()
        self.window_generator = window_generator or CandidateWindowGenerator(self.config)
        self.scorer = scorer or DeterministicClipScorer(self.config)
        self.deduplicator = deduplicator or CandidateDeduplicator(self.config)
        self.selector = selector or ClipSelector(self.config)

    async def process(
        self,
        source_video_id: str,
        storage_driver: StorageDriver,
        transcript: Optional[SpeakerAttributedTranscript] = None,
        campaign_id: str = "default_campaign",
        force_recompute: bool = False,
    ) -> ClipSelectionResult:
        candidates_key = f"sources/{source_video_id}/candidates.json"
        ranked_key = f"sources/{source_video_id}/ranked_candidates.json"
        selected_key = f"sources/{source_video_id}/selected_clips.json"

        # 1. Idempotency Gate
        if not force_recompute and await storage_driver.exists(selected_key):
            logger.info("Selected clips artifact already exists, skipping discovery", source_video_id=source_video_id)
            selected_bytes = await storage_driver.download_bytes(selected_key)
            return ClipSelectionResult.model_validate_json(selected_bytes.decode("utf-8"))

        # 2. Retrieve Perception & Vision Artifacts from Storage
        if transcript is None:
            transcript_key = f"sources/{source_video_id}/speaker_transcript.json"
            if not await storage_driver.exists(transcript_key):
                raise FileNotFoundError(f"Speaker transcript not found in storage for {source_video_id}")
            transcript_bytes = await storage_driver.download_bytes(transcript_key)
            transcript = SpeakerAttributedTranscript.model_validate_json(transcript_bytes.decode("utf-8"))

        scene_cuts: List[SceneCut] = []
        scenes_key = f"sources/{source_video_id}/scenes.json"
        if await storage_driver.exists(scenes_key):
            scenes_bytes = await storage_driver.download_bytes(scenes_key)
            scene_cuts = _scene_list_adapter.validate_json(scenes_bytes.decode("utf-8"))

        active_speakers: List[ActiveSpeakerSegment] = []
        active_key = f"sources/{source_video_id}/active_speaker.json"
        if await storage_driver.exists(active_key):
            active_bytes = await storage_driver.download_bytes(active_key)
            active_speakers = _active_speaker_list_adapter.validate_json(active_bytes.decode("utf-8"))

        # 3. Step A: Candidate Window Generation
        candidates = self.window_generator.generate_windows(
            transcript=transcript,
            scene_cuts=scene_cuts,
            campaign_id=campaign_id,
        )

        # Upload raw candidates to storage
        await storage_driver.upload_bytes(
            data=_candidate_list_adapter.dump_json(candidates, indent=2),
            storage_key=candidates_key,
            content_type="application/json",
        )

        if not candidates:
            logger.warning("No candidate windows discovered from transcript", source_video_id=source_video_id)
            empty_result = ClipSelectionResult(
                source_video_id=source_video_id,
                total_candidates_generated=0,
                quality_threshold=self.config.quality_threshold,
                insufficient_candidate_warning=True,
            )
            await storage_driver.upload_bytes(
                data=empty_result.model_dump_json(indent=2).encode("utf-8"),
                storage_key=selected_key,
                content_type="application/json",
            )
            return empty_result

        # 4. Step B: Candidate Scoring
        scored_pairs = [
            (c, self.scorer.score_candidate(c, active_speakers=active_speakers))
            for c in candidates
        ]

        # 5. Step C: Overlap & Redundancy Deduplication
        deduplicated_pairs = self.deduplicator.deduplicate(scored_pairs)

        # 6. Step D: Clip Ranking & Diversity Selection
        selection_result = self.selector.select_clips(
            source_video_id=source_video_id,
            deduplicated_candidates=deduplicated_pairs,
        )

        # 7. Step E: Upload Canonical Artifacts to Storage Vault
        all_ranked = selection_result.selected_clips + selection_result.rejected_clips
        await storage_driver.upload_bytes(
            data=_ranked_candidate_list_adapter.dump_json(all_ranked, indent=2),
            storage_key=ranked_key,
            content_type="application/json",
        )

        await storage_driver.upload_bytes(
            data=selection_result.model_dump_json(indent=2).encode("utf-8"),
            storage_key=selected_key,
            content_type="application/json",
        )

        logger.info(
            "Clip discovery and selection completed",
            source_video_id=source_video_id,
            total_generated=len(candidates),
            selected_count=len(selection_result.selected_clips),
            quality_threshold=self.config.quality_threshold,
        )

        return selection_result
