# Engine: Telegram Human Review & Control Room

- **Source Files**: `backend/autoclip/telegram/review_bot.py`, `backend/autoclip/api/telegram.py`.
- **Primary Classes / Functions**: `send_clip_review()`, `handle_telegram_update()`, `poll_telegram_updates()`, `_reconcile_remote_clip()`.
- **Purpose**: Mobile-first review portal delivering native MP4 video previews and processing inline button actions.
- **Buttons Supported**:
  - `[ ✅ APPROVE & PUBLISH ]`: Approves clip and immediately launches background publishing.
  - `[ ❌ REJECT ]`: Rejects clip and cancels queued publishing items.
  - `[ 🔄 REQUEST CHANGES ]`: Flags clip for revision and logs operator feedback.
- **Reliability Safeguards**:
  - Instant `answerCallbackQuery` acknowledgment clears client spinner immediately.
  - `_reconcile_remote_clip()` parses card text and Drive/Telegram file IDs to reconstruct missing SQLite records in decoupled environments.
  - Auto-polling mode automatically calls `deleteWebhook(drop_pending_updates=False)` to prevent 409 Conflict.
- **Tests**: `tests/test_duration_and_telegram_regression.py`, `tests/test_final_two_blockers_fix.py`.
