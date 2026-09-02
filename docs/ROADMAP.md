# CLIPPING AUTOMATION — 13-PHASE IMPLEMENTATION ROADMAP

**Document Version:** 1.0.0  
**Status:** APPROVED ENGINEERING ROADMAP  
**Testing Rule:** Do **NOT** run full end-to-end regression tests after every step. Run targeted unit/contract tests for the active phase; run full-cycle integration tests only at explicit major milestones.

---

## 📅 PHASES OVERVIEW

```
Phase 1  ──► Foundation, Data Contracts & Storage Abstraction
Phase 2  ──► Campaign PDF Document Parser (Docling)
Phase 3  ──► Source Video Ingestion & Audio Perception (faster-whisper + pyannote)
Phase 4  ──► Visual Perception & ActiveSpeakerResolver (PySceneDetect + ByteTrack)
Phase 5  ──► Virtual Camera Director & 9:16 Reframe Engine (Kalman Smoothing)
Phase 6  ──► AI Clip Discovery & Compliance Scoring (TextTiling + Qwen2.5-7B)
Phase 7  ──► Kinetic Subtitle Engine & Native FFmpeg Renderer (pysubs2 ASS)
Phase 8  ──► Layered Automated QA (L1-L5) & Deduplication (videohash)
Phase 9  ──► Telegram Approval Gateway & Human-in-the-Loop Bot (aiogram v3)
Phase 10 ──► YouTube Publishing & Quota-Managed Scheduling
Phase 11 ──► Independent Clipping Studio UI (Next.js + react-timeline-editor)
Phase 12 ──► Headless Cloud Deployment & Autonomous Daemon Operation
Phase 13 ──► Performance Optimization, GPU Memory Pooling & Scaling
```

---

## 📋 DETAILED PHASE BREAKDOWN

### Phase 1: Foundation, Data Contracts & Storage Abstraction [COMPLETED]
- **Objective:** Establish the core project scaffolding, configuration manager, strict Pydantic schemas, and pluggable `StorageDriver` (Local Vault + Google Drive API v3) with asynchronous SQLite/PostgreSQL state tracking.
- **Status:** COMPLETED & VERIFIED (22 unit tests passing).

---

### Phase 2: Campaign PDF Document Ingestion & Remote Video Ingestion [COMPLETED]
- **Objective:** Build `DoclingCampaignParser` (with pure-Python fallback) and `RemoteVideoIngestor` (`yt-dlp` adapter) preserving bounding box provenance and writing canonical assets to Google Drive vault.
- **Status:** COMPLETED & VERIFIED (10 unit tests passing).

---

### Phase 3: Audio Perception Engine [COMPLETED]
- **Objective:** Speech-to-text transcription (`faster-whisper`), acoustic speaker diarization (`pyannote.audio` + CPU fallback), deterministic temporal word-to-speaker attribution, and canonical perception artifacts.
- **Status:** COMPLETED & VERIFIED (11 unit tests passing).

---

### Phase 4: Video Understanding & CPU-First Virtual Camera Director [COMPLETED]
- **Objective:** CPU-first video perception and 9:16 portrait reframing:
  1. `PySceneDetectEngine`: Physical shot cut detection with exact frame mapping and scene-boundary camera smoothing hard resets.
  2. `ByteTrackCpuTracker`: Multi-face tracking maintaining stable track IDs across frames with IoU and Kalman association.
  3. `DeterministicActiveSpeakerResolver`: Multi-modal acoustic + visual fusion without TalkNet-ASD.
  4. `KalmanVirtualCameraDirector`: Smooth 9:16 crop trajectory calculation ($1080\times1920$), speaker hysteresis ($1.5-2.0\text{s}$), and strict geometric boundary safety.
  5. **Clip Selection Policy:** Pipeline architecture configured for 5 minimum / 10 maximum high-quality clips per long-form source.
- **Status:** COMPLETED & VERIFIED (12 unit tests passing).

---

### Phase 5: Subtitle Typography & CPU-First FFmpeg Rendering Core [COMPLETED]
- **Objective:** Word-level kinetic subtitle generation and secure FFmpeg video rendering:
  1. `AssSubtitleGenerator`: Clip-local timestamp normalization, word grouping ($3-4$ words/card), karaoke timing (`\k` tags), and safe-zone margin configuration.
  2. `SubtitleStyleConfig`: High-impact typography presets (`Kinetic Gold`, `Neon Cyan`, `Classic White`).
  3. `FFmpegFiltergraphBuilder`: Dynamic piece-wise crop translation with scale to $1080\times1920$ and subtitle burning.
  4. `FFmpegRenderer`: Secure subprocess execution (argument vectors, no shell injection, streamable faststart MP4).
  5. `RenderOrchestrationEngine`: End-to-end media rendering, StorageDriver artifact persistence (`subtitles.ass`, `final_1080x1920.mp4`, `render_output.json`), and complete idempotency.
- **Status:** COMPLETED & VERIFIED (14 unit tests passing).

---

### Phase 6: Clip Candidate Generation, Semantic Scoring, Ranking & Selection [COMPLETED]
- **Objective:** Deterministic semantic candidate discovery and yield governance without paid LLM APIs:
  1. `CandidateWindowGenerator`: Conversational sentence/turn windowing ($20-60\text{s}$).
  2. `DeterministicClipScorer`: Explainable quality scoring breakdown (Hook, Completeness, Curiosity, Specificity, Emotion, Standalone, Visual signals, and Filler/Silence/Repetition penalties).
  3. `CandidateDeduplicator`: Temporal IoU and token Jaccard Non-Maximum Suppression.
  4. `ClipSelector`: Strict quality floor enforcement, 5 minimum / 10 maximum clip selection yield policy without artificial padding.
  5. `ClipDiscoveryEngine`: Full pipeline orchestration and StorageDriver persistence (`candidates.json`, `ranked_candidates.json`, `selected_clips.json`).
- **Status:** COMPLETED & VERIFIED (11 unit tests passing, synthetic 2.5h benchmark verified).

### Phase 7: End-to-End Clip QA, Real-Media Validation & Production Readiness Gate [COMPLETED]
- **Objective:** Production-oriented QA and validation layer verifying entire pipeline execution:
  1. `MediaProber`: Stream probing with ffprobe and OpenCV fallback.
  2. `QAEngine`: Layered checks (media integrity, container, $1080\times1920$ resolution, duration tolerance, subtitle ASS integrity, reframe geometry, and cross-artifact ID consistency).
  3. `QAGatingPolicy`: Explicit `CRITICAL` vs `WARNING` pass/fail gating determining `can_publish`.
  4. Real-media end-to-end integration verified ($1920\times1080 \rightarrow 1080\times1920$ rendering, subtitle burning, and QA probing).
- **Status:** COMPLETED & VERIFIED (9 unit & integration tests passing).

### Phase 8: Cloud Execution Architecture, GitHub Actions Foundation & Remote Job Orchestration [COMPLETED]
- **Objective:** Cloud-native, zero-cost execution foundation on standard GitHub Actions Ubuntu runners:
  1. `WorkerScratchWorkspace`: Isolated ephemeral workspace with path traversal containment and automatic cleanup.
  2. `RemoteStorageStateRepository`: Zero-cost durable job state persistence directly into Google Drive (`jobs/{id}/state.json`), enabling cross-runner resumption without paid external databases.
  3. Workflows: Created `.github/workflows/cloud_smoke_test.yml` and `.github/workflows/pipeline_orchestration.yml`.
  4. Repository Isolation: Configured for clean deployment into a completely new, dedicated GitHub account and repository.
- **Status:** COMPLETED & VERIFIED (6 unit & integration tests passing, 95 total suite passing).

### Phase 9: Telegram Approval Gateway & Human-in-the-Loop Bot [COMPLETED]
- **Objective:** Production-grade Telegram Human Approval Gateway keeping automation cloud-native and resumable:
  1. `ApprovalModels`: Typed Pydantic models (`AWAITING_APPROVAL`, `APPROVED`, `REJECTED`), compact callback protocol (`v1:<A|R>:<id>` under 64 bytes), and audit trail records.
  2. `ApprovalRepository`: Durable state persistence in Google Drive (`jobs/{job_id}/approvals/{req_id}.json` and `jobs/{job_id}/approvals/audit/`).
  3. `SecurityValidator`: Enforces user authorization, chat authorization, replay protection, and prevents sensitive token/path leakage.
  4. `TelegramTransport`: Isolated HTTP Bot API transport with exponential backoff on 429 rate limits, and in-memory mock transport for testing.
  5. `TelegramApprovalDispatcher`: Cloud-friendly update consumer polling callbacks without keeping long-running GitHub Actions alive.
  6. `TelegramApprovalGateway`: Facade for candidate clip dispatch and per-clip approval aggregation (`ApprovalSummary`).
- **Status:** COMPLETED & VERIFIED (15 unit & integration tests passing, 110 total suite passing).

---

### Phase 10: YouTube Publishing & Limit-Aware Scheduling [COMPLETED]
- **Objective:** Production-grade YouTube Data API v3 publishing and scheduling subsystem:
  1. `PublishingModels`: Data contracts for `PublishRequest`, `PublishStatus` (`READY`, `PUBLISHING`, `PUBLISHED`, `FAILED`, `SKIPPED`, `DEFERRED`), `PrivacyStatus`, `YouTubeVideoMetadata`, and `PublishAuditRecord`.
  2. `Gates`: Absolute enforcement of Approval Gate (must be `APPROVED` in canonical Drive state) and QA Gate (must have passed all checks with `can_publish = True`).
  3. `OAuthTokenManager`: Automatic unattended access-token refresh from long-lived refresh token via Google OAuth2 with zero credential leakage.
  4. `Client`: Resumable video upload protocol with dynamic error parsing distinguishing `quotaExceeded` (retryable/deferred), `uploadLimitExceeded` (channel-level daily limit, retryable/deferred), `rateLimitExceeded` (retryable), and permanent errors.
  5. `Idempotency & Remote State`: Deterministic idempotency indexing (`publishing/by_idempotency/{hash}.json`), preventing duplicate uploads across job retries.
  6. `PublishingScheduler`: UTC-based scheduled publication scanner and catch-up engine (`.github/workflows/youtube_scheduler.yml`).
  7. `Channel Identity`: Explicit verification against target `channel_id` before upload.
- **Status:** COMPLETED & VERIFIED (16 unit & integration tests passing, 126 total suite passing).

---

### Phase 11 / 11B: AL AMR Clipping Automation Console & Master Control Hardening [COMPLETED]
- **Objective:** Production-grade media-first control plane and visual operations console (**AL AMR Clipping Automation // Autonomous Video Intelligence & Vertical Media Engine**):
  1. `Canonical 9-Stage Pipeline Sequence`: `01_INGESTION` $\rightarrow$ `02_TRANSCRIPTION` $\rightarrow$ `03_UNDERSTANDING` $\rightarrow$ `04_DISCOVERY` $\rightarrow$ `05_REFRAME` $\rightarrow$ `06_RENDER` $\rightarrow$ `07_QA` $\rightarrow$ `08_APPROVAL` $\rightarrow$ `09_PUBLISH`.
  2. `Master Control & Emergency Operations`: Durable global state (`system/control_state.json`), Emergency Stop with cooperative cancellation, global publishing lock, and automation pause/resume.
  3. `Operator Authorization`: Zero-cost token-based authentication (`X-Operator-Token` / `Authorization: Bearer <token>`) protecting mutating endpoints from unauthorized public access.
  4. `Media-First Canvas & Discovery Matrix`: 9:16 vertical cinema player, EBU safe zone indicators, simulated face tracking bounding box, kinetic karaoke subtitle preview, multi-track alignment scrubber, and candidate discovery matrix with virality score breakdowns.
  5. `Render Deployment Ready`: `render.yaml` configuring lightweight FastAPI web service on Render's free tier with zero local state dependency.
  6. `Visual Validation`: Headless browser screenshots captured and verified across Desktop (1920x1080) and Mobile (390x844).
- **Implemented Files:**
  - `src/clipping/control/models.py`, `repository.py`, `service.py`, `__init__.py`
  - `src/clipping/ui/server.py`, `src/clipping/ui/static/index.html`
  - `render.yaml`, `scripts/capture_ui_screenshots.py`
  - `tests/control/test_master_control.py`, `tests/ui/test_ui_app.py`
- **Status:** COMPLETED & VERIFIED (9 UI & Master Control tests passing, 135 total suite passing).

---

### Phase 12: AL AMR Clipping Automation Cloud Deployment & Autonomous Operation [COMPLETED]
- **Objective:** Production-grade cloud deployment, device-independent autonomous operation, distributed job lease locking, and Master Control cloud integration:
  1. `Render Master Control Web Service`: Always-online lightweight FastAPI/Uvicorn control plane (`render.yaml`) serving UI, health/readiness probe (`/healthz`), and Master Control operations.
  2. `Distributed Job Lease / Locking`: Concurrency control (`src/clipping/state/lease.py` $\rightarrow$ `jobs/{job_id}/lease.json`) preventing duplicate execution by concurrent GitHub Actions runners with automatic stale worker recovery.
  3. `Cooperative Emergency Cancellation`: Cloud runners check `ControlRepository` at pipeline checkpoints and halt cooperatively when Emergency Stop is active.
  4. `GitHub Actions Control Plane Integration`: `GitHubWorkflowDispatcher` triggering `pipeline_orchestration.yml` via REST API upon `RUN NOW` invocation.
  5. `Device Independence`: Zero runtime dependency on user PC, local disk, or home network.
- **Implemented Files:**
  - `src/clipping/state/lease.py`
  - `src/clipping/control/github.py`
  - `src/clipping/cli/pipeline_runner.py` (updated with lease acquisition and cooperative cancellation)
  - `src/clipping/ui/server.py` (updated with `/healthz`, `/api/control/runs`, and GitHub dispatch)
  - `render.yaml`
  - `tests/cloud/test_cloud_deployment_and_operations.py`
- **Status:** COMPLETED & VERIFIED (8 cloud deployment tests passing, 143 total suite passing).

---

### Phase 13: Performance Optimization, GPU Pooling & Scaling
- **Objective:** Implement PyTorch CUDA memory pooling, batch inference optimization, and multi-channel publishing scaling.
- **Dependencies:** Phase 12.
- **Expected Files:**
  - `src/clipping/core/gpu_manager.py`
  - `src/clipping/core/batch_processor.py`
- **Acceptance Criteria:** 1-hour source video fully processed and rendered into 5 vertical shorts in $<6\text{ minutes}$ on GPU or $<25\text{ minutes}$ on pure CPU.
- **Validation:** Full performance benchmark suite.
