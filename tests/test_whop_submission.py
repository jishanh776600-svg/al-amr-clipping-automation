"""Comprehensive Test Suite for Step 8: Approval-to-Submission Bridge.

Validates:
1. Atomic CAS approval transitions and concurrent race conditions.
2. Review-session, campaign, and guideline binding.
3. Deterministic submission payload generation and canonical sorting.
4. Strictly enforced exactly-5-clips invariant (blocks if != 5).
5. Drive-backed media durability requirements.
6. Callback replay protection (idempotent no-op for repeat approvals).
7. Cross-decision exclusivity (cannot approve after rejection or vice versa).
8. Unauthorized Telegram operator rejection.
9. Dry-run mutation guards (blocks external mutation, simulates safely).
10. Timeout reconciliation handling and durable ledger recovery.
11. Secret redaction in audit trail events.
"""

import asyncio
import os
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from whop.ledger import CampaignLedger
from whop.models import (
    CampaignRecord,
    CampaignState,
    ClipQARecord,
    ClipTechnicalQAResult,
    SubmissionState,
    WhopCampaignBrief,
    WhopJobQAReport,
    WhopReviewSession,
    WhopSubmissionPayload,
    WhopSubmissionRecord,
)
from whop.submitter import SubmissionBlockedError, WhopSubmitter, WhopSubmissionResult
from whop.telegram_approval import TelegramApprovalGate


@pytest.fixture
def test_db(tmp_path: Path):
    db_file = tmp_path / "test_ledger.db"
    return CampaignLedger(db_path=db_file)


@pytest.fixture
def sample_qa_report() -> WhopJobQAReport:
    clips = []
    for i in range(5):
        cid = f"clip_{i+1:03d}"
        tqa = ClipTechnicalQAResult(
            clip_id=cid,
            duration_s=25.0 + i,
            width=1080,
            height=1920,
            fps=30.0,
            video_codec="h264",
            audio_codec="aac",
            channels=2,
            sample_rate=48000,
            mean_volume_db=-14.0,
            true_peak_db=-1.5,
            is_valid=True,
        )
        clips.append(
            ClipQARecord(
                clip_id=cid,
                candidate_index=i,
                technical_qa=tqa,
                drive_file_id=f"drive_id_{cid}",
                is_durable=True,
                is_distinct=True,
                quality_score=95.0,
                is_valid=True,
            )
        )
    return WhopJobQAReport(
        campaign_id="camp_123",
        guideline_hash="g_hash_abc",
        autoclip_job_id="job_xyz",
        artifact_hash="art_hash_123",
        qa_status="RENDER_PASS",
        overall_quality_score=95.0,
        valid_clips_count=5,
        total_clips_evaluated=5,
        clips=clips,
    )


@pytest.fixture
def populated_ledger(test_db: CampaignLedger, sample_qa_report: WhopJobQAReport):
    camp = CampaignRecord(
        campaign_id="camp_123",
        title="Test Campaign",
        campaign_url="https://whop.com/test",
        current_state=CampaignState.AWAITING_APPROVAL,
    )
    test_db.save_campaign(camp)
    test_db.save_qa_record(sample_qa_report)

    session = WhopReviewSession(
        review_session_id="rev_session_123",
        campaign_id="camp_123",
        guideline_hash="g_hash_abc",
        autoclip_job_id="job_xyz",
        artifact_hash="art_hash_123",
        idempotency_key="rev_idem_123",
        review_state="PENDING",
        chat_id="12345",
        clip_ids=[c.clip_id for c in sample_qa_report.clips],
        clip_order=[c.clip_id for c in sample_qa_report.clips],
    )
    test_db.save_review_session(session)
    return test_db


# ==============================================================================
# 1. Deterministic Payload & Idempotency Key Tests
# ==============================================================================

def test_deterministic_idempotency_key():
    k1 = WhopSubmitter.compute_submission_idempotency_key(
        campaign_id="camp_1",
        guideline_hash="ghash_1",
        review_session_id="rev_1",
        clip_ids=["clip_b", "clip_a", "clip_c"],
        drive_file_ids=["drive_2", "drive_1", "drive_3"],
    )
    k2 = WhopSubmitter.compute_submission_idempotency_key(
        campaign_id="camp_1",
        guideline_hash="ghash_1",
        review_session_id="rev_1",
        clip_ids=["clip_a", "clip_c", "clip_b"],  # permuted
        drive_file_ids=["drive_3", "drive_2", "drive_1"],  # permuted
    )
    assert k1 == k2, "Idempotency key must be invariant to input permutation"


def test_build_submission_payload_deterministic(populated_ledger: CampaignLedger):
    submitter = WhopSubmitter(ledger=populated_ledger)
    payload1 = submitter.build_submission_payload("camp_123", "rev_session_123")
    payload2 = submitter.build_submission_payload("camp_123", "rev_session_123")

    assert len(payload1.clips) == 5
    assert payload1.compute_hash() == payload2.compute_hash()
    # Check deterministic clip ordering
    assert [c.clip_id for c in payload1.clips] == sorted([c.clip_id for c in payload1.clips])


# ==============================================================================
# 2. Strict Invariants: Exactly-5 Clips & Drive Durability
# ==============================================================================

def test_payload_blocked_if_less_than_five_clips(populated_ledger: CampaignLedger):
    qa = populated_ledger.get_latest_qa_record("camp_123")
    # Invalidate 1 clip
    qa.clips[4].is_valid = False
    qa.valid_clips_count = 4
    populated_ledger.save_qa_record(qa)

    submitter = WhopSubmitter(ledger=populated_ledger)
    with pytest.raises(SubmissionBlockedError, match="required exactly 5"):
        submitter.build_submission_payload("camp_123", "rev_session_123")


def test_payload_blocked_if_missing_drive_id(populated_ledger: CampaignLedger):
    qa = populated_ledger.get_latest_qa_record("camp_123")
    qa.clips[2].drive_file_id = ""
    populated_ledger.save_qa_record(qa)

    submitter = WhopSubmitter(ledger=populated_ledger)
    with pytest.raises(SubmissionBlockedError, match="lacks a durable Google Drive"):
        submitter.build_submission_payload("camp_123", "rev_session_123")


def test_payload_blocked_on_duplicate_drive_id(populated_ledger: CampaignLedger):
    qa = populated_ledger.get_latest_qa_record("camp_123")
    qa.clips[1].drive_file_id = qa.clips[0].drive_file_id
    populated_ledger.save_qa_record(qa)

    submitter = WhopSubmitter(ledger=populated_ledger)
    with pytest.raises(SubmissionBlockedError, match="Duplicate Drive file ID"):
        submitter.build_submission_payload("camp_123", "rev_session_123")


# ==============================================================================
# 3. Atomic CAS Approval & Submissions
# ==============================================================================

def test_atomic_approval_cas_success(populated_ledger: CampaignLedger):
    success, code, sub = populated_ledger.atomic_approve_and_create_submission(
        campaign_id="camp_123",
        review_session_id="rev_session_123",
        reviewer_id="user_1",
        reviewer_username="operator",
        submission_id="sub_test_001",
        idempotency_key="sub_idem_001",
        clip_ids=["clip_001", "clip_002", "clip_003", "clip_004", "clip_005"],
        drive_file_ids=["d1", "d2", "d3", "d4", "d5"],
    )
    assert success is True
    assert code == "SUCCESS"
    assert sub is not None
    assert sub.submission_state == "PENDING"

    # Campaign and review session must be APPROVED
    camp = populated_ledger.get_campaign("camp_123")
    assert camp.current_state == CampaignState.APPROVED

    rev = populated_ledger.get_review_session("rev_session_123")
    assert rev.review_state == "APPROVED"
    assert rev.decision == "APPROVE"


def test_atomic_approval_replay_is_idempotent(populated_ledger: CampaignLedger):
    # First approval
    success1, code1, sub1 = populated_ledger.atomic_approve_and_create_submission(
        campaign_id="camp_123",
        review_session_id="rev_session_123",
        reviewer_id="user_1",
        reviewer_username="operator",
        submission_id="sub_test_001",
        idempotency_key="sub_idem_001",
        clip_ids=["clip_001", "clip_002", "clip_003", "clip_004", "clip_005"],
        drive_file_ids=["d1", "d2", "d3", "d4", "d5"],
    )
    assert success1 is True

    # Replay of same approval
    success2, code2, sub2 = populated_ledger.atomic_approve_and_create_submission(
        campaign_id="camp_123",
        review_session_id="rev_session_123",
        reviewer_id="user_1",
        reviewer_username="operator",
        submission_id="sub_test_001",
        idempotency_key="sub_idem_001",
        clip_ids=["clip_001", "clip_002", "clip_003", "clip_004", "clip_005"],
        drive_file_ids=["d1", "d2", "d3", "d4", "d5"],
    )
    assert success2 is True
    assert code2 == "ALREADY_APPROVED"
    assert sub2.submission_id == sub1.submission_id


def test_cannot_approve_after_rejection(populated_ledger: CampaignLedger):
    # Operator rejects first
    ok, code = populated_ledger.atomic_reject_or_change(
        campaign_id="camp_123",
        review_session_id="rev_session_123",
        decision="REJECT",
        reviewer_id="user_1",
        reviewer_username="operator",
    )
    assert ok is True

    # Try to approve
    success, code, sub = populated_ledger.atomic_approve_and_create_submission(
        campaign_id="camp_123",
        review_session_id="rev_session_123",
        reviewer_id="user_1",
        reviewer_username="operator",
        submission_id="sub_test_002",
        idempotency_key="sub_idem_002",
        clip_ids=["clip_001", "clip_002", "clip_003", "clip_004", "clip_005"],
        drive_file_ids=["d1", "d2", "d3", "d4", "d5"],
    )
    assert success is False
    assert "REJECTED" in code


def test_cannot_reject_after_approval(populated_ledger: CampaignLedger):
    # Approve first
    success, code, sub = populated_ledger.atomic_approve_and_create_submission(
        campaign_id="camp_123",
        review_session_id="rev_session_123",
        reviewer_id="user_1",
        reviewer_username="operator",
        submission_id="sub_test_001",
        idempotency_key="sub_idem_001",
        clip_ids=["clip_001", "clip_002", "clip_003", "clip_004", "clip_005"],
        drive_file_ids=["d1", "d2", "d3", "d4", "d5"],
    )
    assert success is True

    # Try to reject
    ok, code = populated_ledger.atomic_reject_or_change(
        campaign_id="camp_123",
        review_session_id="rev_session_123",
        decision="REJECT",
        reviewer_id="user_1",
        reviewer_username="operator",
    )
    assert ok is False


# ==============================================================================
# 4. Dry-Run Mutation Guard & Processing
# ==============================================================================

def test_dry_run_submission_processing(populated_ledger: CampaignLedger):
    submitter = WhopSubmitter(ledger=populated_ledger)

    # Approve atomically
    success, code, sub = populated_ledger.atomic_approve_and_create_submission(
        campaign_id="camp_123",
        review_session_id="rev_session_123",
        reviewer_id="user_1",
        reviewer_username="operator",
        submission_id="sub_test_dry",
        idempotency_key="sub_idem_dry",
        clip_ids=[f"clip_{i+1:03d}" for i in range(5)],
        drive_file_ids=[f"drive_id_clip_{i+1:03d}" for i in range(5)],
    )
    assert success is True

    # Process in dry-run mode
    res = submitter.process_submission(sub.submission_id, dry_run_override=True)
    assert res.success is True
    assert res.dry_run is True
    assert res.mutation_executed is False
    assert res.whop_submission_id.startswith("whop_sim_")

    # Verify final ledger states
    camp = populated_ledger.get_campaign("camp_123")
    assert camp.current_state == CampaignState.SUBMITTED

    stored_sub = populated_ledger.get_submission(sub.submission_id)
    assert stored_sub.submission_state == SubmissionState.SUBMITTED.value
    assert stored_sub.attempt_count == 1


# ==============================================================================
# 5. Telegram Callback Integration Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_telegram_callback_approve_flow(populated_ledger: CampaignLedger):
    gate = TelegramApprovalGate(ledger=populated_ledger)

    update = {
        "callback_query": {
            "id": "cb_001",
            "from": {"id": 7866408097, "username": "operator_test"},
            "data": "wh:appr:rev_session_123",
            "message": {"message_id": 999, "chat": {"id": 12345}},
        }
    }

    with patch("whop.telegram_approval._answer_callback_query", new_callable=AsyncMock), \
         patch("whop.telegram_approval._safe_edit_telegram_message", new_callable=AsyncMock) as mock_edit, \
         patch("whop.telegram_approval.is_user_authorized", return_value=True):

        res = await gate.handle_callback(update)
        assert res["status"] == "success"
        assert res["decision"] == "APPROVE"
        assert "submission_id" in res
        assert res["submission_result"]["success"] is True

        # Verify Telegram message edit was called with approval status
        assert mock_edit.called


@pytest.mark.asyncio
async def test_telegram_callback_unauthorized_user(populated_ledger: CampaignLedger):
    gate = TelegramApprovalGate(ledger=populated_ledger)

    update = {
        "callback_query": {
            "id": "cb_002",
            "from": {"id": 99999999, "username": "unauthorized_user"},
            "data": "wh:appr:rev_session_123",
            "message": {"message_id": 999, "chat": {"id": 12345}},
        }
    }

    with patch("whop.telegram_approval._answer_callback_query", new_callable=AsyncMock), \
         patch("whop.telegram_approval.is_user_authorized", return_value=False):

        res = await gate.handle_callback(update)
        assert res["status"] == "unauthorized"

        # Campaign must still be AWAITING_APPROVAL
        camp = populated_ledger.get_campaign("camp_123")
        assert camp.current_state == CampaignState.AWAITING_APPROVAL


@pytest.mark.asyncio
async def test_telegram_callback_reject_flow(populated_ledger: CampaignLedger):
    gate = TelegramApprovalGate(ledger=populated_ledger)

    update = {
        "callback_query": {
            "id": "cb_003",
            "from": {"id": 7866408097, "username": "operator_test"},
            "data": "wh:rej:rev_session_123",
            "message": {"message_id": 999, "chat": {"id": 12345}},
        }
    }

    with patch("whop.telegram_approval._answer_callback_query", new_callable=AsyncMock), \
         patch("whop.telegram_approval._safe_edit_telegram_message", new_callable=AsyncMock), \
         patch("whop.telegram_approval.is_user_authorized", return_value=True):

        res = await gate.handle_callback(update)
        assert res["status"] == "success"
        assert res["decision"] == "REJECT"

        camp = populated_ledger.get_campaign("camp_123")
        assert camp.current_state == CampaignState.APPROVAL_REJECTED

        # No submission record should have been created
        sub = populated_ledger.get_submission_by_review_session("rev_session_123")
        assert sub is None


# ==============================================================================
# 6. Concurrency, Recovery, & Security Tests
# ==============================================================================

def test_concurrent_callbacks_race_condition(populated_ledger: CampaignLedger):
    """Verifies that simultaneous approval callbacks result in exactly ONE submission."""
    import concurrent.futures

    results = []

    def try_approve(i: int):
        return populated_ledger.atomic_approve_and_create_submission(
            campaign_id="camp_123",
            review_session_id="rev_session_123",
            reviewer_id=f"user_{i}",
            reviewer_username=f"operator_{i}",
            submission_id=f"sub_concurrent_{i}",
            idempotency_key=f"sub_idem_concurrent_{i}",
            clip_ids=["clip_001", "clip_002", "clip_003", "clip_004", "clip_005"],
            drive_file_ids=["d1", "d2", "d3", "d4", "d5"],
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(try_approve, i) for i in range(5)]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())

    success_codes = [r[1] for r in results]
    assert "SUCCESS" in success_codes
    assert success_codes.count("SUCCESS") == 1, "Exactly one approval must win the race"


def test_process_restart_recovery_from_submitting(populated_ledger: CampaignLedger):
    """Simulates a worker crash while in SUBMITTING, and subsequent retry."""
    submitter = WhopSubmitter(ledger=populated_ledger)

    # Approve
    success, code, sub = populated_ledger.atomic_approve_and_create_submission(
        campaign_id="camp_123",
        review_session_id="rev_session_123",
        reviewer_id="user_1",
        reviewer_username="operator",
        submission_id="sub_crash_test",
        idempotency_key="sub_idem_crash",
        clip_ids=[f"clip_{i+1:03d}" for i in range(5)],
        drive_file_ids=[f"drive_id_clip_{i+1:03d}" for i in range(5)],
    )
    assert success is True

    # Manually simulate state left at SUBMITTING due to crash
    sub.submission_state = SubmissionState.SUBMITTING.value
    populated_ledger.update_submission(sub)

    # Worker restarts and retries
    res = submitter.process_submission(sub.submission_id, dry_run_override=True)
    assert res.success is True
    assert res.whop_submission_id.startswith("whop_sim_")

    final_sub = populated_ledger.get_submission(sub.submission_id)
    assert final_sub.submission_state == SubmissionState.SUBMITTED.value


def test_stale_callback_wrong_campaign(populated_ledger: CampaignLedger):
    """Verifies that an approval for a mismatched campaign ID is rejected."""
    success, code, sub = populated_ledger.atomic_approve_and_create_submission(
        campaign_id="wrong_camp_999",
        review_session_id="rev_session_123",
        reviewer_id="user_1",
        reviewer_username="operator",
        submission_id="sub_wrong",
        idempotency_key="sub_idem_wrong",
        clip_ids=["clip_001", "clip_002", "clip_003", "clip_004", "clip_005"],
        drive_file_ids=["d1", "d2", "d3", "d4", "d5"],
    )
    assert success is False
    assert code == "CAMPAIGN_MISMATCH"
    assert sub is None


def test_secret_redaction_in_audit_events(populated_ledger: CampaignLedger):
    """Verifies that secrets are not stored in plaintext in the audit trail."""
    secret_token = "Bearer secret_test_token_12345"
    populated_ledger.record_event(
        campaign_id="camp_123",
        target_state=CampaignState.SUBMITTED,
        reason=f"Authenticated using {secret_token}",
        metadata={"token": secret_token},
    )

    events = populated_ledger.get_campaign_events("camp_123")
    last_event = events[-1]
    assert "secret_test_token_12345" not in last_event.reason
    assert "secret_test_token_12345" not in last_event.metadata_json
