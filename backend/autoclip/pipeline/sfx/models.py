from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class SFXEvent:
    sound_type: str  # whoosh, pop, ding, boom, riser
    timestamp_s: float
    volume: float = 1.0
    reason: str = ""
    asset_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sound_type": self.sound_type,
            "timestamp_s": round(self.timestamp_s, 3),
            "volume": self.volume,
            "reason": self.reason,
            "asset_path": str(self.asset_path) if self.asset_path else None,
        }
