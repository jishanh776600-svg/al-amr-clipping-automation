"""Unit tests for ApprovalService lifecycle, transitions, idempotency, security, and audit trails."""

import pytest
from clipping.approval.models import (
    ApprovalRequest,
    ApprovalStatus,
    ApprovalAction,
    TelegramCallbackPayload,
)
from clipping.approval.repository import ApprovalRepository
from clipping.approval.transport import MockTelegramTransport
from clipping.approval.security import SecurityValidator
from clipping.approval.service import ApprovalService
from clipping.storage.local import LocalStorageDriver


@pytest.fixture
def approval_setup(temp_vault_dir):
    storage = LocalStorageDriver(root_dir=temp_vault_dir)
    repo = ApprovalRepository(storage_driver=storage)
    transport = MockTelegramTransport()
    security = SecurityValidator(
        allowed_user_ids={999999},
        allowed_chat_ids={-100123456789},
    )
    service = ApprovalService(repository=repo, transport=transport, security_validator=security)
    return {
        "storage": storage,
        "repo": repo,
        "transport": transport,
        "security": security,
        "service": service,
    }


@pytest.mark.asyncio
async def test_create_and_send_request_lifecycle(approval_setup):
    service = approval_setup["service"]
    transport = approval_setup["transport"]
    repo = approval_setup["repo"]

    req = ApprovalRequest(
        approval_request_id="req_test_01",
        job_id="job_test_01",
        source_video_id="src_01",
        clip_id="clip_01",
        clip_index=1,
        title="Mind Blowing Revelation",
        hook_sentence="This changed everything for me.",
        start_time=12.0,
        end_time=42.0,
        duration=30.0,
        score=88.5,
        video_storage_key="clips/clip_01/final_1080x1920.mp4",
    )

    dispatched = await service.create_and_send_request(req, chat_id=-100123456789)
    assert dispatched.telegram_message_id is not None
    assert len(transport.sent_messages) == 1
    assert "Mind Blowing Revelation" in transport.sent_messages[0]["text"]

    # Verify persisted in storage
    saved = await repo.get_request("job_test_01", "req_test_01")
    assert saved is not None
    assert saved.telegram_message_id == dispatched.telegram_message_id

    # Idempotent re-send: Must NOT send another message to Telegram
    re_dispatched = await service.create_and_send_request(req, chat_id=-100123456789)
    assert len(transport.sent_messages) == 1
    assert re_dispatched.telegram_message_id == dispatched.telegram_message_id


@pytest.mark.asyncio
async def test_callback_approval_transition(approval_setup):
    service = approval_setup["service"]
    transport = approval_setup["transport"]
    repo = approval_setup["repo"]

    req = ApprovalRequest(
        approval_request_id="req_approve_01",
        job_id="job_01",
        source_video_id="src_01",
        clip_id="clip_01",
        clip_index=1,
        title="Great Hook",
        start_time=0.0,
        end_time=30.0,
        duration=30.0,
        score=90.0,
        video_storage_key="clips/clip_01/final_1080x1920.mp4",
    )
    await service.create_and_send_request(req, chat_id=-100123456789)

    # Simulate Telegram inline button tap: APPROVE
    callback_data = TelegramCallbackPayload(action=ApprovalAction.APPROVE, approval_request_id="req_approve_01").serialize()
    callback_query = {
        "id": "cb_query_101",
        "from": {"id": 999999, "first_name": "Reviewer"},
        "data": callback_data,
        "message": {"message_id": 1000, "chat": {"id": -100123456789}},
    }

    result = await service.handle_callback_query(callback_query)
    assert result["status"] == "success"
    assert result["decision"] == "approved"

    # Verify request updated to APPROVED
    updated = await repo.get_request("job_01", "req_approve_01")
    assert updated.status == ApprovalStatus.APPROVED
    assert updated.decided_by == 999999
    assert updated.version == 2

    # Verify message edited in Telegram (badge + removed keyboard)
    assert len(transport.edited_messages) == 1
    assert "APPROVED" in transport.edited_messages[0]["text"]
    assert transport.edited_messages[0]["reply_markup"] == {"inline_keyboard": []}

    # Verify audit trail
    audits = await repo.list_audits_for_job("job_01")
    assert len(audits) == 1
    assert audits[0].new_status == ApprovalStatus.APPROVED
    assert audits[0].telegram_user_id == 999999


@pytest.mark.asyncio
async def test_callback_rejection_transition(approval_setup):
    service = approval_setup["service"]
    repo = approval_setup["repo"]

    req = ApprovalRequest(
        approval_request_id="req_reject_01",
        job_id="job_02",
        source_video_id="src_02",
        clip_id="clip_02",
        clip_index=2,
        title="Weak Hook",
        start_time=30.0,
        end_time=60.0,
        duration=30.0,
        score=65.0,
        video_storage_key="clips/clip_02/final_1080x1920.mp4",
    )
    await service.create_and_send_request(req, chat_id=-100123456789)

    # Simulate Telegram inline button tap: REJECT
    callback_data = TelegramCallbackPayload(action=ApprovalAction.REJECT, approval_request_id="req_reject_01").serialize()
    callback_query = {
        "id": "cb_query_102",
        "from": {"id": 999999, "first_name": "Reviewer"},
        "data": callback_data,
        "message": {"message_id": 1000, "chat": {"id": -100123456789}},
    }

    result = await service.handle_callback_query(callback_query)
    assert result["status"] == "success"
    assert result["decision"] == "rejected"

    updated = await repo.get_request("job_02", "req_reject_01")
    assert updated.status == ApprovalStatus.REJECTED


@pytest.mark.asyncio
async def test_callback_replay_idempotency(approval_setup):
    service = approval_setup["service"]
    repo = approval_setup["repo"]

    req = ApprovalRequest(
        approval_request_id="req_replay_01",
        job_id="job_03",
        source_video_id="src_03",
        clip_id="clip_03",
        clip_index=1,
        title="Replay Test",
        start_time=0.0,
        end_time=30.0,
        duration=30.0,
        score=85.0,
        video_storage_key="clips/clip_03/final_1080x1920.mp4",
    )
    await service.create_and_send_request(req, chat_id=-100123456789)

    callback_data = TelegramCallbackPayload(action=ApprovalAction.APPROVE, approval_request_id="req_replay_01").serialize()
    query = {
        "id": "cb_query_103",
        "from": {"id": 999999, "first_name": "Reviewer"},
        "data": callback_data,
        "message": {"message_id": 1000, "chat": {"id": -100123456789}},
    }

    # 1. First tap -> approved
    first_res = await service.handle_callback_query(query)
    assert first_res["status"] == "success"

    # 2. Second tap (replay) -> already_approved, no duplicate mutation or audit spam
    second_res = await service.handle_callback_query(query)
    assert second_res["status"] == "already_approved"

    audits = await repo.list_audits_for_job("job_03")
    assert len(audits) == 1


@pytest.mark.asyncio
async def test_unauthorized_user_rejection(approval_setup):
    service = approval_setup["service"]
    transport = approval_setup["transport"]

    # Attacker with unapproved user ID 666666
    query = {
        "id": "cb_query_attacker",
        "from": {"id": 666666, "first_name": "Attacker"},
        "data": "v1:A:req_any",
        "message": {"message_id": 1000, "chat": {"id": -100123456789}},
    }

    res = await service.handle_callback_query(query)
    assert res["status"] == "unauthorized"
    assert len(transport.answered_callbacks) == 1
    assert "Unauthorized" in transport.answered_callbacks[0]["text"]


@pytest.mark.asyncio
async def test_unauthorized_chat_rejection(approval_setup):
    service = approval_setup["service"]

    # Authorized user but coming from an unknown/unauthorized chat
    query = {
        "id": "cb_query_bad_chat",
        "from": {"id": 999999, "first_name": "Reviewer"},
        "data": "v1:A:req_any",
        "message": {"message_id": 1000, "chat": {"id": -999999999999}},
    }

    res = await service.handle_callback_query(query)
    assert res["status"] == "unauthorized"
