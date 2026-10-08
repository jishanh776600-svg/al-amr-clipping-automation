# AL AMR — Production Invariants & Operational Rules

Every production rule is verified against the codebase:

| # | Production Rule | Status | Verification Reference |
| :--- | :--- | :--- | :--- |
| 1 | **Exactly 5 clips per job** | **IMPLEMENTED** | `backend/autoclip/campaign/candidate_discovery.py` (`target_clips=5`, fails if $<5$) |
| 2 | **Never silently succeed with 1–2 clips** | **IMPLEMENTED** | `backend/autoclip/jobs/worker_runner.py` (triggers `INSUFFICIENT_VALID_CLIPS`) |
| 3 | **Standard clip duration target is 20–30s** | **IMPLEMENTED** | `backend/autoclip/campaign/duration.py` (`resolve_duration_limits()` defaults to 20s–30s) |
| 4 | **Selected BGM must persist** | **IMPLEMENTED** | `backend/autoclip/jobs/dispatcher.py` & `worker_runner.py` (propagates in `job_settings`) |
| 5 | **Explicit BGM selection has priority** | **IMPLEMENTED** | `backend/autoclip/pipeline/audio_mix/engine.py` (explicit track overrides auto-mood) |
| 6 | **Voice remains dominant over BGM** | **IMPLEMENTED** | `backend/autoclip/pipeline/audio_mix/engine.py` (sidechain compression ducks BGM to -18 dB) |
| 7 | **Final loudness target around -14 LUFS** | **IMPLEMENTED** | `backend/autoclip/pipeline/audio_mix/quality_gate.py` (`target_lufs=-14.0`, limit: -1.5 dBTP) |
| 8 | **Container audio matches video duration** | **IMPLEMENTED** | `backend/autoclip/pipeline/audio_mix/engine.py` (`apad` + `dropout_transition=0` + `atrim`) |
| 9 | **Natural speech decay tail ($\ge 0.35$s)** | **IMPLEMENTED** | `backend/autoclip/pipeline/retention/pacing.py` (`natural_tail_s = max(0.35, padding * 4)`) |
| 10 | **Semantic visuals match spoken concepts** | **IMPLEMENTED** | `backend/autoclip/pipeline/broll/semantic_parser.py` (filters metaphors, extracts nouns/verbs) |
| 11 | **A-roll fallback on low confidence** | **IMPLEMENTED** | `backend/autoclip/pipeline/broll/engine.py` (punch-in zoom if Pexels has no matches) |
| 12 | **Avoid irrelevant/random stock footage** | **IMPLEMENTED** | `backend/autoclip/pipeline/broll/scorer.py` (minimum relevance score threshold $\ge 60.0$) |
| 13 | **Avoid excessive static A-roll** | **IMPLEMENTED** | `backend/autoclip/pipeline/broll/engine.py` (gap hunter flags talking heads $>3.0$s) |
| 14 | **Production review through Telegram** | **IMPLEMENTED** | `backend/autoclip/telegram/review_bot.py` (`send_clip_review()` delivers MP4 preview) |
| 15 | **Approval required before publishing** | **IMPLEMENTED** | `backend/autoclip/publishing/service.py` (`verify_publishing_eligibility` requires `APPROVED`) |
| 16 | **Approve $	o$ YouTube + Instagram publish** | **IMPLEMENTED** | `backend/autoclip/telegram/review_bot.py` (`_execute_auto_publish` triggers both platforms) |
| 17 | **Reject $	o$ No publishing** | **IMPLEMENTED** | `backend/autoclip/telegram/review_bot.py` (cancels publishing queue on `REJECTED`) |
| 18 | **Request Changes $	o$ Revision workflow** | **IMPLEMENTED** | `backend/autoclip/telegram/review_bot.py` (transitions to `CHANGES_REQUESTED` with notes) |
| 19 | **Publishing failures are isolated** | **IMPLEMENTED** | `backend/autoclip/publishing/service.py` (YouTube failure does not block Instagram) |
| 20 | **Render is control plane** | **IMPLEMENTED** | `backend/autoclip/app.py` (`AUTOCLIP_NO_WORKER=1` in production) |
| 21 | **GitHub Actions is worker** | **IMPLEMENTED** | `.github/workflows/worker.yml` (on-demand compute runner) |
| 22 | **Google Drive is persistent storage** | **IMPLEMENTED** | `backend/autoclip/storage/drive.py` (uploads final MP4s and cover images) |
| 23 | **Telegram is human review layer** | **IMPLEMENTED** | `backend/autoclip/telegram/review_bot.py` (native interactive video cards) |
