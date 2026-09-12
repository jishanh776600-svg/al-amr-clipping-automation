# AL AMR Clipping Automation — Autonomous Production Orchestration & Job Lifecycle

## 1. Overview & Architecture

The AL AMR Autonomous Production Orchestration system ensures that long-form media processing, multi-stage AI analysis, vertical 9:16 video generation, persistent cloud storage in Google Drive, and multi-platform publishing (Telegram, YouTube Shorts, Instagram Reels) execute with complete reliability, durability, and fault tolerance.

The Render Control Plane (FastAPI + SQLite) acts as the **single authoritative source of truth**. GitHub Actions compute runners, worker processes, and mobile/web clients are untrusted execution engines and thin-client controllers.

```
OPERATOR INTERFACE (Web Console / Android APK)
                     │
                     ▼
          RENDER CONTROL PLANE (FastAPI)
                     │
    ┌────────────────┼────────────────┐
    ▼                ▼                ▼
Authoritative   Idempotent     Crash Recovery &
State Machine    Dispatch        Stale Sweeper
    │                │                │
    └────────────────┬────────────────┘
                     ▼
        GITHUB ACTIONS CLOUD WORKER (Ubuntu)
                     │
         [Heartbeats & Progress Callbacks]
                     │
                     ▼
           GOOGLE DRIVE VAULT (5TB)
                     │
                     ▼
             PUBLISHING ENGINE
         (Telegram, YouTube, Instagram)
                     │
                     ▼
           AUTHORITATIVE MANIFEST
```

---

## 2. Authoritative Job State Machine

All job states and transitions are strictly validated in `backend/autoclip/jobs/orchestrator.py`:

```
          ┌───────────────────────────────────────────────┐
          │                                               │
          ▼                                               │ (Retry)
      [QUEUED] ──────────────────────────┐                │
          │                              │                │
          ▼                              ▼                │
    [DISPATCHING]                  [CANCELLED] ───────────┤
          │                              ▲                │
          ▼                              │ (Cancel)       │
      [RUNNING] ─────────────────────────┤                │
          │                              │                │
          ├──────────────┐               │                │
          ▼              ▼               │                │
    [PROCESSING] ─► [UPLOADING] ─► [PUBLISHING]           │
          │              │               │                │
          ▼              ▼               ▼                │
      [FAILED] ──────────────────────────┴────────────────┘
          │                                 (Retry)
          ▼
        [DONE] (Terminal authoritative state)
```

### Valid Transitions:
- `queued` $\to$ `dispatching`, `running`, `cancelled`, `failed`
- `dispatching` $\to$ `running`, `failed`, `cancel_requested`, `cancelled`
- `running` $\to$ `processing`, `uploading`, `publishing`, `done`, `failed`, `cancel_requested`, `cancelled`
- `processing` $\to$ `uploading`, `publishing`, `done`, `failed`, `cancel_requested`
- `uploading` $\to$ `publishing`, `done`, `failed`, `cancel_requested`
- `publishing` $\to$ `done`, `failed`, `cancel_requested`
- `cancel_requested` $\to$ `cancelled`, `failed`, `done`
- `failed` $\to$ `queued`, `dispatching` (via operator or automated retry)
- `cancelled` $\to$ `queued`, `dispatching` (via operator retry)
- `done` $\to$ terminal (immutable)

---

## 3. Worker Telemetry, Heartbeats & Stale Detection

1. **Heartbeat Tracking**:
   - The worker periodically emits progress and stage heartbeats.
   - The control plane records `last_heartbeat_at = utcnow()` in SQLite on every callback.
2. **Stale Job Detection**:
   - Configurable timeout: `AUTOCLIP_WORKER_HEARTBEAT_TIMEOUT_SECONDS` (default: 300 seconds).
   - If a job is `running`, `dispatching`, `processing`, or `uploading` with no heartbeat within the threshold, the background sweeper marks `stale_at = utcnow()`.
   - If `attempt < max_attempts` and the failure is retryable, the orchestrator automatically schedules a recovery attempt.
   - If `attempt >= max_attempts`, the job transitions to `failed`.

---

## 4. Automatic Retry Policy & Exponential Backoff

1. **Classification**:
   - **Retryable**: Network timeouts, GitHub runner interruptions, temporary API 503s, Google Drive rate limits.
   - **Non-retryable**: Invalid source media, missing source files, unsupported media formats, permanently revoked credentials, operator cancellation.
2. **Attempt Limits**:
   - Configurable max attempts: `AUTOCLIP_MAX_ATTEMPTS` (default: 3).
   - Each retry increments `attempt` and clears prior transient errors.
3. **Exponential Backoff**:
   - Formula: $\text{delay} = \min(\text{max\_s}, \text{base\_s} \times 2^{\text{attempt} - 1})$
   - Configured via `AUTOCLIP_RETRY_BASE_DELAY_SECONDS` (default: 5.0s) and `AUTOCLIP_RETRY_MAX_DELAY_SECONDS` (default: 60.0s).

---

## 5. Job Cancellation Semantics

1. Operator triggers `POST /api/jobs/{id}/cancel`.
2. For `queued` jobs: immediately marked `cancelled`.
3. For `running` / `dispatching` jobs:
   - State set to `cancel_requested`.
   - Control plane signals in-process local runner (if local) or dispatches `POST /repos/{repo}/actions/runs/{run_id}/cancel` to GitHub Actions API (if remote).
   - Worker detects `cancel_requested` on its next heartbeat callback, aborts the pipeline, reports `cancelled`, and exits cleanly.

---

## 6. Callback Hardening & Idempotency

1. **Token Authentication**: All callbacks require `Authorization: Bearer <token>` or `X-API-Key: <token>` matching server secrets.
2. **Duplicate Callback Idempotency**:
   - Multiple `done` callbacks for the same job are safe no-ops.
   - Ingestion of clips, campaign evaluations, exports, and publishing records uses database-level conflict resolution (`ON CONFLICT DO UPDATE`), preventing duplicates.
3. **Anti-Regression**:
   - A callback attempting to move a job from `done` to `running` is safely ignored.

---

## 7. Crash Recovery on Startup

When the Render Control Plane restarts:
1. `orchestrator.reconcile_on_startup()` runs during FastAPI lifespan initialization.
2. Interrupted local jobs are checked: incomplete or corrupt exports are purged, and valid jobs are requeued.
3. In-flight GitHub Actions jobs are preserved without false termination; when the cloud worker sends its next callback, the control plane ingests the progress.
4. An immediate stale sweep detects any jobs whose workers died during the downtime.

---

## 8. Authoritative Job Manifest

The `GET /api/jobs/{id}/manifest` endpoint produces a complete forensic record answering *"What happened to this clip?"*:
- Full job metadata, dispatch mode, and attempt count.
- GitHub Actions run ID, workflow name, status, and URL.
- Source media details.
- Generated clips, hooks, duration, and campaign evaluation scores.
- Export records: resolution, ratio, style, size, Google Drive file ID, Drive web view link, and storage key.
- Publishing records: per-platform status, message/video IDs, destinations, and error logs.
