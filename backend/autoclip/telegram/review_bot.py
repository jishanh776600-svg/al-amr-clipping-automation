"""Telegram Human Review & Auto-Publish Bot.

Delivers interactive video review cards to the operator via Telegram Bot API,
and processes inline keyboard approval callbacks to seamlessly trigger Step 25 & 26 publishing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx

from ..db import models, store
from ..publishing.orchestrator import PublishingOrchestrator
from ..publishing.service import PublishingService

log = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"


def _escape_md(text: str) -> str:
    """Escape special characters for Telegram legacy Markdown."""
    if not text:
        return ""
    for ch in ("_", "*", "`", "["):
        text = text.replace(ch, f"\\{ch}")
    return text


def get_telegram_config(job_settings: dict[str, Any] | None = None) -> tuple[str, str, list[str]]:
    """Return (bot_token, chat_id, allowed_user_ids)."""
    bot_token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    allowed_str = (os.getenv("TELEGRAM_ALLOWED_USER_IDS") or "").strip()
    allowed_ids = [uid.strip() for uid in allowed_str.split(",") if uid.strip()]

    if job_settings:
        if not bot_token:
            bot_token = str(job_settings.get("telegram_bot_token") or (job_settings.get("telegram") or {}).get("bot_token") or "").strip()
        if not chat_id:
            chat_id = str(job_settings.get("telegram_chat_id") or (job_settings.get("telegram") or {}).get("chat_id") or "").strip()

    if chat_id and chat_id not in allowed_ids:
        allowed_ids.append(chat_id)
    return bot_token, chat_id, allowed_ids


def is_telegram_configured(job_settings: dict[str, Any] | None = None) -> bool:
    token, chat_id, _ = get_telegram_config(job_settings)
    return bool(token and chat_id)


def is_user_authorized(from_id: str | int, allowed_ids: list[str]) -> bool:
    """Validate whether the user is authorized to perform approval actions."""
    if not allowed_ids:
        # If no explicit restriction configured, allow operator
        return True
    return str(from_id) in allowed_ids


async def send_clip_review(job_id: str, clip_id: str, force: bool = False, job_settings: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Send an interactive review card with video preview and inline buttons to operator."""
    bot_token, chat_id, _ = get_telegram_config(job_settings)
    if not bot_token or not chat_id:
        log.info("Telegram review skipped: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not configured.")
        return None

    clip = store.get_clip(clip_id)
    if not clip:
        log.warning("Cannot send Telegram review: clip %s not found.", clip_id)
        return None

    # Deduplication check: Do not re-send unless explicitly forced
    approval = store.get_clip_approval(clip_id)
    if not force and approval:
        for h in approval.history:
            if h.get("action") == "TELEGRAM_REVIEW_SENT":
                log.info("Telegram review card already delivered for clip %s. Skipping duplicate.", clip_id)
                return {"status": "already_sent", "clip_id": clip_id}

    final_render = store.get_final_render(clip_id)
    clip_meta = store.get_clip_metadata(clip_id)
    exports = store.list_exports(clip_id)

    drive_link = ""
    export_id = ""
    for exp in exports:
        if exp.drive_web_view_link:
            drive_link = exp.drive_web_view_link
        if exp.id:
            export_id = exp.id

    duration = f"{clip.end_s - clip.start_s:.1f}" if clip.end_s > clip.start_s else "0.0"
    quality_score = f"{final_render.quality_score:.1f}" if final_render else "N/A"
    quality_status = final_render.quality_status if final_render else "PENDING"
    seo_score = f"{clip_meta.compliance_score:.1f}" if clip_meta else "N/A"
    seo_status = clip_meta.compliance_status if clip_meta else "PENDING"
    raw_title = clip_meta.final_title if clip_meta else (clip.title or "AL AMR Highlight")
    raw_desc = clip_meta.final_description if clip_meta else (clip.hook or "")
    safe_title = _escape_md(raw_title)
    safe_hook = _escape_md(clip.hook or "N/A")

    caption_lines = [
        "🎬 *AL AMR Clip Review Required*",
        "",
        f"📌 *Clip ID:* `{clip.id}`",
        f"⏱ *Duration:* {duration}s  |  *Rank:* #{clip.rank}",
        f"🎯 *Hook:* {safe_hook}",
        f"✨ *Quality:* {quality_score} ({quality_status})",
        f"📈 *SEO Score:* {seo_score} ({seo_status})",
        "",
        f"🏷 *Proposed Title:* {safe_title}",
    ]
    if raw_desc:
        short_desc = raw_desc[:200] + ("..." if len(raw_desc) > 200 else "")
        caption_lines.append(f"📝 *Description:* {_escape_md(short_desc)}")
    if drive_link:
        caption_lines.append(f"🔗 [Watch / Download Clip on Drive]({drive_link})")

    caption_text = "\n".join(caption_lines)

    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "✅ APPROVE & PUBLISH", "callback_data": f"tg:appr:{clip_id}"},
                {"text": "❌ REJECT", "callback_data": f"tg:rej:{clip_id}"},
            ],
            [
                {"text": "🔄 REQUEST CHANGES", "callback_data": f"tg:chg:{clip_id}"},
            ],
        ]
    }

    send_video_url = f"{TELEGRAM_API_BASE}/bot{bot_token}/sendVideo"
    send_msg_url = f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage"

    # Attempt 1: If local file is accessible and under 45MB, send real video preview
    media_path = Path(final_render.output_path) if final_render and final_render.output_path else None
    if media_path and media_path.exists():
        size_mb = media_path.stat().st_size / (1024 * 1024)
        if size_mb <= 45:
            try:
                log.info("Sending clip review video to Telegram chat %s (%d MB)...", chat_id, size_mb)
                async with httpx.AsyncClient(timeout=60.0) as client:
                    with open(media_path, "rb") as vf:
                        files = {"video": (media_path.name, vf, "video/mp4")}
                        data = {
                            "chat_id": chat_id,
                            "caption": caption_text[:1024],
                            "parse_mode": "Markdown",
                            "reply_markup": json.dumps(reply_markup),
                        }
                        resp = await client.post(send_video_url, data=data, files=files)
                        if resp.status_code == 200:
                            log.info("Telegram review video delivered successfully for clip %s", clip_id)
                            _record_review_sent(job_id, clip_id, chat_id)
                            return resp.json()
                        elif resp.status_code == 400:
                            # Retry video without markdown parse_mode
                            data_plain = dict(data)
                            data_plain.pop("parse_mode", None)
                            vf.seek(0)
                            files_plain = {"video": (media_path.name, vf, "video/mp4")}
                            resp_plain = await client.post(send_video_url, data=data_plain, files=files_plain)
                            if resp_plain.status_code == 200:
                                log.info("Telegram review video (plain) delivered for clip %s", clip_id)
                                _record_review_sent(job_id, clip_id, chat_id)
                                return resp_plain.json()
                        log.warning("Telegram sendVideo returned HTTP %s: %s", resp.status_code, resp.text)
            except Exception as exc:
                log.warning("Failed sending video directly via Telegram API: %s", exc)

    # Attempt 2: Fallback to sendMessage with drive preview link and interactive keyboard
    try:
        log.info("Sending clip review card message to Telegram chat %s...", chat_id)
        async with httpx.AsyncClient(timeout=20.0) as client:
            data = {
                "chat_id": chat_id,
                "text": caption_text[:4096],
                "parse_mode": "Markdown",
                "disable_web_page_preview": False,
                "reply_markup": reply_markup,
            }
            resp = await client.post(send_msg_url, json=data)
            if resp.status_code == 200:
                log.info("Telegram review message delivered successfully for clip %s", clip_id)
                _record_review_sent(job_id, clip_id, chat_id)
                return resp.json()
            elif resp.status_code == 400:
                # Retry message as plain text
                data_plain = dict(data)
                data_plain.pop("parse_mode", None)
                resp2 = await client.post(send_msg_url, json=data_plain)
                if resp2.status_code == 200:
                    log.info("Telegram review message (plain fallback) delivered for clip %s", clip_id)
                    _record_review_sent(job_id, clip_id, chat_id)
                    return resp2.json()
                else:
                    log.error("Telegram sendMessage plain fallback failed (HTTP %s): %s", resp2.status_code, resp2.text)
                    return None
            else:
                log.error("Telegram sendMessage failed (HTTP %s): %s", resp.status_code, resp.text)
                return None
    except Exception as exc:
        log.error("Exception sending Telegram review card: %s", exc)
        return None


def _record_review_sent(job_id: str, clip_id: str, chat_id: str | int) -> None:
    """Record TELEGRAM_REVIEW_SENT action in approval history for deduplication."""
    try:
        approval = store.get_clip_approval(clip_id)
        if not approval:
            approval = models.ClipApprovalRecord(
                id=models.new_id(),
                clip_id=clip_id,
                job_id=job_id,
                current_status="PENDING_REVIEW",
                version=1,
                publish_eligible=True,
                blocking_reasons=[],
                created_at=models.utcnow(),
                updated_at=models.utcnow(),
            )
            store.create_clip_approval(approval)
        approval.apply_action(
            new_status=approval.current_status,
            operator_action="TELEGRAM_REVIEW_SENT",
            operator_note=f"Delivered review card to Telegram chat {chat_id}",
            actor="system:telegram_bot",
        )
        store.update_clip_approval(approval)
    except Exception as exc:
        log.warning("Could not record review delivery in approval history: %s", exc)


async def handle_telegram_update(update: dict[str, Any]) -> dict[str, Any]:
    """Process incoming Telegram updates, validating authorization and executing actions."""
    bot_token, _, allowed_ids = get_telegram_config()
    if not bot_token:
        return {"status": "error", "message": "Telegram bot token not configured"}

    callback_query = update.get("callback_query")
    if not callback_query:
        # Not an inline button callback, acknowledge gracefully
        return {"status": "ignored", "message": "No callback_query in update"}

    cb_id = callback_query.get("id")
    from_user = callback_query.get("from", {})
    user_id = from_user.get("id")
    username = from_user.get("username", "") or str(user_id)
    cb_data = callback_query.get("data", "")
    message = callback_query.get("message", {})
    message_id = message.get("message_id")
    chat_id = message.get("chat", {}).get("id")

    # 1. Operator Authorization Check
    if not is_user_authorized(user_id, allowed_ids):
        log.warning("Unauthorized operator attempt from user %s (id=%s)", username, user_id)
        await _answer_callback_query(
            bot_token,
            cb_id,
            text="⛔ Unauthorized operator. You do not have permission to review clips.",
            show_alert=True,
        )
        return {"status": "unauthorized", "user_id": user_id}

    if not cb_data.startswith("tg:"):
        await _answer_callback_query(bot_token, cb_id, text="Unknown action.")
        return {"status": "ignored"}

    parts = cb_data.split(":")
    if len(parts) < 3:
        await _answer_callback_query(bot_token, cb_id, text="Invalid callback payload.")
        return {"status": "invalid_data"}

    action = parts[1]
    clip_id = parts[2]

    clip = store.get_clip(clip_id)
    if not clip:
        await _answer_callback_query(bot_token, cb_id, text=f"Clip {clip_id} not found.", show_alert=True)
        return {"status": "not_found", "clip_id": clip_id}

    job_id = clip.job_id
    actor = f"telegram:{username or user_id}"

    # Get or create Step 24 approval record
    approval = store.get_clip_approval(clip_id)
    if not approval:
        approval = models.ClipApprovalRecord(
            id=models.new_id(),
            clip_id=clip_id,
            job_id=job_id,
            current_status="PENDING_REVIEW",
            version=1,
            publish_eligible=True,
            blocking_reasons=[],
            created_at=models.utcnow(),
            updated_at=models.utcnow(),
        )
        store.create_clip_approval(approval)

    if action == "appr":
        # Idempotency check: Already approved
        if approval.current_status == "APPROVED":
            await _answer_callback_query(bot_token, cb_id, text="ℹ️ Clip is already APPROVED.", show_alert=False)
            return {"status": "already_approved", "clip_id": clip_id, "job_id": job_id}

        # Check transition validity
        if not approval.can_transition_to("APPROVED"):
            msg = f"Cannot approve clip: currently {approval.current_status}."
            await _answer_callback_query(bot_token, cb_id, text=msg, show_alert=True)
            return {"status": "invalid_transition", "current": approval.current_status}

        approval.apply_action(
            new_status="APPROVED",
            operator_action="APPROVE",
            operator_note=f"Approved & published via Telegram Bot by @{username}",
            actor=actor,
        )
        store.update_clip_approval(approval)
        log.info("Clip %s APPROVED via Telegram by %s", clip_id, actor)

        # Trigger Step 25 & 26 Publishing Orchestration
        asyncio.create_task(_execute_auto_publish(job_id, clip_id))

        # Acknowledge callback immediately to unblock Telegram UI
        await _answer_callback_query(
            bot_token,
            cb_id,
            text="✅ Clip APPROVED! Auto-publishing to YouTube & Instagram initiated.",
            show_alert=False,
        )

        # Update Telegram message with approval badge and disable buttons
        await _update_telegram_message_status(
            bot_token=bot_token,
            chat_id=chat_id,
            message_id=message_id,
            has_caption=bool(message.get("caption")),
            original_text=message.get("caption") or message.get("text") or "",
            status_text="✅ *APPROVED & AUTO-PUBLISHING STARTED*",
            button_text="✅ Approved & Publishing",
        )

        return {"status": "approved", "clip_id": clip_id, "job_id": job_id}

    elif action == "rej":
        # Idempotency check: Already rejected
        if approval.current_status == "REJECTED":
            await _answer_callback_query(bot_token, cb_id, text="ℹ️ Clip is already REJECTED.", show_alert=False)
            return {"status": "already_rejected", "clip_id": clip_id, "job_id": job_id}

        if not approval.can_transition_to("REJECTED"):
            msg = f"Cannot reject clip: currently {approval.current_status}."
            await _answer_callback_query(bot_token, cb_id, text=msg, show_alert=True)
            return {"status": "invalid_transition", "current": approval.current_status}

        approval.apply_action(
            new_status="REJECTED",
            operator_action="REJECT",
            operator_note=f"Rejected via Telegram Bot by @{username}",
            actor=actor,
        )
        store.update_clip_approval(approval)
        log.info("Clip %s REJECTED via Telegram by %s", clip_id, actor)

        # Cancel any pending/scheduled queue items for this clip
        try:
            PublishingOrchestrator().cancel_items_for_clip(clip_id, reason=f"Rejected via Telegram by @{username}")
        except Exception as exc:
            log.warning("Could not cancel queue items for rejected clip %s: %s", clip_id, exc)

        await _answer_callback_query(bot_token, cb_id, text="❌ Clip rejected.", show_alert=False)

        await _update_telegram_message_status(
            bot_token=bot_token,
            chat_id=chat_id,
            message_id=message_id,
            has_caption=bool(message.get("caption")),
            original_text=message.get("caption") or message.get("text") or "",
            status_text="❌ *REJECTED BY OPERATOR*",
            button_text="❌ Rejected",
        )

        return {"status": "rejected", "clip_id": clip_id, "job_id": job_id}

    elif action == "chg":
        # Idempotency check: Already changes requested
        if approval.current_status == "CHANGES_REQUESTED":
            await _answer_callback_query(bot_token, cb_id, text="ℹ️ Changes already requested.", show_alert=False)
            return {"status": "already_changes_requested", "clip_id": clip_id, "job_id": job_id}

        if not approval.can_transition_to("CHANGES_REQUESTED"):
            msg = f"Cannot request changes: currently {approval.current_status}."
            await _answer_callback_query(bot_token, cb_id, text=msg, show_alert=True)
            return {"status": "invalid_transition", "current": approval.current_status}

        approval.apply_action(
            new_status="CHANGES_REQUESTED",
            operator_action="REQUEST_CHANGES",
            operator_note=f"Changes requested via Telegram Bot by @{username}",
            actor=actor,
        )
        store.update_clip_approval(approval)
        log.info("Clip %s CHANGES_REQUESTED via Telegram by %s", clip_id, actor)

        # Cancel any pending/scheduled queue items for this clip
        try:
            PublishingOrchestrator().cancel_items_for_clip(clip_id, reason=f"Changes requested via Telegram by @{username}")
        except Exception as exc:
            log.warning("Could not cancel queue items for changed clip %s: %s", clip_id, exc)

        await _answer_callback_query(bot_token, cb_id, text="🔄 Changes requested.", show_alert=False)

        await _update_telegram_message_status(
            bot_token=bot_token,
            chat_id=chat_id,
            message_id=message_id,
            has_caption=bool(message.get("caption")),
            original_text=message.get("caption") or message.get("text") or "",
            status_text="🔄 *CHANGES REQUESTED BY OPERATOR*",
            button_text="🔄 Changes Requested",
        )

        return {"status": "changes_requested", "clip_id": clip_id, "job_id": job_id}

    return {"status": "unknown_action", "action": action}


async def _execute_auto_publish(job_id: str, clip_id: str) -> None:
    """Asynchronously publish the approved clip to YouTube and Instagram."""
    log.info("Executing auto-publishing for approved clip %s (job %s)...", clip_id, job_id)
    try:
        service = PublishingService()
        orchestrator = PublishingOrchestrator(service=service)

        # 1. Enqueue publication across enabled standard destinations
        destinations = orchestrator.ensure_default_destinations()
        queued_items = []
        for d in destinations:
            if d.enabled and d.platform in ("youtube", "instagram"):
                try:
                    item = orchestrator.enqueue_publication(
                        job_id=job_id,
                        clip_id=clip_id,
                        destination_id=d.id,
                    )
                    queued_items.append(item)
                except Exception as exc:
                    log.warning("Could not enqueue clip %s to %s: %s", clip_id, d.display_name, exc)

        # 2. Directly trigger publishing via PublishingService for immediate execution
        results = await service.publish_clip_all_destinations(
            job_id=job_id,
            clip_id=clip_id,
            platforms=["youtube", "instagram"],
            dry_run=False,
        )
        log.info(
            "Auto-publishing completed for clip %s: %d destination records updated.",
            clip_id,
            len(results),
        )

        # 3. Report granular platform results back to the operator on Telegram
        bot_token, chat_id, _ = get_telegram_config()
        if bot_token and chat_id and results:
            lines = [f"📢 *Auto-Publish Results for Clip* `{clip_id}`:"]
            for r in results:
                status_icon = "✅ SUCCESS" if r.status == "published" else "❌ FAILED"
                p_name = r.platform.upper()
                if r.status == "published":
                    post_link = f" - [View Post]({r.published_url})" if r.published_url else ""
                    lines.append(f"• *{p_name}*: {status_icon}{post_link}")
                else:
                    err_msg = (r.error_message or "Unknown error")[:120]
                    lines.append(f"• *{p_name}*: {status_icon} (`{err_msg}`)")
            report_text = "\n".join(lines)
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    await client.post(
                        f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage",
                        json={"chat_id": chat_id, "text": report_text, "parse_mode": "Markdown"},
                    )
            except Exception as notify_exc:
                log.warning("Could not send Telegram publish report: %s", notify_exc)

    except Exception as exc:
        log.exception("Auto-publishing failed for approved clip %s: %s", clip_id, exc)


async def _answer_callback_query(
    bot_token: str, callback_query_id: str, text: str, show_alert: bool = False
) -> None:
    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/answerCallbackQuery"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                url,
                json={"callback_query_id": callback_query_id, "text": text, "show_alert": show_alert},
            )
    except Exception as exc:
        log.warning("Failed to answer callback query %s: %s", callback_query_id, exc)


async def _update_telegram_message_status(
    bot_token: str,
    chat_id: int | str,
    message_id: int | str,
    has_caption: bool,
    original_text: str,
    status_text: str,
    button_text: str,
) -> None:
    updated_text = f"{original_text}\n\n{status_text}"
    markup = {"inline_keyboard": [[{"text": button_text, "callback_data": "tg:done"}]]}

    method = "editMessageCaption" if has_caption else "editMessageText"
    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/{method}"
    field_name = "caption" if has_caption else "text"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            data = {
                "chat_id": chat_id,
                "message_id": message_id,
                field_name: updated_text[:1024 if has_caption else 4096],
                "parse_mode": "Markdown",
                "reply_markup": markup,
            }
            await client.post(url, json=data)
    except Exception as exc:
        log.warning("Failed to update message %s status: %s", message_id, exc)
