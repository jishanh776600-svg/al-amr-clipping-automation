# AL AMR / Whop Autonomous Clipping System — Step 7: Telegram Human Approval Gate

## 1. Executive Summary & Pipeline Position

Step 7 establishes the **Telegram Human Approval Gate** within the autonomous pipeline:

```
Whop Discovery
     ↓
CampaignBrief (Step 4)
     ↓
AutoClip Cloud Connector (Step 5)
     ↓
Cloud Render
     ↓
Quality Verification (Step 6)
     ↓
RENDER_READY / acceptable RENDER_WARN (Exactly 5 Valid Clips)
     ↓
THIS STEP: TELEGRAM HUMAN APPROVAL (Step 7)
     ↓
AWAITING_APPROVAL → APPROVED / CHANGES_REQUESTED / APPROVAL_REJECTED
     ↓
[Future Step 8: Multi-Platform Publishing & Whop Submission]
```

### Strict Production Boundaries
- **Zero Publishing**: Step 7 strictly **does not publish** to YouTube Shorts, Instagram Reels, or TikTok.
- **Zero Whop Mutation**: Step 7 strictly **does not submit** proof links, claim campaigns, or join campaigns on Whop (`WHOP_DRY_RUN=true` preserved).
- **Single Canonical Bot**: Step 7 reuses the existing `backend.autoclip.telegram.review_bot` framework without spinning up competing bot consumers or parallel webhook endpoints.
- **Meaning of `APPROVED`**: In Step 7, `APPROVED` strictly denotes: **"HUMAN REVIEW PASSED."** Publishing and proof submission are reserved for Step 8.

---

## 2. Architecture & Design Principles

### Single Canonical Webhook / Polling Pathway
All Telegram interactions flow through the established endpoint:
- **Webhook Endpoint**: `POST /api/telegram/webhook` (and `/telegram/webhook`).
- **Update Dispatcher**: `backend.autoclip.telegram.review_bot.handle_telegram_update()`.
- **Whop Hook**: Callbacks starting with `wh:` are intercepted and delegated to `TelegramApprovalGate.handle_callback()`.
- **Immediate ACK**: Invocations call `_answer_callback_query()` immediately to stop client-side spinners before database writes or state transitions.

### Durability & Cloud Storage Resolution
Media delivery prioritizes durable storage:
1. Google Drive persistent MP4 via `drive_file_id`.
2. Existing Telegram `file_id` (avoiding redundant re-uploads).
3. Verified local file ($\ge 1024$ bytes) if present.
Ephemeral GitHub runner paths (e.g. `/home/runner/work/`) lacking durable backup are strictly rejected.

---

## 3. Approval Eligibility — Hard Gate

Telegram review creation is gated strictly behind Step 6 quality verification. Review sessions can **ONLY** be created when all of the following conditions are satisfied:

| Rule / Invariant | Requirement | Violation Behavior |
| :--- | :--- | :--- |
| **Clip Count** | Exactly 5 valid distinct clips | Deterministic rejection if $0$, $1-4$, or duplicate clips |
| **Duration** | Each clip $20.0	ext{s} - 30.0	ext{s}$ | Deterministic rejection if $< 20.0	ext{s}$ or $> 30.0	ext{s}$ |
| **Technical QA** | H.264/AAC, 1080x1920 (9:16), decode valid | Rejection on corrupt streams or silence/clipping failures |
| **Durability** | Persistent Drive ID or validated local storage | Rejection of runner-only paths without Drive persistence |
| **Mandatory Rules**| 100% satisfied | Rejection if any mandatory brief rule fails or is unresolved |
| **QA Status** | `RENDER_PASS` or acceptable `RENDER_WARN` | Immediate rejection on `INSUFFICIENT_VALID_CLIPS` or `RENDER_FAILED` |

---

## 4. Durable Review Session Lifecycle & Schema

Review sessions are persisted in the SQLite table `whop_review_sessions`:

```sql
CREATE TABLE IF NOT EXISTS whop_review_sessions (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    review_session_id    TEXT NOT NULL UNIQUE,
    campaign_id          TEXT NOT NULL REFERENCES whop_campaigns(campaign_id) ON DELETE CASCADE,
    guideline_hash       TEXT NOT NULL,
    autoclip_job_id      TEXT NOT NULL,
    artifact_hash        TEXT NOT NULL,
    idempotency_key      TEXT NOT NULL UNIQUE,
    review_state         TEXT NOT NULL DEFAULT 'PENDING',
    chat_id              TEXT NOT NULL DEFAULT '',
    message_ids_json     TEXT NOT NULL DEFAULT '{}',
    telegram_file_ids_json TEXT NOT NULL DEFAULT '{}',
    clip_ids_json        TEXT NOT NULL DEFAULT '[]',
    clip_order_json      TEXT NOT NULL DEFAULT '[]',
    reviewer_id          TEXT,
    reviewer_username    TEXT,
    decision             TEXT,
    decision_note        TEXT,
    metadata_json        TEXT NOT NULL DEFAULT '{}',
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);
```

### Deterministic Idempotency Key
$$K_{	ext{idempotency}} = 	ext{SHA-256}(	ext{campaign\_id} : 	ext{guideline\_hash} : 	ext{autoclip\_job\_id} : 	ext{artifact\_hash})$$

- **Create Once**: Re-dispatching the same render job reuses the existing review session without re-sending messages.
- **Partial-Send Recovery**: If Telegram delivery fails mid-batch (e.g. clips 1 and 2 sent, clip 3 fails), re-running continues from clip 3 rather than duplicating messages.
- **Artifact Hash Change**: If a campaign is re-rendered with new media, a new artifact hash generates a new review session.

---

## 5. Review UX & Telegram Delivery

The operator receives:
1. **Summary Header Card**: Campaign title, ID, payout rate, quality score, breakdown of the 5 clips, and any non-fatal warnings.
2. **5 Authentic MP4 Videos**: Uploaded via `sendVideo` (or Telegram `file_id` if known), complete with duration badges and Drive links.
3. **Decision Keyboard**:
   - `[ ✅ APPROVE CAMPAIGN ]` $	o$ `wh:appr:<session_id>`
   - `[ 🔄 REQUEST CHANGES ]` $	o$ `wh:chg:<session_id>`
   - `[ ❌ REJECT CAMPAIGN ]` $	o$ `wh:rej:<session_id>`

---

## 6. Callback Security & Atomic Transitions

### Privileged Action Guards
- **User Authorization**: Verified against `TELEGRAM_ALLOWED_USER_IDS` using numeric ID or `@username`.
- **Expected Chat**: Verified to prevent hijacked session callbacks.
- **Session State Validation**: Must be in `PENDING` state. Duplicate or stale callbacks return an informational acknowledgment without re-running transitions.
- **Zero Secrets**: Callback data contains only compact action and session identifiers (e.g. `wh:appr:rev_12345`).

### Atomic Compare-and-Set
State transitions are executed in a single atomic SQL transaction:
```sql
UPDATE whop_review_sessions
SET review_state = ?, decision = ?, reviewer_id = ?, reviewer_username = ?, updated_at = ?
WHERE review_session_id = ? AND review_state = 'PENDING';
```
If two callbacks arrive simultaneously, only one succeeds; the second receives `conflict_already_updated`.

### State Machine Progression
```
AWAITING_APPROVAL ──(APPROVE)────────────► APPROVED
AWAITING_APPROVAL ──(REQUEST_CHANGES)────► CHANGES_REQUESTED ──► RENDERING
AWAITING_APPROVAL ──(REJECT)─────────────► APPROVAL_REJECTED  ──► REJECTED
```
Every transition appends an immutable event to `whop_campaign_events`.

---

## 7. Verification & Production Diagnostics

Executed [`scripts/verify_step7_live_telegram.py`](file:///c:/Users/jisha/OneDrive/Desktop/automation_clipping/scripts/verify_step7_live_telegram.py):
- **Bot Configured**: `True`
- **Bot Token**: `812218...k7SiyE (len=46)` (Masked)
- **Chat ID**: `786...097 (len=10)` (Masked)
- **Bot Identity**: Authenticated as `@al_amr_clipping_bot` (ID: 8122181001) via `getMe`.
- **Runtime Mode**: Polling (0 pending updates).
- **Physical Media Audit**:
  - Validated local production artifact from Step 6 (`~/.autoclip/exports/fix3_prod_acceptance_job/clip_001.../final.mp4`).
  - No 5-clip complete physical production render exists yet on Render/local storage.
  - Correctly and honestly reported: `NO_REAL_RENDER_ARTIFACT_AVAILABLE_FOR_LIVE_TELEGRAM_QA` without fabricating fake renders or starting unauthorized cloud render jobs.
