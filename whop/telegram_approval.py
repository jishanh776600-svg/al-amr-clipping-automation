"""Whop Telegram Human Approval Gate (Step 7).

Manages the human review and approval process in Telegram for verified
Whop campaign renders (5 valid clips). Enforces strict eligibility,
persists durable review sessions, delivers authentic MP4 videos via sendVideo,
and executes atomic state transitions without triggering publishing or Whop mutations.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from backend.autoclip.telegram.review_bot import (
    TELEGRAM_API_BASE,
    _answer_callback_query,
    _safe_edit_telegram_message,
    _safe_send_telegram_message,
    get_telegram_config,
    is_user_authorized,
)
from backend.autoclip.media_guard import is_valid_mp4, materialize_valid_clip_media
from .ledger import CampaignLedger
from .models import (
    CampaignRecord,
    CampaignState,
    RuleComplianceStatus,
    WhopJobQAReport,
    WhopReviewSession,
    WhopSubmissionRecord,
)
from .quality_verifier import (
    CANONICAL_MAX_DURATION_S,
    CANONICAL_MIN_DURATION_S,
    REQUIRED_VALID_CLIPS_COUNT,
)
from .submitter import WhopSubmitter, SubmissionBlockedError, WhopSubmissionResult

log = logging.getLogger(__name__)


class TelegramApprovalGate:
    """Production Telegram Human Approval Gate for Whop Campaigns."""

    def __init__(
        self,
        ledger: Optional[CampaignLedger] = None,
        submitter: Optional[WhopSubmitter] = None,
    ):
        self.ledger = ledger or CampaignLedger()
        self.submitter = submitter or WhopSubmitter(ledger=self.ledger)

    # ==========================================================================
    # 1. Eligibility Hard Gate
    # ==========================================================================

    def validate_eligibility(
        self,
        campaign_id: str,
        qa_report: WhopJobQAReport,
    ) -> Tuple[bool, List[str]]:
        """Strictly validates whether a campaign QA report qualifies for Telegram review.
        
        Requires:
        - Exactly 5 valid distinct clips.
        - Every accepted clip is 20.0s – 30.0s.
        - Technical video and audio QA passed.
        - Artifact is durable (accessible local path or Drive file ID).
        - Mandatory compliance rules satisfied with no unresolved mandatory rules.
        - QA status must be RENDER_PASS or acceptable RENDER_WARN.
        """
        rejection_reasons: List[str] = []

        # 1. Check QA status
        if qa_report.qa_status not in ("RENDER_PASS", "RENDER_WARN"):
            rejection_reasons.append(
                f"QA status '{qa_report.qa_status}' is ineligible for review. "
                "Must be RENDER_PASS or acceptable RENDER_WARN."
            )

        # 2. Check exactly 5 valid clips
        if qa_report.valid_clips_count != REQUIRED_VALID_CLIPS_COUNT:
            rejection_reasons.append(
                f"Requires exactly {REQUIRED_VALID_CLIPS_COUNT} valid clips, "
                f"but QA report contains {qa_report.valid_clips_count}."
            )

        valid_clips = [c for c in qa_report.clips if c.is_valid]
        if len(valid_clips) != REQUIRED_VALID_CLIPS_COUNT:
            rejection_reasons.append(
                f"Found {len(valid_clips)} valid clip objects, expected {REQUIRED_VALID_CLIPS_COUNT}."
            )

        # 3. Inspect each valid clip
        seen_clip_ids = set()
        for idx, clip in enumerate(valid_clips):
            cid = clip.clip_id
            if cid in seen_clip_ids:
                rejection_reasons.append(f"Duplicate clip ID detected in valid set: '{cid}'.")
            seen_clip_ids.add(cid)

            # Duration check (20.0s - 30.0s)
            dur = clip.technical_qa.duration_s
            if dur < CANONICAL_MIN_DURATION_S or dur > CANONICAL_MAX_DURATION_S:
                rejection_reasons.append(
                    f"Clip '{cid}' duration {dur:.2f}s outside canonical range "
                    f"[{CANONICAL_MIN_DURATION_S:.1f}s, {CANONICAL_MAX_DURATION_S:.1f}s]."
                )

            # Technical media QA check
            if not clip.technical_qa.is_valid:
                rejection_reasons.append(f"Clip '{cid}' failed technical media QA.")

            # Durability check (Drive ID or local path >= 1024 bytes)
            if not clip.is_durable:
                rejection_reasons.append(
                    f"Clip '{cid}' artifact is not durable. Requires Google Drive or persistent storage."
                )

            # Compliance check: no failed or unresolved mandatory rules
            for cr in clip.compliance_results:
                if cr.mandatory and cr.status == RuleComplianceStatus.SUPPORTED_AND_FAILED:
                    rejection_reasons.append(
                        f"Clip '{cid}' failed mandatory rule: '{cr.rule_text}'."
                    )
                elif cr.mandatory and cr.status == RuleComplianceStatus.UNSUPPORTED_REQUIRES_REVIEW:
                    rejection_reasons.append(
                        f"Clip '{cid}' has unresolved mandatory rule: '{cr.rule_text}'."
                    )

        # 4. Check report-level unresolved mandatory rules
        for ur in qa_report.unsupported_rules:
            if ur.mandatory:
                rejection_reasons.append(
                    f"Campaign has unresolved mandatory rule requiring review: '{ur.rule_text}'."
                )

        return (len(rejection_reasons) == 0, rejection_reasons)

    # ==========================================================================
    # 2. Idempotency Key Computation
    # ==========================================================================

    def compute_review_idempotency_key(
        self,
        campaign_id: str,
        guideline_hash: str,
        autoclip_job_id: str,
        artifact_hash: str,
    ) -> str:
        """Computes deterministic idempotency key for review session."""
        raw = f"{campaign_id}:{guideline_hash}:{autoclip_job_id}:{artifact_hash}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    # ==========================================================================
    # 3. Review Session Dispatch & Telegram Delivery
    # ==========================================================================

    async def dispatch_review_session(
        self,
        campaign_id: str,
        autoclip_job_id: str,
        qa_report: Optional[WhopJobQAReport] = None,
        force: bool = False,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
    ) -> WhopReviewSession:
        """Dispatches an interactive 5-clip review session to Telegram.
        
        Enforces:
        - Eligibility validation.
        - Deterministic idempotency & partial-send recovery.
        - Actual MP4 delivery via sendVideo.
        - Campaign state progression to AWAITING_APPROVAL.
        """
        # 1. Resolve QA report if not passed
        if qa_report is None:
            qa_report = self.ledger.get_latest_qa_record(campaign_id)
        if not qa_report:
            raise ValueError(f"Cannot dispatch review: No QA report found for campaign '{campaign_id}'.")

        # 2. Validate eligibility
        is_eligible, reasons = self.validate_eligibility(campaign_id, qa_report)
        if not is_eligible:
            raise ValueError(
                f"Campaign '{campaign_id}' is ineligible for Telegram review: {'; '.join(reasons)}"
            )

        # 3. Check Telegram configuration
        cfg_token, cfg_chat, _ = get_telegram_config()
        token = (bot_token or cfg_token or "").strip()
        chat = (chat_id or cfg_chat or "").strip()
        if not token or not chat:
            raise ValueError(
                "Telegram review cannot dispatch: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is not configured."
            )

        # 4. Check for existing review session (Idempotency)
        idem_key = self.compute_review_idempotency_key(
            campaign_id=campaign_id,
            guideline_hash=qa_report.guideline_hash,
            autoclip_job_id=autoclip_job_id,
            artifact_hash=qa_report.artifact_hash,
        )

        session = self.ledger.get_review_session_by_idempotency_key(idem_key)
        if session and not force:
            valid_clips = [c for c in qa_report.clips if c.is_valid]
            all_sent = all(c.clip_id in session.message_ids for c in valid_clips)
            if all_sent and session.message_ids.get("summary") and session.message_ids.get("decision_card"):
                log.info(
                    "Review session %s already fully delivered to Telegram chat %s. Skipping duplicate.",
                    session.review_session_id,
                    chat,
                )
                return session

        # 5. Initialize or reuse session
        valid_clips = [c for c in qa_report.clips if c.is_valid]
        clip_ids = [c.clip_id for c in valid_clips]

        if not session:
            session_id = f"rev_{idem_key[:16]}"
            session = WhopReviewSession(
                review_session_id=session_id,
                campaign_id=campaign_id,
                guideline_hash=qa_report.guideline_hash,
                autoclip_job_id=autoclip_job_id,
                artifact_hash=qa_report.artifact_hash,
                idempotency_key=idem_key,
                review_state="PENDING",
                chat_id=chat,
                clip_ids=clip_ids,
                clip_order=clip_ids,
                metadata={"qa_status": qa_report.qa_status, "quality_score": qa_report.overall_quality_score},
            )
            session = self.ledger.save_review_session(session)

        # 6. Send Campaign Summary Header Card
        camp = self.ledger.get_campaign(campaign_id)
        camp_title = camp.title if camp else f"Whop Campaign {campaign_id[:8]}"
        payout_info = camp.payout_raw if (camp and camp.payout_raw) else "Standard CPM"

        summary_text = (
            f"🎬 <b>WHOP CAMPAIGN REVIEW REQUIRED</b>\n\n"
            f"📌 <b>Campaign:</b> {html.escape(camp_title)}\n"
            f"🆔 <b>ID:</b> <code>{html.escape(campaign_id)}</code>\n"
            f"💰 <b>Payout / Rate:</b> {html.escape(payout_info)}\n"
            f"✨ <b>Quality Status:</b> {qa_report.qa_status} (Score: {qa_report.overall_quality_score:.1f}/100)\n"
            f"📦 <b>Valid Clips:</b> {len(valid_clips)} / {REQUIRED_VALID_CLIPS_COUNT}\n\n"
            f"<b>Clips Breakdown:</b>\n"
        )
        for idx, c in enumerate(valid_clips, 1):
            dur = c.technical_qa.duration_s
            summary_text += f"• #{idx} (<code>{html.escape(c.clip_id[:12])}</code>) — {dur:.1f}s | Score: {c.quality_score:.1f}\n"

        if qa_report.warnings:
            summary_text += f"\n⚠️ <b>Warnings:</b>\n"
            for w in qa_report.warnings[:3]:
                summary_text += f"• {html.escape(w[:80])}\n"

        summary_text += "\n<i>Reviewing 5 rendered video clips below...</i>"

        if not session.message_ids.get("summary"):
            try:
                sum_resp = await _safe_send_telegram_message(
                    bot_token=token,
                    chat_id=chat,
                    text=summary_text,
                )
                if sum_resp and sum_resp.get("result", {}).get("message_id"):
                    session.message_ids["summary"] = sum_resp["result"]["message_id"]
                    self.ledger.update_review_session(session)
            except Exception as e:
                log.warning("Could not send Telegram summary message: %s", e)

        # 7. Deliver the 5 authentic MP4 videos via sendVideo (with partial-send recovery)
        send_video_url = f"{TELEGRAM_API_BASE}/bot{token}/sendVideo"

        for idx, clip in enumerate(valid_clips, 1):
            cid = clip.clip_id
            if cid in session.message_ids:
                log.info("Clip %s already sent in message %s, skipping.", cid, session.message_ids[cid])
                continue

            # Materialize verified MP4
            media_path = Path(clip.artifact_path) if clip.artifact_path else None
            effective_path: Optional[Path] = None

            if media_path and is_valid_mp4(media_path):
                effective_path = media_path
            else:
                effective_path = await asyncio.to_thread(
                    materialize_valid_clip_media,
                    cid,
                    None,
                    None,
                    clip.drive_file_id,
                )

            caption = (
                f"🎥 <b>Clip #{idx} of {REQUIRED_VALID_CLIPS_COUNT}</b>\n"
                f"📌 <b>ID:</b> <code>{html.escape(cid)}</code>\n"
                f"⏱ <b>Duration:</b> {clip.technical_qa.duration_s:.1f}s\n"
                f"✨ <b>Quality:</b> {clip.quality_score:.1f}/100"
            )
            if clip.drive_file_id:
                caption += f'\n🔗 <a href="https://drive.google.com/file/d/{clip.drive_file_id}/view">Drive Link</a>'

            if effective_path and effective_path.exists():
                try:
                    async with httpx.AsyncClient(timeout=120.0) as client:
                        with open(effective_path, "rb") as vf:
                            files = {"video": (f"{cid}.mp4", vf, "video/mp4")}
                            data = {
                                "chat_id": chat,
                                "caption": caption[:1024],
                                "parse_mode": "HTML",
                                "supports_streaming": "true",
                            }
                            resp = await client.post(send_video_url, data=data, files=files)
                            if resp.status_code == 200:
                                res_json = resp.json()
                                mid = res_json.get("result", {}).get("message_id")
                                fid = (res_json.get("result", {}).get("video") or {}).get("file_id")
                                if mid:
                                    session.message_ids[cid] = mid
                                if fid:
                                    session.telegram_file_ids[cid] = fid
                                self.ledger.update_review_session(session)
                                log.info("Successfully delivered video for clip %s to Telegram (msg %s)", cid, mid)
                            else:
                                log.warning("sendVideo for clip %s returned HTTP %s: %s", cid, resp.status_code, resp.text)
                except Exception as exc:
                    log.warning("Exception sending video for clip %s: %s", cid, exc)

        # 8. Send Campaign-Level Action / Decision Keyboard Card
        decision_text = (
            f"⚖️ <b>OPERATOR DECISION REQUIRED</b>\n\n"
            f"Review the 5 clips above for campaign <b>{html.escape(camp_title)}</b>.\n"
            f"Select an action below to proceed:"
        )
        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "✅ APPROVE CAMPAIGN", "callback_data": f"wh:appr:{session.review_session_id}"},
                ],
                [
                    {"text": "🔄 REQUEST CHANGES", "callback_data": f"wh:chg:{session.review_session_id}"},
                    {"text": "❌ REJECT CAMPAIGN", "callback_data": f"wh:rej:{session.review_session_id}"},
                ],
            ]
        }

        if not session.message_ids.get("decision_card"):
            try:
                dec_resp = await _safe_send_telegram_message(
                    bot_token=token,
                    chat_id=chat,
                    text=decision_text,
                    reply_markup=reply_markup,
                )
                if dec_resp and dec_resp.get("result", {}).get("message_id"):
                    session.message_ids["decision_card"] = dec_resp["result"]["message_id"]
                    self.ledger.update_review_session(session)
            except Exception as e:
                log.warning("Could not send Telegram decision card: %s", e)

        # 9. Transition Campaign State to AWAITING_APPROVAL in Ledger
        if camp:
            curr_state = camp.current_state
            if curr_state in (CampaignState.RENDER_READY, CampaignState.RENDER_WARN):
                self.ledger.transition_state(
                    campaign_id=campaign_id,
                    target_state=CampaignState.AWAITING_APPROVAL,
                    reason=f"Dispatched 5-clip review to Telegram chat {chat}",
                    source="TelegramApprovalGate",
                    metadata={
                        "review_session_id": session.review_session_id,
                        "autoclip_job_id": autoclip_job_id,
                        "idempotency_key": idem_key,
                        "artifact_hash": qa_report.artifact_hash,
                    },
                )

        return session

    # ==========================================================================
    # 4. Callback Security & Atomic State Transitions
    # ==========================================================================

    async def handle_callback(
        self,
        update: Dict[str, Any],
        bot_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Handles operator inline callback query securely, atomically, and bridges to submission."""
        cb = update.get("callback_query") or {}
        cb_id = str(cb.get("id") or "")
        from_user = cb.get("from") or {}
        user_id = from_user.get("id")
        username = from_user.get("username") or str(user_id)
        cb_data = str(cb.get("data") or "")
        message = cb.get("message") or {}
        message_id = message.get("message_id")
        chat = (message.get("chat") or {}).get("id") or ""

        cfg_token, cfg_chat, allowed_ids = get_telegram_config()
        token = (bot_token or cfg_token or "").strip()

        # 1. Immediate Callback Acknowledgment (stops Telegram spinner instantly)
        await _answer_callback_query(token, cb_id, text="Review action received...", show_alert=False)

        # 2. Operator Authorization Check (Precondition 1)
        if not is_user_authorized(user_id, allowed_ids, username):
            log.warning("Unauthorized review action attempt from user %s (id=%s)", username, user_id)
            await _answer_callback_query(
                token,
                cb_id,
                text="⛔ Unauthorized operator. You do not have permission to approve campaigns.",
                show_alert=True,
            )
            return {"status": "unauthorized", "user_id": user_id}

        # 3. Parse and Validate Callback Payload
        # Format: wh:<action>:<review_session_id>
        parts = cb_data.split(":")
        if len(parts) < 3 or parts[0] != "wh":
            return {"status": "invalid_payload", "data": cb_data}

        action = parts[1]
        session_id = parts[2]

        # Precondition 2: Review session exists
        session = self.ledger.get_review_session(session_id)
        if not session:
            await _answer_callback_query(token, cb_id, text=f"Review session '{session_id}' not found.", show_alert=True)
            return {"status": "session_not_found", "session_id": session_id}

        # Precondition 3: Campaign exists
        camp = self.ledger.get_campaign(session.campaign_id)
        if not camp:
            return {"status": "campaign_not_found", "campaign_id": session.campaign_id}

        # Precondition 4 & 8: Check if already decided
        if session.review_state != "PENDING":
            await _answer_callback_query(
                token,
                cb_id,
                text=f"ℹ️ Review session already concluded with decision: {session.decision}.",
                show_alert=False,
            )
            return {
                "status": "already_decided",
                "session_id": session_id,
                "current_decision": session.decision,
            }

        actor = f"telegram:@{username}" if username else f"telegram:{user_id}"

        # Handle Rejection or Changes Requested (Zero Submission Mutation)
        if action in ("chg", "rej"):
            decision_val = "REJECT" if action == "rej" else "REQUEST_CHANGES"
            badge_text = "❌ <b>CAMPAIGN REJECTED</b>" if action == "rej" else "🔄 <b>CHANGES REQUESTED</b>"
            btn_label = "❌ Rejected" if action == "rej" else "🔄 Changes Requested"
            target_camp_state = CampaignState.APPROVAL_REJECTED.value if action == "rej" else CampaignState.CHANGES_REQUESTED.value
            target_rev_state = "REJECTED" if action == "rej" else "CHANGES_REQUESTED"

            success, code = self.ledger.atomic_reject_or_change(
                campaign_id=session.campaign_id,
                review_session_id=session_id,
                decision=decision_val,
                reviewer_id=str(user_id),
                reviewer_username=str(username),
                note=f"Action '{decision_val}' by @{username} via Telegram",
            )
            if not success:
                log.warning("Atomic decision CAS conflict on session %s: %s", session_id, code)
                return {"status": "conflict_already_updated" if "CONFLICT" in code else f"conflict_{code.lower()}", "session_id": session_id}

            if message_id and chat:
                updated_text = (
                    f"{badge_text} by @{html.escape(username)}\n\n"
                    f"<b>Campaign:</b> <code>{html.escape(session.campaign_id)}</code>\n"
                    f"<b>Session:</b> <code>{html.escape(session_id)}</code>\n"
                    f"<b>Decision:</b> <code>{decision_val}</code>\n"
                    f"<b>Recorded at:</b> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
                )
                await _safe_edit_telegram_message(
                    bot_token=token,
                    chat_id=chat,
                    message_id=message_id,
                    text=updated_text,
                    has_caption=False,
                    reply_markup={"inline_keyboard": [[{"text": btn_label, "callback_data": "tg:done"}]]},
                )

            return {
                "status": "success",
                "session_id": session_id,
                "campaign_id": session.campaign_id,
                "decision": decision_val,
                "review_state": target_rev_state,
                "campaign_state": target_camp_state,
            }

        elif action == "appr":
            # Precondition 5: Campaign must be exactly AWAITING_APPROVAL
            if camp.current_state != CampaignState.AWAITING_APPROVAL:
                if camp.current_state in (CampaignState.APPROVED, CampaignState.SUBMITTING, CampaignState.SUBMITTED):
                    existing_sub = self.ledger.get_submission_by_review_session(session_id)
                    return {
                        "status": "already_approved",
                        "session_id": session_id,
                        "campaign_id": session.campaign_id,
                        "submission_id": existing_sub.submission_id if existing_sub else None,
                    }
                return {
                    "status": "invalid_campaign_state",
                    "campaign_state": camp.current_state.value,
                    "expected_state": CampaignState.AWAITING_APPROVAL.value,
                }

            # Retrieve QA record to collect Drive file IDs for deterministic idempotency
            qa_report = self.ledger.get_latest_qa_record(session.campaign_id)
            drive_file_ids = []
            if qa_report:
                drive_file_ids = [c.drive_file_id for c in qa_report.clips if c.is_valid and c.drive_file_id]

            sub_idem_key = WhopSubmitter.compute_submission_idempotency_key(
                campaign_id=session.campaign_id,
                guideline_hash=session.guideline_hash,
                review_session_id=session_id,
                clip_ids=session.clip_ids,
                drive_file_ids=drive_file_ids,
            )
            submission_id = f"sub_{session_id[4:12]}_{sub_idem_key[:8]}"

            # Atomic Compare-and-Set in Ledger:
            # Transitions Review to APPROVED, Campaign to APPROVED, creates durable submission record
            success, code, sub_record = self.ledger.atomic_approve_and_create_submission(
                campaign_id=session.campaign_id,
                review_session_id=session_id,
                reviewer_id=str(user_id),
                reviewer_username=str(username),
                submission_id=submission_id,
                idempotency_key=sub_idem_key,
                clip_ids=session.clip_ids,
                drive_file_ids=drive_file_ids,
                note=f"Approved by @{username} via Telegram",
            )

            if not success or not sub_record:
                if code in ("CONCURRENT_REVIEW_CONFLICT", "CONCURRENT_CAMPAIGN_CONFLICT"):
                    return {"status": "conflict_already_updated", "session_id": session_id}
                log.warning("Atomic approval CAS failed for session %s: %s", session_id, code)
                return {"status": f"approval_failed_{code.lower()}", "session_id": session_id, "code": code}

            # Immediate UI update: Inform operator submission preparation is in progress
            if message_id and chat:
                prep_text = (
                    f"⏳ <b>APPROVAL ACCEPTED</b> by @{html.escape(username)}\n\n"
                    f"Preparing Whop submission for campaign <b>{html.escape(camp.title or session.campaign_id)}</b>...\n"
                    f"🆔 <b>Submission ID:</b> <code>{sub_record.submission_id}</code>\n"
                    f"📦 <b>Clips to Submit:</b> 5 Drive-backed MP4s"
                )
                await _safe_edit_telegram_message(
                    bot_token=token,
                    chat_id=chat,
                    message_id=message_id,
                    text=prep_text,
                    has_caption=False,
                    reply_markup={"inline_keyboard": [[{"text": "⏳ Submitting...", "callback_data": "tg:progress"}]]},
                )

            # Trigger Submission Execution Pipeline (guarded by dry-run)
            sub_res = await asyncio.to_thread(
                self.submitter.process_submission,
                sub_record.submission_id,
            )

            # Final UI Update on Decision Card
            if message_id and chat:
                if sub_res.success:
                    final_badge = "✅ <b>SUBMITTED TO WHOP SUCCESSFULLY</b>"
                    if sub_res.dry_run:
                        final_badge += " (Dry-Run Verified)"
                    btn_text = "✅ Submitted"
                    body_extra = f"🔑 <b>Whop Ref ID:</b> <code>{sub_res.whop_submission_id}</code>\n"
                else:
                    final_badge = "⚠️ <b>APPROVAL RECORDED (SUBMISSION BLOCKED/FAILED)</b>"
                    btn_text = "⚠️ Submission Blocked"
                    body_extra = f"❌ <b>Error:</b> {html.escape(sub_res.error_message or 'Unknown error')}\n"

                final_text = (
                    f"{final_badge}\n\n"
                    f"📌 <b>Campaign:</b> <code>{html.escape(session.campaign_id)}</code>\n"
                    f"🆔 <b>Submission ID:</b> <code>{sub_record.submission_id}</code>\n"
                    f"📦 <b>Valid Clips:</b> 5\n"
                    f"{body_extra}"
                    f"🕒 <b>Recorded at:</b> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
                )
                await _safe_edit_telegram_message(
                    bot_token=token,
                    chat_id=chat,
                    message_id=message_id,
                    text=final_text,
                    has_caption=False,
                    reply_markup={"inline_keyboard": [[{"text": btn_text, "callback_data": "tg:done"}]]},
                )

            return {
                "status": "success",
                "session_id": session_id,
                "campaign_id": session.campaign_id,
                "decision": "APPROVE",
                "review_state": "APPROVED",
                "campaign_state": "APPROVED",
                "submission_id": sub_record.submission_id,
                "submission_result": sub_res.to_dict(),
            }

        else:
            return {"status": "unknown_action", "action": action}

