# AL AMR — Control Plane Architecture (Render)

## 1. Responsibilities

The Control Plane (`backend/autoclip/api/`, `app.py`) serves as the central command node:
- **API & Console Gateway**: Exposes REST APIs for Web Console UI, Android client, and administrative tools.
- **Orchestration**: Manages job creation, validation, dispatching, and status polling.
- **Durable Vault**: Manages AES-256 encrypted credential storage and auto-hydration.
- **Telegram Webhook / Polling**: Receives and processes operator review callbacks.
- **Publishing Orchestration**: Executes authenticated uploads to YouTube Shorts and Instagram Reels upon approval.

---

## 2. Lifespan & Threading Architecture

```mermaid
sequenceDiagram
    participant Render as Render Supervisor
    participant App as FastAPI App (lifespan)
    participant Sweeper as Stale Sweeper (asyncio)
    participant Ticker as Publishing Ticker (asyncio)
    participant Poller as Telegram Poller (asyncio)
    participant DB as SQLite Engine

    Render->>App: Startup
    App->>DB: Checkpoint & Schema Migrations
    App->>Sweeper: Start background task (sweep_stale_jobs)
    App->>Ticker: Start background task (process_due_queue)
    alt Webhook Not Configured
        App->>Poller: Start poll_telegram_updates()
    end
    App-->>Render: Ready (HTTP 200)

    Render->>App: SIGTERM / Shutdown
    App->>Sweeper: Cancel task
    App->>Ticker: Cancel task
    App->>Poller: Cancel task
    App->>DB: PRAGMA wal_checkpoint(TRUNCATE)
    App-->>Render: Clean Exit
```

---

## 3. Render Deployment Blueprint (`render.yaml`)
- **Service Type**: `web`
- **Environment**: `docker`
- **Disk Mount**:
  - Name: `autoclip-data`
  - Mount Path: `/data`
  - Size: 1 GB
- **Environment Variables Configured**:
  - `AUTOCLIP_HOME=/data`
  - `AUTOCLIP_NO_WORKER=1`
  - `AL_AMR_MASTER_KEY` (or generated on first startup)
  - `OPERATOR_TOKEN`
  - `WORKER_CALLBACK_SECRET`
  - `GITHUB_PAT`
  - `GITHUB_REPO`
