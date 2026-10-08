# AL AMR — Complete Git Changelog

This changelog records all milestone commits across the project history:

### Latest Working Tree State (2026-09-20)
- **Problem #1 Resolution**: Fixed audio end-of-clip cutoff / mute elimination via safe seek margin (`+1.0s`), `apad` speech padding, zero-dropout `amix`, and natural speech decay tail ($\ge 0.35$s).
- **Problem #2 Resolution**: Fixed Telegram review inline buttons hanging via immediate `answerCallbackQuery` acknowledgment, decoupled remote clip reconciliation from Telegram card text, and direct Telegram media download fallback.
- **Verification**: 70/70 tests passing (`tests/test_final_two_blockers_fix.py`).

### `bb878e7` (2026-09-20)
- **Title**: fix(broll): refine context indicators requirement and minimum clip length for gap hunting.
- **Affected Systems**: `pipeline/broll/engine.py`, `semantic_parser.py`.
- **Tests**: `tests/test_broll_semantic_engine.py`.

### `4e16647` (2026-09-20)
- **Title**: fix(broll): enhance semantic B-roll engine with figurative mapping, gap hunter, and visual QA.
- **Affected Systems**: `pipeline/broll/`.

### `e6e4586` (2026-09-20)
- **Title**: fix(pipeline): accept RENDER_WARN in final render quality gate and Telegram review delivery.
- **Affected Systems**: `jobs/worker_runner.py`, `api/jobs.py`.

### `c6aee65` (2026-09-20)
- **Title**: fix(publishing): resolve route shadowing on publishing endpoints and harden frontend fetch handling.
- **Affected Systems**: `api/publishing.py`, `frontend/`.

### `6598c17` (2026-09-20)
- **Title**: fix(artifacts): ensure worker enforces Google Drive MP4 persistence and Telegram review delivers actual video via sendVideo.
- **Affected Systems**: `jobs/worker_runner.py`, `telegram/review_bot.py`.

### `3b7bc55` (2026-09-20)
- **Title**: feat(security): embed durable multi-credential vault envelope for autonomous persistence across all restarts.
- **Affected Systems**: `security/vault.py`, `security/vault_envelope.py`.

### `50b9179` (2026-09-20)
- **Title**: feat(publishing): implement real Telegram, YouTube Shorts, and Instagram Reels publishing integrations.
- **Affected Systems**: `publishing/`.

### `5192744` (2026-09-19)
- **Title**: ci(worker): add PEXELS_API_KEY env var and fix max_clips default 3 to 5.
- **Affected Systems**: `.github/workflows/worker.yml`, `campaign/candidate_discovery.py`.

### `622bdac` (2026-09-19)
- **Title**: feat(pipeline): implement semantic visual matching and contextual evidence b-roll engine.
- **Affected Systems**: `pipeline/broll/`.

### `03e88e2` (2026-09-19)
- **Title**: fix(pipeline): fix BGM persistence precedence, calibrate audio ducking loudness, and fix Pexels B-roll overlay timeline sync.
- **Affected Systems**: `pipeline/audio_mix/engine.py`, `pipeline/broll/engine.py`.

### `d704112` (2026-09-19)
- **Title**: fix(vault): anchor master key to persistent volume and execute post-commit WAL truncate checkpoint.
- **Affected Systems**: `db/store.py`, `security/vault.py`.

### `4d22998` (2026-09-18)
- **Title**: fix(pipeline): enforce configured min/max duration constraints across discovery, gates, runner, and worker dispatch.
- **Affected Systems**: `campaign/duration.py`, `pipeline/highlights.py`.

### `5f6cc42` (2026-09-17)
- **Title**: feat(architecture): decouple worker to GitHub Actions, disable Render local worker, add Whisper cache.
- **Affected Systems**: `jobs/dispatcher.py`, `.github/workflows/worker.yml`.
