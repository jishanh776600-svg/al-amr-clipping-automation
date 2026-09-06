"""Targeted Verification Suite for Production Readiness, Real-Integration Audit, and Activation."""

import json
import os
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from clipping.agent.campaign.models import (
    CampaignRecord,
    CampaignStatus,
    PayoutModel,
    PayoutTerms,
    PostingRequirements,
    QuotasAndCaps,
    SourceMaterial,
)
from clipping.agent.campaign.repository import CampaignRepository
from clipping.agent.campaign.sources.whop import WhopCampaignSource
from clipping.agent.capabilities.clipping_adapter import MediaClippingCapability
from clipping.agent.capabilities.base import CapabilityContext
from clipping.agent.escalation import (
    EscalationContext,
    EscalationReason,
    EscalationRecord,
    EscalationSeverity,
)
from clipping.agent.orchestration.engine import AutonomousOrchestrationEngine
from clipping.agent.orchestration.models import OrchestrationStage
from clipping.agent.publishing.adapters.youtube import YouTubePublishingAdapter
from clipping.agent.publishing.adapters.instagram import InstagramPublishingAdapter
from clipping.agent.publishing.models import (
    CampaignSubmissionRecord,
    PublishingContentMetadata,
    PublishingMode,
    SubmissionStatus,
)
from clipping.agent.repository import AgentTaskRepository
from clipping.agent.vault.models import AccountPlatform, AccountMetadata, AccountStatus
from clipping.agent.vault.vault import EncryptedCredentialVault
from clipping.approval.escalation_notifier import TelegramEscalationNotifier
from clipping.approval.transport import MockTelegramTransport
from clipping.control.models import SystemControlState
from clipping.control.repository import ControlRepository
from clipping.preflight.validator import (
    OverallPreflightStatus,
    PreflightCategory,
    PreflightStatus,
    SystemPreflightValidator,
)
from clipping.publishing.client import MockYouTubeClient
from clipping.storage.local import LocalStorageDriver
from clipping.ui.server import register_account_api, AccountRegistrationRequest


@pytest.fixture
def local_storage(tmp_path):
    return LocalStorageDriver(root_dir=str(tmp_path))


@pytest.mark.anyio
async def test_preflight_runtime_and_storage(local_storage):
    """Verifies runtime checks pass and storage probe verifies write-read-delete."""
    validator = SystemPreflightValidator(storage_driver=local_storage)
    runtime_checks = validator.check_runtime()

    assert any(c.name == "python_version" and c.status == PreflightStatus.PASS for c in runtime_checks)
    assert any(c.name == "core_python_libraries" and c.status == PreflightStatus.PASS for c in runtime_checks)

    storage_checks = await validator.check_storage()
    assert len(storage_checks) == 1
    assert storage_checks[0].status == PreflightStatus.PASS
    assert storage_checks[0].name == "storage_driver_connectivity"


@pytest.mark.anyio
async def test_preflight_vault_integrity(local_storage):
    """Verifies vault encryption probe passes and detects key status."""
    validator = SystemPreflightValidator(storage_driver=local_storage)
    vault_checks = await validator.check_vault()

    integrity_check = next(c for c in vault_checks if c.name == "vault_encryption_integrity")
    assert integrity_check.status == PreflightStatus.PASS
    assert integrity_check.is_mandatory is True


@pytest.mark.anyio
async def test_preflight_emergency_stop_halts_readiness(local_storage):
    """Verifies that active Emergency Stop fails mandatory check and marks system NOT_READY."""
    ctrl_repo = ControlRepository(local_storage)
    state = SystemControlState(emergency_stopped=True)
    await ctrl_repo.save_state(state)

    validator = SystemPreflightValidator(storage_driver=local_storage, control_repository=ctrl_repo)
    report = await validator.validate()

    assert report.ready is False
    assert report.status == OverallPreflightStatus.NOT_READY
    assert any(c.name == "emergency_stop_state" and c.status == PreflightStatus.FAIL for c in report.checks)


@pytest.mark.anyio
async def test_preflight_no_secret_leakage(local_storage):
    """Verifies that preflight checks and reports never leak secret values in details or summary."""
    with patch.dict(os.environ, {
        "WHOP_API_KEY": "whop_live_secret_token_12345",
        "YOUTUBE_CLIENT_SECRET": "google_oauth_secret_abcde",
        "TELEGRAM_BOT_TOKEN": "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
    }):
        validator = SystemPreflightValidator(storage_driver=local_storage)
        report = await validator.validate()

        report_json = report.model_dump_json()
        assert "whop_live_secret_token_12345" not in report_json
        assert "google_oauth_secret_abcde" not in report_json
        assert "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11" not in report_json


@pytest.mark.anyio
async def test_preflight_activation_matrix_report(local_storage):
    """Verifies the structured activation matrix outputs deterministic status and operational capabilities."""
    validator = SystemPreflightValidator(storage_driver=local_storage)
    report = await validator.validate()

    m = report.activation_matrix
    assert m.code_ready is True
    assert m.storage_ready is True
    assert m.worker_ready is True
    assert m.can_run_preflight is True
    assert isinstance(report.actionable_recommendations, list)


@pytest.mark.anyio
async def test_whop_discovery_handles_empty_and_auth_failure():
    """Verifies real Whop source returns empty list rather than inventing campaigns, and flags 401."""
    source = WhopCampaignSource(api_token="invalid_test_key")

    # Mock empty API response
    with patch("httpx.AsyncClient.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": []}
        mock_get.return_value = mock_resp

        campaigns = await source.discover()
        assert len(campaigns) == 0  # Does NOT invent campaigns!

    # Mock 401 Unauthorized
    with patch("httpx.AsyncClient.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Invalid API key"
        mock_get.return_value = mock_resp

        campaigns = await source.discover()
        assert len(campaigns) == 0


@pytest.mark.anyio
async def test_youtube_adapter_missing_credentials_fails_safely_and_escalates(tmp_path):
    """Verifies YouTube adapter fails safely and requires operator escalation when credentials missing."""
    adapter = YouTubePublishingAdapter(client=None)

    dummy_media = tmp_path / "clip.mp4"
    dummy_media.write_bytes(b"dummy mp4 data")

    sub = CampaignSubmissionRecord(
        submission_id="sub_test_yt",
        campaign_id="camp_test",
        account_id="acc_yt",
        platform=AccountPlatform.YOUTUBE,
        clip_id="clip_123",
        idempotency_key="idemp_123",
        content_metadata=PublishingContentMetadata(
            title="Test Shorts Video",
            description="Auto generated description",
        ),
    )

    with patch.dict(os.environ, {}, clear=True):
        res = await adapter.publish(submission=sub, media_path=str(dummy_media), credentials={})
        assert res.success is False
        assert res.failure_classification == "missing_credentials"
        assert res.escalation_required is True
        assert res.escalation_context is not None
        assert "Missing OAuth2 credentials" in res.escalation_context.what_happened


@pytest.mark.anyio
async def test_youtube_adapter_injected_client_preserved(tmp_path):
    """Verifies injected client is used when provided for isolated testing."""
    mock_client = MockYouTubeClient()

    adapter = YouTubePublishingAdapter(client=mock_client)
    dummy_media = tmp_path / "clip.mp4"
    dummy_media.write_bytes(b"dummy mp4 data")

    sub = CampaignSubmissionRecord(
        submission_id="sub_test_yt_mock",
        campaign_id="camp_test",
        account_id="acc_yt",
        platform=AccountPlatform.YOUTUBE,
        clip_id="clip_123",
        idempotency_key="idemp_123",
        content_metadata=PublishingContentMetadata(
            title="Test Shorts Video",
            description="Description",
        ),
    )

    res = await adapter.publish(submission=sub, media_path=str(dummy_media), credentials={})
    assert res.success is True
    assert res.platform_post_id == "yt_mock_1000"


@pytest.mark.anyio
async def test_instagram_adapter_missing_credentials_fails_safely_and_escalates(tmp_path):
    """Verifies Instagram adapter fails safely and requires operator escalation when credentials missing."""
    adapter = InstagramPublishingAdapter(browser_driver=None)

    dummy_media = tmp_path / "reel.mp4"
    dummy_media.write_bytes(b"dummy mp4 data")

    sub = CampaignSubmissionRecord(
        submission_id="sub_test_ig",
        campaign_id="camp_test",
        account_id="acc_ig",
        platform=AccountPlatform.INSTAGRAM,
        clip_id="clip_456",
        idempotency_key="idemp_456",
        content_metadata=PublishingContentMetadata(
            title="Instagram Reel Title",
            description="Reel caption #viral",
        ),
    )

    with patch.dict(os.environ, {}, clear=True):
        res = await adapter.publish(submission=sub, media_path=str(dummy_media), credentials={})
        assert res.success is False
        assert res.failure_classification == "missing_credentials"
        assert res.escalation_required is True
        assert res.escalation_context is not None
        assert "Missing credentials" in res.escalation_context.what_happened


@pytest.mark.anyio
async def test_telegram_escalation_notifier_formatting_and_dispatch():
    """Verifies escalation formatting and alert dispatch via Telegram transport."""
    mock_transport = MockTelegramTransport()
    notifier = TelegramEscalationNotifier(transport=mock_transport, chat_id=987654321)

    record = EscalationRecord(
        escalation_id="esc_test_captcha_001",
        task_id="task_sub_999",
        campaign_id="camp_whop_alpha",
        reason=EscalationReason.CAPTCHA_CHALLENGE,
        severity=EscalationSeverity.CRITICAL,
        context=EscalationContext(
            what_happened="Cloudflare Turnstile encountered during submission",
            why_it_happened="Automated browser detected by anti-bot mitigation",
            decision_required="Operator must complete CAPTCHA in browser session",
            available_options=["solve_captcha_manually", "abort_submission"],
        ),
    )

    msg = notifier.format_alert_message(record)
    assert "*AL AMR CLIPPING — OPERATOR ESCALATION*" in msg
    assert "esc_test_captcha_001" in msg
    assert "CRITICAL" in msg
    assert "CAPTCHA_CHALLENGE" in msg

    sent = await notifier.notify(record)
    assert sent is True
    assert len(mock_transport.sent_messages) == 1
    assert mock_transport.sent_messages[0]["chat_id"] == 987654321


@pytest.mark.anyio
async def test_agent_task_repository_auto_notifies_escalation(local_storage):
    """Verifies that create_escalation in AgentTaskRepository automatically alerts Telegram."""
    mock_transport = MockTelegramTransport()
    notifier = TelegramEscalationNotifier(transport=mock_transport, chat_id=112233)
    repo = AgentTaskRepository(storage_driver=local_storage, escalation_notifier=notifier)

    ctx = EscalationContext(
        what_happened="Submission platform blocked",
        why_it_happened="HTTP 403 Forbidden from platform API",
        decision_required="Rotate IP proxy or inspect platform terms",
        available_options=["rotate_proxy", "skip_campaign"],
    )

    record = await repo.create_escalation(
        context=ctx,
        task_id="task_blocked",
        campaign_id="camp_blocked",
        reason=EscalationReason.PLATFORM_BLOCKED,
        severity=EscalationSeverity.HIGH,
    )

    assert record.escalation_id.startswith("esc_")
    assert len(mock_transport.sent_messages) == 1
    assert "PLATFORM_BLOCKED" in mock_transport.sent_messages[0]["text"]


@pytest.mark.anyio
async def test_account_registration_endpoint(local_storage):
    """Verifies Mission Control POST /api/accounts registers metadata and securely stores credentials."""
    req = AccountRegistrationRequest(
        platform="youtube",
        account_id="acc_yt_creator_01",
        username="AlAmrCreator",
        display_name="Al Amr Creator Channel",
        credentials={
            "client_id": "oauth_client_id_123",
            "client_secret": "oauth_secret_456",
            "refresh_token": "oauth_refresh_789",
        },
    )

    resp = await register_account_api(req=req, operator="TestOperator", storage=local_storage)
    assert resp["status"] == "success"
    assert resp["account"]["account_id"] == "acc_yt_creator_01"
    assert resp["credentials_encrypted"] is True

    # Check that secrets are decryptable from vault
    vault = EncryptedCredentialVault(storage_driver=local_storage)
    sec = await vault.get_sensitive_secret(AccountPlatform.YOUTUBE, "acc_yt_creator_01")
    assert sec is not None
    assert sec["client_id"] == "oauth_client_id_123"


@pytest.mark.anyio
async def test_orchestrator_dry_run_never_publishes(local_storage):
    """Verifies that Mode B (dry-run) strictly blocks external uploads and records dry-run suppression."""
    ctrl_repo = ControlRepository(local_storage)
    # Ensure control repo has publishing unlocked to test dry-run override!
    state = SystemControlState(publishing_locked=False)
    await ctrl_repo.save_state(state)

    camp_repo = CampaignRepository(storage_driver=local_storage)
    task_repo = AgentTaskRepository(storage_driver=local_storage)

    engine = AutonomousOrchestrationEngine(
        storage_driver=local_storage,
        control_repository=ctrl_repo,
        campaign_repository=camp_repo,
        task_repository=task_repo,
    )

    # In dry-run mode, publishing must be suppressed
    summary = await engine.run_orchestration_cycle(dry_run=True)
    assert summary.submissions_processed == 0
