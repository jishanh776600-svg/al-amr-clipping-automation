# Engine: Deduplication & Idempotency

- **Source Files**: `backend/autoclip/publishing/service.py`, `backend/autoclip/db/store.py`.
- **Purpose**: Prevents duplicate video renders, duplicate Google Drive uploads, and double-posting to social platforms.
- **Idempotency Keys**:
  - Publications: `{job_id}:{clip_id}:{platform}:{destination_id}`.
  - Review messages: Telegram message deduplication via `ClipApprovalRecord.telemetry`.
