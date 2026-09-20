# AL AMR — Rollback Procedure: blitz.cloud → Render

> **Use this if blitz staging or production deployment fails and you need to return to Render.**

---

## Before You Rollback — Check These First

1. Is Render service still "Online" or "Suspended"?
   - If suspended: go to https://dashboard.render.com → `al-amr-automation` → Resume
2. Is the Render database still intact? (It should be — Render persistent disk was not touched)
3. Are any jobs currently running on blitz? (Let them finish or mark as failed before rollback)

---

## Rollback Steps

### 1. Resume Render Service (if suspended)

```
https://dashboard.render.com
→ al-amr-automation
→ Resume (or Restart if already running but unresponsive)
```

Wait 60 seconds, then verify:
```powershell
Invoke-WebRequest "https://al-amr-automation.onrender.com/health" -UseBasicParsing
# Expected: {"status":"ok","service":"autoclip","version":"..."}
```

### 2. Switch Telegram Webhook Back to Render

**Only if you already switched the webhook to blitz** (Step 8 of BLITZ_DEPLOY_GUIDE.md):

```powershell
$botToken = "<your-telegram-bot-token>"  # Keep secret!
$renderUrl = "https://al-amr-automation.onrender.com"

# Option A: Restore webhook to Render URL
Invoke-RestMethod "https://api.telegram.org/bot$botToken/setWebhook" `
  -Method POST `
  -Body (@{url="$renderUrl/api/telegram/webhook"} | ConvertTo-Json) `
  -ContentType "application/json"

# Option B: Delete webhook to fall back to polling mode
Invoke-RestMethod "https://api.telegram.org/bot$botToken/deleteWebhook" `
  -Method POST
```

After switching back, verify:
```powershell
Invoke-RestMethod "https://api.telegram.org/bot$botToken/getWebhookInfo"
# Confirm url points to Render or is empty (polling mode)
```

### 3. Restore Render Environment Variable (if changed)

If you changed `AUTOCLIP_API_URL` on Render as part of blitz testing:
```
https://dashboard.render.com
→ al-amr-automation
→ Environment
→ Set AUTOCLIP_API_URL = https://al-amr-automation.onrender.com
→ Save Changes (triggers redeploy)
```

### 4. Verify Render is Fully Operational

```powershell
$renderUrl = "https://al-amr-automation.onrender.com"

# Health
Invoke-WebRequest "$renderUrl/health" -UseBasicParsing

# Readiness
Invoke-WebRequest "$renderUrl/ready" -UseBasicParsing

# Dashboard (manual)
Start-Process "$renderUrl/"

# Dispatch status
Invoke-WebRequest "$renderUrl/api/jobs/dispatch-status" `
  -Headers @{"Authorization"="Bearer alamr-op-2024-secure"} `
  -UseBasicParsing
```

### 5. Verify Telegram Callbacks Work

Send a test message to the Telegram bot and verify the bot responds.

### 6. Stop blitz App (Optional)

To prevent blitz from processing duplicate callbacks while Render is active:
```
https://beta.blitz.cloud/
→ al-amr app
→ Stop (or Pause)
```

> Do NOT delete the blitz app — you may want to retry the migration later.

---

## Database Consistency After Rollback

**Important**: If any jobs were created on blitz after the database was migrated there:
- Those jobs will NOT be in the Render database
- Any callbacks from GitHub Actions pointing to blitz URLs will fail

**Recovery steps for in-flight jobs:**
1. Note any job IDs that were running on blitz
2. In Render dashboard, access the Render shell or logs
3. Use the API to mark those jobs as failed: `PATCH /api/jobs/{id}` with `{"status":"failed","error":"Control plane rollback"}`
4. Re-create jobs manually from the dashboard if needed

---

## Root Cause Investigation

After stabilizing on Render, document what failed on blitz:
1. Check blitz app logs: dashboard → app → Logs tab
2. Check for missing env vars, permission errors, or startup crashes
3. Review this guide and retry blitz deployment after fixing the root cause

---

*Render is retained as rollback until blitz has been stable for 7+ days with no issues.*
