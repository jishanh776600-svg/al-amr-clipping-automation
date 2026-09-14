"""BGM mixing package: speech-aware ducking, seamless looping, and audio normalization."""

from .engine import BGMMixingEngine
from .models import AudioGateResult, DuckingConfig
from .quality_gate import AudioQualityGate

__all__ = [
    "AudioGateResult",
    "AudioQualityGate",
    "BGMMixingEngine",
    "DuckingConfig",
]
