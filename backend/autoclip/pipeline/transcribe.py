"""Transcribe stage — audio to word-level timestamps.

faster-whisper does the transcription; WhisperX (optional) adds speaker labels.
Word-level timing is non-negotiable: clip boundaries, caption animation, and
trim-handle snapping all derive from it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from .. import paths
from ..config import WhisperSettings
from ..system import GPUInfo, report
from .transcript import Segment, Transcript, Word

log = logging.getLogger(__name__)


class TranscriptionError(RuntimeError):
    """Transcription could not be completed."""


def resolve_compute(settings: WhisperSettings, gpu: GPUInfo | None = None) -> tuple[str, str]:
    """Return the ``(device, compute_type)`` to run Whisper with.

    An explicit ``settings.compute_type`` wins, but only if the device actually
    supports it. An override the backend will reject fails at model load with a
    message that points at cuDNN rather than at the setting, so an unsupported
    override is warned about and ignored rather than honoured into a crash.
    """
    gpu = gpu if gpu is not None else report().gpu
    automatic = gpu.compute_type

    override = settings.compute_type
    if not override:
        return gpu.device, automatic

    supported = gpu.supported_compute_types
    if supported and override not in supported:
        log.warning(
            "whisper.compute_type is set to %r, which %s does not support "
            "(supported: %s). Using %r instead.",
            override,
            gpu.device,
            ", ".join(sorted(supported)),
            automatic,
        )
        return gpu.device, automatic

    return gpu.device, override


def _model_load_error(exc: Exception, device: str, compute_type: str) -> TranscriptionError:
    """Turn a Whisper model-load failure into something actionable.

    The two causes look identical from the outside and need opposite fixes, so
    they're distinguished rather than lumped under one guess.
    """
    message = str(exc)
    lowered = message.lower()

    if "compute type" in lowered or "not support" in lowered:
        supported = report().gpu.supported_compute_types
        options = ", ".join(sorted(supported)) if supported else "unknown"
        return TranscriptionError(
            f"This device does not support the '{compute_type}' compute type.\n"
            f"Supported on {device}: {options}.\n\n"
            "Clear whisper.compute_type in config.json to let AutoClip choose, "
            "or set it to one of the supported values."
        )

    if device == "cuda" and ("cudnn" in lowered or "library" in lowered or "dll" in lowered):
        return TranscriptionError(
            f"Could not load Whisper on the GPU: {message}\n\n"
            "This is a missing CUDA runtime library. Install it with "
            "`uv pip install 'autoclip[gpu]'`, then retry — the job resumes "
            "from this stage."
        )

    return TranscriptionError(f"Could not load the Whisper model: {message}")


def transcribe(
    audio: Path,
    settings: WhisperSettings | None = None,
    *,
    duration_s: float | None = None,
    on_progress: Callable[[float], None] | None = None,
    on_status: Callable[[str, float], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> Transcript:
    """Transcribe an audio file into a :class:`Transcript` with word timings.

    Preconditions:
        audio is a decodable audio file, ideally 16 kHz mono as produced by
        :func:`autoclip.pipeline.prepare.extract_audio`.
    """
    settings = settings or WhisperSettings()

    if on_status:
        on_status("Preparing speech transcription engine...", 0.01)

    # Must happen before faster-whisper pulls in CTranslate2, which resolves its
    # CUDA dependencies at import.
    from ..cuda import ensure_cuda_libraries

    ensure_cuda_libraries()

    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover - faster-whisper is a core dep
        raise TranscriptionError(
            "faster-whisper is not installed. Run `autoclip doctor` for details."
        ) from exc

    device, compute_type = resolve_compute(settings)

    import os
    # Tune CPU threads for available cores to avoid OpenMP over-subscription
    if device == "cpu":
        avail_cpus = os.cpu_count() or 2
        cpu_threads = min(avail_cpus, 2)
    else:
        cpu_threads = 4

    model = None
    candidate_models = [settings.model]
    if settings.model not in ("base", "tiny"):
        candidate_models.extend(["base", "tiny"])

    last_load_exc: Exception | None = None
    whisper_cache_dir = paths.models_dir() / "whisper"
    whisper_cache_dir.mkdir(parents=True, exist_ok=True)

    for cand in candidate_models:
        try:
            log.info(
                "Loading Whisper model=%s device=%s compute_type=%s cpu_threads=%d",
                cand,
                device,
                compute_type,
                cpu_threads,
            )
            if on_status:
                on_status(f"Loading Whisper {cand} model...", 0.03)
            model = WhisperModel(
                cand,
                device=device,
                compute_type=compute_type,
                cpu_threads=cpu_threads,
                num_workers=1,
                download_root=str(whisper_cache_dir),
            )
            settings.model = cand
            break
        except Exception as exc:
            err_msg = str(exc).lower()
            last_load_exc = exc
            if any(term in err_msg for term in ["memory", "alloc", "oom", "resource", "out of memory"]):
                log.warning("Whisper model %s failed with memory error (%s). Trying fallback candidate...", cand, exc)
                continue
            if cand != candidate_models[-1]:
                log.warning("Whisper model %s failed (%s). Retrying with fallback model...", cand, exc)
                continue
            break

    if model is None:
        raise _model_load_error(last_load_exc or RuntimeError("Failed to load Whisper model"), device, compute_type) from last_load_exc

    if on_status:
        on_status("Detecting voice activity (VAD) & audio features...", 0.06)

    segments_iter, info = model.transcribe(
        str(audio),
        language=settings.language or None,
        word_timestamps=True,
        vad_filter=True,
        # Trims long silences before decoding, which both speeds things up and
        # stops Whisper hallucinating text into empty audio.
        vad_parameters={"min_silence_duration_ms": 500},
        condition_on_previous_text=False,
    )

    if on_status:
        on_status("Transcribing initial speech segment...", 0.09)

    total = duration_s or getattr(info, "duration", 0.0) or 0.0
    transcript = Transcript(
        language=getattr(info, "language", "") or settings.language,
        model=settings.model,
        source="whisper",
    )

    import time
    start_time = time.monotonic()
    max_duration_s = max(300.0, (duration_s or 60.0) * 6.0)

    for segment in segments_iter:
        if cancelled is not None and cancelled():
            raise TranscriptionError("Transcription cancelled.")

        if time.monotonic() - start_time > max_duration_s:
            log.warning(
                "Transcription reached bounded timeout (%.1fs). Terminating segment iteration early.",
                max_duration_s,
            )
            break

        first_word = len(transcript.words)
        for word in getattr(segment, "words", None) or []:
            text = (word.word or "").strip()
            if not text:
                continue
            transcript.words.append(Word(text=text, start=float(word.start), end=float(word.end)))

        # A segment with no word timings still carries text worth keeping for
        # display, but it can't contribute to word-indexed clip boundaries.
        last_word = max(first_word, len(transcript.words) - 1)
        transcript.segments.append(
            Segment(
                text=(segment.text or "").strip(),
                start=float(segment.start),
                end=float(segment.end),
                first_word=first_word,
                last_word=last_word,
            )
        )

        if on_progress and total:
            on_progress(min(1.0, float(segment.end) / total))

    if not transcript.words:
        raise TranscriptionError(
            "No speech was detected in this audio. If the file really does contain "
            "speech, try a larger Whisper model or check that the correct audio track "
            "was extracted."
        )

    if on_progress:
        on_progress(1.0)

    log.info(
        "Transcribed %d words in %d segments.", len(transcript.words), len(transcript.segments)
    )
    return transcript


# --------------------------------------------------------------------------
# Diarization
# --------------------------------------------------------------------------


def diarize(
    audio: Path,
    transcript: Transcript,
    *,
    hf_token: str | None = None,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
) -> Transcript:
    """Label each word with a speaker, in place.

    Uses pyannote via WhisperX. Returns the transcript unchanged (with a logged
    warning) if diarization isn't available — speaker labels improve reframing
    but are never required for the pipeline to complete.

    Preconditions:
        transcript already has word-level timings.
    """
    if not transcript.words:
        return transcript

    pipeline_cls = _load_diarization_pipeline()
    if pipeline_cls is None:
        log.warning(
            "WhisperX is not installed; skipping diarization. "
            "Install it with `uv pip install 'autoclip[diarization]'`."
        )
        return transcript

    if not hf_token:
        log.warning(
            "Diarization needs a HuggingFace token. Set one with "
            "`autoclip config set-secret huggingface_token`, and accept the pyannote "
            "model licences on huggingface.co. Continuing without speaker labels."
        )
        return transcript

    device = report().gpu.device
    try:
        pipeline = pipeline_cls(use_auth_token=hf_token, device=device)
        diarization = pipeline(str(audio), min_speakers=min_speakers, max_speakers=max_speakers)
    except Exception as exc:
        log.warning("Diarization failed (%s); continuing without speaker labels.", exc)
        return transcript

    turns = _diarization_turns(diarization)
    if not turns:
        return transcript

    _assign_speakers(transcript, turns)
    log.info("Diarization labelled %d speakers.", len(transcript.speakers))
    return transcript


def _load_diarization_pipeline():
    """Import WhisperX's diarization pipeline across its API reshuffles."""
    try:
        from whisperx.diarize import DiarizationPipeline

        return DiarizationPipeline
    except ImportError:
        pass
    try:
        from whisperx import DiarizationPipeline  # type: ignore[attr-defined]

        return DiarizationPipeline
    except (ImportError, AttributeError):
        return None


def _diarization_turns(diarization) -> list[tuple[str, float, float]]:
    """Normalise WhisperX output into ``(speaker, start, end)`` tuples.

    Recent versions return a pandas DataFrame; older ones return a pyannote
    ``Annotation``. Both are handled so the extra can be upgraded independently.
    """
    turns: list[tuple[str, float, float]] = []

    if hasattr(diarization, "itertuples"):  # DataFrame
        for row in diarization.itertuples():
            speaker = getattr(row, "speaker", None)
            start = getattr(row, "start", None)
            end = getattr(row, "end", None)
            if speaker is not None and start is not None and end is not None:
                turns.append((str(speaker), float(start), float(end)))
        return turns

    if hasattr(diarization, "itertracks"):  # pyannote Annotation
        for segment, _, speaker in diarization.itertracks(yield_label=True):
            turns.append((str(speaker), float(segment.start), float(segment.end)))

    return turns


def _assign_speakers(transcript: Transcript, turns: list[tuple[str, float, float]]) -> None:
    """Attach a speaker to every word by maximum temporal overlap.

    Overlap rather than midpoint containment: words straddling a turn boundary
    are common, and the speaker who covers more of the word is the better guess.
    """
    turns = sorted(turns, key=lambda t: t[1])

    for word in transcript.words:
        best_speaker: str | None = None
        best_overlap = 0.0
        for speaker, start, end in turns:
            if start > word.end:
                break
            overlap = min(word.end, end) - max(word.start, start)
            if overlap > best_overlap:
                best_overlap = overlap
                best_speaker = speaker
        word.speaker = best_speaker

    for segment in transcript.segments:
        segment.speaker = transcript.dominant_speaker(segment.first_word, segment.last_word)
