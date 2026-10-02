"""AutoClip Cloud Client & Connector (Step 5).

Connects the Whop CampaignBrief pipeline to the existing AutoClip cloud/control-plane API.
Provides:
- Health checking and readiness diagnostics against the AutoClip control plane.
- Deterministic conversion from WhopCampaignBrief to canonical AutoClip job payload.
- Source URL normalization and prerequisite validation.
- Cryptographic idempotency keys derived from (campaign_id, guideline_hash, source_hash).
- Persistent ledger integration via whop_autoclip_jobs with duplicate job protection.
- Conservative bounded retries with exponential backoff on transient errors.
- Strict dry-run enforcement under WHOP_DRY_RUN=true (zero remote job mutations).
- Complete secret redaction across all logs, errors, and diagnostics.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

from .config import AutoClipConfig, sanitize_text
from .ledger import CampaignLedger
from .models import (
    CampaignRecord,
    CampaignState,
    ParsingStatus,
    RuleCategory,
    WhopAutoClipJobRecord,
    WhopCampaignBrief,
    validate_campaign_brief,
)

log = logging.getLogger(__name__)


# ==============================================================================
# 1. Error Taxonomy
# ==============================================================================

class AutoClipError(Exception):
    """Base exception for all AutoClip client operations."""
    code: str = "AUTCLIP_ERROR"

    def __init__(
        self,
        message: str,
        code: Optional[str] = None,
        status_code: Optional[int] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.code = code or self.code
        self.status_code = status_code
        self.details = details or {}
        # Ensure message is strictly sanitized of any secrets
        clean_msg = sanitize_text(message)
        super().__init__(f"[{self.code}] {clean_msg}")


class AutoClipUnavailableError(AutoClipError):
    """Raised when the AutoClip control plane cannot be reached (DNS, network down)."""
    code = "AUTCLIP_UNAVAILABLE"


class AutoClipTimeoutError(AutoClipError):
    """Raised when an HTTP connection or read times out."""
    code = "AUTCLIP_TIMEOUT"


class AutoClipAuthError(AutoClipError):
    """Raised when authentication fails (HTTP 401)."""
    code = "AUTCLIP_AUTH_FAILED"


class AutoClipForbiddenError(AutoClipError):
    """Raised when authorization fails (HTTP 403)."""
    code = "AUTCLIP_FORBIDDEN"


class AutoClipBadRequestError(AutoClipError):
    """Raised when the server rejects a request as malformed (HTTP 400)."""
    code = "AUTCLIP_BAD_REQUEST"


class AutoClipValidationError(AutoClipError):
    """Raised when the request payload fails server-side validation (HTTP 422)."""
    code = "AUTCLIP_VALIDATION_FAILED"


class AutoClipNotFoundError(AutoClipError):
    """Raised when a requested resource or job is not found (HTTP 404)."""
    code = "AUTCLIP_JOB_NOT_FOUND"


class AutoClipServerError(AutoClipError):
    """Raised when the server responds with a 5xx error."""
    code = "AUTCLIP_SERVER_ERROR"


class AutoClipMalformedResponseError(AutoClipError):
    """Raised when the server response cannot be parsed or lacks required fields."""
    code = "AUTCLIP_MALFORMED_RESPONSE"


class AutoClipSourceMissingError(AutoClipError):
    """Raised when an eligible campaign lacks valid source media URLs."""
    code = "AUTCLIP_SOURCE_MISSING"


class AutoClipDuplicateReused(AutoClipError):
    """Informational indicator that an existing job was reused (idempotency)."""
    code = "AUTCLIP_DUPLICATE_REUSED"


# ==============================================================================
# 2. Structured Data Models
# ==============================================================================

@dataclass
class HealthCheckResult:
    healthy: bool
    service: str
    version: str
    ready: bool
    checks: Dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AutoClipJobPayload:
    campaign_id: str
    campaign_title: str
    video_url: str
    sources: List[str]
    source_hash: str
    guideline_hash: str
    idempotency_key: str
    request_hash: str
    campaign_brief: Dict[str, Any]
    unsupported_rules: List[str] = field(default_factory=list)
    settings: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AutoClipJobResult:
    job_id: str
    campaign_id: str
    status: str
    normalized_status: CampaignState
    tracking_url: str
    idempotency_key: str
    reused: bool = False
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    raw_response: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["normalized_status"] = self.normalized_status.value
        return d


# Mapping from raw AutoClip JobStatus to Whop CampaignState
AUTOCLIP_STATUS_MAP: Dict[str, CampaignState] = {
    "queued": CampaignState.INGESTED,
    "dispatching": CampaignState.INGESTED,
    "running": CampaignState.RENDERING,
    "processing": CampaignState.RENDERING,
    "uploading": CampaignState.RENDERING,
    "publishing": CampaignState.RENDERING,
    "done": CampaignState.RENDER_READY,
    "failed": CampaignState.RENDER_FAILED,
    "cancel_requested": CampaignState.RENDER_FAILED,
    "cancelled": CampaignState.RENDER_FAILED,
    "dry_run_validated": CampaignState.INGESTED,
}


def normalize_autoclip_status(raw_status: str) -> CampaignState:
    """Deterministically maps AutoClip server status to Whop CampaignState."""
    clean = str(raw_status).strip().lower()
    return AUTOCLIP_STATUS_MAP.get(clean, CampaignState.INGESTED)


# ==============================================================================
# 3. AutoClip Cloud Client
# ==============================================================================

class AutoClipClient:
    """Production client for interacting with the AutoClip cloud/control-plane API."""

    def __init__(
        self,
        config: Optional[AutoClipConfig] = None,
        ledger: Optional[CampaignLedger] = None,
    ) -> None:
        self.config = config or AutoClipConfig.from_env()
        self.ledger = ledger
        self.base_url = self.config.base_url.rstrip("/")

    def _get_headers(self) -> Dict[str, str]:
        """Constructs safe headers with authentication."""
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "AL-AMR-Whop-Connector/1.0",
        }
        if self.config.api_token:
            headers["Authorization"] = f"Bearer {self.config.api_token}"
            headers["X-API-Key"] = self.config.api_token
        return headers

    def _request(
        self,
        method: str,
        endpoint: str,
        json_data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Executes an HTTP request with bounded retries and strict error classification."""
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        if params:
            query = urllib.parse.urlencode(params)
            url = f"{url}?{query}"

        req_timeout = timeout or self.config.timeout_s
        body_bytes = None
        if json_data is not None:
            body_bytes = json.dumps(json_data).encode("utf-8")

        headers = self._get_headers()
        max_attempts = self.config.max_retries if method.upper() == "GET" else 1

        last_error: Optional[Exception] = None

        for attempt in range(1, max_attempts + 1):
            req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method.upper())
            try:
                with urllib.request.urlopen(req, timeout=req_timeout) as resp:
                    resp_bytes = resp.read()
                    if not resp_bytes:
                        return {}
                    try:
                        return json.loads(resp_bytes.decode("utf-8"))
                    except Exception as parse_err:
                        raise AutoClipMalformedResponseError(
                            f"Failed to parse JSON response from {endpoint}: {parse_err}"
                        ) from parse_err

            except urllib.error.HTTPError as http_err:
                status = http_err.code
                err_body = ""
                try:
                    err_body = http_err.read().decode("utf-8", errors="replace")
                except Exception:
                    pass

                # Never retry client-side errors (400, 401, 403, 404, 422)
                if status == 401:
                    raise AutoClipAuthError(
                        f"Authentication failed against AutoClip API: {err_body or 'Unauthorized'}",
                        status_code=401,
                    ) from http_err
                elif status == 403:
                    raise AutoClipForbiddenError(
                        f"Access forbidden by AutoClip API: {err_body or 'Forbidden'}",
                        status_code=403,
                    ) from http_err
                elif status == 404:
                    raise AutoClipNotFoundError(
                        f"Resource not found at {endpoint}: {err_body or 'Not Found'}",
                        status_code=404,
                    ) from http_err
                elif status == 422:
                    raise AutoClipValidationError(
                        f"Payload validation failed on AutoClip API: {err_body or 'Unprocessable Entity'}",
                        status_code=422,
                    ) from http_err
                elif status == 400:
                    raise AutoClipBadRequestError(
                        f"Bad request sent to AutoClip API: {err_body or 'Bad Request'}",
                        status_code=400,
                    ) from http_err
                elif status in (500, 502, 503, 504):
                    last_error = AutoClipServerError(
                        f"AutoClip server error (HTTP {status}): {err_body or 'Internal Server Error'}",
                        status_code=status,
                    )
                else:
                    raise AutoClipError(
                        f"HTTP error {status} from AutoClip API: {err_body}",
                        status_code=status,
                    ) from http_err

            except (urllib.error.URLError, TimeoutError, OSError) as net_err:
                err_str = str(net_err).lower()
                if "timed out" in err_str:
                    last_error = AutoClipTimeoutError(f"Connection to AutoClip timed out: {net_err}")
                else:
                    last_error = AutoClipUnavailableError(f"AutoClip API unavailable: {net_err}")

            if attempt < max_attempts:
                backoff = self.config.retry_backoff_factor * (2 ** (attempt - 1))
                time.sleep(backoff)

        if last_error:
            raise last_error
        raise AutoClipUnavailableError(f"Failed to communicate with AutoClip at {endpoint}")

    # ==========================================================================
    # 4. Health Check
    # ==========================================================================

    def health_check(self) -> HealthCheckResult:
        """Performs a safe connectivity check against AutoClip control plane.
        
        Validates both liveness (/api/health) and readiness (/api/ready).
        """
        start_t = time.monotonic()
        try:
            # 1. Check liveness
            health_data = self._request("GET", "/api/health")
            if not isinstance(health_data, dict) or health_data.get("status") != "ok":
                raise AutoClipMalformedResponseError(
                    f"Unexpected health response structure: {health_data}"
                )

            # 2. Check readiness
            ready = True
            checks: Dict[str, Any] = {}
            try:
                ready_data = self._request("GET", "/api/ready")
                if isinstance(ready_data, dict):
                    ready = bool(ready_data.get("ready", True))
                    checks = ready_data.get("checks", {})
            except Exception as r_err:
                log.debug("Readiness check soft-warning: %s", r_err)

            latency = round((time.monotonic() - start_t) * 1000.0, 2)
            return HealthCheckResult(
                healthy=True,
                service=str(health_data.get("service", "autoclip")),
                version=str(health_data.get("version", "unknown")),
                ready=ready,
                checks=checks,
                latency_ms=latency,
            )

        except AutoClipError as ac_err:
            latency = round((time.monotonic() - start_t) * 1000.0, 2)
            return HealthCheckResult(
                healthy=False,
                service="autoclip",
                version="unknown",
                ready=False,
                latency_ms=latency,
                error=sanitize_text(str(ac_err)),
            )
        except Exception as exc:
            latency = round((time.monotonic() - start_t) * 1000.0, 2)
            return HealthCheckResult(
                healthy=False,
                service="autoclip",
                version="unknown",
                ready=False,
                latency_ms=latency,
                error=sanitize_text(f"Unexpected health check error: {exc}"),
            )

    # ==========================================================================
    # 5. Source Normalization & Prerequisite Validation
    # ==========================================================================

    def normalize_sources(
        self,
        brief: WhopCampaignBrief,
        source_urls: Optional[List[str]] = None,
    ) -> List[str]:
        """Validates and normalizes candidate source media URLs.
        
        Ensures that guideline documents (PDFs, DOCX) are never mistakenly
        treated as video sources.
        """
        candidates: List[str] = []
        if source_urls:
            candidates.extend(source_urls)
        if brief.allowed_sources:
            candidates.extend(brief.allowed_sources)

        normalized: List[str] = []
        seen = set()

        for raw_url in candidates:
            if not raw_url or not isinstance(raw_url, str):
                continue
            clean = raw_url.strip()
            if not clean.startswith("http://") and not clean.startswith("https://"):
                continue

            lower = clean.lower()
            # Reject guideline documents and text files as video sources
            if any(lower.endswith(ext) for ext in (".pdf", ".docx", ".doc", ".txt", ".md", ".json")):
                continue

            # Accept YouTube, Drive folders/files, Dropbox, or direct media streams
            is_valid_source = (
                "youtube.com" in lower
                or "youtu.be" in lower
                or "drive.google.com" in lower
                or "dropbox.com" in lower
                or any(lower.endswith(ext) for ext in (".mp4", ".mov", ".mkv", ".webm", ".m4v"))
                or "s3.amazonaws.com" in lower
            )

            if is_valid_source and clean not in seen:
                seen.add(clean)
                normalized.append(clean)

        if not normalized:
            raise AutoClipSourceMissingError(
                f"Campaign '{brief.campaign_id}' has no valid source media URLs. "
                f"AutoClip cannot render clips without a video source link."
            )

        def source_priority(url: str) -> int:
            u = url.lower()
            # Tier 0: Direct MP4/video files or S3 (fastest download, zero anti-bot rate limits)
            if any(u.split("?")[0].endswith(ext) for ext in (".mp4", ".mov", ".mkv", ".webm")) or "amazonaws.com" in u:
                return 0
            # Tier 1: Google Drive (fast download, high bandwidth)
            if "drive.google.com" in u:
                return 1
            # Tier 2: Dropbox / generic
            if "dropbox.com" in u:
                return 2
            # Tier 3: YouTube (heavily throttled on cloud VMs)
            return 3

        # Sort primarily by source speed/reliability tier, then alphabetically for deterministic stability
        normalized.sort(key=lambda s: (source_priority(s), s))
        return normalized

    # ==========================================================================
    # 6. Idempotency & Hashing
    # ==========================================================================

    @staticmethod
    def compute_source_hash(sources: List[str]) -> str:
        """Generates a stable SHA-256 fingerprint for a list of source URLs."""
        canonical = "\n".join(sorted(s.strip() for s in sources if s and s.strip()))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def compute_idempotency_key(
        campaign_id: str,
        guideline_hash: str,
        source_hash: str,
    ) -> str:
        """Calculates a deterministic idempotency key for job creation."""
        combined = f"{campaign_id}:{guideline_hash}:{source_hash}"
        return hashlib.sha256(combined.encode("utf-8")).hexdigest()

    @staticmethod
    def compute_request_hash(payload_dict: Dict[str, Any]) -> str:
        """Generates a deterministic SHA-256 hash of the complete canonical payload."""
        canonical_json = json.dumps(payload_dict, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    # ==========================================================================
    # 7. Payload Builder
    # ==========================================================================

    def build_job_payload(
        self,
        brief: WhopCampaignBrief,
        source_urls: Optional[List[str]] = None,
    ) -> AutoClipJobPayload:
        """Converts WhopCampaignBrief into the canonical AutoClip job request payload.
        
        Strictly preserves all mandatory rules and explicitly tracks unsupported rules.
        """
        # 1. Validate brief integrity
        brief_errors = validate_campaign_brief(brief)
        if brief_errors:
            raise AutoClipValidationError(
                f"WhopCampaignBrief validation failed: {'; '.join(brief_errors)}"
            )

        # 2. Bridge to canonical AutoClip CampaignBrief
        autoclip_brief = brief.to_autoclip_brief()
        brief_dict = autoclip_brief.model_dump(mode="json")

        # 3. Normalize sources
        valid_sources = self.normalize_sources(brief, source_urls)
        primary_video_url = valid_sources[0]
        source_hash = self.compute_source_hash(valid_sources)

        # 4. Identify unsupported / unmapped rules
        unsupported: List[str] = []
        for r in brief.rules:
            # Operational instructions are for human operators, not the video renderer
            if r.is_operational or r.status == "OPERATIONAL":
                unsupported.append(f"Operational rule excluded from video pipeline: {r.text}")
            elif r.status == "INTERPRETATION_REQUIRED":
                unsupported.append(f"Ambiguous rule requiring manual review: {r.text}")
            elif r.category in (RuleCategory.SUBMISSION, RuleCategory.PUBLISHING) and r.mandatory:
                unsupported.append(f"Platform submission rule deferred to future step: {r.text}")

        # 5. Build settings dictionary conforming to AutoClip schemas
        min_dur = float(brief.duration_min_s or 20.0)
        max_dur = float(brief.duration_max_s or 90.0)
        destinations = [p.lower() for p in (brief.supported_platforms or ["youtube"])]

        settings: Dict[str, Any] = {
            "caption_style": brief.caption_preset or "bold_pop",
            "min_duration_s": min_dur,
            "max_duration_s": max_dur,
            "destinations": destinations,
            "clips": {
                "min_duration_s": min_dur,
                "max_duration_s": max_dur,
                "max_clips": 5,
            },
            "export": {
                "caption_style": brief.caption_preset or "bold_pop",
                "ratio": "9:16",
            },
            "campaign": brief_dict,
        }

        # 6. Compute hashes and idempotency key
        g_hash = brief.guideline_hash or "no_guidelines"
        idempotency_key = self.compute_idempotency_key(brief.campaign_id, g_hash, source_hash)

        raw_req = {
            "video_url": primary_video_url,
            "campaign_url": brief.campaign_url,
            "caption_style": settings["caption_style"],
            "destinations": destinations,
            "overrides": settings,
        }
        request_hash = self.compute_request_hash(raw_req)

        return AutoClipJobPayload(
            campaign_id=brief.campaign_id,
            campaign_title=brief.title,
            video_url=primary_video_url,
            sources=valid_sources,
            source_hash=source_hash,
            guideline_hash=g_hash,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            campaign_brief=brief_dict,
            unsupported_rules=unsupported,
            settings=settings,
        )

    # ==========================================================================
    # 8. Job Creation & Idempotency
    # ==========================================================================

    def create_job(
        self,
        brief: WhopCampaignBrief,
        source_urls: Optional[List[str]] = None,
    ) -> AutoClipJobResult:
        """Safely creates an AutoClip job with deterministic idempotency.
        
        - If an identical job already exists in the ledger, reuses it (zero duplicate jobs).
        - If dry_run is True, validates the complete request without mutating the server.
        - Persists the resulting job record into the SQLite ledger.
        """
        payload = self.build_job_payload(brief, source_urls)

        # 1. Idempotency Check: query ledger for existing job
        if self.ledger:
            existing = self.ledger.get_autoclip_job_by_idempotency_key(payload.idempotency_key)
            if not existing:
                existing = self.ledger.get_autoclip_job(
                    payload.campaign_id, payload.guideline_hash, payload.source_hash
                )
            if existing:
                # If we are running in real execution mode, do NOT reuse a simulated dry_run job
                if not self.config.dry_run and str(existing.autoclip_job_id).startswith("dry_run_"):
                    log.info(
                        "Found simulated dry_run job '%s' in ledger, but live execution is requested; bypassing mock cache to dispatch real job.",
                        existing.autoclip_job_id,
                    )
                else:
                    log.info(
                        "Reusing existing AutoClip job '%s' for campaign '%s' (idempotency key: %s)",
                        existing.autoclip_job_id,
                        brief.campaign_id,
                        payload.idempotency_key[:12],
                    )
                    tracking_url = f"{self.base_url}/jobs/{existing.autoclip_job_id}"
                    return AutoClipJobResult(
                        job_id=existing.autoclip_job_id,
                        campaign_id=existing.campaign_id,
                        status=existing.status,
                        normalized_status=normalize_autoclip_status(existing.status),
                        tracking_url=tracking_url,
                        idempotency_key=existing.idempotency_key,
                        reused=True,
                        created_at=existing.created_at,
                        raw_response=json.loads(existing.metadata_json) if existing.metadata_json else {},
                    )

        # 2. Dry-Run Safety Check
        if self.config.dry_run:
            simulated_job_id = f"dry_run_{payload.idempotency_key[:16]}"
            log.info(
                "WHOP_DRY_RUN=true: Validated AutoClip payload for campaign '%s'. "
                "Simulated job ID: %s (no remote worker dispatched).",
                brief.campaign_id,
                simulated_job_id,
            )
            tracking_url = f"{self.base_url}/jobs/{simulated_job_id}"
            result = AutoClipJobResult(
                job_id=simulated_job_id,
                campaign_id=brief.campaign_id,
                status="dry_run_validated",
                normalized_status=CampaignState.INGESTED,
                tracking_url=tracking_url,
                idempotency_key=payload.idempotency_key,
                reused=False,
                raw_response={"dry_run": True, "payload": payload.to_dict()},
            )

            # Persist dry-run record into ledger if available
            if self.ledger:
                job_rec = WhopAutoClipJobRecord(
                    campaign_id=brief.campaign_id,
                    guideline_hash=payload.guideline_hash,
                    autoclip_job_id=simulated_job_id,
                    idempotency_key=payload.idempotency_key,
                    status="dry_run_validated",
                    request_hash=payload.request_hash,
                    source_hash=payload.source_hash,
                    metadata_json=json.dumps({"dry_run": True, "unsupported_rules": payload.unsupported_rules}),
                )
                self.ledger.save_autoclip_job(job_rec)

            return result

        # 3. Live Non-Dry-Run Execution: dispatch to existing AutoClip control-plane endpoint
        request_body = {
            "video_url": payload.video_url,
            "campaign_url": brief.campaign_url,
            "caption_style": payload.settings.get("caption_style", "bold_pop"),
            "destinations": payload.settings.get("destinations", ["youtube"]),
            "overrides": payload.settings,
        }

        log.info(
            "Dispatching job creation to AutoClip API for campaign '%s' (video_url: %s)...",
            brief.campaign_id,
            payload.video_url,
        )

        resp_data = self._request("POST", "/api/jobs/create-autonomous", json_data=request_body)

        job_id = resp_data.get("id")
        server_status = resp_data.get("status", "queued")

        if not job_id:
            raise AutoClipMalformedResponseError(
                f"AutoClip server did not return a valid job ID: {resp_data}"
            )

        tracking_url = f"{self.base_url}/jobs/{job_id}"
        normalized = normalize_autoclip_status(server_status)

        # 4. Persist to ledger and transition campaign state
        if self.ledger:
            meta = {
                "server_response": resp_data,
                "unsupported_rules": payload.unsupported_rules,
                "sources": payload.sources,
            }
            job_rec = WhopAutoClipJobRecord(
                campaign_id=brief.campaign_id,
                guideline_hash=payload.guideline_hash,
                autoclip_job_id=job_id,
                idempotency_key=payload.idempotency_key,
                status=server_status,
                request_hash=payload.request_hash,
                source_hash=payload.source_hash,
                metadata_json=json.dumps(meta),
            )
            self.ledger.save_autoclip_job(job_rec)

            # Legally transition campaign to INGESTED in the ledger
            try:
                self.ledger.transition_state(
                    brief.campaign_id,
                    CampaignState.INGESTED,
                    reason=f"AutoClip job created: {job_id} ({server_status})",
                    source="autoclip_connector",
                    metadata={"job_id": job_id, "idempotency_key": payload.idempotency_key},
                )
            except Exception as tr_err:
                log.warning("Could not transition ledger campaign state: %s", tr_err)

        return AutoClipJobResult(
            job_id=job_id,
            campaign_id=brief.campaign_id,
            status=server_status,
            normalized_status=normalized,
            tracking_url=tracking_url,
            idempotency_key=payload.idempotency_key,
            reused=False,
            raw_response=resp_data,
        )

    # ==========================================================================
    # 9. Status Polling
    # ==========================================================================

    def get_job_status(self, job_id: str) -> Dict[str, Any]:
        """Retrieves and normalizes status for an existing AutoClip job."""
        if job_id.startswith("dry_run_"):
            return {
                "job_id": job_id,
                "status": "dry_run_validated",
                "normalized_status": CampaignState.INGESTED.value,
                "progress": 1.0,
                "current_stage": "dry_run_complete",
                "error": None,
                "tracking_url": f"{self.base_url}/jobs/{job_id}",
            }

        resp_data = self._request("GET", f"/api/jobs/{job_id}")
        raw_status = resp_data.get("status", "unknown")
        normalized = normalize_autoclip_status(raw_status)

        # Update ledger status if available
        if self.ledger:
            err = resp_data.get("error")
            self.ledger.update_autoclip_job_status(
                autoclip_job_id=job_id,
                status=raw_status,
                last_error=err,
                metadata={"last_polled_at": datetime.now(timezone.utc).isoformat()},
            )

        return {
            "job_id": job_id,
            "status": raw_status,
            "normalized_status": normalized.value,
            "progress": float(resp_data.get("progress", 0.0)),
            "current_stage": str(resp_data.get("current_stage", "")),
            "error": resp_data.get("error"),
            "dispatch_mode": resp_data.get("dispatch_mode", "local"),
            "tracking_url": f"{self.base_url}/jobs/{job_id}",
            "created_at": resp_data.get("created_at"),
            "updated_at": resp_data.get("updated_at"),
            "raw": resp_data,
        }

    def get_job_final_renders(
        self,
        job_id: str,
        approved_only: bool = False,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieves Step 22 final render records from the AutoClip control plane."""
        if job_id.startswith("dry_run_"):
            return []

        query_params = []
        if approved_only:
            query_params.append("approved_only=true")
        if status:
            query_params.append(f"status={status}")

        endpoint = f"/api/jobs/{job_id}/final-renders"
        if query_params:
            endpoint += "?" + "&".join(query_params)

        res = self._request("GET", endpoint)
        if isinstance(res, list):
            return res
        return []

    def get_job_clips(self, job_id: str) -> List[Dict[str, Any]]:
        """Retrieves candidate/clip records associated with an AutoClip job."""
        if job_id.startswith("dry_run_"):
            return []

        res = self._request("GET", f"/api/jobs/{job_id}/clips")
        if isinstance(res, list):
            return res
        return []

    def poll_job_completion(
        self,
        job_id: str,
        timeout_s: float = 600.0,
        poll_interval_s: float = 5.0,
    ) -> Dict[str, Any]:
        """Polls job status until render execution reaches terminal state or timeout."""
        start_t = time.monotonic()
        while True:
            info = self.get_job_status(job_id)
            raw_st = str(info.get("status", "")).lower()

            if raw_st in ("done", "failed", "cancelled", "cancel_requested", "dry_run_validated"):
                return info

            elapsed = time.monotonic() - start_t
            if elapsed >= timeout_s:
                raise AutoClipTimeoutError(
                    f"Job {job_id} did not complete within {timeout_s:.1f}s (current status: {raw_st})"
                )

            time.sleep(poll_interval_s)

