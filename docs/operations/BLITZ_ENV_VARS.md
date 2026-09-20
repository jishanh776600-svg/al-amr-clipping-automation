# AL AMR — blitz.cloud Environment Variables Reference

> **Security**: Set ALL secret values in the blitz.cloud dashboard Environment tab.
> They are encrypted at rest by blitz. NEVER commit secrets to code or Docker images.
>
> This file lists variable NAMES and descriptions only — NO secret values.

---

## Required Configuration Variables (Non-Secret)

| Variable | Value | Notes |
|----------|-------|-------|
| `AUTOCLIP_HOME` | `/data` | Persistent disk mount path |
| `AUTOCLIP_API_URL` | `https://al-amr.<you>.blitz.cloud` | **Your actual blitz URL** — used for callback URLs |
| `AUTOCLIP_ENV` | `production` | Enables cloud environment mode |
| `AUTOCLIP_NO_WORKER` | `1` | Disables in-process worker (uses GitHub Actions instead) |
| `AUTOCLIP_DISPATCH_MODE` | `github` | Forces GitHub Actions dispatch mode |
| `GITHUB_REPOSITORY` | `jishanh776600-svg/al-amr-clipping-automation` | GitHub repo for workflow dispatch |
| `GITHUB_WORKFLOW` | `worker.yml` | Workflow file to dispatch |
| `GITHUB_REF` | `main` | Branch to dispatch on |
| `PYTHONUNBUFFERED` | `1` | Ensure logs appear in real-time |

---

## Required Secret Variables

> Set these in blitz dashboard → Environment tab.
> Values from your C-drive backup zip or current `.env` file.

| Variable | Source | Notes |
|----------|--------|-------|
| `AL_AMR_MASTER_KEY` | C-drive backup `.env` | **Master encryption key for credential vault** — must match Render value to preserve existing encrypted credentials |
| `OPERATOR_TOKEN` | `render.yaml` line 28 | `alamr-op-2024-secure` (from render.yaml — or change to something new) |
| `AUTOCLIP_API_KEY` | Same as `OPERATOR_TOKEN` | Must match for backward compat |
| `GITHUB_PAT` | GitHub Settings → PAT | Personal access token with `workflow` scope |

---

## Publishing & Integration Secrets

| Variable | Source | Notes |
|----------|--------|-------|
| `TELEGRAM_BOT_TOKEN` | BotFather | Telegram bot token |
| `TELEGRAM_CHAT_ID` | Telegram chat | Numeric chat ID for review delivery |
| `GOOGLE_DRIVE_CLIENT_ID` | Google Cloud Console | OAuth 2.0 client ID |
| `GOOGLE_DRIVE_CLIENT_SECRET` | Google Cloud Console | OAuth 2.0 client secret |
| `GOOGLE_DRIVE_REFRESH_TOKEN` | OAuth flow | Long-lived refresh token |
| `GOOGLE_DRIVE_ROOT_FOLDER_ID` | Google Drive | Root folder ID for artifact storage |
| `YOUTUBE_CLIENT_ID` | Google Cloud Console | YouTube Data API v3 client ID |
| `YOUTUBE_CLIENT_SECRET` | Google Cloud Console | YouTube Data API v3 client secret |
| `YOUTUBE_REFRESH_TOKEN` | OAuth flow | YouTube long-lived refresh token |
| `INSTAGRAM_ACCESS_TOKEN` | Meta Graph API | Instagram publishing token |
| `INSTAGRAM_ACCOUNT_ID` | Meta Graph API | Instagram business account ID |

---

## Optional Variables

| Variable | Default | Notes |
|----------|---------|-------|
| `TELEGRAM_WEBHOOK_URL` | (empty) | Set to `https://<blitz-url>/api/telegram/webhook` to enable webhook mode instead of polling |
| `TELEGRAM_ALLOWED_USER_IDS` | (empty) | Comma-separated Telegram user IDs allowed to use the bot |
| `TELEGRAM_ALLOWED_CHAT_IDS` | (empty) | Comma-separated allowed chat IDs |
| `AUTOCLIP_ACTIVE_PROVIDER` | `anthropic` | Default LLM provider |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

---

## Important Notes

### `AL_AMR_MASTER_KEY` — Critical for Credential Continuity

The vault uses this key to encrypt/decrypt all stored credentials (GitHub PAT, Telegram token, Drive credentials, etc.).

**If you use a DIFFERENT key on blitz than on Render:**
- The vault cannot decrypt existing credentials stored in the SQLite DB
- You will see "Encryption key mismatch" in the Settings UI
- You will need to re-enter all credentials manually via the Settings UI

**To preserve existing credentials:**
1. Use EXACTLY the same `AL_AMR_MASTER_KEY` value that was set on Render
2. Export the Render SQLite DB and import it to blitz persistent storage

**If starting fresh (new vault):**
- Set any new strong random key (at least 32 chars)
- Re-enter all credentials via the Settings UI after blitz starts

### Total Variable Count

The free plan allows 64 variables per app. This configuration uses approximately 22 variables — well within limits.

---

## Quick Verification After Setting Variables

After setting all variables and restarting the app, verify:

```bash
# 1. App health
curl https://al-amr.<you>.blitz.cloud/health
# → {"status":"ok","service":"autoclip","version":"..."}

# 2. App readiness (all checks)
curl https://al-amr.<you>.blitz.cloud/ready
# → {"status":"ok","ready":true,"checks":{...}}

# 3. Dispatch readiness (requires OPERATOR_TOKEN auth)
curl -H "Authorization: Bearer <OPERATOR_TOKEN>" \
  https://al-amr.<you>.blitz.cloud/api/jobs/dispatch-status
# → {"ready":true,"capability":"AVAILABLE",...}
```
