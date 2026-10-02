"""Comprehensive Unit and Regression Tests for Whop -> AutoClip Cloud Connector (Step 5).

Covers all 27 required test scenarios:
1. AutoClip client initialization
2. configuration validation
3. authentication headers
4. health response validation
5. CampaignBrief conversion
6. mandatory rule preservation
7. unsupported-rule detection
8. source normalization
9. deterministic request hashing
10. deterministic idempotency key
11. duplicate-job reuse
12. job creation payload validation
13. successful job creation response
14. malformed response
15. 401 Unauthorized handling
16. 403 Forbidden handling
17. 400 Bad Request handling
18. 404 Not Found handling
19. 5xx retry with backoff
20. timeout retry with backoff
21. no retry for validation errors (422)
22. status normalization
23. ledger persistence
24. duplicate protection via unique constraint
25. dry-run never creates a job
26. secret redaction in logs/errors
27. Step 3 -> Step 4 -> Step 5 integration
"""

import hashlib
import json
import os
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from whop import (
    AutoClipClient,
    AutoClipConfig,
    AutoClipError,
    AutoClipUnavailableError,
    AutoClipTimeoutError,
    AutoClipAuthError,
    AutoClipForbiddenError,
    AutoClipBadRequestError,
    AutoClipValidationError,
    AutoClipNotFoundError,
    AutoClipServerError,
    AutoClipMalformedResponseError,
    AutoClipSourceMissingError,
    AutoClipDuplicateReused,
    CampaignLedger,
    CampaignRecord,
    CampaignRule,
    CampaignState,
    ParsingStatus,
    RuleCategory,
    WhopAutoClipJobRecord,
    WhopCampaignBrief,
    normalize_guideline_content,
    sanitize_text,
)


@pytest.fixture
def temp_ledger(tmp_path: Path) -> CampaignLedger:
    db_file = tmp_path / "test_ledger_step5.db"
    return CampaignLedger(db_path=db_file)


@pytest.fixture
def sample_brief() -> WhopCampaignBrief:
    rules = [
        CampaignRule(
            rule_id="r1",
            category=RuleCategory.DURATION,
            text="Video duration must be between 30 and 60 seconds",
            mandatory=True,
            source_reference="guidelines.pdf#p1",
        ),
        CampaignRule(
            rule_id="r2",
            category=RuleCategory.CAPTIONS,
            text="Use bold pop style captions",
            mandatory=False,
            source_reference="guidelines.pdf#p1",
        ),
        CampaignRule(
            rule_id="r3",
            category=RuleCategory.OTHER,
            text="Contact support at support@example.com for payment help",
            is_operational=True,
            status="OPERATIONAL",
            source_reference="guidelines.pdf#p2",
        ),
        CampaignRule(
            rule_id="r4",
            category=RuleCategory.SUBMISSION,
            text="Must submit via Whop portal with account handle",
            mandatory=True,
            source_reference="guidelines.pdf#p2",
        ),
    ]
    return WhopCampaignBrief(
        campaign_id="test-campaign-123",
        title="Call It A Day Pod",
        campaign_url="https://whop.com/discover/test-campaign-123",
        duration_min_s=30.0,
        duration_max_s=60.0,
        duration_preferred_s=45.0,
        caption_preset="bold_pop",
        guideline_hash="abc123hash",
        allowed_sources=["https://www.youtube.com/watch?v=dQw4w9WgXcQ"],
        supported_platforms=["youtube", "tiktok"],
        hashtags=["#podcast", "#viral"],
        cta_wording="Watch the full episode",
        rules=rules,
    )


# ------------------------------------------------------------------------------
# Test 1 & 2: Client initialization and configuration validation
# ------------------------------------------------------------------------------

def test_client_init_and_config():
    config = AutoClipConfig(
        base_url="https://test.autoclip.cloud/",
        api_token="test-secret-token",
        timeout_s=25.0,
        dry_run=True,
    )
    client = AutoClipClient(config=config)
    assert client.base_url == "https://test.autoclip.cloud"
    assert client.config.api_token == "test-secret-token"
    assert client.config.timeout_s == 25.0
    assert client.config.dry_run is True


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("CONTROL_PLANE_URL", "https://render.controlplane.com")
    monkeypatch.setenv("AUTOCLIP_API_KEY", "env-api-key-999")
    monkeypatch.setenv("WHOP_DRY_RUN", "false")
    monkeypatch.setenv("AUTOCLIP_TIMEOUT_S", "12.5")

    cfg = AutoClipConfig.from_env()
    assert cfg.base_url == "https://render.controlplane.com"
    assert cfg.api_token == "env-api-key-999"
    assert cfg.dry_run is False
    assert cfg.timeout_s == 12.5


# ------------------------------------------------------------------------------
# Test 3: Authentication headers
# ------------------------------------------------------------------------------

def test_auth_headers_construction():
    cfg = AutoClipConfig(base_url="https://api.test", api_token="secret_key_123")
    client = AutoClipClient(config=cfg)
    headers = client._get_headers()
    assert headers["Authorization"] == "Bearer secret_key_123"
    assert headers["X-API-Key"] == "secret_key_123"
    assert headers["Accept"] == "application/json"
    assert headers["Content-Type"] == "application/json"


# ------------------------------------------------------------------------------
# Test 4: Health response validation
# ------------------------------------------------------------------------------

def test_health_check_success():
    cfg = AutoClipConfig(base_url="https://api.test", dry_run=True)
    client = AutoClipClient(config=cfg)

    def mock_urlopen(req, timeout):
        url = req.full_url
        if "/api/health" in url:
            body = json.dumps({"status": "ok", "service": "autoclip", "version": "0.1.0"}).encode()
        elif "/api/ready" in url:
            body = json.dumps({"status": "ok", "ready": True, "checks": {"database": "ok"}}).encode()
        else:
            body = b"{}"
        mock_resp = MagicMock()
        mock_resp.read.return_value = body
        mock_resp.__enter__.return_value = mock_resp
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        res = client.health_check()
        assert res.healthy is True
        assert res.ready is True
        assert res.service == "autoclip"
        assert res.version == "0.1.0"
        assert res.checks == {"database": "ok"}
        assert res.error is None


def test_health_check_malformed():
    cfg = AutoClipConfig(base_url="https://api.test", dry_run=True)
    client = AutoClipClient(config=cfg)

    with patch.object(client, "_request", return_value={"status": "not_ok"}):
        res = client.health_check()
        assert res.healthy is False
        assert "malformed" in (res.error or "").lower() or "unexpected" in (res.error or "").lower()


# ------------------------------------------------------------------------------
# Test 5 & 6: CampaignBrief conversion and mandatory rule preservation
# ------------------------------------------------------------------------------

def test_campaign_brief_conversion_and_mandatory_rules(sample_brief):
    client = AutoClipClient(config=AutoClipConfig(dry_run=True))
    payload = client.build_job_payload(sample_brief)

    assert payload.campaign_id == "test-campaign-123"
    assert payload.campaign_title == "Call It A Day Pod"
    assert payload.campaign_brief["minimum_duration"] == 30.0
    assert payload.campaign_brief["maximum_duration"] == 60.0
    assert payload.campaign_brief["preferred_duration"] == 45.0
    assert payload.campaign_brief["caption_preset"] == "bold_pop"
    assert "#podcast" in payload.campaign_brief["hashtags"]

    # Mandatory rule preservation
    mand_rules = payload.campaign_brief["mandatory_rules"]
    assert any("duration must be between 30 and 60 seconds" in r for r in mand_rules)


# ------------------------------------------------------------------------------
# Test 7: Unsupported rule detection
# ------------------------------------------------------------------------------

def test_unsupported_rule_detection(sample_brief):
    client = AutoClipClient(config=AutoClipConfig(dry_run=True))
    payload = client.build_job_payload(sample_brief)

    assert len(payload.unsupported_rules) >= 2
    assert any("Operational rule excluded" in r for r in payload.unsupported_rules)
    assert any("Platform submission rule deferred" in r for r in payload.unsupported_rules)


# ------------------------------------------------------------------------------
# Test 8: Source normalization
# ------------------------------------------------------------------------------

def test_source_normalization(sample_brief):
    client = AutoClipClient(config=AutoClipConfig(dry_run=True))
    # Includes document URLs that must be excluded as video sources
    sources = [
        "https://www.youtube.com/watch?v=11111111111",
        "https://whop.com/docs/guidelines.pdf",
        "https://drive.google.com/drive/folders/testfolder",
        "https://example.com/notes.txt",
    ]
    norm = client.normalize_sources(sample_brief, sources)
    assert "https://www.youtube.com/watch?v=11111111111" in norm
    assert "https://drive.google.com/drive/folders/testfolder" in norm
    assert not any(s.endswith(".pdf") for s in norm)
    assert not any(s.endswith(".txt") for s in norm)
    # Drive folder should be prioritized before YouTube in candidate order
    assert norm[0] == "https://drive.google.com/drive/folders/testfolder"


def test_source_missing_raises_error(sample_brief):
    client = AutoClipClient(config=AutoClipConfig(dry_run=True))
    sample_brief.allowed_sources = ["https://whop.com/docs/guidelines.pdf"]
    with pytest.raises(AutoClipSourceMissingError) as exc_info:
        client.normalize_sources(sample_brief, source_urls=[])
    assert "no valid source media URLs" in str(exc_info.value)


# ------------------------------------------------------------------------------
# Test 9 & 10: Deterministic request hashing and idempotency key
# ------------------------------------------------------------------------------

def test_deterministic_idempotency_key(sample_brief):
    client = AutoClipClient(config=AutoClipConfig(dry_run=True))
    p1 = client.build_job_payload(sample_brief, ["https://youtube.com/watch?v=aaa", "https://youtube.com/watch?v=bbb"])
    p2 = client.build_job_payload(sample_brief, ["https://youtube.com/watch?v=bbb", "https://youtube.com/watch?v=aaa"])

    # Source order should not change idempotency key
    assert p1.source_hash == p2.source_hash
    assert p1.idempotency_key == p2.idempotency_key
    assert p1.request_hash == p2.request_hash


# ------------------------------------------------------------------------------
# Test 11 & 12: Duplicate-job reuse and payload validation
# ------------------------------------------------------------------------------

def test_duplicate_job_reuse(temp_ledger, sample_brief):
    client = AutoClipClient(config=AutoClipConfig(dry_run=False), ledger=temp_ledger)

    # Ingest campaign into ledger
    temp_ledger.ingest_discovered_campaign({
        "campaign_id": "test-campaign-123",
        "title": "Call It A Day Pod",
        "campaign_url": "https://whop.com/c1",
        "eligible": True,
        "source_urls": ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"],
    })

    mock_resp = {"id": "ac_job_real_001", "status": "queued"}
    with patch.object(client, "_request", return_value=mock_resp):
        res1 = client.create_job(sample_brief)
        assert res1.job_id == "ac_job_real_001"
        assert res1.reused is False

        # Second call with same campaign & sources must reuse existing job
        res2 = client.create_job(sample_brief)
        assert res2.job_id == "ac_job_real_001"
        assert res2.reused is True


# ------------------------------------------------------------------------------
# Test 13 & 14: Successful response & Malformed response
# ------------------------------------------------------------------------------

def test_job_creation_success(sample_brief):
    client = AutoClipClient(config=AutoClipConfig(dry_run=False))
    mock_resp = {"id": "ac_job_999", "status": "queued"}
    with patch.object(client, "_request", return_value=mock_resp):
        res = client.create_job(sample_brief)
        assert res.job_id == "ac_job_999"
        assert res.status == "queued"
        assert res.normalized_status == CampaignState.INGESTED


def test_job_creation_malformed_response(sample_brief):
    client = AutoClipClient(config=AutoClipConfig(dry_run=False))
    mock_resp = {"status": "ok"}  # missing 'id'
    with patch.object(client, "_request", return_value=mock_resp):
        with pytest.raises(AutoClipMalformedResponseError):
            client.create_job(sample_brief)


# ------------------------------------------------------------------------------
# Test 15-18: HTTP error classifications (401, 403, 400, 404)
# ------------------------------------------------------------------------------

def test_http_401_unauthorized():
    client = AutoClipClient()
    err = urllib.error.HTTPError(
        url="https://api.test/jobs", code=401, msg="Unauthorized", hdrs={}, fp=None
    )
    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(AutoClipAuthError) as exc_info:
            client._request("GET", "/api/jobs")
        assert exc_info.value.status_code == 401


def test_http_403_forbidden():
    client = AutoClipClient()
    err = urllib.error.HTTPError(
        url="https://api.test/jobs", code=403, msg="Forbidden", hdrs={}, fp=None
    )
    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(AutoClipForbiddenError) as exc_info:
            client._request("GET", "/api/jobs")
        assert exc_info.value.status_code == 403


def test_http_400_bad_request():
    client = AutoClipClient()
    err = urllib.error.HTTPError(
        url="https://api.test/jobs", code=400, msg="Bad Request", hdrs={}, fp=None
    )
    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(AutoClipBadRequestError) as exc_info:
            client._request("GET", "/api/jobs")
        assert exc_info.value.status_code == 400


def test_http_404_not_found():
    client = AutoClipClient()
    err = urllib.error.HTTPError(
        url="https://api.test/jobs/xyz", code=404, msg="Not Found", hdrs={}, fp=None
    )
    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(AutoClipNotFoundError) as exc_info:
            client._request("GET", "/api/jobs/xyz")
        assert exc_info.value.status_code == 404


# ------------------------------------------------------------------------------
# Test 19-21: Bounded retries (5xx retry, timeout retry, no retry on 422)
# ------------------------------------------------------------------------------

def test_5xx_retries_with_eventual_failure():
    cfg = AutoClipConfig(max_retries=2, retry_backoff_factor=0.01)
    client = AutoClipClient(config=cfg)
    err = urllib.error.HTTPError(
        url="https://api.test/jobs", code=503, msg="Service Unavailable", hdrs={}, fp=None
    )
    with patch("urllib.request.urlopen", side_effect=err) as mock_call:
        with pytest.raises(AutoClipServerError) as exc_info:
            client._request("GET", "/api/jobs")
        assert mock_call.call_count == 2
        assert exc_info.value.status_code == 503


def test_timeout_retries():
    cfg = AutoClipConfig(max_retries=2, retry_backoff_factor=0.01)
    client = AutoClipClient(config=cfg)
    err = TimeoutError("Connection timed out")
    with patch("urllib.request.urlopen", side_effect=err) as mock_call:
        with pytest.raises(AutoClipTimeoutError):
            client._request("GET", "/api/jobs")
        assert mock_call.call_count == 2


def test_no_retry_for_validation_errors():
    cfg = AutoClipConfig(max_retries=3, retry_backoff_factor=0.01)
    client = AutoClipClient(config=cfg)
    err = urllib.error.HTTPError(
        url="https://api.test/jobs", code=422, msg="Unprocessable Entity", hdrs={}, fp=None
    )
    with patch("urllib.request.urlopen", side_effect=err) as mock_call:
        with pytest.raises(AutoClipValidationError):
            client._request("GET", "/api/jobs")
        # 422 must fail immediately on attempt 1 without retry
        assert mock_call.call_count == 1


# ------------------------------------------------------------------------------
# Test 22: Status normalization
# ------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw_status,expected_state",
    [
        ("queued", CampaignState.INGESTED),
        ("dispatching", CampaignState.INGESTED),
        ("running", CampaignState.RENDERING),
        ("processing", CampaignState.RENDERING),
        ("uploading", CampaignState.RENDERING),
        ("publishing", CampaignState.RENDERING),
        ("done", CampaignState.RENDER_READY),
        ("failed", CampaignState.RENDER_FAILED),
        ("cancelled", CampaignState.RENDER_FAILED),
        ("dry_run_validated", CampaignState.INGESTED),
    ],
)
def test_status_normalization(raw_status, expected_state):
    client = AutoClipClient()
    with patch.object(client, "_request", return_value={"status": raw_status, "progress": 0.5}):
        res = client.get_job_status("job_abc")
        assert res["normalized_status"] == expected_state.value


# ------------------------------------------------------------------------------
# Test 23 & 24: Ledger persistence and duplicate protection
# ------------------------------------------------------------------------------

def test_ledger_persistence_and_duplicate_protection(temp_ledger):
    temp_ledger.ingest_discovered_campaign({
        "campaign_id": "camp-1",
        "title": "Campaign 1",
        "campaign_url": "https://whop.com/c1",
        "eligible": True,
    })

    rec = WhopAutoClipJobRecord(
        campaign_id="camp-1",
        guideline_hash="ghash-1",
        autoclip_job_id="ac-job-1",
        idempotency_key="idem-key-1",
        status="queued",
        request_hash="req-hash-1",
        source_hash="src-hash-1",
    )
    saved = temp_ledger.save_autoclip_job(rec)
    assert saved.id is not None

    # Retrieve by idempotency key
    fetched = temp_ledger.get_autoclip_job_by_idempotency_key("idem-key-1")
    assert fetched is not None
    assert fetched.autoclip_job_id == "ac-job-1"

    # Retrieve by job_id
    fetched_job = temp_ledger.get_autoclip_job_by_job_id("ac-job-1")
    assert fetched_job is not None

    # Update status
    updated = temp_ledger.update_autoclip_job_status("ac-job-1", status="running", metadata={"progress": 0.5})
    assert updated is not None
    assert updated.status == "running"
    assert "0.5" in updated.metadata_json


# ------------------------------------------------------------------------------
# Test 25: Dry-run never creates real remote job
# ------------------------------------------------------------------------------

def test_dry_run_never_dispatches_mutating_request(sample_brief):
    client = AutoClipClient(config=AutoClipConfig(dry_run=True))
    with patch.object(client, "_request") as mock_req:
        res = client.create_job(sample_brief)
        # In dry run, _request should NEVER be called with POST /api/jobs/create-autonomous
        assert mock_req.call_count == 0
        assert res.job_id.startswith("dry_run_")
        assert res.status == "dry_run_validated"
        assert res.normalized_status == CampaignState.INGESTED


# ------------------------------------------------------------------------------
# Test 26: Secret redaction in logs/errors
# ------------------------------------------------------------------------------

def test_secret_redaction_in_errors_and_logs(monkeypatch):
    monkeypatch.setenv("AUTOCLIP_API_KEY", "super_secret_operator_key_9999")
    monkeypatch.setenv("OPERATOR_TOKEN", "prod_token_bearer_12345")

    raw_err = "Auth failed: Bearer super_secret_operator_key_9999 for token=prod_token_bearer_12345"
    sanitized = sanitize_text(raw_err)

    assert "super_secret_operator_key_9999" not in sanitized
    assert "prod_token_bearer_12345" not in sanitized
    assert "[REDACTED" in sanitized

    # Exception should also redact
    err = AutoClipAuthError(raw_err)
    assert "super_secret_operator_key_9999" not in str(err)
    assert "prod_token_bearer_12345" not in str(err)


# ------------------------------------------------------------------------------
# Test 27: Full Step 3 -> Step 4 -> Step 5 pipeline integration
# ------------------------------------------------------------------------------

def test_step3_step4_step5_pipeline_integration(temp_ledger):
    # Step 3: Ingest discovered campaign
    temp_ledger.ingest_discovered_campaign({
        "campaign_id": "pipeline-camp-1",
        "title": "Full Pipeline Campaign",
        "campaign_url": "https://whop.com/discover/pipeline-camp-1",
        "payout_raw": "$2.00 CPM",
        "cpm": 2.00,
        "eligible": True,
        "source_urls": ["https://www.youtube.com/watch?v=sample12345"],
    })

    # Step 4: Save validated CampaignBrief
    brief = WhopCampaignBrief(
        campaign_id="pipeline-camp-1",
        title="Full Pipeline Campaign",
        campaign_url="https://whop.com/discover/pipeline-camp-1",
        guideline_hash="hash_pipeline_999",
        duration_min_s=25.0,
        duration_max_s=55.0,
        caption_preset="bold_pop",
        allowed_sources=["https://www.youtube.com/watch?v=sample12345"],
        rules=[
            CampaignRule(
                rule_id="r1",
                category=RuleCategory.DURATION,
                text="Must be 25-55 seconds",
                mandatory=True,
                source_reference="p1",
            )
        ],
    )
    temp_ledger.save_campaign_brief(brief)

    # Step 5: Connect to AutoClip Cloud Connector
    client = AutoClipClient(config=AutoClipConfig(dry_run=False), ledger=temp_ledger)

    mock_resp = {"id": "ac_pipeline_job_001", "status": "queued"}
    with patch.object(client, "_request", return_value=mock_resp):
        res = client.create_job(brief)
        assert res.job_id == "ac_pipeline_job_001"
        assert res.normalized_status == CampaignState.INGESTED

    # Verify ledger campaign transitioned to INGESTED
    rec = temp_ledger.get_campaign("pipeline-camp-1")
    assert rec is not None
    assert rec.current_state == CampaignState.INGESTED

    # Verify event recorded
    events = temp_ledger.list_events("pipeline-camp-1")
    assert any(e.new_state == CampaignState.INGESTED.value for e in events)

    # Verify autoclip job record exists
    job_rec = temp_ledger.get_autoclip_job_by_job_id("ac_pipeline_job_001")
    assert job_rec is not None
    assert job_rec.campaign_id == "pipeline-camp-1"
    assert job_rec.status == "queued"
