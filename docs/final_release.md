# AL AMR CLIPPING AUTOMATION — FINAL PRODUCTION RELEASE SPECIFICATION & CERTIFICATION (STEP 14)

## 1. System Overview & Product Mission

**AL AMR Clipping Automation** is a production-grade autonomous system engineered to transform long-form video content into optimized, vertical short-form clips (9:16) with active-speaker face tracking, kinetic captions, campaign guideline compliance evaluation, and automated multi-channel distribution.

The system is designed around a strictly autonomous **3-Input Operator Model**:
1. **Source Video**: Direct media file upload OR public YouTube URL.
2. **Campaign Guidelines**: Brand brief document (PDF/DOCX) OR Google Drive/Docs document.
3. **Publishing Destinations**: Google Drive Vault (archival), Telegram Channel (live publication), and YouTube Shorts (safe dry-run).

Once the operator provides these three inputs and clicks **"Launch Autonomous Clipping"**, AL AMR handles all downstream acquisition, processing, rendering, archiving, and publishing without requiring any operator intervention or technical configuration.

---

## 2. Architecture & Components

```
                                    +---------------------------------------+
                                    |         Operator Interfaces           |
                                    |  (React Web Console & Android Native) |
                                    +-------------------+-------------------+
                                                        |
                                                        v
                                    +---------------------------------------+
                                    |         Control Plane (FastAPI)       |
                                    |  - SQLite Authoritative State         |
                                    |  - SSRF Security Perimeter            |
                                    |  - Guideline Extraction (PDF/DOCX/GD) |
                                    |  - Forensic Job Manifest (/manifest)  |
                                    +-------------------+-------------------+
                                                        |
                                                        v
                                    +---------------------------------------+
                                    |      On-Demand Worker (GitHub/GHA)    |
                                    |  - Headless Container Sandbox         |
                                    |  - Proof-of-Origin Token Service      |
                                    +-------------------+-------------------+
                                                        |
         +----------------------------------------------+----------------------------------------------+
         |                                                                                             |
         v                                                                                             v
+-------------------------------+                                                             +-------------------------------+
|     Source Acquisition        |                                                             |    Media Processing Pipeline  |
| - Primary: yt-dlp             |                                                             | - Faster-Whisper Extraction   |
|   (6 InnerTube Strategies)    |                                                             | - Autonomous LLM / Scoring    |
| - Secondary: HTTP API Stream  |                                                             | - MediaPipe 9:16 Reframing    |
| - Unified Media Validation    |                                                             | - Kinetic Styled Captions     |
| - SHA-256 Provenance Check    |                                                             | - FFmpeg Video Rendering      |
+-------------------------------+                                                             +-------------------------------+
                                                                                                               |
                                                                                                               v
                                                                                              +-------------------------------+
                                                                                              |    Publishing & Distribution  |
                                                                                              | - Google Drive Vault Archive  |
                                                                                              | - Telegram Channel (Live)     |
                                                                                              | - YouTube Shorts (Safe Dry-Run)|
                                                                                              +-------------------------------+
```

---

## 3. Strict 3-Input Operator Experience

Operators are never exposed to internal downloader switches, PO tokens, Deno execution environments, InnerTube clients, browser cookies, proxy parameters, or provider dropdowns:

| Input | Supported Formats | Human-Facing UI Controls |
|---|---|---|
| **Input 1: Source** | Public YouTube URL (`watch?v=`, `youtu.be/`, `shorts/`, `embed/`) OR local video file (`.mp4`, `.mov`, `.mkv`, `.webm`, `.avi`) | Simple URL text box or Drag-and-drop file upload zone |
| **Input 2: Guidelines** | PDF (`.pdf`), Word (`.docx`), Google Drive share link, or Google Docs URL | Drag-and-drop document upload zone or Google Drive link input |
| **Input 3: Destinations** | Google Drive Media Vault, Telegram Channel, YouTube Shorts (Dry-run safe) | Interactive platform selection checkboxes |

---

## 4. Source Acquisition Resilience & Operational Reality Check

### Realistic Production Guarantee
AL AMR **does not** falsely claim that every arbitrary YouTube URL will succeed 100% of the time, as YouTube frequently modifies anti-bot challenges and blocks datacenter cloud IP ranges. 

Instead, the production guarantee is:
1. **Autonomous Multi-Strategy Acquisition**: AL AMR automatically attempts headless media retrieval using upstream `yt-dlp` across 6 resilient InnerTube client configurations (mobile, visionos, tv) alongside local Proof-of-Origin (PO) tokens and Deno JS challenge solving.
2. **Deterministic Fallback**: If the primary provider encounters anti-bot challenges, the system automatically falls back to secondary legitimate HTTP streaming endpoints (`AUTOCLIP_ACQUISITION_ENDPOINT`) if configured.
3. **Clean Actionable Failure**: If all providers are blocked or the media cannot be acquired, AL AMR immediately fails safely without Python or yt-dlp tracebacks, presenting the operator with clear guidance:
   > *"AL AMR could not acquire source media automatically from this link across all configured acquisition providers. The source may be restricted, blocked, or unavailable. Please upload the video file directly to proceed."*

---

## 5. Security Perimeter & SSRF Controls

Located in `backend/autoclip/pipeline/source_acquisition/security.py`:
- **SSRF Prevention**: All remote URLs are validated prior to network connections. Requests targeting `127.0.0.1`, `localhost`, RFC 1918 private subnets (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), link-local addresses (`169.254.0.0/16`), and cloud instance metadata endpoints (`169.254.169.254`, `metadata.google.internal`) are blocked.
- **Protocol Whitelist**: Only `http://` and `https://` are permitted. Dangerous protocols (`file://`, `ftp://`, `gopher://`, `javascript:`, `data:`) are rejected immediately.
- **Directory Traversal**: Output destination paths are strictly sandboxed using `safe_target_path()`.
- **Media Validation Gate**: Acquired files are validated via `validate_media_gate()` to ensure non-empty files, reject disguised HTML error documents, confirm valid FFprobe container streams with audio/video, and generate SHA-256 provenance hashes.

---

## 6. Web Console & Android Client Parity

Both operator applications have been audited and verified for feature parity:
- **React Web Console**: Clean 3-input New Job screen, real-time SSE progress streaming (`useJobStream`), robust 401 handling via `AuthModal` (preventing infinite loading spinners), and interactive clip review.
- **Android Native Client**: Jetpack Compose application mirroring the 3-input model, built and verified across both `assembleDebug` and `assembleRelease`.

---

## 7. Publishing & Safe Distribution Defaults

- **Google Drive Vault**: Durable cloud media archival with persistent File IDs and web-view links.
- **Telegram**: Production-grade direct publishing to designated channels via Bot API.
- **YouTube Shorts**: Safe **DRY-RUN** mode by default (`status="ready_for_upload"`). Live uploads are only enabled when `YOUTUBE_PUBLISH_LIVE=true` is explicitly configured.

---

## 8. Forensic Job Manifest (`/api/jobs/{job_id}/manifest`)

The authoritative forensic manifest answers **"What happened to this clip?"** without exposing raw logs or secrets:
- Complete source provenance (URL, filename, SHA-256 hash, duration, size, provider name, attempt count).
- Campaign guideline brief and extraction telemetry (word count, char count, drive file ID).
- Scoring breakdown per candidate clip (hook score, density score, CTA score, viral score).
- MediaPipe active-speaker crop paths and kinetic subtitle styles.
- Drive archival links and external publishing records.
- GitHub runner job ID, run status, and execution timestamps.
