# AL AMR — Disaster Recovery & Reconstruction Manual

This document details catastrophic recovery procedures so an engineer can reconstruct AL AMR from scratch without external conversation history.

---

## Scenario A: GitHub Repository Lost
1. **Source Recovery**: Extract `backup/source/al_amr_source_snapshot.zip` into a clean directory.
2. **Git Init**: Run `git init && git checkout -b main`.
3. **Commit Source**: `git add . && git commit -m "feat: restore AL AMR production codebase from verified backup"`.
4. **Link Remote**: Set `git remote add origin git@github.com:<org>/<repo>.git` and push with `git push -u origin main --force`.
5. **Configure Secrets**: Re-populate GitHub Actions repository secrets (`WORKER_CALLBACK_SECRET`, `GOOGLE_DRIVE_CLIENT_ID`, `GOOGLE_DRIVE_CLIENT_SECRET`, `GOOGLE_DRIVE_REFRESH_TOKEN`, `GOOGLE_DRIVE_ROOT_FOLDER_ID`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `PEXELS_API_KEY`).

---

## Scenario B: Render Service & Persistent Disk Lost
1. **Create Web Service on Render**: Connect to GitHub repository.
2. **Mount Disk**: Attach persistent disk `autoclip-data` mounted at `/data` (1 GB).
3. **Restore SQLite**: Copy `backup/database/pipeline.db.backup` to `/data/autoclip.db`.
4. **Restore Master Key**: Copy master key to `/data/.master_key` (or set `AL_AMR_MASTER_KEY` environment variable).
5. **Set Environment Variables**: Configure environment variables from `config/example.env`.
6. **Deploy**: Trigger deployment. Run health check at `GET /api/settings/health`.

---

## Scenario C: SQLite Database Corrupted
1. Stop the Render service.
2. Delete corrupted `/data/autoclip.db` and any `-wal` or `-shm` temporary files.
3. Copy `backup/database/pipeline.db.backup` to `/data/autoclip.db`.
4. Run `PRAGMA integrity_check;` to verify integrity.
5. Restart service.

---

## Scenario D: Telegram Bot Token Lost / Revoked
1. Open `@BotFather` in Telegram.
2. Create new bot or reset bot token (`/token`).
3. Update `TELEGRAM_BOT_TOKEN` in Render dashboard and GitHub Secrets.
4. If webhook was used, trigger `POST /bot{token}/deleteWebhook` or restart service (auto-polling cleans conflicts automatically).

---

## Scenario E: Minimum Viable Recovery Checklist
- [ ] Source code restored from `al_amr_source_snapshot.zip`.
- [ ] Database restored from `pipeline.db.backup`.
- [ ] `GITHUB_PAT` configured in vault.
- [ ] `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` configured.
- [ ] Run `pytest tests/test_final_two_blockers_fix.py`.
