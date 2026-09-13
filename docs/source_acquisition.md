# AL AMR Clipping Automation — Source Acquisition Subsystem

## 1. Overview & Architectural Motivation

The **Source Acquisition Subsystem** is an isolated, multi-provider abstraction layer designed to acquire local video media bytes from remote URLs with production-grade resilience, multi-provider deterministic fallback, unified media validation, strict SSRF security controls, and forensic provenance tracking.

### Core Architectural Guarantees
- **Strict 3-Input Operator Model**: Operators provide only:
  1. Video file OR YouTube URL
  2. Campaign Guidelines PDF/DOCX OR Google Drive document
  3. Destination platforms (e.g. Telegram, YouTube Shorts, Drive)
  Operators never configure yt-dlp arguments, InnerTube clients, PO tokens, browser cookies, proxy endpoints, or downloader selections.
- **Provider Decoupling**: Downstream pipeline stages (Whisper transcription, campaign evaluation, active-speaker reframing, animated subtitles, rendering, Drive archiving, publishing) remain 100% agnostic to how source media bytes were acquired.
- **Multi-Provider Deterministic Fallback**: Automatic failover from the primary provider (`yt-dlp` with cloud-resilient InnerTube client negotiation) to secondary providers (e.g., legitimate HTTP microservice streaming) before surfacing actionable guidance to the operator.
- **Unified Media Validation Gate**: Strict post-acquisition verification guaranteeing non-empty, non-HTML, playable video and audio streams with SHA-256 hash provenance.
- **Zero-Trust Security Perimeter**: Complete prevention of Server-Side Request Forgery (SSRF) and directory traversal.

---

## 2. Architecture & Components

```
                +---------------------------------------+
                |           Operator Input              |
                |  (Video Upload OR Remote Video URL)   |
                +-------------------+-------------------+
                                    |
                                    v
                +---------------------------------------+
                |    SourceAcquisitionRegistry          |
                |    - Enforces SSRF Security Perimeter |
                |    - Manages Fallback Orchestration   |
                +-------------------+-------------------+
                                    |
            +-----------------------+-----------------------+
            | (Attempt 1)                                   | (Attempt 2 - Fallback)
            v                                               v
+-------------------------------+               +-------------------------------+
|    YtDlpAcquisitionProvider   |               |  HttpApiAcquisitionProvider   |
|  - Upstream yt-dlp            |  [On Block]   |  - Legitimate Media Proxy/API |
|  - 6 InnerTube Strategies     | ------------> |  - Configured via Environment |
|  - Server-side cookie support |               |  - Chunked Stream Ingestion   |
+---------------+---------------+               +---------------+---------------+
                |                                               |
                +-----------------------+-----------------------+
                                        |
                                        v
                        +-------------------------------+
                        |    Unified Media Gate         |
                        |  - Non-empty (size > 0)       |
                        |  - Disguised HTML rejection   |
                        |  - FFprobe Container/Stream   |
                        |  - Compute Provenance SHA-256 |
                        +---------------+---------------+
                                        |
                                        v
                        +-------------------------------+
                        |       Validated Source        |
                        |    (Continues Pipeline)       |
                        +-------------------------------+
```

---

## 3. Provider Interface & Contracts

Located in `backend/autoclip/pipeline/source_acquisition/base.py`:

```python
class SourceAcquisitionProvider(abc.ABC):
    @property
    @abc.abstractmethod
    def provider_name(self) -> str:
        """Unique identifying name for this acquisition provider."""
        ...

    @abc.abstractmethod
    def is_configured(self) -> bool:
        """Return True if this provider is configured and available."""
        ...

    @abc.abstractmethod
    def acquire(
        self,
        source_url: str,
        target_dir: Path,
        job_context: JobContext | None = None,
        on_progress: Callable[[float], None] | None = None,
    ) -> AcquisitionResult:
        """Acquire source media bytes for source_url into target_dir."""
        ...
```

### Normalized `AcquisitionResult` Artifact
Every provider returns a uniform `AcquisitionResult` containing:
- `success`: Boolean indicating acquisition success.
- `local_media_path`: Validated file path on disk.
- `provider_name`: Name of the provider that succeeded (e.g. `"yt-dlp"` or `"http-api"`).
- `source_url`: Normalized source URL.
- `duration`: Verified media duration in seconds.
- `file_size`: Total byte length.
- `sha256`: Cryptographic SHA-256 checksum for provenance and auditability.
- `acquisition_attempts`: Number of provider attempts made.
- `media_info`: Verified FFprobe stream data (`has_video`, `has_audio`, `width`, `height`, `fps`).
- `provider_metadata`: Provider diagnostic data including strategy name and attempt history.

---

## 4. Canonical Error Classifications

To prevent raw tracebacks from reaching operators or downstream handlers, all acquisition failures are normalized into canonical classifications (`SourceErrorCode`):

| Code | Meaning | Operator Hint |
|------|---------|---------------|
| `SOURCE_INVALID_URL` | Malformed URL or forbidden protocol | Verify link format (must start with https:// or http://). |
| `SOURCE_UNAVAILABLE` | Video removed, 404, or private content | Verify link exists and is publicly viewable. |
| `SOURCE_ACCESS_BLOCKED` | Cloud IP blocked, captcha, or bot challenge | YouTube is restricting automated retrieval from this cloud environment. Upload video directly. |
| `SOURCE_AUTH_REQUIRED` | Age-restricted, private, or login required | Video requires credentials. Upload source video directly. |
| `SOURCE_NETWORK_ERROR` | Connection reset, network timeout, DNS fail | Network communication failed. Transient retry eligible. |
| `SOURCE_PROVIDER_TIMEOUT` | External provider timed out during download | Download timed out. Re-attempt or upload directly. |
| `SOURCE_PROVIDER_UNAVAILABLE` | External acquisition endpoint unreachable | Secondary provider service unavailable. |
| `SOURCE_FORMAT_ERROR` | Requested video format unavailable | No compatible streams found. |
| `SOURCE_MEDIA_INVALID` | Corrupt container, 0-byte, or HTML response | Media failed validation gate. Upload file directly. |
| `SOURCE_ALL_PROVIDERS_FAILED` | All configured providers failed | Clear operator guidance advising direct file upload. |

---

## 5. Security Perimeter & SSRF Defenses

Located in `backend/autoclip/pipeline/source_acquisition/security.py`:

- **Scheme Validation**: Strictly allows only `http` and `https`. Rejects `file://`, `ftp://`, `gopher://`, `javascript:`, and `data:` schemes.
- **SSRF Blocklist**: Rejects loopback (`127.0.0.1`, `localhost`, `::1`), RFC 1918 private IP ranges (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), link-local IPs (`169.254.0.0/16`), multicast, and cloud instance metadata services (`169.254.169.254`, `metadata.google.internal`).
- **Path Traversal Protection**: `safe_target_path()` strictly verifies destination paths reside within the sandboxed job workspace directory.

---

## 6. Unified Media Validation Gate

Located in `backend/autoclip/pipeline/source_acquisition/validation.py`:

Every acquired media file must pass through `validate_media_gate()` before being accepted into the pipeline:
1. **File Existence & Type**: Confirms the target is a regular file on disk.
2. **Non-Zero Size**: Rejects 0-byte truncated downloads.
3. **HTML Masquerade Detection**: Inspects leading file bytes for `<!doctype html`, `<html`, or `<head` blocks returned by anti-bot web gateways.
4. **FFprobe Stream Inspection**: Verifies valid container format, valid video stream, valid audio stream (required for Whisper transcription), and positive duration (`duration_s > 0`).
5. **SHA-256 Provenance**: Calculates cryptographic hash recorded in job manifest.

---

## 7. Configuration & Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `AUTOCLIP_ACQUISITION_ENDPOINT` | None | Optional secondary HTTP media acquisition service URL. |
| `AUTOCLIP_ACQUISITION_KEY` | None | Optional authentication Bearer / API token for secondary provider. |
| `AUTOCLIP_ACQUISITION_TIMEOUT` | `120.0` | Timeout in seconds for secondary provider requests. |
| `AUTOCLIP_COOKIES_FILE` | None | Path to optional Netscape cookies file. |
| `YOUTUBE_COOKIES_TEXT` | None | Secret text for temporary cookie provisioning. |

---

## 8. Manifest Provenance Integration

The job manifest (`/api/jobs/{job_id}/manifest`) now authoritative records source acquisition metadata:

```json
{
  "source": {
    "id": "src_12345",
    "title": "Campaign Video Title",
    "duration_s": 42.5,
    "type": "youtube",
    "source_acquisition": {
      "provider": "yt-dlp",
      "attempts": 1,
      "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      "file_size": 15428900,
      "duration_s": 42.5,
      "attempts_history": []
    }
  }
}
```
