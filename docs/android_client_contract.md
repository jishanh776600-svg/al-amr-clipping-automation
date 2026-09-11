# AL AMR — Mobile/Android API Integration Contract

This document specifies the REST and Server-Sent Events (SSE) API contract that the AL AMR mobile application (Step 5 Android APK) consumes.

---

## 1. Architectural Principles

1. **Thin-Client Architecture**:
   - The Android client is strictly an operator/control device.
   - It **NEVER** runs local Whisper, local FFmpeg, local MediaPipe, or AI highlight selection on mobile hardware.
   - All compute, downloading, model inference, and rendering occur on the persistent remote AL AMR backend.

2. **Disconnected Client Invariant**:
   - Once a job is accepted (`POST /api/jobs` returns HTTP 201 with `{ "id": "..." }`), the mobile device can safely close the app, disconnect from Wi-Fi/cellular, or turn off.
   - The remote server continues processing autonomously.
   - Upon reconnecting, the app fetches current state (`GET /api/jobs/{id}`) or connects to the event stream (`GET /api/jobs/{id}/events`).

3. **Configurable Remote Host**:
   - The mobile client must allow configuring the backend URL:
     ```
     https://<remote-alamr-server-host>
     ```
   - It must never assume `localhost` or `127.0.0.1`.

4. **Authentication**:
   - Requests include the operator secret via header:
     ```http
     Authorization: Bearer <OPERATOR_TOKEN_OR_API_KEY>
     ```
     or
     ```http
     X-API-Key: <OPERATOR_TOKEN_OR_API_KEY>
     ```

---

## 2. API Endpoints

### 2.1 System Health & Readiness

#### `GET /health`
- **Response**: `200 OK`
```json
{
  "status": "ok",
  "service": "autoclip",
  "version": "0.1.0"
}
```

#### `GET /ready`
- **Response**: `200 OK` (or `503 Service Unavailable` if degraded)
```json
{
  "status": "ok",
  "ready": true,
  "service": "autoclip",
  "version": "0.1.0",
  "checks": {
    "database": "ok",
    "storage": "ok",
    "ffmpeg": "ok",
    "worker": {
      "status": "running",
      "queued_jobs": 0,
      "has_active_job": false
    }
  }
}
```

---

### 2.2 Source Ingestion

#### `POST /api/sources/youtube`
- **Payload**:
```json
{
  "url": "https://www.youtube.com/watch?v=VIDEO_ID"
}
```
- **Response**: `201 Created`
```json
{
  "id": "src_12345",
  "type": "youtube",
  "url": "https://www.youtube.com/watch?v=VIDEO_ID",
  "title": "Example Video Title",
  "duration_s": 620.5,
  "width": 1920,
  "height": 1080
}
```

#### `POST /api/sources/upload`
- **Multipart Form Upload**: file field `file`.
- **Response**: `201 Created` with Source object.

---

### 2.3 Job Creation & Campaign Enforcement

#### `POST /api/jobs`
- **Payload**:
```json
{
  "source_id": "src_12345",
  "provider": "openai",
  "campaign": {
    "id": "camp_tech_01",
    "name": "Tech Insights Campaign",
    "target_clip_count": 3,
    "minimum_duration": 15.0,
    "maximum_duration": 60.0,
    "banned_words": ["crypto", "scam"],
    "required_words": ["software"],
    "hook_required": true,
    "minimum_hook_score": 5.0,
    "cta_required": false,
    "aspect_ratio": "9:16",
    "caption_preset": "bold_pop"
  }
}
```
- **Response**: `201 Created`
```json
{
  "id": "job_98765",
  "source_id": "src_12345",
  "status": "queued",
  "current_stage": "",
  "progress": 0.0,
  "provider": "openai"
}
```

---

### 2.4 Real-Time Progress Streaming

#### `GET /api/jobs/{id}/events`
- **Protocol**: Server-Sent Events (`text/event-stream`).
- **Events**:
  - `snapshot`: Complete Job object on connection.
  - `progress`:
    ```json
    {
      "stage": "reframe",
      "stage_progress": 0.65,
      "overall": 0.58,
      "message": "Reframing clip 2/3"
    }
    ```
  - `done`: Emitted when all clips and exports are finished.
  - `failed`: Emitted on terminal failure with `{ "error": "..." }`.

---

### 2.5 Clip Retrieval & Review

#### `GET /api/jobs/{id}/clips`
- **Response**: `200 OK`
```json
[
  {
    "id": "clip_abc",
    "job_id": "job_98765",
    "rank": 1,
    "start_s": 42.1,
    "end_s": 75.3,
    "duration_s": 33.2,
    "title": "Why Compilers Fail",
    "hook": "The biggest misconception about compilers...",
    "score": 88,
    "status": "exported",
    "evaluation": {
      "clip_id": "clip_abc",
      "campaign_id": "camp_tech_01",
      "approved": true,
      "final_score": 8.6,
      "hook_score": 9.0,
      "cta_score": 7.0,
      "density_score": 8.5,
      "viral_score": 8.8,
      "hard_failures": [],
      "soft_warnings": []
    },
    "exports": [
      {
        "id": "exp_001",
        "clip_id": "clip_abc",
        "ratio": "9:16",
        "style": "bold_pop",
        "size_bytes": 12458920,
        "download_url": "/api/exports/exp_001/download"
      }
    ]
  }
]
```

#### `GET /api/exports/{id}/download`
- **Response**: `200 OK` (`video/mp4`) download attachment media binary.

---

### 2.6 HTTP Range Video Streaming (In-App Player / ExoPlayer)

#### `GET /api/exports/{id}/stream`
- **Headers Accepted**: `Range: bytes=start-end` (standard HTTP 206 partial content request)
- **Response**: `206 Partial Content` (or `200 OK` if no Range header)
  - `Content-Type: video/mp4`
  - `Accept-Ranges: bytes`
  - `Content-Range: bytes start-end/total`
  - `Content-Disposition: inline; filename="clip_9x16.mp4"`
- **Client Behavior**:
  - Android `VideoView` / `ExoPlayer` or Web `<video>` can seek, stream, and play immediately without buffering the entire multi-megabyte file to disk first.

---

### 2.7 Global Clips Library (Cross-Job Gallery)

#### `GET /api/clips?limit=50`
- **Query Parameters**:
  - `limit`: Integer (optional, default: 50)
- **Response**: `200 OK`
```json
[
  {
    "id": "clip_abc",
    "job_id": "job_98765",
    "rank": 1,
    "start_s": 42.1,
    "end_s": 75.3,
    "duration_s": 33.2,
    "title": "Why Compilers Fail",
    "hook": "The biggest misconception about compilers...",
    "score": 88,
    "status": "exported",
    "evaluation": { ... },
    "exports": [
      {
        "id": "exp_001",
        "clip_id": "clip_abc",
        "ratio": "9:16",
        "style": "bold_pop",
        "size_bytes": 12458920,
        "download_url": "/api/exports/exp_001/download",
        "stream_url": "/api/exports/exp_001/stream"
      }
    ]
  }
]
```

---

### 2.8 Campaign Brief Presets

#### `GET /api/campaigns`
- **Response**: `200 OK`
```json
[
  {
    "id": "camp_tech_01",
    "name": "Tech Insights Campaign",
    "description": "Short form tech punchlines",
    "brief": {
      "id": "camp_tech_01",
      "name": "Tech Insights Campaign",
      "target_clip_count": 3,
      "minimum_duration": 15.0,
      "maximum_duration": 60.0,
      "banned_words": ["crypto", "scam"],
      "required_words": ["software"],
      "hook_required": true,
      "minimum_hook_score": 5.0,
      "cta_required": false,
      "aspect_ratio": "9:16",
      "caption_preset": "bold_pop"
    },
    "created_at": "2026-09-11T12:00:00Z",
    "updated_at": "2026-09-11T12:00:00Z"
  }
]
```

#### `POST /api/campaigns`
- **Payload**:
```json
{
  "id": "camp_custom_01",
  "name": "E-Commerce Virality",
  "description": "High retention product hooks",
  "brief": {
    "name": "E-Commerce Virality",
    "target_clip_count": 3,
    "minimum_duration": 20.0,
    "maximum_duration": 50.0,
    "minimum_hook_score": 7.0
  }
}
```
- **Response**: `201 Created` with CampaignPreset object.

#### `GET /api/campaigns/{id}`
- **Response**: `200 OK` with CampaignPreset object.

#### `DELETE /api/campaigns/{id}`
- **Response**: `204 No Content`

