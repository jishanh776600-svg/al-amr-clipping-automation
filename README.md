# AL AMR CLIPPING AUTOMATION
**Autonomous Video Intelligence & Vertical Media Engine**

> **Autonomous, zero-mandatory-cost, device-independent vertical video clipping pipeline and operations console.**  
> Transforms Campaign PDFs and long-form video assets into platform-ready 9:16 vertical shorts with active speaker reframing, kinetic subtitles, automated compliance checking, Telegram human-in-the-loop governance, and YouTube Shorts scheduling.

---

## 🏗️ Cloud Operational Topology

```
                   ┌────────────────────────────────────────────────┐
                   │                  OPERATOR                      │
                   │ (Browser Console / Telegram Smartphone Client) │
                   └───────┬────────────────────────────────┬───────┘
                           │                                │
                           ▼                                ▼
            ┌─────────────────────────────┐   ┌─────────────────────────────┐
            │   AL AMR MASTER CONSOLE     │   │   TELEGRAM APPROVAL GATE    │
            │   (Render Free Web Service) │   │ (Async Human Decision Bot)  │
            └──────────────┬──────────────┘   └──────────────┬──────────────┘
                           │                                 │
                           │  Read / Mutate Canonical State  │
                           ▼                                 ▼
            ┌───────────────────────────────────────────────────────────────┐
            │                     GOOGLE DRIVE STORAGE                      │
            │       (Canonical State, Control Locks, Videos, Receipts)      │
            └──────────────┬────────────────────────────────┬───────────────┘
                           │                                │
        Dispatch / Polling │                                │ Fetch / Store Checkpoints
                           ▼                                ▼
            ┌───────────────────────────────────────────────────────────────┐
            │              EPHEMERAL GITHUB ACTIONS WORKERS                 │
            │  (Standard Ubuntu Public Runners: Whisper, CV, FFmpeg Render) │
            └──────────────────────────────┬────────────────────────────────┘
                                           │
                                           │ Quota-Managed Scheduled Release
                                           ▼
                            ┌──────────────────────────────┐
                            │    YOUTUBE DATA API V3       │
                            │   (Published Shorts Video)   │
                            └──────────────────────────────┘
```

---

## 🎯 Core Architectural Tenets

1. **Zero Mandatory Software & API Cost ($0 Target)**
   - Render Free-Tier Web Service (FastAPI Master Control plane).
   - Standard Public GitHub Actions Runners (Ephemeral CPU execution workers).
   - Google Drive Storage (Canonical persistent state, media artifacts, audit records).
   - Permissively licensed open-source models (`faster-whisper`, `pyannote`, OpenCV, PySceneDetect).
2. **True Device Independence**
   - The user's personal PC, local disk, and home internet are **not required** for runtime execution.
   - The system operates continuously with the user's PC completely powered off.
3. **Canonical 9-Stage Pipeline Sequence**
   `01 INGESTION` $\rightarrow$ `02 TRANSCRIPTION` $\rightarrow$ `03 UNDERSTANDING` $\rightarrow$ `04 DISCOVERY` $\rightarrow$ `05 REFRAME` $\rightarrow$ `06 RENDER` $\rightarrow$ `07 QA` $\rightarrow$ `08 APPROVAL` $\rightarrow$ `09 PUBLISH`.
4. **Master Control & Emergency Operations**
   - Durable emergency stop and publishing lock in Google Drive (`system/control_state.json`).
   - Cooperative worker cancellation between pipeline checkpoints.
   - Distributed job lease management (`jobs/{job_id}/lease.json`) to prevent concurrent runner collision.
5. **Human-in-the-Loop Governance**
   - Asynchronous Telegram approval messages with per-clip [Approve] / [Reject] buttons.
   - Media-first AL AMR Web Console with 9:16 vertical cinema preview, kinetic subtitle overlay, and virality scoring.

---

## 🚀 Cloud Deployment Instructions

### 1. Render Control Console Deployment
1. Connect the repository to **Render** as a **Web Service**.
2. Select **Runtime:** Python 3.
3. **Build Command:** `pip install -e .`
4. **Start Command:** `uvicorn clipping.ui.server:app --host 0.0.0.0 --port $PORT`
5. **Health Check Path:** `/healthz`
6. Configure the following environment variables in Render:
   - `ENVIRONMENT`: `production`
   - `STORAGE_DRIVER`: `gdrive`
   - `GOOGLE_DRIVE_ROOT_FOLDER_ID`: Remote root folder ID in Google Drive.
   - `GOOGLE_APPLICATION_CREDENTIALS_JSON`: Base64 or plain JSON of the Google Cloud Service Account.
   - `OPERATOR_TOKEN`: Master Control secret token for authorizing mutating requests.
   - `GITHUB_REPO`: `owner/repo` for GitHub Actions workflow dispatch.
   - `GITHUB_PAT`: Fine-grained Personal Access Token with `actions:write` permission.
   - `TELEGRAM_BOT_TOKEN` & `TELEGRAM_CHAT_ID`: Telegram bot credentials.
   - `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN`: YouTube Data API credentials.

### 2. GitHub Actions Ephemeral Compute Deployment
Configure GitHub Repository Secrets (`Settings` $\rightarrow$ `Secrets and variables` $\rightarrow$ `Actions`):
- `GOOGLE_APPLICATION_CREDENTIALS_JSON`
- `GOOGLE_DRIVE_FOLDER_ID`
- `HF_TOKEN` (Optional for gated models)
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
- `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN`

---

## 🛠️ Operational Runbook

| Operation | Trigger | Effect |
|:---|:---|:---|
| **Run Pipeline Now** | Web Console: `⚡ RUN NOW` | Creates job record in Google Drive and dispatches `pipeline_orchestration.yml` via GitHub API. |
| **Emergency Stop** | Web Console: `🛑 EMERGENCY STOP` | Writes `EMERGENCY_STOPPED` to `system/control_state.json`. Blocks new runs, pauses workers at checkpoints, and defers publishing. |
| **Resume Automation** | Web Console: `▶ RESUME AUTOMATION` | Clears emergency stops and pause locks, returning system to `OPERATIONAL`. |
| **Publishing Lock** | Web Console: `🔒 LOCK PUBLISHING` | Blocks all YouTube Shorts uploads while keeping approved clips intact in Google Drive. |
| **Telegram Approval** | Telegram: `✅ Approve` / `❌ Reject` | Asynchronously updates approval status in Google Drive and logs immutable audit trail. |
| **Scheduled Publishing** | GitHub Actions: `youtube_scheduler.yml` | Scans for due approved clips, verifies quota and control gates, uploads to YouTube, and records receipts. |

---

## 📁 Repository Structure

```
automation_clipping/
├── docs/
│   ├── MASTER_SPECIFICATION.md
│   ├── ARCHITECTURE.md
│   └── ROADMAP.md
├── src/
│   └── clipping/
│       ├── core/           # Interfaces, Constants, Workspaces, Settings
│       ├── control/        # Master Control, Emergency Operations, GitHub Dispatcher
│       ├── storage/        # StorageDriver (GoogleDrive, LocalVault, S3)
│       ├── state/          # Remote State Repository, Distributed Lease Manager
│       ├── document/       # Campaign PDF Parser (Docling)
│       ├── ingestion/      # Remote Video Ingestor (yt-dlp)
│       ├── perception/     # faster-whisper, pyannote Diarization
│       ├── director/       # Face Tracking, Active Speaker Resolver, Kalman 9:16 Reframe
│       ├── intelligence/   # Semantic Window Discovery, Qwen2.5 Virality Scoring
│       ├── rendering/      # SubtitleEngine (ASS Karaoke), CPU FFmpeg Compositor
│       ├── qa/             # 5-Layer Automated QA Engine (Media, LUFS, Text Bounds)
│       ├── approval/       # Telegram Approval Gateway & Dispatcher
│       ├── publishing/     # YouTube Data API v3 Publisher, Scheduler, Granular Quota
│       └── ui/             # AL AMR Web Console (FastAPI Server + HTML5 UI)
├── tests/                  # 143 Unit, Contract, Integration & Cloud Tests
├── scripts/                # Screenshot Capture, Utility Scripts
├── render.yaml             # Render Free Web Service Deployment Blueprint
├── pyproject.toml          # Build configuration & dependencies
└── README.md
```

---

## 🧪 Verification & Test Suite

Run the full test suite across all 12 phases:
```bash
pytest -v
```
**143 passing tests across all modules (100% pass rate).**
