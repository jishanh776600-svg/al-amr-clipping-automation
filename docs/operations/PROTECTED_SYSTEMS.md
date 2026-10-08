# AL AMR — Protected Systems Register

The following core modules are protected from casual refactoring:

| Protected Module | Critical Responsibility | Risk of Unintended Modification |
| :--- | :--- | :--- |
| `backend/autoclip/pipeline/audio_mix/engine.py` | Audio mix padding (`apad`), zero dropout, and safe keyframe seeking | End-of-clip voice cutoff / silence |
| `backend/autoclip/telegram/review_bot.py` | Immediate callback answering, remote clip reconciliation | Telegram button spinners hanging, publishing blocked |
| `backend/autoclip/jobs/dispatcher.py` | GitHub Actions workflow dispatch | Worker dispatch failure |
| `backend/autoclip/jobs/worker_runner.py` | 5-clip guarantee and worker callback | Premature pipeline termination |
| `backend/autoclip/publishing/service.py` | Multi-platform publishing idempotency & media resolution | Double publishing or missing media errors |
| `backend/autoclip/security/vault.py` | AES-256 encrypted credential persistence | Credential loss across container redeploys |
| `backend/autoclip/db/schema.py` | 27 normalized SQLite tables and WAL mode | Database schema corruption or FK violations |
