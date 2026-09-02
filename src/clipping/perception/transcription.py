"""Speech-to-Text Transcription Engine using faster-whisper."""

import os
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from clipping.contracts.perception import RawTranscript, WordTimestamp
from clipping.logging.logger import get_logger

logger = get_logger("clipping.perception.transcription")


class TranscriptionEngine(ABC):
    """Abstract interface for speech transcription."""

    backend_name: str = "transcription_engine"

    @abstractmethod
    async def transcribe(self, audio_file_path: str, source_video_id: str) -> RawTranscript:
        """Extracts word-level timestamps and speech text from an audio file."""
        pass


class FasterWhisperTranscriptionEngine(TranscriptionEngine):
    """Production faster-whisper ASR implementation with CTranslate2 backend."""

    backend_name: str = "faster_whisper"

    def __init__(
        self,
        model_size: str = "base",
        device: str = "auto",
        compute_type: str = "auto",
        language: Optional[str] = None,
        beam_size: int = 5,
        vad_enabled: bool = True,
        whisper_model: Optional[Any] = None,
    ):
        self.model_size = model_size
        self.device = self._resolve_device(device)
        self.compute_type = self._resolve_compute_type(compute_type, self.device)
        self.language = language
        self.beam_size = beam_size
        self.vad_enabled = vad_enabled
        self._model = whisper_model

    def _resolve_device(self, device: str) -> str:
        if device == "auto":
            try:
                import torch
                return "cuda" if torch.cuda.is_available() else "cpu"
            except ImportError:
                return "cpu"
        return device

    def _resolve_compute_type(self, compute_type: str, device: str) -> str:
        if compute_type == "auto":
            return "float16" if device == "cuda" else "int8"
        return compute_type

    @property
    def model(self) -> Any:
        if self._model is None:
            from faster_whisper import WhisperModel
            logger.info(
                "Initializing faster-whisper model",
                model_size=self.model_size,
                device=self.device,
                compute_type=self.compute_type,
            )
            self._model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
            )
        return self._model

    async def transcribe(self, audio_file_path: str, source_video_id: str) -> RawTranscript:
        if not os.path.isfile(audio_file_path):
            raise FileNotFoundError(f"Audio file not found for transcription: {audio_file_path}")

        vad_params = dict(min_silence_duration_ms=500, threshold=0.5) if self.vad_enabled else None

        segments_generator, info = self.model.transcribe(
            audio_file_path,
            beam_size=self.beam_size,
            language=self.language,
            vad_filter=self.vad_enabled,
            vad_parameters=vad_params,
            word_timestamps=True,
        )

        all_words: List[WordTimestamp] = []
        transcript_text_parts: List[str] = []

        for segment in segments_generator:
            transcript_text_parts.append(segment.text.strip())
            if segment.words:
                for w in segment.words:
                    word_clean = w.word.strip()
                    if not word_clean:
                        continue
                    # Validate timestamp bounds
                    start_t = max(0.0, float(w.start))
                    end_t = max(start_t + 0.01, float(w.end))
                    prob = max(0.0, min(1.0, float(w.probability if hasattr(w, "probability") else 0.95)))

                    all_words.append(
                        WordTimestamp(
                            word=word_clean,
                            start=start_t,
                            end=end_t,
                            probability=prob,
                            speaker_id=None,
                        )
                    )

        full_text = " ".join(transcript_text_parts).strip()
        detected_lang = getattr(info, "language", None)
        lang_prob = float(getattr(info, "language_probability", 1.0) or 1.0)

        logger.info(
            "Transcription completed",
            source_video_id=source_video_id,
            total_words=len(all_words),
            detected_language=detected_lang,
        )

        return RawTranscript(
            source_video_id=source_video_id,
            language=detected_lang,
            language_probability=lang_prob,
            text=full_text,
            words=all_words,
        )
