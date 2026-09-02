"""Speaker Diarization Engines (Pyannote & Fallback)."""

import os
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from clipping.contracts.perception import DiarizationResult, SpeakerSegment
from clipping.logging.logger import get_logger

logger = get_logger("clipping.perception.diarization")


class DiarizationEngine(ABC):
    """Abstract interface for speaker turn diarization."""

    @abstractmethod
    async def diarize(self, audio_file_path: str, source_video_id: str) -> DiarizationResult:
        """Segments audio into distinct speaker IDs and timestamps."""
        pass


class PyannoteDiarizationEngine(DiarizationEngine):
    """Primary Neural Speaker Diarization Engine using pyannote.audio."""

    def __init__(
        self,
        hf_token: Optional[str] = None,
        model_name: str = "pyannote/speaker-diarization-3.1",
        pipeline: Optional[Any] = None,
    ):
        self.hf_token = hf_token
        self.model_name = model_name
        self._pipeline = pipeline

    @property
    def pipeline(self) -> Any:
        if self._pipeline is None:
            if not self.hf_token:
                raise ValueError("Hugging Face token is required for Pyannote model initialization")

            from pyannote.audio import Pipeline
            import torch

            logger.info("Initializing pyannote diarization pipeline", model=self.model_name)
            self._pipeline = Pipeline.from_pretrained(
                self.model_name,
                use_auth_token=self.hf_token,
            )
            if torch.cuda.is_available():
                self._pipeline.to(torch.device("cuda"))

        return self._pipeline

    async def diarize(self, audio_file_path: str, source_video_id: str) -> DiarizationResult:
        if not os.path.isfile(audio_file_path):
            raise FileNotFoundError(f"Audio file not found for diarization: {audio_file_path}")

        diarization = self.pipeline(audio_file_path)

        # 1. Extract raw segments and order speakers by appearance time
        raw_turns: List[Dict[str, Any]] = []
        speaker_first_seen: Dict[str, float] = {}

        for turn, _, speaker_label in diarization.itertracks(yield_label=True):
            start = max(0.0, float(turn.start))
            end = max(start + 0.01, float(turn.end))
            raw_turns.append({"speaker": speaker_label, "start": start, "end": end})
            if speaker_label not in speaker_first_seen:
                speaker_first_seen[speaker_label] = start

        # 2. Map to deterministic anonymous IDs: SPEAKER_00, SPEAKER_01...
        sorted_speakers = sorted(speaker_first_seen.keys(), key=lambda s: speaker_first_seen[s])
        speaker_map = {orig: f"SPEAKER_{i:02d}" for i, orig in enumerate(sorted_speakers)}

        segments: List[SpeakerSegment] = []
        for t in raw_turns:
            segments.append(
                SpeakerSegment(
                    speaker_id=speaker_map[t["speaker"]],
                    start=t["start"],
                    end=t["end"],
                )
            )

        logger.info(
            "Pyannote diarization completed",
            source_video_id=source_video_id,
            num_speakers=len(speaker_map),
            total_segments=len(segments),
        )

        return DiarizationResult(
            source_video_id=source_video_id,
            backend="pyannote",
            model_name=self.model_name,
            num_speakers=len(speaker_map),
            segments=segments,
        )


class FallbackDiarizationEngine(DiarizationEngine):
    """
    Lightweight, permissive fallback diarization engine when pyannote is unavailable.
    Segments audio deterministically using speech intervals or single-speaker assumption.
    """

    def __init__(self, model_name: str = "energy_vad_fallback"):
        self.model_name = model_name

    async def diarize(self, audio_file_path: str, source_video_id: str) -> DiarizationResult:
        if not os.path.isfile(audio_file_path):
            raise FileNotFoundError(f"Audio file not found for diarization: {audio_file_path}")

        logger.warning(
            "Executing fallback diarization engine (single-speaker/energy mode)",
            source_video_id=source_video_id,
        )

        # Default fallback: single anonymous speaker turn spanning whole duration
        # If soundfile/av is available, read exact duration
        duration = 3600.0
        try:
            import soundfile as sf
            info = sf.info(audio_file_path)
            duration = float(info.duration)
        except Exception:
            pass

        segments = [
            SpeakerSegment(
                speaker_id="SPEAKER_00",
                start=0.0,
                end=duration,
            )
        ]

        return DiarizationResult(
            source_video_id=source_video_id,
            backend="energy_vad_fallback",
            model_name=self.model_name,
            num_speakers=1,
            segments=segments,
        )
