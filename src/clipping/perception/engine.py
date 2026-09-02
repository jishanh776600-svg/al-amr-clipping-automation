"""End-to-End Audio Perception Orchestration Engine."""

import os
import tempfile
from typing import Optional, Tuple
from clipping.contracts.perception import (
    RawTranscript,
    DiarizationResult,
    SpeakerAttributedTranscript,
    PerceptionMetadata,
)
from clipping.perception.transcription import (
    TranscriptionEngine,
    FasterWhisperTranscriptionEngine,
)
from clipping.perception.diarization import (
    DiarizationEngine,
    PyannoteDiarizationEngine,
    FallbackDiarizationEngine,
)
from clipping.perception.alignment import (
    AlignmentEngine,
    TemporalAttributionEngine,
)
from clipping.storage.base import StorageDriver
from clipping.storage.keys import StorageKeyBuilder
from clipping.logging.logger import get_logger

logger = get_logger("clipping.perception.engine")


class AudioPerceptionEngine:
    """
    Orchestrates speech transcription, diarization, alignment, and artifact persistence.
    Operates headlessly and independently of local client devices.
    """

    def __init__(
        self,
        transcription_engine: Optional[TranscriptionEngine] = None,
        diarization_engine: Optional[DiarizationEngine] = None,
        alignment_engine: Optional[AlignmentEngine] = None,
    ):
        self.transcription_engine = transcription_engine or FasterWhisperTranscriptionEngine()
        self.diarization_engine = diarization_engine or FallbackDiarizationEngine()
        self.alignment_engine = alignment_engine or TemporalAttributionEngine()

    async def process(
        self,
        source_video_id: str,
        storage_driver: StorageDriver,
        force_recompute: bool = False,
    ) -> Tuple[SpeakerAttributedTranscript, PerceptionMetadata]:
        raw_key = f"sources/{source_video_id}/transcript_raw.json"
        diar_key = f"sources/{source_video_id}/diarization.json"
        speaker_key = f"sources/{source_video_id}/speaker_transcript.json"
        meta_key = f"sources/{source_video_id}/perception_metadata.json"

        # 1. Idempotency Check
        if not force_recompute and (
            await storage_driver.exists(raw_key)
            and await storage_driver.exists(diar_key)
            and await storage_driver.exists(speaker_key)
            and await storage_driver.exists(meta_key)
        ):
            logger.info("Perception artifacts already exist, skipping inference", source_video_id=source_video_id)
            speaker_bytes = await storage_driver.download_bytes(speaker_key)
            meta_bytes = await storage_driver.download_bytes(meta_key)
            return (
                SpeakerAttributedTranscript.model_validate_json(speaker_bytes.decode("utf-8")),
                PerceptionMetadata.model_validate_json(meta_bytes.decode("utf-8")),
            )

        # 2. Ephemeral Audio Extraction
        audio_storage_key = StorageKeyBuilder.source_audio_wav(source_video_id)
        master_storage_key = StorageKeyBuilder.source_master_video(source_video_id)

        target_key = audio_storage_key if await storage_driver.exists(audio_storage_key) else master_storage_key
        if not await storage_driver.exists(target_key):
            raise FileNotFoundError(f"Neither audio nor master video found in storage for {source_video_id}")

        source_checksum = await storage_driver.checksum(target_key)
        warnings = []

        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_audio_path = os.path.join(tmp_dir, "audio_stream.tmp")
            logger.info("Retrieving media for perception inference", source_key=target_key)
            await storage_driver.download(target_key, temp_audio_path)

            # 3. Step A: Speech-to-Text Transcription
            raw_transcript = await self.transcription_engine.transcribe(
                audio_file_path=temp_audio_path,
                source_video_id=source_video_id,
            )

            # 4. Step B: Speaker Diarization (with graceful fallback)
            try:
                diarization_result = await self.diarization_engine.diarize(
                    audio_file_path=temp_audio_path,
                    source_video_id=source_video_id,
                )
            except Exception as e:
                logger.warning(
                    "Primary diarization engine failed, executing fallback",
                    error=str(e),
                    source_video_id=source_video_id,
                )
                warnings.append(f"Primary diarization failed: {e}. Executed FallbackDiarizationEngine.")
                fallback = FallbackDiarizationEngine()
                diarization_result = await fallback.diarize(temp_audio_path, source_video_id)

            # 5. Step C: Alignment & Speaker Attribution
            speaker_transcript = self.alignment_engine.align(
                raw_transcript=raw_transcript,
                diarization=diarization_result,
            )

            # 6. Step D: Metadata Compilation
            audio_duration = 0.0
            if raw_transcript.words:
                audio_duration = raw_transcript.words[-1].end
            elif diarization_result.segments:
                audio_duration = diarization_result.segments[-1].end

            asr_backend = (
                self.transcription_engine.backend_name
                if hasattr(self.transcription_engine, "backend_name")
                and isinstance(self.transcription_engine.backend_name, str)
                else "faster_whisper"
            )
            asr_model = (
                self.transcription_engine.model_size
                if hasattr(self.transcription_engine, "model_size")
                and isinstance(self.transcription_engine.model_size, str)
                else "base"
            )
            asr_device = (
                self.transcription_engine.device
                if hasattr(self.transcription_engine, "device")
                and isinstance(self.transcription_engine.device, str)
                else "cpu"
            )
            asr_compute_type = (
                self.transcription_engine.compute_type
                if hasattr(self.transcription_engine, "compute_type")
                and isinstance(self.transcription_engine.compute_type, str)
                else "int8"
            )
            asr_vad = (
                self.transcription_engine.vad_enabled
                if hasattr(self.transcription_engine, "vad_enabled")
                and isinstance(self.transcription_engine.vad_enabled, bool)
                else True
            )

            metadata = PerceptionMetadata(
                source_video_id=source_video_id,
                asr_backend=asr_backend,
                asr_model=asr_model,
                asr_device=asr_device,
                asr_compute_type=asr_compute_type,
                asr_vad_enabled=asr_vad,
                diarization_backend=diarization_result.backend,
                diarization_model=diarization_result.model_name,
                detected_language=raw_transcript.language,
                num_speakers=diarization_result.num_speakers,
                total_words=len(speaker_transcript.words),
                audio_duration_seconds=audio_duration,
                source_checksum=source_checksum,
                warnings=warnings,
            )

            # 7. Step E: Persist All 4 Canonical Artifacts to Storage Vault
            logger.info("Persisting perception artifacts to storage vault", source_video_id=source_video_id)

            await storage_driver.upload_bytes(
                data=raw_transcript.model_dump_json(indent=2).encode("utf-8"),
                storage_key=raw_key,
                content_type="application/json",
            )
            await storage_driver.upload_bytes(
                data=diarization_result.model_dump_json(indent=2).encode("utf-8"),
                storage_key=diar_key,
                content_type="application/json",
            )
            await storage_driver.upload_bytes(
                data=speaker_transcript.model_dump_json(indent=2).encode("utf-8"),
                storage_key=speaker_key,
                content_type="application/json",
            )
            await storage_driver.upload_bytes(
                data=metadata.model_dump_json(indent=2).encode("utf-8"),
                storage_key=meta_key,
                content_type="application/json",
            )

            return speaker_transcript, metadata
