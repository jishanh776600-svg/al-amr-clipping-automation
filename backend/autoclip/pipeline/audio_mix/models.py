"""Data models and configuration for BGM mixing and ducking."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DuckingConfig:
    """Configuration parameters for speech-aware ducking and loudness control."""

    duck_attenuation_db: float = 16.0
    attack_ms: float = 150.0
    release_ms: float = 450.0
    threshold: float = 0.08
    ratio: float = 5.0
    speech_weight: float = 1.0
    bgm_weight: float = 0.40
    fade_in_s: float = 0.3
    fade_out_s: float = 0.5
    target_lufs: float = -14.0
    true_peak_limit: float = -1.5
    lra: float = 11.0
    limiter_limit: float = 0.95

    def to_dict(self) -> dict[str, Any]:
        return {
            "duck_attenuation_db": self.duck_attenuation_db,
            "attack_ms": self.attack_ms,
            "release_ms": self.release_ms,
            "threshold": self.threshold,
            "ratio": self.ratio,
            "speech_weight": self.speech_weight,
            "bgm_weight": self.bgm_weight,
            "fade_in_s": self.fade_in_s,
            "fade_out_s": self.fade_out_s,
            "target_lufs": self.target_lufs,
            "true_peak_limit": self.true_peak_limit,
            "lra": self.lra,
            "limiter_limit": self.limiter_limit,
        }


@dataclass
class AudioGateResult:
    """Outcome of evaluating mixed audio quality against compliance rules."""

    status: str = "MIX_PASS"  # "MIX_PASS", "MIX_WARN", "MIX_REJECT"
    quality_score: float = 100.0
    integrated_lufs: float = -14.0
    true_peak_db: float = -1.5
    duration_s: float = 0.0
    expected_duration_s: float = 0.0
    duration_diff_s: float = 0.0
    warnings: list[str] = field(default_factory=list)
    rejection_reasons: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
