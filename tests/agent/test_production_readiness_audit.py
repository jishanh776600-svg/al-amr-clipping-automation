"""Targeted Verification Suite for Production Readiness, Real-Integration Audit, and Activation."""

import os
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from clipping.agent.escalation import (
    EscalationContext,
    EscalationReason,
    EscalationRecord,
    EscalationSeverity,
)
from clipping.agent.publishing.adapters.youtube import YouTubePublishingAdapter
from clipping.agent.publishing.adapters.instagram import InstagramPublishingAdapter
from clipping.agent.publishing.models import (
    CampaignSubmissionRecord,
    PublishingContentMetadata,
    PublishingMode,
    SubmissionStatus,
)
from clipping.agent.repository import AgentTaskRepository
from clipping.agent.vault.models import AccountPlatform
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
async def test_youtube_adapter_missing_credentials_fails_safely(tmp_path):
    """Verifies YouTube adapter fails safely when credentials are unset in production."""
    adapter = YouTubePublishingAdapter(client=None)  # No injected mock client

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
        assert res.platform_post_id is None
        assert "credentials missing" in res.error_message


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
async def test_instagram_adapter_missing_credentials_fails_safely(tmp_path):
    """Verifies Instagram adapter fails safely when tokens/browser session are missing."""
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
        assert res.platform_post_id is None
        assert "Instagram credentials missing" in res.error_message


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

    # Format check
    msg = notifier.format_alert_message(record)
    assert "*AL AMR CLIPPING — OPERATOR ESCALATION*" in msg
    assert "esc_test_captcha_001" in msg
    assert "CRITICAL" in msg
    assert "CAPTCHA_CHALLENGE" in msg
    assert "Cloudflare Turnstile" in msg
    assert "solve_captcha_manually" in msg

    # Delivery check
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
    sent_text = mock_transport.sent_messages[0]["text"]
    assert "PLATFORM_BLOCKED" in sent_text
    assert "task_blocked" in sent_text
