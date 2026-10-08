# Engine: Audio Mixing & Loudness Normalization

- **Source Files**: `backend/autoclip/pipeline/audio_mix/engine.py`, `backend/autoclip/pipeline/audio_mix/models.py`, `backend/autoclip/pipeline/audio_mix/quality_gate.py`.
- **Primary Classes / Functions**: `BGMMixingEngine`, `DuckingConfig`, `AudioQualityGate`.
- **Purpose**: Balances speech clarity over background music, ducks BGM during voice, enforces broadcast loudness (-14 LUFS), and prevents premature audio cutoff.
- **Inputs**: Speech audio file (`speech.mp4` / `wav`), `BGMAssetRecord`, duration in seconds.
- **Outputs**: Mixed AAC audio stream (`mixed.aac` or `wav`), `BGMMixRecord`, `AudioGateResult`.
- **Execution Logic**:
  1. **Safe Seek Margin**: `-ss {start} -t {duration_s + 1.0}` ensures keyframe rounding never starves end-of-clip audio packets.
  2. **Speech Padding (`apad`)**: `[speech_raw]apad=whole_dur={duration_s:.3f}[speech_main]` guarantees speech input never runs out before the video completes.
  3. **Sidechain Ducking**: Sidechain compressor attenuates BGM by -18 dB during speech (attack: 100ms, release: 350ms).
  4. **amix Clamping**: `amix=inputs=2:duration=first:dropout_transition=0` mixed with `atrim=0:{duration_s:.3f}` ensures zero dead container air.
  5. **Audio Quality Gate**: Asserts integrated loudness (-14.0 LUFS $\pm 1.5$) and true peak limit ($\le -1.5$ dBTP).
- **Tests**: `tests/test_bgm_mixing.py`, `tests/test_final_two_blockers_fix.py`.
