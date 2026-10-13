import pytest
from unittest.mock import AsyncMock, patch
from autoclip.api.creator import (
    DevicePollIn,
    start_creator_youtube_device_code,
    check_creator_youtube_device_poll,
)
from autoclip.db import models


@pytest.mark.asyncio
async def test_creator_youtube_device_code_start():
    fake_creator = models.CreatorAccount(
        id="usr_test1",
        passcode="test_pass_123",
        display_name="Tester",
        role="creator",
        daily_quota=20,
    )

    with patch("autoclip.api.creator.YouTubePublisher") as MockPub, \
         patch("autoclip.api.creator.request_youtube_device_code", new_callable=AsyncMock) as mock_req:
        mock_instance = MockPub.return_value
        mock_instance.client_id = "test_client_id.apps.googleusercontent.com"
        mock_req.return_value = {
            "success": True,
            "device_code": "dev_code_123",
            "user_code": "ABCD-EFGH",
            "verification_url": "https://www.google.com/device",
            "expires_in": 1800,
            "interval": 5,
        }

        res = await start_creator_youtube_device_code(creator=fake_creator)
        assert res["success"] is True
        assert res["user_code"] == "ABCD-EFGH"
        assert res["device_code"] == "dev_code_123"


@pytest.mark.asyncio
async def test_creator_youtube_device_code_poll_authorized():
    fake_creator = models.CreatorAccount(
        id="usr_test1",
        passcode="test_pass_123",
        display_name="Tester",
        role="creator",
        daily_quota=20,
    )

    payload = DevicePollIn(device_code="dev_code_123")

    with patch("autoclip.api.creator.YouTubePublisher") as MockPub, \
         patch("autoclip.api.creator.poll_youtube_device_token", new_callable=AsyncMock) as mock_poll, \
         patch("autoclip.db.store.update_creator_account") as mock_update:
        mock_instance = MockPub.return_value
        mock_instance.client_id = "test_client_id.apps.googleusercontent.com"
        mock_instance.client_secret = "test_secret"

        mock_poll.return_value = {
            "success": True,
            "status": "authorized",
            "refresh_token": "1//refresh_token_abc",
            "channel_title": "Test Channel",
            "channel_id": "UC_test123",
        }

        res = await check_creator_youtube_device_poll(payload=payload, creator=fake_creator)
        assert res["success"] is True
        assert res["status"] == "authorized"
        assert res["channel_title"] == "Test Channel"
        assert fake_creator.youtube_channel_title == "Test Channel"
        assert fake_creator.youtube_channel_id == "UC_test123"
        assert fake_creator.youtube_refresh_token == "1//refresh_token_abc"
        mock_update.assert_called_once_with(fake_creator)


@pytest.mark.asyncio
async def test_creator_youtube_device_code_poll_pending():
    fake_creator = models.CreatorAccount(
        id="usr_test1",
        passcode="test_pass_123",
        display_name="Tester",
        role="creator",
        daily_quota=20,
    )

    payload = DevicePollIn(device_code="dev_code_123")

    with patch("autoclip.api.creator.YouTubePublisher") as MockPub, \
         patch("autoclip.api.creator.poll_youtube_device_token", new_callable=AsyncMock) as mock_poll:
        mock_instance = MockPub.return_value
        mock_instance.client_id = "test_client_id.apps.googleusercontent.com"
        mock_instance.client_secret = "test_secret"

        mock_poll.return_value = {
            "success": False,
            "status": "pending",
            "error": "authorization_pending",
        }

        res = await check_creator_youtube_device_poll(payload=payload, creator=fake_creator)
        assert res["success"] is True
        assert res["status"] == "pending"
