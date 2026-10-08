# Engine: Final Rendering & Video Packaging

- **Source Files**: `backend/autoclip/pipeline/final_render/engine.py`, `backend/autoclip/pipeline/export.py`, `backend/autoclip/pipeline/ffmpeg.py`.
- **Primary Classes / Functions**: `FinalRenderEngine`, `build_export_command()`, `run_ffmpeg()`.
- **Purpose**: Assembles all visual and acoustic layers into a compliant 1080x1920 MP4 vertical short video.
- **Filtergraph Architecture**:
  - `[0:v]` A-roll video cropped/scaled to 1080x1920.
  - `[1:v]` B-roll overlay cutaways layered via `overlay` filter with precise `enable='between(t, st, et)'`.
  - `[2:s]` ASS dynamic animated subtitle stream burned in via `subtitles` filter.
  - `[a]` Mixed AAC audio track mapped to output.
- **Encoder Settings**: `libx264`, preset `medium` (or `veryfast` on worker), CRF 20, pixel format `yuv420p`, AAC 192 kbps at 48 kHz.
- **Tests**: `tests/test_final_render.py`, `tests/test_export_render.py`.
