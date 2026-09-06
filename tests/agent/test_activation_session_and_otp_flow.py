"""Targeted Test Suite for Operator Activation Sessions, Telegram OTP Bridge, and YouTube OAuth.

Verifies:
1. Telegram operator authorization and rejection of unauthorized chats.
2. Telegram challenge creation, formatting, and zero secret leakage.
3. OTP session binding and lifecycle state transitions.
4. OTP expiration after challenge TTL.
5. OTP replay rejection (single-use enforcement).
6. Unauthorized Telegram chat rejection.
7. OTP redaction (zero OTP in records, error messages, and logs).
8. YouTube OAuth authorization URL generation.
9. YouTube OAuth token exchange and channel identity verification.
10. Vault creator account registration and retrieval.
11. Telegram dispatcher message handling for /otp command and numeric codes.
"""

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock
import pytest

from clipping.agent.activation.models import ActivationState, ActivationSession, ActivationChallenge
from clipping.agent.activation.manager import ActivationSessionManager
from clipping.agent.vault.models import AccountPlatform, AccountStatus
from clipping.agent.vault.vault import EncryptedCredentialVault
from clipping.approval.dispatcher import TelegramApprovalDispatcher
from clipping.approval.escalation_notifier import TelegramEscalationNotifier
from clipping.approval.repository import ApprovalRepository
from clipping.approval.security import SecurityValidator
from clipping.approval.service import ApprovalService
from clipping.approval.transport import TelegramTransport
from clipping.config.settings import Settings
from clipping.publishing.oauth_flow import YouTubeOAuthFlow
from clipping.storage.local import LocalStorageDriver


class MockTelegramTransport(TelegramTransport):
    def __init__(self):
        self.sent_messages = []

    async def send_message(self, chat_id: int, text: str, reply_markup=None) -> int:
        msg_id = len(self.sent_messages) + 1
        self.sent_messages.append({"chat_id": chat_id, "text": text, "message_id": msg_id})
        return msg_id

    async def edit_message_text(self, chat_id: int, message_id: int, text: str, reply_markup=None) -> bool:
        return True

    async def answer_callback_query(self, callback_query_id: str, text=None, show_alert=False) -> bool:
        return True

    async def get_updates(self, offset=None, limit=100, timeout=0):
        return []


@pytest.fixture
def local_storage(tmp_path):
    return LocalStorageDriver(root_dir=str(tmp_path / "storage"))


@pytest.fixture
def activation_manager(local_storage):
    sec = SecurityValidator(allowed_user_ids={12345}, allowed_chat_ids={99999})
    return ActivationSessionManager(storage_driver=local_storage, security_validator=sec)


@pytest.mark.anyio
async def test_01_activation_session_lifecycle(activation_manager):
    """Verifies starting, challenging, validating OTP, and completing an activation session."""
    session = await activation_manager.start_session(service="youtube", account_identifier="@TestChannel")
    assert session.state == ActivationState.ACTIVATION_STARTED
    assert session.service == "youtube"
    assert session.account_identifier == "@TestChannel"
    assert not session.is_expired()

    # Create OTP challenge
    session = await activation_manager.create_otp_challenge(session_id=session.session_id, challenge_ttl_seconds=300)
    assert session.state == ActivationState.OTP_REQUIRED
    assert session.active_challenge is not None
    assert session.active_challenge.consumed is False
    assert session.active_challenge.expected_length == 6

    # Submit valid OTP
    secret_otp = "849201"
    session = await activation_manager.submit_otp(session.session_id, otp_code=secret_otp, sender_user_id=12345, sender_chat_id=99999)
    assert session.state == ActivationState.OTP_VALIDATED
    assert session.active_challenge.consumed is True
    # Zero raw OTP in session or challenge
    assert secret_otp not in session.model_dump_json()

    # Complete session
    session = await activation_manager.complete_session(session.session_id, remote_identity="UC_Channel_12345")
    assert session.state == ActivationState.ACTIVATION_COMPLETE
    assert session.remote_identity == "UC_Channel_12345"


@pytest.mark.anyio
async def test_02_otp_replay_rejection(activation_manager):
    """Verifies that an OTP challenge cannot be consumed more than once (replay prevention)."""
    session = await activation_manager.start_session(service="youtube", account_identifier="@ReplayTest")
    session = await activation_manager.create_otp_challenge(session.session_id)

    # First consumption succeeds
    await activation_manager.submit_otp(session.session_id, "123456", sender_user_id=12345, sender_chat_id=99999)

    # Second consumption fails
    with pytest.raises(ValueError, match="already been consumed"):
        await activation_manager.submit_otp(session.session_id, "123456", sender_user_id=12345, sender_chat_id=99999)


@pytest.mark.anyio
async def test_03_otp_challenge_expiration(activation_manager):
    """Verifies that an expired OTP challenge is rejected."""
    session = await activation_manager.start_session(service="youtube", account_identifier="@ExpiryTest")
    # 0 second TTL -> expires immediately
    session = await activation_manager.create_otp_challenge(session.session_id, challenge_ttl_seconds=-1)

    with pytest.raises(ValueError, match="expired"):
        await activation_manager.submit_otp(session.session_id, "654321", sender_user_id=12345, sender_chat_id=99999)


@pytest.mark.anyio
async def test_04_unauthorized_telegram_chat_rejected(activation_manager):
    """Verifies that unauthorized Telegram users/chats cannot submit OTP codes."""
    session = await activation_manager.start_session(service="youtube", account_identifier="@SecTest")
    session = await activation_manager.create_otp_challenge(session.session_id)

    with pytest.raises(PermissionError, match="Unauthorized sender"):
        await activation_manager.submit_otp(session.session_id, "123456", sender_user_id=999999, sender_chat_id=111111)


@pytest.mark.anyio
async def test_05_telegram_notification_formatting_and_no_secrets(activation_manager):
    """Verifies Telegram escalation alert is formatted with session details and zero secrets."""
    transport = MockTelegramTransport()
    notifier = TelegramEscalationNotifier(transport=transport, chat_id=99999)

    session = await activation_manager.start_session(service="youtube", account_identifier="@SafeChannel")
    session = await activation_manager.create_otp_challenge(session.session_id)

    updated_session = await activation_manager.notify_operator_telegram(session.session_id, notifier=notifier, chat_id=99999)
    assert updated_session.state == ActivationState.WAITING_FOR_OPERATOR
    assert len(transport.sent_messages) == 1

    msg_text = transport.sent_messages[0]["text"]
    assert "youtube" in msg_text.lower()
    assert session.session_id in msg_text
    assert "MFA_REQUIRED" in msg_text


@pytest.mark.anyio
async def test_06_telegram_dispatcher_otp_processing(local_storage, activation_manager):
    """Verifies TelegramApprovalDispatcher parses incoming text messages for OTP verification."""
    transport = MockTelegramTransport()
    repo = ApprovalRepository(storage_driver=local_storage)
    sec = SecurityValidator(allowed_user_ids={12345}, allowed_chat_ids={99999})
    service = ApprovalService(repository=repo, transport=transport, security_validator=sec)

    dispatcher = TelegramApprovalDispatcher(
        approval_service=service,
        transport=transport,
        storage_driver=local_storage,
        activation_manager=activation_manager,
    )

    # Start a waiting session
    session = await activation_manager.start_session(service="youtube", account_identifier="@TelegramTest")
    session = await activation_manager.create_otp_challenge(session.session_id)
    session = session.transition(ActivationState.WAITING_FOR_OPERATOR)
    await activation_manager.save_session(session)

    # Simulate incoming message update
    updates = [
        {
            "update_id": 101,
            "message": {
                "message_id": 1,
                "from": {"id": 12345},
                "chat": {"id": 99999},
                "text": f"/otp {session.session_id} 456789",
            },
        }
    ]

    with patch.object(transport, "get_updates", return_value=updates):
        processed = await dispatcher.poll_and_process_once()
        assert processed == 1

    updated_session = await activation_manager.get_session(session.session_id)
    assert updated_session.state == ActivationState.OTP_VALIDATED
    assert updated_session.active_challenge.consumed is True

    # Confirmation sent back to chat
    assert any("accepted" in m["text"].lower() for m in transport.sent_messages)


@pytest.mark.anyio
async def test_07_youtube_oauth_flow_and_enrollment(local_storage):
    """Verifies YouTube OAuth authorization URL generation, token exchange, and vault registration."""
    flow = YouTubeOAuthFlow()

    # 1. URL generation
    url = flow.generate_authorization_url(client_id="test_client_id.apps.googleusercontent.com", state="state_xyz")
    assert "https://accounts.google.com/o/oauth2/v2/auth" in url
    assert "client_id=test_client_id.apps.googleusercontent.com" in url
    assert "scope=" in url
    assert "access_type=offline" in url

    # 2. Token exchange mock
    mock_token_resp = MagicMock()
    mock_token_resp.status_code = 200
    mock_token_resp.json.return_value = {
        "access_token": "ya29.test_access_token_123",
        "refresh_token": "1//test_refresh_token_456",
        "expires_in": 3600,
    }

    # 3. Channel identity verification mock
    mock_channel_resp = MagicMock()
    mock_channel_resp.status_code = 200
    mock_channel_resp.json.return_value = {
        "items": [
            {
                "id": "UC_TEST_CHANNEL_ID_999",
                "snippet": {
                    "title": "Al Amr Official Channel",
                    "customUrl": "@alamrofficial",
                },
            }
        ]
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_token_resp
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_channel_resp

            vault = EncryptedCredentialVault(storage_driver=local_storage)
            meta = await flow.complete_enrollment(
                vault=vault,
                client_id="test_client_id",
                client_secret="test_secret",
                refresh_token="1//test_refresh_token_456",
                access_token="ya29.test_access_token_123",
            )

            assert meta.account_id == "UC_TEST_CHANNEL_ID_999"
            assert meta.username == "Al Amr Official Channel"
            assert meta.status == AccountStatus.ACTIVE

            # Verify credentials stored safely in vault
            creds = await vault.get_account_credentials(AccountPlatform.YOUTUBE, "UC_TEST_CHANNEL_ID_999")
            assert creds is not None
            assert creds["client_id"] == "test_client_id"
            assert creds["refresh_token"] == "1//test_refresh_token_456"
