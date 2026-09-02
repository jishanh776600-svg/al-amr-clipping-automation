"""Unit tests for Approval models and compact callback payload serialization."""

import pytest
from clipping.approval.models import (
    ApprovalAction,
    ApprovalStatus,
    TelegramCallbackPayload,
    ApprovalRequest,
    ApprovalAuditRecord,
    ApprovalSummary,
)


def test_telegram_callback_payload_serialization():
    payload = TelegramCallbackPayload(action=ApprovalAction.APPROVE, approval_request_id="req_clip_001")
    serialized = payload.serialize()
    assert serialized == "v1:A:req_clip_001"
    assert len(serialized.encode("utf-8")) <= 64

    parsed = TelegramCallbackPayload.parse(serialized)
    assert parsed.action == ApprovalAction.APPROVE
    assert parsed.approval_request_id == "req_clip_001"


def test_telegram_callback_payload_reject_action():
    payload = TelegramCallbackPayload(action=ApprovalAction.REJECT, approval_request_id="req_clip_002")
    serialized = payload.serialize()
    assert serialized == "v1:R:req_clip_002"

    parsed = TelegramCallbackPayload.parse(serialized)
    assert parsed.action == ApprovalAction.REJECT
    assert parsed.approval_request_id == "req_clip_002"


def test_telegram_callback_payload_invalid_format():
    with pytest.raises(ValueError):
        TelegramCallbackPayload.parse("invalid_payload")

    with pytest.raises(ValueError):
        TelegramCallbackPayload.parse("v2:A:req_01")  # Invalid protocol version

    with pytest.raises(ValueError):
        TelegramCallbackPayload.parse("v1:X:req_01")  # Invalid action code


def test_telegram_callback_payload_exceeds_64_bytes():
    long_id = "x" * 65
    with pytest.raises(Exception):
        TelegramCallbackPayload(action=ApprovalAction.APPROVE, approval_request_id=long_id)


def test_approval_request_model_validation():
    req = ApprovalRequest(
        approval_request_id="req_001",
        job_id="job_001",
        source_video_id="src_001",
        clip_id="clip_001",
        clip_index=1,
        title="Hook Title",
        start_time=10.0,
        end_time=40.0,
        duration=30.0,
        score=92.5,
        video_storage_key="clips/clip_001/final_1080x1920.mp4",
        status=ApprovalStatus.AWAITING_APPROVAL,
    )
    assert req.approval_request_id == "req_001"
    assert req.status == ApprovalStatus.AWAITING_APPROVAL
    assert req.duration == 30.0


def test_approval_summary_model():
    summary = ApprovalSummary(
        job_id="job_123",
        total_clips=5,
        approved_count=3,
        rejected_count=2,
        awaiting_count=0,
        all_decided=True,
    )
    assert summary.all_decided is True
    assert summary.total_clips == 5
