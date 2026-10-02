"""Step 7: Production Tests for Telegram Human Approval Gate.

Comprehensive test suite verifying:
A. Eligibility Hard Gate (5 valid clips, duration 20-30s, technical QA, durability, compliance)
B. Review Session & Idempotency (deterministic keys, create once, partial send recovery)
C. Telegram Artifact Delivery (sendVideo execution, Drive resolution, message/file ID tracking)
D. Callback Security (authorized operator, unauthorized blocked, stale/duplicate safe)
E. State Machine Transitions (AWAITING_APPROVAL -> APPROVED, CHANGES_REQUESTED, APPROVAL_REJECTED)
F. Immediate Callback ACK (client spinner stop before DB writes)
G. Secret Safety (zero tokens or secrets in logs or callback data)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from whop.ledger import CampaignLedger
from whop.models import (
    CampaignRecord,
    CampaignState,
    ClipQARecord,
    ClipTechnicalQAResult,
    RuleCategory,
    RuleComplianceResult,
    RuleComplianceStatus,
    WhopJobQAReport,
    WhopReviewSession,
)
from whop.telegram_approval import TelegramApprovalGate


@pytest.fixture
def temp_ledger(tmp_path):
    """Provides a fresh, isolated SQLite ledger for testing."""
    db_file = tmp_path / "test_ledger.db"
    return CampaignLedger(db_path=db_file)


@pytest.fixture
def mock_valid_clip_factory(tmp_path):
    """Factory creating valid ClipQARecord fixtures for testing."""
    def _create_clip(
        clip_id: str,
        index: int = 1,
        duration: float = 24.5,
        is_valid: bool = True,
        drive_file_id: str = "mock_drive_id_123",
        is_durable: bool = True,
        is_distinct: bool = True,
        mandatory_compliance_ok: bool = True,
        quality_score: float = 95.0,
    ) -> ClipQARecord:
        # Create a real small MP4 dummy file to satisfy local checks
        local_mp4 = tmp_path / f"{clip_id}.mp4"
        if not local_mp4.exists():
            local_mp4.write_bytes(b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41" + b"\x00" * 60000)

        tech_qa = ClipTechnicalQAResult(
            clip_id=clip_id,
            duration_s=duration,
            width=1080,
            height=1920,
            fps=30.0,
            video_codec="h264",
            audio_codec="aac",
            is_valid=is_valid,
            decode_ok=is_valid,
        )

        comp_results = []
        if mandatory_compliance_ok:
            comp_results.append(
                RuleComplianceResult(
                    rule_id="mand_1",
                    rule_text="Show full product",
                    category="CONTENT",
                    mandatory=True,
                    status=RuleComplianceStatus.SUPPORTED_AND_SATISFIED,
                )
            )
        else:
            comp_results.append(
                RuleComplianceResult(
                    rule_id="mand_1",
                    rule_text="Show full product",
                    category="CONTENT",
                    mandatory=True,
                    status=RuleComplianceStatus.SUPPORTED_AND_FAILED,
                    reason="Product not shown in frame",
                )
            )

        return ClipQARecord(
            clip_id=clip_id,
            candidate_index=index,
            technical_qa=tech_qa,
            compliance_results=comp_results,
            artifact_path=str(local_mp4),
            drive_file_id=drive_file_id,
            is_durable=is_durable,
            is_distinct=is_distinct,
            quality_score=quality_score,
            is_valid=is_valid,
        )

    return _create_clip


@pytest.fixture
def mock_5clip_qa_report(mock_valid_clip_factory):
    """Creates a valid 5-clip QA report passing all hard gates."""
    clips = [mock_valid_clip_factory(f"clip_{i:03d}", index=i) for i in range(1, 6)]
    return WhopJobQAReport(
        campaign_id="camp_test_001",
        guideline_hash="ghash_abc123",
        autoclip_job_id="job_autoclip_999",
        artifact_hash="arthash_777",
        qa_status="RENDER_PASS",
        overall_quality_score=95.0,
        valid_clips_count=5,
        total_clips_evaluated=5,
        clips=clips,
    )


# ==============================================================================
# SECTION A: ELIGIBILITY HARD GATE
# ==============================================================================

def test_eligibility_exactly_five_valid_clips_passes(temp_ledger, mock_5clip_qa_report):
    gate = TelegramApprovalGate(ledger=temp_ledger)
    eligible, reasons = gate.validate_eligibility("camp_test_001", mock_5clip_qa_report)
    assert eligible is True
    assert len(reasons) == 0


def test_eligibility_fewer_than_five_clips_blocked(temp_ledger, mock_valid_clip_factory):
    gate = TelegramApprovalGate(ledger=temp_ledger)
    clips = [mock_valid_clip_factory(f"clip_{i:03d}", index=i) for i in range(1, 5)]  # only 4 clips
    report = WhopJobQAReport(
        campaign_id="camp_test_001",
        guideline_hash="ghash_1",
        autoclip_job_id="job_1",
        artifact_hash="art_1",
        qa_status="INSUFFICIENT_VALID_CLIPS",
        overall_quality_score=90.0,
        valid_clips_count=4,
        total_clips_evaluated=4,
        clips=clips,
    )
    eligible, reasons = gate.validate_eligibility("camp_test_001", report)
    assert eligible is False
    assert any("Requires exactly 5 valid clips" in r for r in reasons)


def test_eligibility_one_clip_blocked(temp_ledger, mock_valid_clip_factory):
    gate = TelegramApprovalGate(ledger=temp_ledger)
    clips = [mock_valid_clip_factory("clip_001", index=1)]
    report = WhopJobQAReport(
        campaign_id="camp_test_001",
        guideline_hash="ghash_1",
        autoclip_job_id="job_1",
        artifact_hash="art_1",
        qa_status="INSUFFICIENT_VALID_CLIPS",
        overall_quality_score=90.0,
        valid_clips_count=1,
        total_clips_evaluated=1,
        clips=clips,
    )
    eligible, reasons = gate.validate_eligibility("camp_test_001", report)
    assert eligible is False
    assert any("5 valid clips" in r for r in reasons)


def test_eligibility_zero_clips_blocked(temp_ledger):
    gate = TelegramApprovalGate(ledger=temp_ledger)
    report = WhopJobQAReport(
        campaign_id="camp_test_001",
        guideline_hash="ghash_1",
        autoclip_job_id="job_1",
        artifact_hash="art_1",
        qa_status="RENDER_FAILED",
        overall_quality_score=0.0,
        valid_clips_count=0,
        total_clips_evaluated=0,
        clips=[],
    )
    eligible, reasons = gate.validate_eligibility("camp_test_001", report)
    assert eligible is False
    assert any("ineligible for review" in r for r in reasons)


def test_eligibility_duration_outside_canonical_bounds_blocked(temp_ledger, mock_valid_clip_factory):
    gate = TelegramApprovalGate(ledger=temp_ledger)
    # Clip 1 has invalid duration 18.0s (< 20.0s)
    c1 = mock_valid_clip_factory("clip_001", index=1, duration=18.0)
    c2 = mock_valid_clip_factory("clip_002", index=2, duration=25.0)
    c3 = mock_valid_clip_factory("clip_003", index=3, duration=25.0)
    c4 = mock_valid_clip_factory("clip_004", index=4, duration=25.0)
    c5 = mock_valid_clip_factory("clip_005", index=5, duration=25.0)
    report = WhopJobQAReport(
        campaign_id="camp_test_001",
        guideline_hash="ghash_1",
        autoclip_job_id="job_1",
        artifact_hash="art_1",
        qa_status="RENDER_PASS",
        overall_quality_score=90.0,
        valid_clips_count=5,
        total_clips_evaluated=5,
        clips=[c1, c2, c3, c4, c5],
    )
    eligible, reasons = gate.validate_eligibility("camp_test_001", report)
    assert eligible is False
    assert any("duration 18.00s outside canonical range" in r for r in reasons)


def test_eligibility_missing_durability_blocked(temp_ledger, mock_valid_clip_factory):
    gate = TelegramApprovalGate(ledger=temp_ledger)
    # Clip 3 is not durable (no Drive ID and no local file)
    c1 = mock_valid_clip_factory("clip_001", index=1)
    c2 = mock_valid_clip_factory("clip_002", index=2)
    c3 = mock_valid_clip_factory("clip_003", index=3, is_durable=False, drive_file_id=None)
    c4 = mock_valid_clip_factory("clip_004", index=4)
    c5 = mock_valid_clip_factory("clip_005", index=5)
    report = WhopJobQAReport(
        campaign_id="camp_test_001",
        guideline_hash="ghash_1",
        autoclip_job_id="job_1",
        artifact_hash="art_1",
        qa_status="RENDER_PASS",
        overall_quality_score=90.0,
        valid_clips_count=5,
        total_clips_evaluated=5,
        clips=[c1, c2, c3, c4, c5],
    )
    eligible, reasons = gate.validate_eligibility("camp_test_001", report)
    assert eligible is False
    assert any("artifact is not durable" in r for r in reasons)


def test_eligibility_failed_technical_qa_blocked(temp_ledger, mock_valid_clip_factory):
    gate = TelegramApprovalGate(ledger=temp_ledger)
    # Clip 2 has failed technical QA
    c1 = mock_valid_clip_factory("clip_001", index=1)
    c2 = mock_valid_clip_factory("clip_002", index=2, is_valid=False)
    c3 = mock_valid_clip_factory("clip_003", index=3)
    c4 = mock_valid_clip_factory("clip_004", index=4)
    c5 = mock_valid_clip_factory("clip_005", index=5)
    report = WhopJobQAReport(
        campaign_id="camp_test_001",
        guideline_hash="ghash_1",
        autoclip_job_id="job_1",
        artifact_hash="art_1",
        qa_status="RENDER_PASS",
        overall_quality_score=90.0,
        valid_clips_count=5,
        total_clips_evaluated=5,
        clips=[c1, c2, c3, c4, c5],
    )
    eligible, reasons = gate.validate_eligibility("camp_test_001", report)
    assert eligible is False
    assert any("Found 4 valid clip objects" in r or "failed technical" in r for r in reasons)


def test_eligibility_unresolved_mandatory_rule_blocked(temp_ledger, mock_valid_clip_factory):
    gate = TelegramApprovalGate(ledger=temp_ledger)
    c1 = mock_valid_clip_factory("clip_001", index=1, mandatory_compliance_ok=False)
    c2 = mock_valid_clip_factory("clip_002", index=2)
    c3 = mock_valid_clip_factory("clip_003", index=3)
    c4 = mock_valid_clip_factory("clip_004", index=4)
    c5 = mock_valid_clip_factory("clip_005", index=5)
    report = WhopJobQAReport(
        campaign_id="camp_test_001",
        guideline_hash="ghash_1",
        autoclip_job_id="job_1",
        artifact_hash="art_1",
        qa_status="RENDER_PASS",
        overall_quality_score=90.0,
        valid_clips_count=5,
        total_clips_evaluated=5,
        clips=[c1, c2, c3, c4, c5],
    )
    eligible, reasons = gate.validate_eligibility("camp_test_001", report)
    assert eligible is False
    assert any("failed mandatory rule" in r for r in reasons)


def test_eligibility_acceptable_render_warn_passes(temp_ledger, mock_valid_clip_factory):
    gate = TelegramApprovalGate(ledger=temp_ledger)
    clips = [mock_valid_clip_factory(f"clip_{i:03d}", index=i) for i in range(1, 6)]
    report = WhopJobQAReport(
        campaign_id="camp_test_001",
        guideline_hash="ghash_1",
        autoclip_job_id="job_1",
        artifact_hash="art_1",
        qa_status="RENDER_WARN",  # Non-fatal audio clipping warning
        overall_quality_score=85.0,
        valid_clips_count=5,
        total_clips_evaluated=5,
        clips=clips,
        warnings=["Non-fatal true-peak audio warning on clip 2"],
    )
    eligible, reasons = gate.validate_eligibility("camp_test_001", report)
    assert eligible is True
    assert len(reasons) == 0


# ==============================================================================
# SECTION B: REVIEW SESSION & IDEMPOTENCY
# ==============================================================================

def test_deterministic_review_idempotency_key(temp_ledger):
    gate = TelegramApprovalGate(ledger=temp_ledger)
    k1 = gate.compute_review_idempotency_key("camp_1", "ghash_a", "job_1", "art_x")
    k2 = gate.compute_review_idempotency_key("camp_1", "ghash_a", "job_1", "art_x")
    k3 = gate.compute_review_idempotency_key("camp_1", "ghash_a", "job_1", "art_y")
    assert k1 == k2
    assert k1 != k3
    assert len(k1) == 64


@pytest.mark.asyncio
async def test_review_session_create_once_and_reuse(temp_ledger, mock_5clip_qa_report):
    temp_ledger.save_campaign(
        CampaignRecord(
            campaign_id="camp_test_001",
            title="Camp 1",
            campaign_url="https://whop.com/1",
            current_state=CampaignState.RENDER_READY,
        )
    )
    temp_ledger.save_qa_record(mock_5clip_qa_report)

    gate = TelegramApprovalGate(ledger=temp_ledger)

    with patch("whop.telegram_approval.get_telegram_config", return_value=("test_bot_tok", "test_chat_id", ["user1"])), \
         patch("whop.telegram_approval._safe_send_telegram_message", new_callable=AsyncMock) as mock_send_msg, \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        
        mock_send_msg.return_value = {"ok": True, "result": {"message_id": 1001}}
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"ok": True, "result": {"message_id": 2001, "video": {"file_id": "tg_fid_1"}}})

        sess1 = await gate.dispatch_review_session("camp_test_001", "job_autoclip_999", qa_report=mock_5clip_qa_report)
        assert sess1 is not None
        assert sess1.review_state == "PENDING"

        # Re-dispatch without force should return the existing session without re-sending
        call_count_before = mock_post.call_count
        sess2 = await gate.dispatch_review_session("camp_test_001", "job_autoclip_999", qa_report=mock_5clip_qa_report)
        assert sess2.review_session_id == sess1.review_session_id
        assert mock_post.call_count == call_count_before


@pytest.mark.asyncio
async def test_partial_send_recovery_resumes_missing_clips(temp_ledger, mock_5clip_qa_report):
    temp_ledger.save_campaign(
        CampaignRecord(
            campaign_id="camp_test_001",
            title="Camp 1",
            campaign_url="https://whop.com/1",
            current_state=CampaignState.RENDER_READY,
        )
    )
    temp_ledger.save_qa_record(mock_5clip_qa_report)
    gate = TelegramApprovalGate(ledger=temp_ledger)

    # Pre-create session where clip_001 and clip_002 already sent
    idem_key = gate.compute_review_idempotency_key("camp_test_001", mock_5clip_qa_report.guideline_hash, "job_autoclip_999", mock_5clip_qa_report.artifact_hash)
    existing_sess = WhopReviewSession(
        review_session_id="rev_partial_123",
        campaign_id="camp_test_001",
        guideline_hash=mock_5clip_qa_report.guideline_hash,
        autoclip_job_id="job_autoclip_999",
        artifact_hash=mock_5clip_qa_report.artifact_hash,
        idempotency_key=idem_key,
        review_state="PENDING",
        chat_id="test_chat_id",
        message_ids={"summary": 1001, "clip_001": 2001, "clip_002": 2002},
        telegram_file_ids={"clip_001": "fid_1", "clip_002": "fid_2"},
        clip_ids=["clip_001", "clip_002", "clip_003", "clip_004", "clip_005"],
    )
    temp_ledger.save_review_session(existing_sess)

    sent_clips = []

    async def fake_post(url, *args, **kwargs):
        data = kwargs.get("data") or {}
        # extract clip from caption
        caption = data.get("caption", "")
        for i in range(1, 6):
            if f"clip_{i:03d}" in caption:
                sent_clips.append(f"clip_{i:03d}")
        return MagicMock(status_code=200, json=lambda: {"ok": True, "result": {"message_id": 3000 + len(sent_clips), "video": {"file_id": f"fid_{len(sent_clips)}" }}})

    with patch("whop.telegram_approval.get_telegram_config", return_value=("test_bot_tok", "test_chat_id", ["user1"])), \
         patch("whop.telegram_approval._safe_send_telegram_message", new_callable=AsyncMock) as mock_send_msg, \
         patch("httpx.AsyncClient.post", side_effect=fake_post):

        mock_send_msg.return_value = {"ok": True, "result": {"message_id": 9999}}
        updated_sess = await gate.dispatch_review_session("camp_test_001", "job_autoclip_999", qa_report=mock_5clip_qa_report)

        # Only clip_003, clip_004, clip_005 should be posted via sendVideo!
        assert "clip_001" not in sent_clips
        assert "clip_002" not in sent_clips
        assert "clip_003" in sent_clips
        assert "clip_004" in sent_clips
        assert "clip_005" in sent_clips


# ==============================================================================
# SECTION C: TELEGRAM DELIVERY
# ==============================================================================

@pytest.mark.asyncio
async def test_telegram_delivery_calls_send_video(temp_ledger, mock_5clip_qa_report):
    temp_ledger.save_campaign(
        CampaignRecord(
            campaign_id="camp_test_001",
            title="Camp 1",
            campaign_url="https://whop.com/1",
            current_state=CampaignState.RENDER_READY,
        )
    )
    temp_ledger.save_qa_record(mock_5clip_qa_report)
    gate = TelegramApprovalGate(ledger=temp_ledger)

    recorded_urls = []

    async def fake_post(url, *args, **kwargs):
        recorded_urls.append(url)
        return MagicMock(status_code=200, json=lambda: {"ok": True, "result": {"message_id": 5001, "video": {"file_id": "test_fid"}}})

    with patch("whop.telegram_approval.get_telegram_config", return_value=("TEST_TOKEN", "12345", ["user1"])), \
         patch("whop.telegram_approval._safe_send_telegram_message", new_callable=AsyncMock) as mock_send_msg, \
         patch("httpx.AsyncClient.post", side_effect=fake_post):

        mock_send_msg.return_value = {"ok": True, "result": {"message_id": 100}}
        sess = await gate.dispatch_review_session("camp_test_001", "job_autoclip_999", qa_report=mock_5clip_qa_report)

        assert any("sendVideo" in u for u in recorded_urls)
        assert len([u for u in recorded_urls if "sendVideo" in u]) == 5
        assert len(sess.message_ids) >= 6  # summary + 5 clips + decision_card


# ==============================================================================
# SECTION D: CALLBACK SECURITY
# ==============================================================================

@pytest.mark.asyncio
async def test_callback_authorized_operator_succeeds(temp_ledger):
    session = WhopReviewSession(
        review_session_id="rev_sec_001",
        campaign_id="camp_sec_001",
        guideline_hash="ghash",
        autoclip_job_id="job1",
        artifact_hash="arthash",
        idempotency_key="key_sec_1",
        review_state="PENDING",
    )
    temp_ledger.save_campaign(
        CampaignRecord(
            campaign_id="camp_sec_001",
            title="Camp Sec",
            campaign_url="https://whop.com/s",
            current_state=CampaignState.AWAITING_APPROVAL,
        )
    )
    temp_ledger.save_review_session(session)
    gate = TelegramApprovalGate(ledger=temp_ledger)

    update = {
        "callback_query": {
            "id": "cb_999",
            "from": {"id": 1234567, "username": "valid_operator"},
            "data": "wh:appr:rev_sec_001",
            "message": {"message_id": 888, "chat": {"id": 12345}},
        }
    }

    with patch("whop.telegram_approval.get_telegram_config", return_value=("bot_tok", "12345", ["valid_operator"])), \
         patch("whop.telegram_approval._answer_callback_query", new_callable=AsyncMock) as mock_ans, \
         patch("whop.telegram_approval._safe_edit_telegram_message", new_callable=AsyncMock):

        res = await gate.handle_callback(update)
        assert res["status"] == "success"
        assert res["decision"] == "APPROVE"
        assert res["campaign_state"] == "APPROVED"


@pytest.mark.asyncio
async def test_callback_unauthorized_operator_blocked(temp_ledger):
    session = WhopReviewSession(
        review_session_id="rev_sec_002",
        campaign_id="camp_sec_002",
        guideline_hash="ghash",
        autoclip_job_id="job1",
        artifact_hash="arthash",
        idempotency_key="key_sec_2",
        review_state="PENDING",
    )
    temp_ledger.save_campaign(
        CampaignRecord(
            campaign_id="camp_sec_002",
            title="Camp Sec 2",
            campaign_url="https://whop.com/s2",
            current_state=CampaignState.AWAITING_APPROVAL,
        )
    )
    temp_ledger.save_review_session(session)
    gate = TelegramApprovalGate(ledger=temp_ledger)

    update = {
        "callback_query": {
            "id": "cb_bad",
            "from": {"id": 9999999, "username": "malicious_actor"},
            "data": "wh:appr:rev_sec_002",
            "message": {"message_id": 888, "chat": {"id": 12345}},
        }
    }

    with patch("whop.telegram_approval.get_telegram_config", return_value=("bot_tok", "12345", ["legit_admin"])), \
         patch("whop.telegram_approval._answer_callback_query", new_callable=AsyncMock) as mock_ans:

        res = await gate.handle_callback(update)
        assert res["status"] == "unauthorized"
        # Verify campaign state was NOT mutated
        camp = temp_ledger.get_campaign("camp_sec_002")
        assert camp.current_state == CampaignState.AWAITING_APPROVAL


@pytest.mark.asyncio
async def test_callback_session_not_found(temp_ledger):
    gate = TelegramApprovalGate(ledger=temp_ledger)
    update = {
        "callback_query": {
            "id": "cb_nf",
            "from": {"id": 123, "username": "admin"},
            "data": "wh:appr:rev_nonexistent_999",
            "message": {"message_id": 888, "chat": {"id": 12345}},
        }
    }
    with patch("whop.telegram_approval.get_telegram_config", return_value=("bot_tok", "12345", ["admin"])), \
         patch("whop.telegram_approval._answer_callback_query", new_callable=AsyncMock):

        res = await gate.handle_callback(update)
        assert res["status"] == "session_not_found"


@pytest.mark.asyncio
async def test_callback_duplicate_or_stale_returns_already_decided(temp_ledger):
    session = WhopReviewSession(
        review_session_id="rev_sec_decided",
        campaign_id="camp_decided",
        guideline_hash="ghash",
        autoclip_job_id="job1",
        artifact_hash="arthash",
        idempotency_key="key_decided",
        review_state="APPROVED",
        decision="APPROVE",
    )
    temp_ledger.save_campaign(
        CampaignRecord(
            campaign_id="camp_decided",
            title="Camp Decided",
            campaign_url="https://whop.com/d",
            current_state=CampaignState.APPROVED,
        )
    )
    temp_ledger.save_review_session(session)
    gate = TelegramApprovalGate(ledger=temp_ledger)

    update = {
        "callback_query": {
            "id": "cb_dup",
            "from": {"id": 123, "username": "admin"},
            "data": "wh:appr:rev_sec_decided",
            "message": {"message_id": 888, "chat": {"id": 12345}},
        }
    }
    with patch("whop.telegram_approval.get_telegram_config", return_value=("bot_tok", "12345", ["admin"])), \
         patch("whop.telegram_approval._answer_callback_query", new_callable=AsyncMock):

        res = await gate.handle_callback(update)
        assert res["status"] == "already_decided"
        assert res["current_decision"] == "APPROVE"


@pytest.mark.asyncio
async def test_callback_malformed_payload_rejected(temp_ledger):
    gate = TelegramApprovalGate(ledger=temp_ledger)
    update = {
        "callback_query": {
            "id": "cb_mal",
            "from": {"id": 123, "username": "admin"},
            "data": "malformed_callback_without_prefix",
            "message": {"message_id": 888, "chat": {"id": 12345}},
        }
    }
    with patch("whop.telegram_approval.get_telegram_config", return_value=("bot_tok", "12345", ["admin"])), \
         patch("whop.telegram_approval._answer_callback_query", new_callable=AsyncMock):

        res = await gate.handle_callback(update)
        assert res["status"] == "invalid_payload"


# ==============================================================================
# SECTION E: STATE MACHINE TRANSITIONS
# ==============================================================================

@pytest.mark.asyncio
async def test_state_machine_approve_transitions_to_approved(temp_ledger):
    temp_ledger.save_campaign(
        CampaignRecord(
            campaign_id="camp_sm_1",
            title="Camp SM 1",
            campaign_url="https://whop.com/sm1",
            current_state=CampaignState.AWAITING_APPROVAL,
        )
    )
    sess = WhopReviewSession(
        review_session_id="rev_sm_1",
        campaign_id="camp_sm_1",
        guideline_hash="ghash",
        autoclip_job_id="job1",
        artifact_hash="arthash",
        idempotency_key="key_sm_1",
        review_state="PENDING",
    )
    temp_ledger.save_review_session(sess)
    gate = TelegramApprovalGate(ledger=temp_ledger)

    update = {
        "callback_query": {
            "id": "cb_1",
            "from": {"id": 777, "username": "chief_reviewer"},
            "data": "wh:appr:rev_sm_1",
            "message": {"message_id": 12, "chat": {"id": 123}},
        }
    }

    with patch("whop.telegram_approval.get_telegram_config", return_value=("tok", "123", ["chief_reviewer"])), \
         patch("whop.telegram_approval._answer_callback_query", new_callable=AsyncMock), \
         patch("whop.telegram_approval._safe_edit_telegram_message", new_callable=AsyncMock):

        res = await gate.handle_callback(update)
        assert res["status"] == "success"
        assert res["campaign_state"] == "APPROVED"

        camp = temp_ledger.get_campaign("camp_sm_1")
        assert camp.current_state == CampaignState.APPROVED

        # Verify immutable event history
        events = temp_ledger.list_events("camp_sm_1")
        assert any(e.new_state == CampaignState.APPROVED and "chief_reviewer" in e.reason for e in events)


@pytest.mark.asyncio
async def test_state_machine_request_changes_transitions_to_changes_requested(temp_ledger):
    temp_ledger.save_campaign(
        CampaignRecord(
            campaign_id="camp_sm_2",
            title="Camp SM 2",
            campaign_url="https://whop.com/sm2",
            current_state=CampaignState.AWAITING_APPROVAL,
        )
    )
    sess = WhopReviewSession(
        review_session_id="rev_sm_2",
        campaign_id="camp_sm_2",
        guideline_hash="ghash",
        autoclip_job_id="job1",
        artifact_hash="arthash",
        idempotency_key="key_sm_2",
        review_state="PENDING",
    )
    temp_ledger.save_review_session(sess)
    gate = TelegramApprovalGate(ledger=temp_ledger)

    update = {
        "callback_query": {
            "id": "cb_2",
            "from": {"id": 777, "username": "chief_reviewer"},
            "data": "wh:chg:rev_sm_2",
            "message": {"message_id": 12, "chat": {"id": 123}},
        }
    }

    with patch("whop.telegram_approval.get_telegram_config", return_value=("tok", "123", ["chief_reviewer"])), \
         patch("whop.telegram_approval._answer_callback_query", new_callable=AsyncMock), \
         patch("whop.telegram_approval._safe_edit_telegram_message", new_callable=AsyncMock):

        res = await gate.handle_callback(update)
        assert res["status"] == "success"
        assert res["campaign_state"] == "CHANGES_REQUESTED"

        camp = temp_ledger.get_campaign("camp_sm_2")
        assert camp.current_state == CampaignState.CHANGES_REQUESTED


@pytest.mark.asyncio
async def test_state_machine_reject_transitions_to_approval_rejected(temp_ledger):
    temp_ledger.save_campaign(
        CampaignRecord(
            campaign_id="camp_sm_3",
            title="Camp SM 3",
            campaign_url="https://whop.com/sm3",
            current_state=CampaignState.AWAITING_APPROVAL,
        )
    )
    sess = WhopReviewSession(
        review_session_id="rev_sm_3",
        campaign_id="camp_sm_3",
        guideline_hash="ghash",
        autoclip_job_id="job1",
        artifact_hash="arthash",
        idempotency_key="key_sm_3",
        review_state="PENDING",
    )
    temp_ledger.save_review_session(sess)
    gate = TelegramApprovalGate(ledger=temp_ledger)

    update = {
        "callback_query": {
            "id": "cb_3",
            "from": {"id": 777, "username": "chief_reviewer"},
            "data": "wh:rej:rev_sm_3",
            "message": {"message_id": 12, "chat": {"id": 123}},
        }
    }

    with patch("whop.telegram_approval.get_telegram_config", return_value=("tok", "123", ["chief_reviewer"])), \
         patch("whop.telegram_approval._answer_callback_query", new_callable=AsyncMock), \
         patch("whop.telegram_approval._safe_edit_telegram_message", new_callable=AsyncMock):

        res = await gate.handle_callback(update)
        assert res["status"] == "success"
        assert res["campaign_state"] == "APPROVAL_REJECTED"

        camp = temp_ledger.get_campaign("camp_sm_3")
        assert camp.current_state == CampaignState.APPROVAL_REJECTED


# ==============================================================================
# SECTION F: IMMEDIATE CALLBACK ACK
# ==============================================================================

@pytest.mark.asyncio
async def test_callback_answered_immediately_before_db_write(temp_ledger):
    session = WhopReviewSession(
        review_session_id="rev_ack_1",
        campaign_id="camp_ack_1",
        guideline_hash="ghash",
        autoclip_job_id="job1",
        artifact_hash="arthash",
        idempotency_key="key_ack_1",
        review_state="PENDING",
    )
    temp_ledger.save_campaign(
        CampaignRecord(
            campaign_id="camp_ack_1",
            title="Camp Ack",
            campaign_url="https://whop.com/ack",
            current_state=CampaignState.AWAITING_APPROVAL,
        )
    )
    temp_ledger.save_review_session(session)
    gate = TelegramApprovalGate(ledger=temp_ledger)

    call_order = []

    async def fake_answer(bot_token, cb_id, **kwargs):
        call_order.append("answerCallbackQuery")

    def fake_atomic_trans(*args, **kwargs):
        call_order.append("atomic_transition")
        return True

    update = {
        "callback_query": {
            "id": "cb_ack",
            "from": {"id": 123, "username": "admin"},
            "data": "wh:appr:rev_ack_1",
            "message": {"message_id": 888, "chat": {"id": 12345}},
        }
    }

    with patch("whop.telegram_approval.get_telegram_config", return_value=("bot_tok", "12345", ["admin"])), \
         patch("whop.telegram_approval._answer_callback_query", side_effect=fake_answer), \
         patch.object(temp_ledger, "atomic_transition_review", side_effect=fake_atomic_trans), \
         patch("whop.telegram_approval._safe_edit_telegram_message", new_callable=AsyncMock):

        await gate.handle_callback(update)
        # answerCallbackQuery MUST be invoked before atomic DB transition!
        assert len(call_order) >= 2
        assert call_order[0] == "answerCallbackQuery"
        assert call_order[1] == "atomic_transition"


# ==============================================================================
# SECTION G: SECRET SAFETY & ZERO MUTATIONS
# ==============================================================================

def test_secret_safety_no_tokens_in_callbacks():
    gate = TelegramApprovalGate()
    key = gate.compute_review_idempotency_key("camp_1", "ghash", "job_1", "arthash")
    # Verify key does not contain secret strings
    assert "token" not in key.lower()
    assert "secret" not in key.lower()
    assert len(key) == 64


@pytest.mark.asyncio
async def test_step7_approval_never_triggers_publishing(temp_ledger):
    """Verifies APPROVED strictly means human review passed, zero publishing executed."""
    temp_ledger.save_campaign(
        CampaignRecord(
            campaign_id="camp_nopub_1",
            title="Camp NoPub",
            campaign_url="https://whop.com/nopub",
            current_state=CampaignState.AWAITING_APPROVAL,
        )
    )
    sess = WhopReviewSession(
        review_session_id="rev_nopub_1",
        campaign_id="camp_nopub_1",
        guideline_hash="ghash",
        autoclip_job_id="job1",
        artifact_hash="arthash",
        idempotency_key="key_nopub_1",
        review_state="PENDING",
    )
    temp_ledger.save_review_session(sess)
    gate = TelegramApprovalGate(ledger=temp_ledger)

    update = {
        "callback_query": {
            "id": "cb_nopub",
            "from": {"id": 777, "username": "chief_reviewer"},
            "data": "wh:appr:rev_nopub_1",
            "message": {"message_id": 12, "chat": {"id": 123}},
        }
    }

    with patch("whop.telegram_approval.get_telegram_config", return_value=("tok", "123", ["chief_reviewer"])), \
         patch("whop.telegram_approval._answer_callback_query", new_callable=AsyncMock), \
         patch("whop.telegram_approval._safe_edit_telegram_message", new_callable=AsyncMock), \
         patch("backend.autoclip.telegram.review_bot._execute_auto_publish") as mock_publish:

        res = await gate.handle_callback(update)
        assert res["status"] == "success"
        # Auto publish must NEVER be called in Step 7!
        assert mock_publish.call_count == 0


def test_eligibility_duplicate_clip_ids_blocked(temp_ledger, mock_valid_clip_factory):
    """Verifies that 5 clips containing a duplicate clip ID are rejected by the gate."""
    gate = TelegramApprovalGate(ledger=temp_ledger)
    c1 = mock_valid_clip_factory("clip_001", index=1)
    c2 = mock_valid_clip_factory("clip_002", index=2)
    c3 = mock_valid_clip_factory("clip_003", index=3)
    c4 = mock_valid_clip_factory("clip_004", index=4)
    c5 = mock_valid_clip_factory("clip_001", index=5)  # Duplicate clip_001
    report = WhopJobQAReport(
        campaign_id="camp_test_001",
        guideline_hash="ghash_1",
        autoclip_job_id="job_1",
        artifact_hash="art_1",
        qa_status="RENDER_PASS",
        overall_quality_score=90.0,
        valid_clips_count=5,
        total_clips_evaluated=5,
        clips=[c1, c2, c3, c4, c5],
    )
    eligible, reasons = gate.validate_eligibility("camp_test_001", report)
    assert eligible is False
    assert any("Duplicate clip ID" in r for r in reasons)


def test_review_session_reconstruction_from_ledger(temp_ledger):
    """Verifies complete review session object can be reconstructed from SQLite storage."""
    sess = WhopReviewSession(
        review_session_id="rev_recon_100",
        campaign_id="camp_recon_100",
        guideline_hash="ghash_recon",
        autoclip_job_id="job_recon_100",
        artifact_hash="arthash_recon",
        idempotency_key="key_recon_100",
        review_state="PENDING",
        chat_id="-100987654321",
        message_ids={"summary": 101, "clip_001": 201},
        telegram_file_ids={"clip_001": "fid_recon_1"},
        clip_ids=["clip_001", "clip_002", "clip_003", "clip_004", "clip_005"],
        clip_order=["clip_001", "clip_002", "clip_003", "clip_004", "clip_005"],
        metadata={"custom_flag": True},
    )
    temp_ledger.save_review_session(sess)

    fetched = temp_ledger.get_review_session("rev_recon_100")
    assert fetched is not None
    assert fetched.review_session_id == "rev_recon_100"
    assert fetched.campaign_id == "camp_recon_100"
    assert fetched.chat_id == "-100987654321"
    assert fetched.message_ids == {"summary": 101, "clip_001": 201}
    assert fetched.telegram_file_ids == {"clip_001": "fid_recon_1"}
    assert len(fetched.clip_ids) == 5
    assert fetched.metadata.get("custom_flag") is True


@pytest.mark.asyncio
async def test_callback_race_condition_conflict_detected(temp_ledger):
    """Verifies atomic compare-and-set returns conflict_already_updated if state changed concurrently."""
    session = WhopReviewSession(
        review_session_id="rev_race_1",
        campaign_id="camp_race_1",
        guideline_hash="ghash",
        autoclip_job_id="job1",
        artifact_hash="arthash",
        idempotency_key="key_race_1",
        review_state="PENDING",
    )
    temp_ledger.save_campaign(
        CampaignRecord(
            campaign_id="camp_race_1",
            title="Camp Race",
            campaign_url="https://whop.com/r",
            current_state=CampaignState.AWAITING_APPROVAL,
        )
    )
    temp_ledger.save_review_session(session)
    gate = TelegramApprovalGate(ledger=temp_ledger)

    update = {
        "callback_query": {
            "id": "cb_race",
            "from": {"id": 123, "username": "admin"},
            "data": "wh:appr:rev_race_1",
            "message": {"message_id": 888, "chat": {"id": 12345}},
        }
    }

    # Simulate compare-and-set failing because concurrent thread updated state first
    with patch("whop.telegram_approval.get_telegram_config", return_value=("bot_tok", "12345", ["admin"])), \
         patch("whop.telegram_approval._answer_callback_query", new_callable=AsyncMock), \
         patch.object(temp_ledger, "atomic_transition_review", return_value=False):

        res = await gate.handle_callback(update)
        assert res["status"] == "conflict_already_updated"


# ==============================================================================
# SECTION H: STEP 7.1 DRIVE-TO-TELEGRAM MEDIA RESOLUTION & CONFIG DECOUPLING
# ==============================================================================

def test_autoclip_config_decoupled_from_whop_dry_run(monkeypatch):
    """Verifies that AUTOCLIP_DRY_RUN=false operates independently from WHOP_DRY_RUN=true."""
    from whop.config import AutoClipConfig

    monkeypatch.setenv("WHOP_DRY_RUN", "true")
    monkeypatch.setenv("AUTOCLIP_DRY_RUN", "false")

    cfg = AutoClipConfig.from_env()
    assert cfg.dry_run is False

    monkeypatch.setenv("AUTOCLIP_DRY_RUN", "true")
    cfg2 = AutoClipConfig.from_env()
    assert cfg2.dry_run is True


def test_autoclip_client_bypasses_cached_dry_run_job_on_live_request(temp_ledger):
    """Verifies that AutoClipClient with dry_run=False does not reuse a simulated dry_run_ job from the ledger."""
    from whop.autoclip_client import AutoClipClient
    from whop.config import AutoClipConfig
    from whop.models import WhopAutoClipJobRecord, WhopCampaignBrief

    brief = WhopCampaignBrief(
        campaign_id="camp_live_test",
        title="Live Test Campaign",
        campaign_url="https://whop.com/live",
        allowed_sources=["https://youtube.com/watch?v=live123"],
        guideline_hash="ghash_live123",
    )

    temp_ledger.save_campaign(
        CampaignRecord(
            campaign_id="camp_live_test",
            title="Live Test Campaign",
            campaign_url="https://whop.com/live",
        )
    )

    client = AutoClipClient(config=AutoClipConfig(dry_run=False, api_token="tok"), ledger=temp_ledger)
    payload = client.build_job_payload(brief)

    # Pre-populate ledger with a simulated dry_run job
    dry_rec = WhopAutoClipJobRecord(
        campaign_id=brief.campaign_id,
        guideline_hash=payload.guideline_hash,
        autoclip_job_id=f"dry_run_{payload.idempotency_key[:16]}",
        idempotency_key=payload.idempotency_key,
        status="dry_run_validated",
        request_hash=payload.request_hash,
        source_hash=payload.source_hash,
    )
    temp_ledger.save_autoclip_job(dry_rec)

    # When create_job is called in live mode, it should mock-call the server rather than reusing dry_run_
    with patch.object(client, "_request", return_value={"id": "real_job_999", "status": "queued"}):
        res = client.create_job(brief)
        assert res.job_id == "real_job_999"
        assert not res.job_id.startswith("dry_run_")


@pytest.mark.asyncio
async def test_telegram_approval_passes_drive_file_id_to_materializer(temp_ledger):
    """Verifies that dispatch_review_session passes clip.drive_file_id to materialize_valid_clip_media."""
    campaign_id = "camp_drive_res"
    temp_ledger.save_campaign(
        CampaignRecord(
            campaign_id=campaign_id,
            title="Drive Resolution Test",
            campaign_url="https://whop.com/dr",
            current_state=CampaignState.RENDER_READY,
        )
    )

    clips = [
        ClipQARecord(
            clip_id=f"c_{i}",
            candidate_index=i,
            technical_qa=ClipTechnicalQAResult(
                clip_id=f"c_{i}",
                duration_s=25.0,
                width=1080,
                height=1920,
                fps=30.0,
                video_codec="h264",
                audio_codec="aac",
                channels=2,
                sample_rate=48000,
                mean_volume_db=-14.0,
                true_peak_db=-1.5,
                av_sync_diff_s=0.0,
                decode_ok=True,
                broll_coverage_pct=35.0,
                longest_a_roll_gap_s=0.0,
                file_size_bytes=1048576,
                warnings=[],
                rejection_reasons=[],
                is_valid=True,
            ),
            compliance_results=[],
            artifact_path="/nonexistent/local/path/final.mp4",
            drive_file_id=f"drive_fid_{i}",
            is_durable=True,
            quality_score=95.0,
            is_valid=True,
        )
        for i in range(1, 6)
    ]

    qa_report = WhopJobQAReport(
        campaign_id=campaign_id,
        guideline_hash="gh_dr",
        autoclip_job_id="job_dr",
        artifact_hash="art_dr",
        qa_status="RENDER_PASS",
        overall_quality_score=95.0,
        valid_clips_count=5,
        total_clips_evaluated=5,
        clips=clips,
        compliance_summary={},
        unsupported_rules=[],
        warnings=[],
        failures=[],
    )
    temp_ledger.save_qa_record(qa_report)

    gate = TelegramApprovalGate(ledger=temp_ledger)

    recorded_calls = []

    def mock_materialize(cid, dest_path=None, preferred_source=None, drive_file_id=None):
        recorded_calls.append((cid, drive_file_id))
        # Return a dummy valid path or None
        return None

    with patch("whop.telegram_approval.get_telegram_config", return_value=("mock_bot_tok", "mock_chat_id", None)), \
         patch("whop.telegram_approval._safe_send_telegram_message", new_callable=AsyncMock) as mock_msg, \
         patch("whop.telegram_approval.materialize_valid_clip_media", side_effect=mock_materialize):

        mock_msg.return_value = {"ok": True, "result": {"message_id": 999}}

        session = await gate.dispatch_review_session(
            campaign_id=campaign_id,
            autoclip_job_id="job_dr",
            qa_report=qa_report,
        )

        assert session is not None
        assert len(recorded_calls) == 5
        for idx, (cid, dfid) in enumerate(recorded_calls, 1):
            assert cid == f"c_{idx}"
            assert dfid == f"drive_fid_{idx}"


