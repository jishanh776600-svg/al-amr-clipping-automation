# Whop Cloud-Browser Foundation (Step 1 Specification)

> **Important**: This workflow is read-only and does not join, claim, apply to, or submit any Whop campaign.

---

## 1. What Step 1 Does
Step 1 establishes the verified, secure cloud-browser foundation required for all future Whop automation:
- **Cloud Execution**: Runs inside GitHub Actions on Ubuntu with Playwright Chromium.
- **Session Injection**: Ingests serialized session state (`WHOP_COOKIES`) from GitHub Secrets and restores authenticated cookies without human interaction.
- **Entry Point Verification**: Safely navigates to Whop (`https://whop.com`), waits for stabilization, and inspects the page to determine whether the session is authenticated.
- **Safe Diagnostic Artifacts**: Generates non-sensitive diagnostic artifacts:
  - `01_whop_authenticated.png` (or `01_whop_unauthenticated.png`)
  - `diagnostic_report.json`
- **Zero Mutation**: Strictly read-only. No campaigns are joined, accepted, claimed, or modified.

---

## 2. Required GitHub Secret
To enable authenticated cloud browsing, configure the following secret in your GitHub repository:

* **Name**: `WHOP_COOKIES`
* **Location**: Repository `Settings` → `Secrets and variables` → `Actions` → `New repository secret`
* **Format**: A JSON array of cookie objects exported from an authenticated Whop browser session:
  ```json
  [
    {
      "name": "whop_session",
      "value": "...",
      "domain": ".whop.com",
      "path": "/"
    },
    {
      "name": "cf_clearance",
      "value": "...",
      "domain": ".whop.com",
      "path": "/"
    }
  ]
  ```
  *(Full Playwright `storage_state.json` format containing `{"cookies": [...], "origins": [...]}` is also fully supported).*

---

## 3. How to Manually Run the Workflow
1. Navigate to the **Actions** tab in your GitHub repository.
2. Under **Workflows** in the left sidebar, click **Whop Access Test (Step 1 Foundation)**.
3. Click the **Run workflow** dropdown on the right.
4. (Optional) Adjust the Whop entry point URL if testing a specific dashboard subpath (default: `https://whop.com`).
5. Click the green **Run workflow** button.

---

## 4. Expected Diagnostic Artifacts
After the workflow completes, download the **`whop-access-diagnostics`** artifact bundle from the workflow run summary:
- **`01_whop_authenticated.png`**: High-resolution screenshot of the authenticated Whop landing page.
- **`diagnostic_report.json`**: Safe diagnostic metadata report structured as follows:
  ```json
  {
    "success": true,
    "timestamp": "2026-10-02T14:30:00.000Z",
    "final_url": "https://whop.com/dashboards",
    "page_title": "Whop - Dashboard",
    "authenticated": true,
    "reason": "session_valid",
    "dry_run": true,
    "cookies_detected": 12,
    "screenshot_file": "01_whop_authenticated.png"
  }
  ```

---

## 5. How Authentication Is Detected
The detector inspects the stabilized page state:
1. **Cloudflare Interstitial Check**: Detects if Cloudflare challenge pages (`"Just a moment..."` or Turnstile) are active (`cloudflare_challenge_detected`).
2. **Redirect to Login**: Detects if the browser was bounced to `/login`, `/sign-in`, or an unauthenticated portal (`login_required`).
3. **Authenticated Indicators**: Detects presence of user navigation, avatar/profile menus, or dashboard access on Whop domains (`session_valid` / `session_active`).

---

## 6. What Failures Mean
| Error Message / Reason | Cause | Corrective Action |
| :--- | :--- | :--- |
| `WHOP_COOKIES secret is missing or empty` | The repository secret has not been configured yet. | Add `WHOP_COOKIES` in GitHub Secrets. |
| `WHOP_COOKIES is malformed JSON` | The JSON syntax in the secret contains an error. | Re-export cookies using a cookie editor extension and paste valid JSON. |
| `login_required` | The cookies provided have expired or are missing critical session tokens. | Log into Whop in your browser to refresh your session, then re-export fresh cookies. |
| `cloudflare_challenge_detected` | Cloudflare flagged the cloud runner IP. | Ensure `cf_clearance` cookie from your active session is included in the cookie export. |

---

## 7. What Step 1 Deliberately Does NOT Do
To guarantee 100% safety and compliance, the following features are strictly disabled and blocked:
- ❌ No campaign discovery or web scraping.
- ❌ No CPM parsing or platform filtering.
- ❌ No clicking "Join Campaign", "Apply", or "Accept".
- ❌ No downloading campaign assets.
- ❌ No AutoClip API job creation or rendering.
- ❌ No Telegram notifications or approval prompts.
- ❌ No Whop submission form interaction.
- ❌ No automatic cron schedule (manual `workflow_dispatch` only).
