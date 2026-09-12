# AL AMR — Production Publishing & Distribution Automation

This document outlines the architecture, configuration, idempotency semantics, and API contract for multi-platform clip distribution in AL AMR Clipping Automation.

---

## 1. Architectural Overview

The distribution pipeline operates under strict thin-client and remote-first invariants:
```
OPERATOR (Web Console / Android APK)
        ↓
RENDER CONTROL PLANE (FastAPI)
        ↓
GITHUB ACTIONS ON-DEMAND WORKER (Ubuntu cloud runner)
        ↓
PROCESSING & RENDERING (MediaPipe, Whisper, FFmpeg)
        ↓
GOOGLE DRIVE VAULT (AL-AMR/clips/{job_id}/)
        ↓
PUBLISHING SERVICE (Multi-Platform Orchestrator)
    ├── Telegram Bot API (sendVideo with captions & Drive link)
    ├── YouTube Shorts API (OAuth2 Resumable Upload + Safety Gate)
    └── Instagram Reels API (Meta Graph API 2-phase Container Upload)
        ↓
DURABLE AUDIT LOG (publishing_records in SQLite)
        ↓
CALLBACK TO CONTROL PLANE & REAL-TIME SSE BROADCAST
```

---

## 2. Supported Platforms & Adapters

### 2.1 Telegram (`TelegramPublisher`)
- **API**: Telegram Bot API (`https://api.telegram.org/bot<TOKEN>/sendVideo`)
- **Credentials**:
  - `TELEGRAM_BOT_TOKEN`: Bot token from @BotFather.
  - `TELEGRAM_CHAT_ID`: Channel (`@alamr_drops` or channel ID) or operator chat ID.
  - `TELEGRAM_ALLOWED_USER_IDS`: Optional CSV of allowed operator user IDs.
- **Features**:
  - Direct video binary push via streaming multipart upload.
  - Caption formatting with title, hook, campaign CTA, hashtags, and Google Drive backup link.
  - Support for custom destination chat IDs.

### 2.2 YouTube Shorts (`YouTubePublisher`)
- **API**: YouTube Data API v3 (`videos.insert` resumable upload protocol)
- **Credentials**:
  - `YOUTUBE_CLIENT_ID`: Google Cloud Console OAuth 2.0 Client ID.
  - `YOUTUBE_CLIENT_SECRET`: OAuth 2.0 Client Secret.
  - `YOUTUBE_REFRESH_TOKEN`: Authorized refresh token with `https://www.googleapis.com/auth/youtube.upload`.
- **Safety Defaults**:
  - Defaults to **Dry-Run Mode** unless `YOUTUBE_PUBLISH_LIVE=true` is set.
  - Dry-run verifies credentials, generates access tokens, and validates media without creating public/unlisted videos.
  - Live uploads enforce vertical 9:16 `#Shorts` tag in title and description.

### 2.3 Instagram Reels (`InstagramPublisher`)
- **API**: Meta Graph API v19.0+ (`media` container creation & `media_publish`)
- **Credentials**:
  - `INSTAGRAM_ACCESS_TOKEN` (or `META_ACCESS_TOKEN`): User/Page access token with `instagram_basic`, `instagram_content_publish`.
  - `INSTAGRAM_ACCOUNT_ID`: Instagram Business or Creator account ID.
- **Features**:
  - Two-phase upload: creates container with public streaming URL (`/api/exports/{id}/stream` or Google Drive direct download), polls for container status `FINISHED`, and triggers publish.
  - Graceful stop if missing app review permissions (`Requires app review for public Reels`).

---

## 3. Idempotency & Safety Guarantees

1. **Unique Target Constraint**:
   ```sql
   UNIQUE(export_id, platform, destination)
   ```
   Multiple worker retries or duplicate operator triggers will **never** create duplicate posts on social media or duplicate database records.

2. **Published State Invariant**:
   If a record exists for `(export_id, platform, destination)` with `status = 'published'`, subsequent publish calls instantly return the existing record without re-uploading.

3. **Platform Error Isolation**:
   Failure on one platform (e.g. Meta Graph token expiry) does not impede execution on other platforms (e.g. Telegram or YouTube) or cause pipeline crashes.

---

## 4. REST API Contract

### 4.1 List Publishing Records
```http
GET /api/publishing?job_id=job_123&platform=youtube&status=published&limit=50
```
**Response**: `200 OK`
```json
[
  {
    "id": "pub_01hxyz...",
    "export_id": "exp_01habc...",
    "job_id": "job_123",
    "platform": "youtube",
    "status": "published",
    "destination": "",
    "external_id": "dQw4w9WgXcQ",
    "error": null,
    "metadata": {
      "title": "AL AMR Highlight #Shorts",
      "url": "https://www.youtube.com/shorts/dQw4w9WgXcQ"
    },
    "created_at": "2026-09-12T15:00:00Z",
    "updated_at": "2026-09-12T15:01:00Z"
  }
]
```

### 4.2 Platform Status
```http
GET /api/publishing/platforms
```
**Response**: `200 OK`
```json
[
  {
    "platform": "telegram",
    "available": true,
    "configured": true,
    "details": "Bot token and Chat ID present"
  },
  {
    "platform": "youtube",
    "available": true,
    "configured": true,
    "details": "OAuth2 configured (Live: false)"
  },
  {
    "platform": "instagram",
    "available": true,
    "configured": false,
    "details": "Missing INSTAGRAM_ACCESS_TOKEN or INSTAGRAM_ACCOUNT_ID"
  }
]
```

### 4.3 Trigger Publish on Export
```http
POST /api/exports/{export_id}/publish
Content-Type: application/json

{
  "platforms": ["telegram", "youtube"],
  "title": "AL AMR Elite Hook",
  "description": "Watch full episode now #Shorts",
  "tags": ["ALAMR", "Shorts"],
  "dry_run": false
}
```
**Response**: `200 OK` with `list[PublishingRecordOut]`

### 4.4 Retry Failed Publish
```http
POST /api/publishing/{record_id}/retry
```
**Response**: `200 OK` with `PublishingRecordOut`
