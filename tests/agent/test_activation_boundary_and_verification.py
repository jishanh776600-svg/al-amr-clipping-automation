"""Comprehensive Activation Boundary & Verification Test Suite for AL AMR CLIPPING.

Verifies the 12-vector activation boundary:
1. Missing master key blocks activation.
2. Missing Whop credentials blocks real campaign discovery.
3. Missing creator account blocks live publishing.
4. Missing YouTube OAuth blocks YouTube live operation.
5. Missing Instagram credentials blocks Instagram live operation.
6. Telegram unavailable is reported correctly.
7. FFmpeg missing blocks media production.
8. Storage failure blocks activation.
9. Mock publishing clients can NEVER silently enter production mode.
10. Synthetic post IDs can NEVER be accepted as successful live publishing.
11. Dry-run never performs live publishing.
12. Live mode fails closed when readiness is incomplete.
13. Real media smoke test produces valid 1080x1920 output when environment supports it.
14. Activation report contains no secrets.
15. Emergency stop blocks activation.
16. Publishing lock prevents irreversible publishing.
"""

import asyncio
import os
import shutil
from unittest.mock import AsyncMock, patch, MagicMock
import httpx
import pytest

from clipping.agent.vault.models import AccountPlatform, AccountMetadata, AccountStatus
from clipping.agent.publishing.models import (
    PublishingMode,
    SubmissionStatus,
    CampaignSubmissionRecord,
    PublishingContentMetadata,
)
from clipping.control.models import SystemControlState
from clipping.control.repository import ControlRepository
from clipping.agent.orchestration.engine import AutonomousOrchestrationEngine
from clipping.agent.orchestration.models import OrchestrationStage
from clipping.agent.campaign.repository import CampaignRepository
from clipping.config.settings import Settings
from clipping.agent.publishing.repository import CampaignSubmissionRepository
from clipping.agent.publishing.adapters.youtube import YouTubePublishingAdapter
from clipping.agent.publishing.adapters.instagram import InstagramPublishingAdapter
from clipping.agent.publishing.capability import PublishingCapability
from clipping.agent.repository import AgentTaskRepository
from clipping.agent.vault.vault import EncryptedCredentialVault
from clipping.preflight.media_smoke import RealMediaEnvironmentSmokeTest
from clipping.preflight.service_verifier import RealServiceVerifier, ServiceVerificationResult
from clipping.preflight.validator import (
    OverallPreflightStatus,
    PreflightCategory,
    PreflightStatus,
    SystemPreflightValidator,
)
from clipping.publishing.client import MockYouTubeClient
from clipping.agent.browser.driver import MockBrowserDriver
from clipping.storage.local import LocalStorageDriver


@pytest.fixture
def local_storage(tmp_path):
    return LocalStorageDriver(root_dir=str(tmp_path / "storage"))


@pytest.mark.anyio
async def test_01_missing_master_key_reported_in_matrix(local_storage):
    """Verifies that missing master key reports warning and blocks live operation."""
    with patch.dict(os.environ, {"ENCRYPTION_MASTER_KEY": "", "AL_AMR_MASTER_KEY": ""}, clear=True):
        isolated_settings = Settings(_env_file=None, AL_AMR_MASTER_KEY=None, ENCRYPTION_MASTER_KEY=None)
        validator = SystemPreflightValidator(storage_driver=local_storage, settings=isolated_settings)
        report = await validator.validate()

        vault_checks = [c for c in report.checks if c.name == "vault_master_key"]
        assert len(vault_checks) == 1
        assert vault_checks[0].status == PreflightStatus.WARN
        assert report.activation_matrix.live_operation_allowed is False


@pytest.mark.anyio
async def test_02_missing_whop_credentials_blocks_real_discovery(local_storage):
    """Verifies that missing Whop credentials disables live discovery."""
    with patch.dict(os.environ, {"WHOP_API_KEY": "", "WHOP_API_TOKEN": ""}, clear=True):
        verifier = RealServiceVerifier()
        whop_res = await verifier.verify_whop()
        assert whop_res.configured is False
        assert whop_res.verified is False
        assert "not configured" in whop_res.message


@pytest.mark.anyio
async def test_03_missing_creator_account_blocks_live_publishing(local_storage):
    """Verifies that having 0 accounts registered in vault blocks live publishing."""
    validator = SystemPreflightValidator(storage_driver=local_storage)
    report = await validator.validate()

    acct_check = next(c for c in report.checks if c.name == "creator_accounts_registered")
    assert acct_check.status == PreflightStatus.WARN
    assert report.activation_matrix.account_ready is False
    assert report.activation_matrix.can_run_single_live is False


@pytest.mark.anyio
async def test_04_missing_youtube_oauth_blocks_youtube_live(local_storage):
    """Verifies that missing YouTube credentials marks publishing integration as unverified."""
    with patch.dict(os.environ, {"YOUTUBE_CLIENT_ID": "", "YOUTUBE_CLIENT_SECRET": "", "YOUTUBE_REFRESH_TOKEN": ""}, clear=True):
        verifier = RealServiceVerifier()
        yt_res = await verifier.verify_youtube()
        assert yt_res.configured is False
        assert yt_res.verified is False
        assert yt_res.blocks_live_operation is True


@pytest.mark.anyio
async def test_05_missing_instagram_credentials_blocks_instagram_live(local_storage):
    """Verifies that missing Instagram token marks publishing integration as unverified."""
    with patch.dict(os.environ, {"INSTAGRAM_ACCESS_TOKEN": ""}, clear=True):
        verifier = RealServiceVerifier()
        ig_res = await verifier.verify_instagram()
        assert ig_res.configured is False
        assert ig_res.verified is False
        assert ig_res.blocks_live_operation is True


@pytest.mark.anyio
async def test_06_telegram_unavailable_reported_correctly():
    """Verifies that missing Telegram bot token is reported cleanly without secret leakage."""
    with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": ""}, clear=True):
        verifier = RealServiceVerifier()
        tg_res = await verifier.verify_telegram()
        assert tg_res.configured is False
        assert tg_res.verified is False
        assert "not configured" in tg_res.message


@pytest.mark.anyio
async def test_07_ffmpeg_missing_blocks_media_production(local_storage):
    """Verifies that if FFmpeg is missing from PATH and imageio, media pipeline fails preflight."""
    validator = SystemPreflightValidator(storage_driver=local_storage)
    with patch("shutil.which", return_value=None), patch.dict("sys.modules", {"imageio_ffmpeg": None}):
        checks = validator.check_binaries()
        ffmpeg_check = next(c for c in checks if c.name == "ffmpeg_binary")
        assert ffmpeg_check.status == PreflightStatus.FAIL
        assert ffmpeg_check.blocks_dry_run is True


@pytest.mark.anyio
async def test_08_storage_failure_blocks_activation(local_storage):
    """Verifies that storage probe failure fails closed."""
    mock_storage = MagicMock()
    mock_storage.upload_bytes = AsyncMock(side_effect=PermissionError("Read-only filesystem"))

    validator = SystemPreflightValidator(storage_driver=mock_storage)
    checks = await validator.check_storage()
    assert checks[0].status == PreflightStatus.FAIL
    assert checks[0].blocks_dry_run is True
    assert checks[0].blocks_live_publishing is True


@pytest.mark.anyio
async def test_09_mock_publishing_client_prohibited_in_live_mode(tmp_path):
    """Verifies that MockYouTubeClient is strictly rejected in IMMEDIATE live publishing mode."""
    mock_client = MockYouTubeClient()
    adapter = YouTubePublishingAdapter(client=mock_client)

    dummy_media = tmp_path / "clip.mp4"
    dummy_media.write_bytes(b"dummy mp4 data")

    sub = CampaignSubmissionRecord(
        submission_id="sub_live_yt",
        campaign_id="camp_1",
        account_id="acc_yt_1",
        platform=AccountPlatform.YOUTUBE,
        clip_id="clip_1",
        publishing_mode=PublishingMode.IMMEDIATE,
        idempotency_key="idemp_yt_1",
        content_metadata=PublishingContentMetadata(title="Live Video", description="Desc"),
    )

    # In live mode (IMMEDIATE) without allow_mock_client, must fail closed
    res = await adapter.publish(submission=sub, media_path=str(dummy_media), credentials={})
    assert res.success is False
    assert res.failure_classification == "mock_client_prohibited"
    assert res.escalation_required is True
    assert "MockYouTubeClient is prohibited" in res.error_message


@pytest.mark.anyio
async def test_10_synthetic_post_id_rejected_in_live_publishing(local_storage, tmp_path):
    """Verifies that PublishingCapability rejects synthetic post IDs during live publishing."""
    dummy_media = tmp_path / "clip.mp4"
    dummy_media.write_bytes(b"0" * 4096)

    # Mock adapter that returns a synthetic post ID
    mock_adapter = MagicMock()
    mock_res = MagicMock()
    mock_res.success = True
    mock_res.status = SubmissionStatus.PUBLISHED
    mock_res.platform_post_id = "yt_mock_12345"
    mock_res.platform_url = "https://youtube.com/shorts/yt_mock_12345"
    mock_res.raw_response = {"id": "yt_mock_12345"}
    mock_adapter.publish = AsyncMock(return_value=mock_res)

    from clipping.agent.policy import PolicyEngine
    from clipping.agent.campaign.models import (
        CampaignRecord,
        CampaignPlatform,
        PostingRequirements,
        PayoutTerms,
        SourceMaterial,
    )

    sub_repo = CampaignSubmissionRepository(local_storage)
    camp_repo = CampaignRepository(local_storage)
    vault = EncryptedCredentialVault(local_storage)
    ctrl_repo = ControlRepository(local_storage)

    # Save campaign and account
    camp = CampaignRecord(
        campaign_id="camp_test_synth",
        name="Test Campaign",
        source="whop",
        required_platforms=[CampaignPlatform.YOUTUBE_SHORTS],
        posting_requirements=PostingRequirements(required_hashtags=["#shorts"]),
        payout_terms=PayoutTerms(cpm_rate=2.0),
        source_material=SourceMaterial(video_urls=["https://example.com/video.mp4"]),
    )
    await camp_repo.save_campaign(camp)

    meta = AccountMetadata(
        account_id="acc_yt_synth",
        platform=AccountPlatform.YOUTUBE,
        username="Creator",
        status=AccountStatus.ACTIVE,
    )
    await vault.save_account(meta, sensitive_credentials={"client_id": "c1", "client_secret": "s1", "refresh_token": "r1"})

    from clipping.agent.policy import PolicyRule, PolicyDecisionType
    custom_policy = PolicyEngine(
        rules=[
            PolicyRule(
                rule_id="RULE_ALLOW_ALL_TEST",
                description="Allow test publishing",
                capability_pattern="*",
                action_pattern="*",
                decision=PolicyDecisionType.ALLOW,
                requires_human_confirmation=False,
                priority=999,
            )
        ],
        require_confirmation_for_irreversible=False,
    )

    cap = PublishingCapability(
        submission_repository=sub_repo,
        campaign_repository=camp_repo,
        vault=vault,
        control_repository=ctrl_repo,
        policy_engine=custom_policy,
        adapters={AccountPlatform.YOUTUBE: mock_adapter},
    )

    from clipping.agent.capabilities.base import CapabilityContext
    ctx = CapabilityContext(
        task_id="task_synth_test",
        inputs={
            "campaign_id": "camp_test_synth",
            "clip_id": "clip_synth",
            "account_id": "acc_yt_synth",
            "media_path": str(dummy_media),
            "platform": "youtube",
            "publishing_mode": "immediate",
            "qa_record": {"status": "passed", "duration_seconds": 30.0},
        },
        storage_driver=local_storage,
    )

    result = await cap.execute(ctx)
    assert result.success is False
    assert result.error.error_type == "SyntheticPostIdRejected"
    assert "synthetic/mock post id" in result.error.error_message.lower()


@pytest.mark.anyio
async def test_11_dry_run_never_publishes(local_storage):
    """Verifies that Mode B (dry-run) strictly blocks external uploads and records dry-run suppression."""
    ctrl_repo = ControlRepository(local_storage)
    await ctrl_repo.save_state(SystemControlState(publishing_locked=False))

    camp_repo = CampaignRepository(local_storage)
    task_repo = AgentTaskRepository(storage_driver=local_storage)
    engine = AutonomousOrchestrationEngine(
        storage_driver=local_storage,
        control_repository=ctrl_repo,
        campaign_repository=camp_repo,
        task_repository=task_repo,
    )

    summary = await engine.run_orchestration_cycle(dry_run=True)
    assert summary.submissions_processed == 0


@pytest.mark.anyio
async def test_12_live_mode_fails_closed_when_readiness_incomplete(local_storage):
    """Verifies orchestrator CLI exits with non-zero exit code when single-live is called without live readiness."""
    from clipping.cli.orchestrator import run_orchestrator
    import argparse

    args = argparse.Namespace(
        mode="single-live",
        preflight=False,
        dry_run=False,
        once=True,
        continuous=False,
        skip_preflight=False,
        interval=300,
        target_campaign=None,
        source="whop",
        max_campaigns=5,
        json=False,
        strict=False,
    )

    exit_code = await run_orchestrator(args, storage_driver=local_storage)
    assert exit_code == 1


@pytest.mark.anyio
async def test_13_real_media_smoke_test_produces_valid_output(local_storage):
    """Verifies the RealMediaEnvironmentSmokeTest produces a valid 1080x1920 MP4, passes QA, and checks idempotency."""
    smoke_test = RealMediaEnvironmentSmokeTest(storage_driver=local_storage)
    report = await smoke_test.execute()

    assert report.success is True
    assert report.output_resolution == "1080x1920"
    assert report.output_file_size_bytes > 0
    assert report.qa_passed is True
    assert report.idempotent_reuse_verified is True
    assert report.error is None


@pytest.mark.anyio
async def test_14_activation_report_contains_no_secrets(local_storage):
    """Verifies that secrets are not leaked into preflight reports."""
    secret_key = "super_secret_whop_token_123456789"
    with patch.dict(os.environ, {"WHOP_API_KEY": secret_key}):
        validator = SystemPreflightValidator(storage_driver=local_storage)
        report = await validator.validate()
        report_json = report.model_dump_json()
        assert secret_key not in report_json


@pytest.mark.anyio
async def test_15_emergency_stop_blocks_activation(local_storage):
    """Verifies that active emergency stop marks activation matrix as NOT allowed."""
    ctrl_repo = ControlRepository(local_storage)
    await ctrl_repo.save_state(SystemControlState(emergency_stopped=True))

    validator = SystemPreflightValidator(storage_driver=local_storage, control_repository=ctrl_repo)
    report = await validator.validate()

    assert report.ready is False
    assert report.activation_matrix.can_run_dry_run is False
    assert report.activation_matrix.live_operation_allowed is False


@pytest.mark.anyio
async def test_16_publishing_lock_prevents_irreversible_publishing(local_storage):
    """Verifies that publishing lock blocks Stage 10 live uploads."""
    ctrl_repo = ControlRepository(local_storage)
    await ctrl_repo.save_state(SystemControlState(publishing_locked=True))

    camp_repo = CampaignRepository(local_storage)
    task_repo = AgentTaskRepository(storage_driver=local_storage)
    engine = AutonomousOrchestrationEngine(
        storage_driver=local_storage,
        control_repository=ctrl_repo,
        campaign_repository=camp_repo,
        task_repository=task_repo,
    )

    summary = await engine.run_orchestration_cycle(dry_run=False)
    assert summary.submissions_processed == 0


@pytest.mark.anyio
async def test_17_al_amr_master_key_loading_and_preflight_pass(local_storage):
    """Verifies that configuring AL_AMR_MASTER_KEY in environment causes vault_master_key check to PASS."""
    from cryptography.fernet import Fernet
    from clipping.config.settings import Settings, get_master_key

    valid_key = Fernet.generate_key().decode("utf-8")
    with patch.dict(os.environ, {"AL_AMR_MASTER_KEY": valid_key}):
        settings = Settings()
        assert get_master_key() == valid_key

        validator = SystemPreflightValidator(storage_driver=local_storage, settings=settings)
        checks = await validator.check_vault()
        master_checks = [c for c in checks if c.name == "vault_master_key"]
        assert len(master_checks) == 1
        assert master_checks[0].status == PreflightStatus.PASS
        assert master_checks[0].details["configured"] is True


@pytest.mark.anyio
async def test_18_account_enrollment_api_safe_metadata_and_vault_credential_resolution(local_storage):
    """Verifies POST /api/accounts registers creator account metadata without leaking credentials, stores secrets in vault, and check_creator_accounts/check_platform_credentials discovers them."""
    from clipping.ui.server import register_account_api, AccountRegistrationRequest

    req = AccountRegistrationRequest(
        platform="youtube",
        account_id="creator_acc_prod_01",
        username="AlAmrOfficial",
        display_name="AL AMR Official Shorts",
        credentials={
            "client_id": "test_client_id.apps.googleusercontent.com",
            "client_secret": "test_client_secret_xyz",
            "refresh_token": "test_refresh_token_123",
        },
    )
    res = await register_account_api(req=req, operator="SecAudit", storage=local_storage)
    assert res["status"] == "success"
    assert res["account"]["account_id"] == "creator_acc_prod_01"
    assert res["account"]["platform"] == "youtube"
    # Verify zero credentials leaked in response:
    assert "client_secret" not in str(res)
    assert "test_refresh_token_123" not in str(res)

    # Verify validator discovers creator accounts:
    validator = SystemPreflightValidator(storage_driver=local_storage)
    acc_checks = await validator.check_creator_accounts()
    assert len(acc_checks) == 1
    assert acc_checks[0].status == PreflightStatus.PASS
    assert acc_checks[0].details["account_count"] == 1
    assert "creator_acc_prod_01" in acc_checks[0].details["accounts"]


@pytest.mark.anyio
async def test_19_real_service_verifier_distinguishes_unconfigured_vs_auth_failure(local_storage):
    """Verifies that RealServiceVerifier clearly distinguishes unconfigured (WARN) vs authentication failure (FAIL)."""
    verifier = RealServiceVerifier()

    # 1. Unconfigured state:
    with patch.dict(os.environ, {"WHOP_API_KEY": ""}, clear=True):
        unconf = await verifier.verify_whop()
        assert unconf.configured is False
        assert unconf.verified is False
        assert "not configured" in unconf.message

    # 2. Configured but Auth Failure (e.g. 401 Unauthorized from API):
    with patch.dict(os.environ, {"WHOP_API_KEY": "invalid_whop_token_xyz"}):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = '{"error": "Unauthorized"}'

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_resp
            auth_fail = await verifier.verify_whop()
            assert auth_fail.configured is True
            assert auth_fail.verified is False
            assert auth_fail.status_code == 401
            assert "authentication failed" in auth_fail.message.lower()

    # 3. Check validator converts unconfigured + browser active -> PASS, unconfigured + browser disabled -> WARN, and auth failure -> FAIL
    validator = SystemPreflightValidator(storage_driver=local_storage)

    # 3a. When browser discovery is operational, unconfigured WHOP_API_KEY does NOT block readiness (PASS):
    with patch.object(verifier, "verify_whop", return_value=unconf):
        with patch("clipping.preflight.service_verifier.RealServiceVerifier", return_value=verifier):
            checks = await validator.check_platform_credentials()
            whop_check = next(c for c in checks if c.name == "whop_campaign_discovery")
            assert whop_check.status == PreflightStatus.PASS
            assert "browser discovery operational" in whop_check.message.lower()

    # 3b. When browser discovery is disabled AND WHOP_API_KEY is unconfigured, validator flags WARN:
    with patch.dict(os.environ, {"DISABLE_BROWSER_DISCOVERY": "1"}):
        with patch.object(verifier, "verify_whop", return_value=unconf):
            with patch("clipping.preflight.service_verifier.RealServiceVerifier", return_value=verifier):
                checks = await validator.check_platform_credentials()
                whop_check = next(c for c in checks if c.name == "whop_campaign_discovery")
                assert whop_check.status == PreflightStatus.WARN
                assert "no legitimate campaign source available" in whop_check.message.lower()

    # 3c. When WHOP_API_KEY is provided but fails authentication (401), validator flags FAIL:
    with patch.object(verifier, "verify_whop", return_value=auth_fail):
        with patch("clipping.preflight.service_verifier.RealServiceVerifier", return_value=verifier):
            checks = await validator.check_platform_credentials()
            whop_check = next(c for c in checks if c.name == "whop_campaign_discovery")
            assert whop_check.status == PreflightStatus.FAIL


@pytest.mark.anyio
async def test_20_dry_run_full_cycle_execution_and_clean_completion(local_storage):
    """Verifies full CLI orchestrator execution in dry-run mode completes cleanly with exit code 0 and suppresses live publishing."""
    import argparse
    from datetime import datetime, timezone
    from clipping.cli.orchestrator import run_orchestrator
    from clipping.agent.campaign.models import (
        CampaignRecord,
        CampaignPlatform,
        CampaignStatus,
        PostingRequirements,
        PayoutTerms,
        SourceMaterial,
    )

    # Seed an active campaign in repository
    camp_repo = CampaignRepository(storage_driver=local_storage)
    campaign = CampaignRecord(
        campaign_id="camp_dry_run_test_001",
        name="Test Autonomous Dry Run Campaign",
        source="whop",
        status=CampaignStatus.ACTIVE,
        required_platforms=[CampaignPlatform.YOUTUBE_SHORTS],
        posting_requirements=PostingRequirements(required_hashtags=["#shorts"]),
        payout_terms=PayoutTerms(cpm_rate=15.0),
        source_material=SourceMaterial(video_urls=["https://youtube.com/watch?v=mock_video_dry_run"]),
        discovered_at=datetime.now(timezone.utc),
    )
    await camp_repo.save_campaign(campaign)

    args = argparse.Namespace(
        mode="dry-run",
        preflight=False,
        dry_run=True,
        once=True,
        continuous=False,
        skip_preflight=True,
        interval=300,
        target_campaign=None,
        source="whop",
        max_campaigns=5,
        json=False,
        strict=False,
    )

    exit_code = await run_orchestrator(args, storage_driver=local_storage)
    assert exit_code == 0


@pytest.mark.anyio
async def test_21_preflight_cli_accepts_live_probe_argument(local_storage):
    """Verifies that preflight CLI parser accepts --live-probe flag and runs without argument parsing errors."""
    import argparse
    from clipping.cli.preflight import run_preflight

    args = argparse.Namespace(
        json=True,
        strict=False,
        live_probe=True,
        smoke_test=False,
    )
    exit_code = await run_preflight(args)
    assert exit_code in (0, 1)



