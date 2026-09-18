"""Regression tests for duration bounds enforcement (20-30s canonical default)
and Telegram review bot dispatch.
"""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from autoclip.campaign.duration import resolve_duration_limits
from autoclip.config import ClipSettings
from autoclip.pipeline.transcript import Transcript, Word
from autoclip.pipeline.highlights import detect
from autoclip.telegram.review_bot import get_telegram_config, is_telegram_configured, send_clip_review
from autoclip.db import models, store
from autoclip.api.schemas import WorkerCallbackIn


def test_default_duration_is_strictly_20_to_30():
    """Verify that without explicit overrides, duration limits resolve to (20.0, 30.0)."""
    # No settings provided
    min_d, max_d = resolve_duration_limits()
    assert min_d == 20.0
    assert max_d == 30.0

    # Empty settings
    min_d, max_d = resolve_duration_limits(job_settings={})
    assert min_d == 20.0
    assert max_d == 30.0

    # None settings
    min_d, max_d = resolve_duration_limits(job_settings=None, campaign_spec=None, campaign_brief=None)
    assert min_d == 20.0
    assert max_d == 30.0

    # ClipSettings default
    settings = ClipSettings()
    assert settings.min_duration_s == 20.0
    assert settings.max_duration_s == 30.0


def test_custom_operator_duration_preserved():
    """Verify that explicit valid operator settings are preserved."""
    min_d, max_d = resolve_duration_limits(job_settings={"min_duration_s": 22.0, "max_duration_s": 28.0})
    assert min_d == 22.0
    assert max_d == 28.0

    min_d, max_d = resolve_duration_limits(job_settings={"clips": {"min_duration_s": 15.0, "max_duration_s": 45.0}})
    assert min_d == 15.0
    assert max_d == 45.0


@pytest.mark.asyncio
async def test_highlights_detect_bounds_to_duration():
    """Verify highlights.detect does not generate 68-70s clips when default is 20-30s."""
    words = [Word(start=float(i), end=float(i) + 0.8, text=f"word{i}") for i in range(100)]
    transcript = Transcript(words=words, language='en')

    cfg = ClipSettings(min_duration_s=20.0, max_duration_s=30.0)
    mock_provider = MagicMock()
    mock_provider.name = "mock"
    mock_provider.detect_highlights = AsyncMock(return_value=MagicMock(clips=[]))

    clips = await detect(transcript, mock_provider, config=cfg, job_id="test-job")

    assert len(clips) > 0
    for clip in clips:
        dur = clip.end_s - clip.start_s
        assert dur <= 30.0 + 1.0, f"Clip duration {dur}s exceeds 30s"
        assert dur >= 15.0, f"Clip duration {dur}s is too short"


def test_telegram_config_resolution():
    """Verify Telegram config resolves from job_settings and CredentialVault."""
    # 1. From job_settings
    job_settings = {
        'telegram': {
            'bot_token': 'test_token_123',
            'chat_id': '-100123456789'
        }
    }
    token, chat_id, _ = get_telegram_config(job_settings=job_settings)
    assert token == 'test_token_123'
    assert chat_id == '-100123456789'
    assert is_telegram_configured(job_settings=job_settings) is True

    # 2. From Vault when env is empty
    with patch.dict('os.environ', {}, clear=True), \
         patch('autoclip.security.vault.get_vault') as mock_vault_getter:
        mock_vault = MagicMock()
        mock_vault.retrieve_secret.side_effect = lambda k: 'vault_val' if 'telegram' in k.lower() else None
        mock_vault_getter.return_value = mock_vault

        token, chat_id, _ = get_telegram_config(job_settings=None)
        assert token == 'vault_val'
        assert chat_id == 'vault_val'


def test_worker_callback_approvals_schema():
    """Verify WorkerCallbackIn parses approvals list."""
    payload = WorkerCallbackIn(
        status='completed',
        rendered_clip_ids=['clip-1'],
        exports=[{'clip_id': 'clip-1', 'path': '/tmp/out.mp4'}],
        approvals=[{
            'clip_id': 'clip-1',
            'telegram_message_id': 9999,
            'status': 'PENDING',
            'title': 'Test Clip'
        }]
    )
    assert payload.approvals is not None
    assert len(payload.approvals) == 1
    assert payload.approvals[0]['telegram_message_id'] == 9999


@pytest.mark.asyncio
async def test_telegram_send_clip_review_idempotent():
    """Verify send_clip_review skips sending if approval record already has TELEGRAM_REVIEW_SENT."""
    existing_approval = models.ClipApprovalRecord(
        id='app-1',
        clip_id='clip-1',
        job_id='job-1',
        current_status='PENDING_REVIEW',
        version=1,
        publish_eligible=True,
        blocking_reasons=[],
        history=[{"operator_action": "TELEGRAM_REVIEW_SENT"}],
        telemetry={"telegram_message_id": 12345},
        created_at=models.utcnow(),
        updated_at=models.utcnow(),
    )
    clip = models.Clip(
        id='clip-1',
        job_id='job-1',
        start_s=0.0,
        end_s=25.0,
        rank=1,
        created_at=models.utcnow(),
    )

    with patch('autoclip.db.store.get_clip', return_value=clip), \
         patch('autoclip.db.store.get_clip_approval', return_value=existing_approval), \
         patch('autoclip.telegram.review_bot._safe_send_telegram_message') as mock_send:
        res = await send_clip_review(
            job_id='job-1',
            clip_id='clip-1',
            job_settings={'telegram': {'bot_token': 'test', 'chat_id': '123'}}
        )
        assert res == {"status": "already_sent", "clip_id": "clip-1"}
        mock_send.assert_not_called()

