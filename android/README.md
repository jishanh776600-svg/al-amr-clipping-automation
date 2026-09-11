# AL AMR Mobile Operator Console (Android Thin Client)

## 0. Non-Negotiable Architecture Invariant
**The Android application is STRICTLY A THIN CLIENT.**

The mobile device NEVER executes:
- Whisper transcription
- LLM candidate ranking or hook scoring
- MediaPipe active speaker detection
- Video reframing or cropping
- Caption rendering
- FFmpeg rendering
- Local clip extraction

All heavy workloads run on the persistent AL AMR remote compute node. The Android app acts solely as an **operator control surface** to monitor, trigger, review, and dispatch jobs.

---

## 1. Disconnected Invariant
The remote server is the authoritative state machine. When an operator:
- Submits a job and turns off their phone
- Closes the app or disconnects from Wi-Fi
- Restarts their device

**Server processing continues completely uninterrupted.** Upon reopening the app, the client immediately syncs authoritative state from `GET /api/jobs/{id}` and resumes live monitoring.

---

## 2. Media Streaming Architecture
The Android client never downloads the entire video file before playback. Instead, it streams directly using standard **HTTP Range Requests (RFC 7233)** via:
```http
GET /api/exports/{export_id}/stream
```
- Returns `206 Partial Content` with `Accept-Ranges: bytes`
- Instant scrubbing and playback inside Android `VideoView` / `ExoPlayer`
- Drastically reduces mobile bandwidth and eliminates phone storage bloat

---

## 3. Remote Authentication
Supports optional token authentication:
- Transmitted as `Authorization: Bearer <token>` and `X-API-Key: <token>`
- Persisted locally in Android `SharedPreferences`
- Validated via `GET /ready` and `GET /health`

---

## 4. How to Build & Run

### Prerequisites
- JDK 21 (installed)
- Android SDK (Platform 36+, Build Tools 36.0.0+)

### Building Debug APK
From this `android/` directory:
```bash
./gradlew assembleDebug
```
The resulting APK is generated at:
```
app/build/outputs/apk/debug/app-debug.apk
```

### Installation
```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
```
