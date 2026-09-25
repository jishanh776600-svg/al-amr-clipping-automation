# AL AMR — blitz.cloud Deployment Guide

> **Render remains active until this deployment is fully verified. Do not disable it.**

---

## Prerequisites

- [x] blitz.cloud account created at https://beta.blitz.cloud/
- [x] Docker image published to Docker Hub as `<your-dockerhub-username>/al-amr-clipping-automation:latest`
- [x] All secrets and credentials ready (from C-drive backup or current `.env`)
- [x] Google Drive credentials configured

---

## Step 1: Push Docker Image to Docker Hub

Run these commands locally (substitute your Docker Hub username):

```powershell
# From the repo root
docker build -t <dockerhub-username>/al-amr-clipping-automation:latest .

# Login to Docker Hub
docker login

# Push the image
docker push <dockerhub-username>/al-amr-clipping-automation:latest
```

**Verify the image runs locally first:**
```powershell
docker run --rm -p 8000:8000 `
  -e AUTOCLIP_HOME=/data `
  -e AUTOCLIP_ENV=production `
  -e AUTOCLIP_NO_WORKER=1 `
  -v autoclip-data:/data `
  <dockerhub-username>/al-amr-clipping-automation:latest

# In another terminal:
Invoke-WebRequest http://localhost:8000/health -UseBasicParsing
```

> **If GHCR is preferred** (recommended — free for public repos):
> ```powershell
> docker build -t ghcr.io/jishanh776600-svg/al-amr-clipping-automation:latest .
> echo $env:GITHUB_TOKEN | docker login ghcr.io -u jishanh776600-svg --password-stdin
> docker push ghcr.io/jishanh776600-svg/al-amr-clipping-automation:latest
> ```
> GHCR images must be set to **public** visibility (GitHub → Packages → al-amr-clipping-automation → Package settings → Change visibility → Public).

---

## Step 2: Create the blitz.cloud App

1. Open https://beta.blitz.cloud/ → click **"Host something new"**
2. Choose **"An app that is already packaged up"**
3. Search for your image: `<dockerhub-username>/al-amr-clipping-automation` or `ghcr.io/jishanh776600-svg/al-amr-clipping-automation`
4. Select tag: `latest`
5. App name: `al-amr` (this becomes your URL: `https://al-amr.jishanh776600.blitz.cloud`)
6. Under **Advanced settings**:
   - **Port**: `8000` (should be auto-detected from `EXPOSE 8000`)
   - **Persistent folders**: `/data` (should be auto-detected from `VOLUME ["/data"]`)
7. Click **"Put it online"**

---

## Step 3: Configure Environment Variables

In blitz dashboard → your app → **Environment** tab, add **all** variables from [`BLITZ_ENV_VARS.md`](./BLITZ_ENV_VARS.md).

**Set secret values** from your C-drive backup zip or the current `.env` file.

After setting all variables, click **Restart** on the Environment page.

---

## Step 4: Verify Health

Once the app shows "Online":

```powershell
# Replace with your actual blitz URL
$blitzUrl = "https://al-amr.<you>.blitz.cloud"

# Health check
Invoke-WebRequest "$blitzUrl/health" -UseBasicParsing
# Expected: {"status":"ok","service":"autoclip","version":"..."}

# Readiness check
Invoke-WebRequest "$blitzUrl/ready" -UseBasicParsing
# Expected: {"status":"ok","ready":true,...}

# Dashboard
Start-Process "$blitzUrl/"
# Expected: React dashboard loads

# API docs
Start-Process "$blitzUrl/docs"
# Expected: Swagger UI loads
```

---

## Step 5: Verify Credentials Hydrate

In the dashboard → Settings → Credentials tab, verify:
- GitHub PAT: shows `•••••••• Configured (…XXXX)`
- Telegram Bot Token: shows `•••••••• Configured`
- Google Drive credentials: all 4 show configured

If any show "Not configured" or "Encryption key mismatch":
- Verify `AL_AMR_MASTER_KEY` matches the value used on Render
- Re-enter the credential via the Settings UI

---

## Step 6: Verify GitHub Dispatch

```powershell
$blitzUrl = "https://al-amr.<you>.blitz.cloud"
$token = "alamr-op-2024-secure"  # Your OPERATOR_TOKEN

Invoke-WebRequest "$blitzUrl/api/jobs/dispatch-status" `
  -Headers @{"Authorization"="Bearer $token"} `
  -UseBasicParsing
# Expected: {"ready":true,"capability":"AVAILABLE",...}
```

---

## Step 7: Run a Staging End-to-End Test

1. Open dashboard at `https://al-amr.<you>.blitz.cloud`
2. Create a new job with a YouTube URL
3. Verify GitHub Actions triggers: https://github.com/jishanh776600-svg/al-amr-clipping-automation/actions
4. Monitor job progress in dashboard (Events stream)
5. Verify 5 clips produced, 20–30s each
6. Verify Google Drive upload (check Drive → autoclip-db-backups folder)
7. Verify Telegram review card received with actual MP4
8. Test Approve, Reject, Request Changes buttons

---

## Step 8: Switch Telegram Webhook (Optional Upgrade)

Currently polling mode is active. To upgrade to webhook (lower latency):

```powershell
# Set this in blitz Environment tab:
# TELEGRAM_WEBHOOK_URL = https://al-amr.<you>.blitz.cloud/api/telegram/webhook

# Then call Telegram Bot API to register the webhook:
$botToken = "<your-telegram-bot-token>"  # Keep secret!
$webhookUrl = "https://al-amr.<you>.blitz.cloud/api/telegram/webhook"

Invoke-RestMethod "https://api.telegram.org/bot$botToken/setWebhook" `
  -Method POST `
  -Body (@{url=$webhookUrl} | ConvertTo-Json) `
  -ContentType "application/json"
```

---

## Step 9: Production Cutover

Only after all staging tests pass:

1. Run final DB backup: `python scripts/db_backup_drive.py --label pre-cutover`
2. Verify blitz is healthy: `GET /health` → `{"status":"ok"}`
3. Verify all credentials hydrate
4. Verify GitHub dispatch ready
5. Switch Telegram webhook (Step 8 above)
6. Monitor logs in blitz dashboard → app → Logs tab
7. Keep Render available (do NOT delete yet)
8. Run controlled production job
9. Monitor for 24 hours before decommissioning Render

---

## Rollback to Render

See [`ROLLBACK_RENDER.md`](./ROLLBACK_RENDER.md) for exact rollback procedure.

---

## Periodic Database Backup (Recommended)

Run the backup script manually or set up a periodic task:

```powershell
# Manual backup
python scripts/db_backup_drive.py --label manual

# Verify backup worked
# Check Google Drive → autoclip-db-backups folder
```

On blitz, you can also set up a simple cron job via the blitz App Store or run the backup script from GitHub Actions on a schedule.

---

## blitz.cloud Limits (Free Plan)

| Limit | Value |
|-------|-------|
| Apps | 15 |
| Memory per app | Up to 2 GB total |
| Storage | 10 GB total |
| Databases | 1 |
| Env vars per app | 64 |
| Persistent folders | Up to 6 |

> **Important**: blitz does NOT back up persistent files (only managed databases). 
> Our `scripts/db_backup_drive.py` provides the file-level backup via Google Drive.
