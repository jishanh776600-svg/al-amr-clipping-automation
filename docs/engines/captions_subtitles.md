# Engine: Dynamic Captions & Subtitles

- **Source Files**: `backend/autoclip/pipeline/captions.py`, `backend/autoclip/pipeline/filters.py`.
- **Primary Classes / Functions**: `CaptionEngine`, `generate_ass_subtitles()`, `SubtitleStyleConfig`.
- **Purpose**: Generates dynamic, high-engagement animated subtitles burned directly into the vertical video.
- **Presets**: 25 visual presets including:
  - `beast_mode`: Bold yellow/cyan text with thick black outline.
  - `minimal_clean`: Subtle white typography with soft drop shadow.
  - `neon_cyber`: Glowing magenta/cyan animated text.
- **Features**:
  - Real-time karaoke word highlighting based on Faster-Whisper timestamps.
  - Semantic keyword highlighting (numbers, money, and key verbs colored distinctly).
- **Tests**: `tests/test_captions.py`, `tests/test_dynamic_captions.py`.
