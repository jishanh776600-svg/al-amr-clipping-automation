"""Unit tests for TelegramApprovalGateway, Dispatcher polling, and token security."""

import pytest
from datetime import datetime, timezone
from clipping.approval.models import (
    ApprovalAction,
    TelegramCallbackPayload,
)
from clipping.approval.repository import ApprovalRepository
from clipping.approval.transport import MockTelegramTransport, mask_bot_token
from clipping.approval.security import SecurityValidator
from clipping.approval.service import ApprovalService
from clipping.approval.gateway import TelegramApprovalGateway
from clipping.approval.dispatcher import TelegramApprovalDispatcher
from clipping.contracts.clip import ClipCandidate, ClipScore, RankedCandidate
from clipping.contracts.rendering import RenderOutput
from clipping.storage.local import LocalStorageDriver


def test_mask_bot_token():
    sensitive_url = "https://api.telegram.org/bot123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11/sendMessage"
    masked = mask_bot_token(sensitive_url)
    assert "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11" not in masked
    assert "bot<MASKED_TOKEN>" in masked


@pytest.fixture
def gateway_setup(temp_vault_dir):
    storage = LocalStorageDriver(root_dir=temp_vault_dir)
    repo = ApprovalRepository(storage_driver=storage)
    transport = MockTelegramTransport()
    security = SecurityValidator(
        allowed_user_ids={888888},
        allowed_chat_ids={-100555555555},
    )
    service = ApprovalService(repository=repo, transport=transport, security_validator=security)
    gateway = TelegramApprovalGateway(approval_service=service, approval_repository=repo)
    dispatcher = TelegramApprovalDispatcher(
        approval_service=service,
        transport=transport,
        storage_driver=storage,
    )
    return {
        "storage": storage,
        "repo": repo,
        "transport": transport,
        "service": service,
        "gateway": gateway,
        "dispatcher": dispatcher,
    }


@pytest.mark.asyncio
async def test_gateway_dispatch_and_summary(gateway_setup):
    gateway = gateway_setup["gateway"]
    service = gateway_setup["service"]

    job_id = "job_gw_01"
    source_id = "src_gw_01"

    # Create 3 candidate clips
    candidates = []
    render_outs = {}
    for i in range(1, 4):
        cid = f"clip_gw_{i:02d}"
        cand = RankedCandidate(
            candidate=ClipCandidate(
                candidate_id=cid,
                source_video_id=source_id,
                start_time=(i - 1) * 30.0,
                end_time=i * 30.0,
                duration=30.0,
                transcript_text=f"Clip transcript {i}",
                hook_sentence=f"Hook number {i}",
            ),
            score=ClipScore(
                candidate_id=cid,
                hook_strength=80.0,
                narrative_completeness=85.0,
                curiosity_factor=80.0,
                overall_virality_score=82.0 + i,
            ),
            rank=i,
        )
        candidates.append(cand)
        render_outs[cid] = RenderOutput(
            clip_id=cid,
            output_storage_key=f"clips/{cid}/final.mp4",
            duration_seconds=30.0,
            file_size_bytes=1000,
            render_time_seconds=1.0,
            completed_at=datetime.now(timezone.utc),
        )

    # 1. Dispatch cards to Telegram
    dispatched = await gateway.dispatch_candidate_clips(
        job_id=job_id,
        source_video_id=source_id,
        ranked_candidates=candidates,
        render_outputs=render_outs,
        chat_id=-100555555555,
    )
    assert len(dispatched) == 3

    # 2. Check initial summary
    summary_init = await gateway.get_approval_summary(job_id)
    assert summary_init.total_clips == 3
    assert summary_init.awaiting_count == 3
    assert summary_init.approved_count == 0
    assert summary_init.rejected_count == 0
    assert summary_init.all_decided is False

    # 3. Simulate decision on Clip 1 (Approve) and Clip 2 (Reject)
    req1_id = dispatched[0].approval_request_id
    req2_id = dispatched[1].approval_request_id

    cb1 = TelegramCallbackPayload(action=ApprovalAction.APPROVE, approval_request_id=req1_id).serialize()
    await service.handle_callback_query({
        "id": "q1",
        "from": {"id": 888888, "first_name": "Boss"},
        "data": cb1,
        "message": {"message_id": 1000, "chat": {"id": -100555555555}},
    })

    cb2 = TelegramCallbackPayload(action=ApprovalAction.REJECT, approval_request_id=req2_id).serialize()
    await service.handle_callback_query({
        "id": "q2",
        "from": {"id": 888888, "first_name": "Boss"},
        "data": cb2,
        "message": {"message_id": 1001, "chat": {"id": -100555555555}},
    })

    summary_mid = await gateway.get_approval_summary(job_id)
    assert summary_mid.approved_count == 1
    assert summary_mid.rejected_count == 1
    assert summary_mid.awaiting_count == 1
    assert summary_mid.all_decided is False

    # 4. Decide on Clip 3 (Approve)
    req3_id = dispatched[2].approval_request_id
    cb3 = TelegramCallbackPayload(action=ApprovalAction.APPROVE, approval_request_id=req3_id).serialize()
    await service.handle_callback_query({
        "id": "q3",
        "from": {"id": 888888, "first_name": "Boss"},
        "data": cb3,
        "message": {"message_id": 1002, "chat": {"id": -100555555555}},
    })

    # Final summary check
    summary_final = await gateway.get_approval_summary(job_id)
    assert summary_final.approved_count == 2
    assert summary_final.rejected_count == 1
    assert summary_final.awaiting_count == 0
    assert summary_final.all_decided is True

    # Check approved clips
    approved_clips = await gateway.get_approved_clips(job_id)
    assert len(approved_clips) == 2
    assert {c.clip_id for c in approved_clips} == {"clip_gw_01", "clip_gw_03"}


@pytest.mark.asyncio
async def test_dispatcher_polling_and_offset_checkpoint(gateway_setup):
    dispatcher = gateway_setup["dispatcher"]
    transport = gateway_setup["transport"]
    repo = gateway_setup["repo"]
    storage = gateway_setup["storage"]

    # Seed a pending request
    from clipping.approval.models import ApprovalRequest, ApprovalStatus
    req = ApprovalRequest(
        approval_request_id="req_poll_01",
        job_id="job_poll_01",
        source_video_id="src_01",
        clip_id="clip_01",
        clip_index=1,
        title="Poll Clip",
        start_time=0.0,
        end_time=20.0,
        duration=20.0,
        score=95.0,
        video_storage_key="clips/clip_01/final.mp4",
        status=ApprovalStatus.AWAITING_APPROVAL,
    )
    await repo.save_request(req)

    # Queue mock updates into transport
    cb_data = TelegramCallbackPayload(action=ApprovalAction.APPROVE, approval_request_id="req_poll_01").serialize()
    transport.queued_updates = [
        {
            "update_id": 5001,
            "callback_query": {
                "id": "q_poll_1",
                "from": {"id": 888888, "first_name": "Reviewer"},
                "data": cb_data,
                "message": {"message_id": 2000, "chat": {"id": -100555555555}},
            },
        }
    ]

    # Poll and process
    count = await dispatcher.poll_and_process_once(limit=10)
    assert count == 1

    # Verify request updated to APPROVED
    updated = await repo.get_request("job_poll_01", "req_poll_01")
    assert updated.status == ApprovalStatus.APPROVED

    # Verify offset was checkpointed in storage
    offset = await dispatcher.get_current_offset()
    assert offset == 5002
