# Third-Party Components & License Compliance Notice

This document records third-party component references, architectural inspirations, and open-source licensing compliance for **AL AMR CLIPPING** (`al-amr-clipping-automation`).

---

## 1. Architectural References & Clean-Room Boundaries

### SupoClip
- **License**: GNU Affero General Public License v3.0 (AGPL-3.0)
- **Usage**: **Architecture Reference ONLY**.
- **Compliance Boundary**:
  - **Zero code** from SupoClip is copied, vendored, imported, linked, or embedded within this repository.
  - All pipeline engines (`RemoteVideoIngestor`, `FasterWhisperTranscriptionEngine`, `KalmanVirtualCameraDirector`, `DeterministicClipScorer`, `AssSubtitleGenerator`, `QAEngine`, `TelegramApprovalGateway`) are custom, clean-room implementations developed specifically for AL AMR CLIPPING.
  - No AGPL-3.0 copyleft taint applies to this repository.

---

## 2. Permissive Open-Source References (MIT License)

### OpenClip
- **License**: MIT License
- **Usage**: Conceptual reference for dynamic face bounding box smoothing and aspect ratio transformation.

### Chopify
- **License**: MIT License
- **Usage**: Heuristic reference for speech pause segmentation and silence detection boundaries.

### ClippyMe
- **License**: MIT License
- **Usage**: Stylistic reference for Advanced SubStation Alpha (`.ass`) karaoke formatting tags and safe-zone positioning.

---

## 3. Runtime Libraries & External Tools

| Library / Tool | Upstream License | Purpose in AL AMR CLIPPING |
| :--- | :--- | :--- |
| **faster-whisper** | MIT License | CTranslate2-accelerated Whisper transcription with word-level timestamps. |
| **opencv-python** | Apache 2.0 | Video stream dimension probing, frame extraction, and face detection. |
| **pyscenedetect** | BSD 3-Clause | Shot boundary and visual cut detection. |
| **ffmpeg / ffprobe** | LGPL 2.1+ / GPL | Audio extraction, video reframing, loudness normalization, and subtitle burning. Invoked as external CLI binary. |
| **fastapi** | MIT License | Master Control REST API and static UI serving. |
| **pydantic** | MIT License | High-integrity data validation and type enforcement. |
| **httpx** | BSD 3-Clause | Asynchronous HTTP transports for Telegram Bot and Google OAuth APIs. |
| **cryptography** | Apache 2.0 / BSD | AES-GCM credential vault encryption. |

---

## 4. Verification

All components used in production execution comply with zero-cost autonomous operational guidelines and respect their respective upstream intellectual property rights.
