# Engine: Multi-Stage Quality Gates

- **Source Files**: `backend/autoclip/pipeline/validator.py`, `backend/autoclip/pipeline/audio_mix/quality_gate.py`, `backend/autoclip/pipeline/final_render/quality_gate.py`, `backend/autoclip/seo/quality_gate.py`.
- **Quality Gates Evaluated**:
  1. **Acquisition Gate**: Validates video resolution $\ge 720p$ and non-corrupt container.
  2. **Highlight Gate**: Enforces exactly 5 valid candidates within [20s, 30s] duration.
  3. **Audio Gate**: Evaluates integrated loudness (-14 LUFS $\pm 1.5$) and audio stream duration match.
  4. **Render Gate**: Accepts `RENDER_PASS` and `RENDER_WARN` (advisory warnings permitted; fatal corruptions blocked).
  5. **SEO Gate**: Checks title length, hook appeal, and minimum 3 relevant hashtags.
- **Tests**: `tests/test_final_render.py`, `tests/test_render_warn_gate_regression.py`.
