"""Telegram Human Review & Auto-Publish Bot.

Delivers interactive video review cards to the operator via Telegram Bot API,
and processes inline keyboard approval callbacks to seamlessly trigger Step 25 & 26 publishing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import httpx

from ..db import models, store
from ..publishing.orchestrator import PublishingOrchestrator
from ..publishing.service import PublishingService

log = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"

# Concurrency & In-Progress Tracking
_publishing_in_progress: set[str] = set()
_publishing_lock = asyncio.Lock()
_background_tasks: set[asyncio.Task] = set()


def _escape_md(text: str) -> str:
    """Escape special characters for Telegram legacy Markdown."""
    if not text:
        return ""
    for ch in ("_", "*", "`", "["):
        text = text.replace(ch, f"\\{ch}")
    return text


def _sanitize_error(err: str) -> str:
    """Sanitize error message to strip sensitive credentials and cap length."""
    if not err:
        return "Unknown error"
    # Mask potential token/key patterns
    cleaned = re.sub(
        r"(token|key|secret|authorization|bearer)[=:\s]+[A-Za-z0-9_\-\.]{8,}",
        r"\1=***REDACTED***",
        str(err),
        flags=re.IGNORECASE,
    )
    # Strip any brackets or unescaped markdown syntax that could break formatting
    cleaned = _escape_md(cleaned)
    if len(cleaned) > 200:
        cleaned = cleaned[:197] + "..."
    return cleaned


def _format_card_content(
    original_text: str,
    status_header: str,
    platforms_status: dict[str, str],
    has_caption: bool,
) -> str:
    """Compose review card text with status badge while respecting Telegram length limits."""
    delimiter = "\n\n━━━━━━━━━━━━━━━━━━━━\n"
    base = (original_text or "").split(delimiter)[0].strip()

    status_block = f"{delimiter.strip()}\n{status_header}\n"
    for plat, st in platforms_status.items():
        status_block += f"\n• *{plat}*: {st}"

    max_len = 1020 if has_caption else 4000
    available_base = max_len - len(status_block) - 4
    if len(base) > available_base:
        base = base[: max(0, available_base - 3)] + "..."
    return f"{base}\n\n{status_block}"


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

    if not bot_token or not chat_id:
        try:
            from ..security.vault import get_vault
            vault = get_vault()
            if not bot_token:
                v_tok = vault.retrieve_secret("telegram_bot_token") or vault.retrieve_secret("TELEGRAM_BOT_TOKEN")
                if v_tok:
                    bot_token = v_tok.strip()
            if not chat_id:
                v_cid = vault.retrieve_secret("telegram_chat_id") or vault.retrieve_secret("TELEGRAM_CHAT_ID")
                if v_cid:
                    chat_id = v_cid.strip()
        except Exception:
            pass

    return bot_token, chat_id, allowed_ids


def is_telegram_configured(job_settings: dict[str, Any] | None = None) -> bool:
    token, chat_id, _ = get_telegram_config(job_settings)
    return bool(token and chat_id)


def is_user_authorized(from_id: str | int, allowed_ids: list[str]) -> bool:
    """Validate whether the user is authorized to perform approval actions."""
    if not allowed_ids:
        return True
    return str(from_id) in allowed_ids


async def _safe_edit_telegram_message(
    bot_token: str,
    chat_id: int | str,
    message_id: int | str | None,
    text: str,
    has_caption: bool = False,
    reply_markup: dict[str, Any] | None = None,
) -> bool:
    """Safely edit a Telegram message or caption with Markdown protection and length capping."""
    if not message_id:
        return False

    method = "editMessageCaption" if has_caption else "editMessageText"
    field_name = "caption" if has_caption else "text"
    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/{method}"
    max_len = 1020 if has_caption else 4000

    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        field_name: text[:max_len],
        "parse_mode": "Markdown",
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                return True
            # Retry without parse_mode if Markdown parsing failed (HTTP 400)
            if resp.status_code == 400:
                payload_plain = dict(payload)
                payload_plain.pop("parse_mode", None)
                resp_plain = await client.post(url, json=payload_plain)
                if resp_plain.status_code == 200:
                    return True
                log.warning("Telegram %s plain retry failed (HTTP %s): %s", method, resp_plain.status_code, resp_plain.text)
            else:
                log.warning("Telegram %s failed (HTTP %s): %s", method, resp.status_code, resp.text)
    except Exception as exc:
        log.warning("Exception in _safe_edit_telegram_message: %s", exc)
    return False


async def _safe_send_telegram_message(
    bot_token: str,
    chat_id: int | str,
    text: str,
    reply_to_message_id: int | str | None = None,
    reply_markup: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Send a Telegram message with Markdown formatting and plain text fallback."""
    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage"
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text[:4096],
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
    }
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 400:
                payload_plain = dict(payload)
                payload_plain.pop("parse_mode", None)
                resp_plain = await client.post(url, json=payload_plain)
                if resp_plain.status_code == 200:
                    return resp_plain.json()
                log.warning("Telegram sendMessage plain retry failed (HTTP %s): %s", resp_plain.status_code, resp_plain.text)
            else:
                log.warning("Telegram sendMessage failed (HTTP %s): %s", resp.status_code, resp.text)
    except Exception as exc:
        log.warning("Exception in _safe_send_telegram_message: %s", exc)
    return None


async def _answer_callback_query(
    bot_token: str, callback_query_id: str, text: str, show_alert: bool = False
) -> None:
    """Acknowledge Telegram callback query immediately to stop client-side spinner."""
    if not callback_query_id:
        return
    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/answerCallbackQuery"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                url,
                json={"callback_query_id": callback_query_id, "text": text, "show_alert": show_alert},
            )
    except Exception as exc:
        log.warning("Failed to answer callback query %s: %s", callback_query_id, exc)


def _record_review_sent(
    job_id: str,
    clip_id: str,
    chat_id: str | int,
    message_id: str | int | None = None,
    has_caption: bool = False,
) -> None:
    """Record TELEGRAM_REVIEW_SENT action in approval history and telemetry for deduplication."""
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
        if message_id:
            approval.telemetry["telegram_message_id"] = message_id
            approval.telemetry["telegram_chat_id"] = str(chat_id)
            approval.telemetry["telegram_has_caption"] = has_caption

        approval.operator_action = "TELEGRAM_REVIEW_SENT"
        approval.operator_note = f"Delivered review card to Telegram chat {chat_id}"
        approval.history.append({
            "from_status": approval.current_status,
            "to_status": approval.current_status,
            "operator_action": "TELEGRAM_REVIEW_SENT",
            "operator_note": f"Delivered review card to Telegram chat {chat_id}",
            "actor": "system:telegram_bot",
            "version": approval.version,
            "timestamp": models.utcnow(),
        })
        approval.updated_at = models.utcnow()
        store.update_clip_approval(approval)
    except Exception as exc:
        log.warning("Could not record review delivery in approval history: %s", exc)


async def send_clip_review(
    job_id: str,
    clip_id: str,
    force: bool = False,
    job_settings: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
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
            if h.get("operator_action") == "TELEGRAM_REVIEW_SENT" or h.get("action") == "TELEGRAM_REVIEW_SENT":
                log.info("Telegram review card already delivered for clip %s. Skipping duplicate.", clip_id)
                return {"status": "already_sent", "clip_id": clip_id}

    final_render = store.get_final_render(clip_id)
    clip_meta = store.get_clip_metadata(clip_id)
    exports = store.list_exports(clip_id)

    drive_link = ""
    for exp in exports:
        if exp.drive_web_view_link:
            drive_link = exp.drive_web_view_link
            break

    media_path = Path(final_render.output_path) if final_render and final_render.output_path else None
    has_local_media = bool(media_path and media_path.exists())
    has_drive_media = bool(drive_link)

    # Only send review cards for clips that are actually rendered and have media or drive preview
    if not final_render or (not has_local_media and not has_drive_media):
        log.warning(
            "Cannot send Telegram review: clip %s has no verified media (final_render=%s, local_media=%s, drive=%s).",
            clip_id,
            bool(final_render),
            has_local_media,
            has_drive_media,
        )
        return None

    # Gate behind campaign compliance if campaign is configured for the job
    job = store.get_job(job_id)
    has_campaign = bool(
        job and (
            job.settings.get("campaign")
            or job.settings.get("guideline")
            or getattr(job, "campaign_spec_id", None)
            or store.get_guideline_for_job(job_id)
        )
    )

    compliance_badge = "PASSED ✅"
    if clip_meta:
        comp_data = clip_meta.telemetry.get("campaign_compliance", {})
        comp_passed = comp_data.get("passed", False) if comp_data else (clip_meta.compliance_status in ("COMPLIANT", "ACCEPTABLE_WITH_WARNINGS"))
        if has_campaign and not comp_passed:
            log.warning(
                "Cannot send Telegram review: clip %s has not passed campaign compliance (%s, violations: %s)",
                clip_id,
                clip_meta.compliance_status,
                clip_meta.validation_errors,
            )
            return None
        compliance_badge = "PASSED ✅" if comp_passed else f"{clip_meta.compliance_status} ⚠️"
    elif has_campaign:
        compliance_badge = "PENDING ⏳"

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
        f"📋 *Campaign Compliance:* {compliance_badge}",
        f"📈 *SEO Score:* {seo_score} ({seo_status})",
        "",
        f"🏷 *Proposed Title:* {safe_title}",
    ]
    if clip_meta and clip_meta.final_hashtags:
        caption_lines.append(f"🔖 *Hashtags:* {_escape_md(' '.join(clip_meta.final_hashtags[:6]))}")
    if clip_meta and clip_meta.final_cta:
        caption_lines.append(f"📣 *CTA:* {_escape_md(clip_meta.final_cta[:100])}")
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
                            resp_json = resp.json()
                            msg_id = resp_json.get("result", {}).get("message_id")
                            _record_review_sent(job_id, clip_id, chat_id, message_id=msg_id, has_caption=True)
                            return resp_json
                        elif resp.status_code == 400:
                            # Retry video without markdown parse_mode
                            data_plain = dict(data)
                            data_plain.pop("parse_mode", None)
                            vf.seek(0)
                            files_plain = {"video": (media_path.name, vf, "video/mp4")}
                            resp_plain = await client.post(send_video_url, data=data_plain, files=files_plain)
                            if resp_plain.status_code == 200:
                                log.info("Telegram review video (plain) delivered for clip %s", clip_id)
                                resp_json = resp_plain.json()
                                msg_id = resp_json.get("result", {}).get("message_id")
                                _record_review_sent(job_id, clip_id, chat_id, message_id=msg_id, has_caption=True)
                                return resp_json
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
                resp_json = resp.json()
                msg_id = resp_json.get("result", {}).get("message_id")
                _record_review_sent(job_id, clip_id, chat_id, message_id=msg_id, has_caption=False)
                return resp_json
            elif resp.status_code == 400:
                # Retry message as plain text
                data_plain = dict(data)
                data_plain.pop("parse_mode", None)
                resp2 = await client.post(send_msg_url, json=data_plain)
                if resp2.status_code == 200:
                    log.info("Telegram review message (plain fallback) delivered for clip %s", clip_id)
                    resp_json = resp2.json()
                    msg_id = resp_json.get("result", {}).get("message_id")
                    _record_review_sent(job_id, clip_id, chat_id, message_id=msg_id, has_caption=False)
                    return resp_json
                else:
                    log.error("Telegram sendMessage plain fallback failed (HTTP %s): %s", resp2.status_code, resp2.text)
                    return None
            else:
                log.error("Telegram sendMessage failed (HTTP %s): %s", resp.status_code, resp.text)
                return None
    except Exception as exc:
        log.error("Exception sending Telegram review card: %s", exc)
        return None


async def handle_telegram_update(update: dict[str, Any]) -> dict[str, Any]:
    """Process incoming Telegram updates, validating authorization and executing actions."""
    bot_token, _, allowed_ids = get_telegram_config()
    if not bot_token:
        return {"status": "error", "message": "Telegram bot token not configured"}

    callback_query = update.get("callback_query")
    if not callback_query:
        return {"status": "ignored", "message": "No callback_query in update"}

    cb_id = callback_query.get("id") or ""
    from_user = callback_query.get("from", {})
    user_id = from_user.get("id")
    username = from_user.get("username", "") or str(user_id)
    cb_data = callback_query.get("data", "")
    message = callback_query.get("message", {})
    message_id = message.get("message_id")
    chat_id = message.get("chat", {}).get("id") or os.getenv("TELEGRAM_CHAT_ID") or ""
    has_caption = bool(message.get("caption"))
    original_text = message.get("caption") or message.get("text") or ""

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

    # 2. Prevent interactions while already busy or done
    if cb_data in ("tg:busy", "tg:done"):
        await _answer_callback_query(
            bot_token,
            cb_id,
            text="⏳ Action already completed or currently in progress.",
            show_alert=False,
        )
        return {"status": "already_handled", "action": cb_data}

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
    actor = f"telegram:@{username}" if username else f"telegram:{user_id}"

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

    # ------------------------------------------------------------------
    # ACTION: APPROVE & PUBLISH
    # ------------------------------------------------------------------
    if action == "appr":
        # Check if already currently uploading in-memory
        if clip_id in _publishing_in_progress:
            await _answer_callback_query(
                bot_token,
                cb_id,
                text="⏳ Publishing is already in progress for this clip. Please wait.",
                show_alert=False,
            )
            return {"status": "already_publishing", "clip_id": clip_id, "job_id": job_id}

        # Check existing publications: if both YouTube and Instagram succeeded, prevent re-publishing
        existing_pubs = store.list_publications_for_clip(clip_id)
        published_platforms = {p.platform.lower() for p in existing_pubs if p.status == "PUBLISHED"}
        if "youtube" in published_platforms and "instagram" in published_platforms:
            await _answer_callback_query(
                bot_token,
                cb_id,
                text="✅ Clip is already published to all platforms.",
                show_alert=False,
            )
            return {"status": "already_published", "clip_id": clip_id, "job_id": job_id}

        # Immediate callback query acknowledgment to stop Telegram client spinner instantly
        await _answer_callback_query(
            bot_token,
            cb_id,
            text="⏳ Approval received! Initiating auto-publish...",
            show_alert=False,
        )

        # Update DB approval state
        if approval.current_status != "APPROVED":
            if approval.can_transition_to("APPROVED"):
                approval.apply_action(
                    new_status="APPROVED",
                    operator_action="APPROVE",
                    operator_note=f"Approved & published via Telegram Bot by @{username}",
                    actor=actor,
                )
                store.update_clip_approval(approval)
                log.info("Clip %s transitioned to APPROVED via Telegram by %s", clip_id, actor)
            else:
                err_msg = f"Cannot approve clip: currently in status {approval.current_status}."
                await _safe_send_telegram_message(
                    bot_token,
                    chat_id,
                    f"⚠️ {err_msg}",
                    reply_to_message_id=message_id,
                )
                return {"status": "invalid_transition", "current": approval.current_status}
        else:
            # Already APPROVED, record that operator re-triggered publishing
            approval.operator_action = "APPROVE"
            approval.operator_note = f"Publishing re-triggered via Telegram Bot by @{username}"
            approval.history.append({
                "from_status": "APPROVED",
                "to_status": "APPROVED",
                "operator_action": "APPROVE",
                "operator_note": f"Publishing re-triggered via Telegram Bot by @{username}",
                "actor": actor,
                "version": approval.version,
                "timestamp": models.utcnow(),
            })
            approval.updated_at = models.utcnow()
            store.update_clip_approval(approval)

        # Immediate Review Card Update: Show "Publishing started..." and disable interactive buttons
        initial_status_text = _format_card_content(
            original_text=original_text,
            status_header="⏳ *APPROVED — Publishing started...*",
            platforms_status={
                "YouTube Shorts": "⏳ Queued",
                "Instagram Reels": "⏳ Queued",
            },
            has_caption=has_caption,
        )
        await _safe_edit_telegram_message(
            bot_token=bot_token,
            chat_id=chat_id,
            message_id=message_id,
            text=initial_status_text,
            has_caption=has_caption,
            reply_markup={"inline_keyboard": [[{"text": "⏳ Publishing in progress...", "callback_data": "tg:busy"}]]},
        )

        # Launch Step 25 & 26 Publishing in Background Task
        task = asyncio.create_task(
            _execute_auto_publish(
                job_id=job_id,
                clip_id=clip_id,
                chat_id=chat_id,
                message_id=message_id,
                has_caption=has_caption,
                original_text=original_text,
                bot_token=bot_token,
            )
        )
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

        return {"status": "approved", "clip_id": clip_id, "job_id": job_id}

    # ------------------------------------------------------------------
    # ACTION: REJECT
    # ------------------------------------------------------------------
    elif action == "rej":
        await _answer_callback_query(bot_token, cb_id, text="❌ Clip rejected.", show_alert=False)

        if approval.current_status == "REJECTED":
            return {"status": "already_rejected", "clip_id": clip_id, "job_id": job_id}

        if not approval.can_transition_to("REJECTED"):
            msg = f"Cannot reject clip: currently {approval.current_status}."
            await _safe_send_telegram_message(bot_token, chat_id, f"⚠️ {msg}", reply_to_message_id=message_id)
            return {"status": "invalid_transition", "current": approval.current_status}

        approval.apply_action(
            new_status="REJECTED",
            operator_action="REJECT",
            operator_note=f"Rejected via Telegram Bot by @{username}",
            actor=actor,
        )
        store.update_clip_approval(approval)
        log.info("Clip %s REJECTED via Telegram by %s", clip_id, actor)

        try:
            PublishingOrchestrator().cancel_items_for_clip(clip_id, reason=f"Rejected via Telegram by @{username}")
        except Exception as exc:
            log.warning("Could not cancel queue items for rejected clip %s: %s", clip_id, exc)

        await _update_telegram_message_status(
            bot_token=bot_token,
            chat_id=chat_id,
            message_id=message_id,
            has_caption=has_caption,
            original_text=original_text,
            status_text="❌ *REJECTED BY OPERATOR*",
            button_text="❌ Rejected",
        )
        return {"status": "rejected", "clip_id": clip_id, "job_id": job_id}

    # ------------------------------------------------------------------
    # ACTION: REQUEST CHANGES
    # ------------------------------------------------------------------
    elif action == "chg":
        await _answer_callback_query(bot_token, cb_id, text="🔄 Changes requested.", show_alert=False)

        if approval.current_status == "CHANGES_REQUESTED":
            return {"status": "already_changes_requested", "clip_id": clip_id, "job_id": job_id}

        if not approval.can_transition_to("CHANGES_REQUESTED"):
            msg = f"Cannot request changes: currently {approval.current_status}."
            await _safe_send_telegram_message(bot_token, chat_id, f"⚠️ {msg}", reply_to_message_id=message_id)
            return {"status": "invalid_transition", "current": approval.current_status}

        approval.apply_action(
            new_status="CHANGES_REQUESTED",
            operator_action="REQUEST_CHANGES",
            operator_note=f"Changes requested via Telegram Bot by @{username}",
            actor=actor,
        )
        store.update_clip_approval(approval)
        log.info("Clip %s CHANGES_REQUESTED via Telegram by %s", clip_id, actor)

        try:
            PublishingOrchestrator().cancel_items_for_clip(clip_id, reason=f"Changes requested via Telegram by @{username}")
        except Exception as exc:
            log.warning("Could not cancel queue items for changed clip %s: %s", clip_id, exc)

        await _update_telegram_message_status(
            bot_token=bot_token,
            chat_id=chat_id,
            message_id=message_id,
            has_caption=has_caption,
            original_text=original_text,
            status_text="🔄 *CHANGES REQUESTED BY OPERATOR*",
            button_text="🔄 Changes Requested",
        )
        return {"status": "changes_requested", "clip_id": clip_id, "job_id": job_id}

    return {"status": "unknown_action", "action": action}


async def _execute_auto_publish(
    job_id: str,
    clip_id: str,
    chat_id: int | str | None = None,
    message_id: int | str | None = None,
    has_caption: bool = False,
    original_text: str = "",
    bot_token: str | None = None,
) -> dict[str, Any]:
    """Asynchronously publish the approved clip to YouTube and Instagram."""
    if not bot_token or not chat_id:
        cfg_token, cfg_chat, _ = get_telegram_config()
        bot_token = bot_token or cfg_token
        chat_id = chat_id or cfg_chat

    if not original_text:
        approval = store.get_clip_approval(clip_id)
        if approval and approval.telemetry:
            chat_id = chat_id or approval.telemetry.get("telegram_chat_id")
            message_id = message_id or approval.telemetry.get("telegram_message_id")
            has_caption = has_caption or bool(approval.telemetry.get("telegram_has_caption"))

    async with _publishing_lock:
        if clip_id in _publishing_in_progress:
            log.warning("Publishing already in progress for clip %s. Skipping duplicate trigger.", clip_id)
            return {"status": "already_in_progress", "clip_id": clip_id}
        _publishing_in_progress.add(clip_id)

    log.info("Executing auto-publishing for approved clip %s (job %s)...", clip_id, job_id)
    platforms_status: dict[str, str] = {
        "YouTube Shorts": "⏳ Queued",
        "Instagram Reels": "⏳ Queued",
    }
    results: dict[str, models.PublicationRecord | None] = {}

    try:
        service = PublishingService()
        orchestrator = PublishingOrchestrator(service=service)

        # Enqueue in orchestrator for tracking/audit
        try:
            destinations = orchestrator.ensure_default_destinations()
            for d in destinations:
                if d.enabled and d.platform in ("youtube", "instagram"):
                    try:
                        orchestrator.enqueue_publication(
                            job_id=job_id,
                            clip_id=clip_id,
                            destination_id=d.id,
                        )
                    except Exception as enc_exc:
                        log.debug("Enqueue publication note for %s: %s", d.display_name, enc_exc)
        except Exception as dest_exc:
            log.warning("Could not ensure default destinations: %s", dest_exc)

        # -------------------------------------------------------------
        # 1. Publish to YouTube Shorts
        # -------------------------------------------------------------
        platforms_status["YouTube Shorts"] = "⏳ Uploading..."
        if bot_token and chat_id and message_id:
            curr_text = _format_card_content(
                original_text=original_text,
                status_header="⏳ *APPROVED — Publishing in progress...*",
                platforms_status=platforms_status,
                has_caption=has_caption,
            )
            await _safe_edit_telegram_message(
                bot_token=bot_token,
                chat_id=chat_id,
                message_id=message_id,
                text=curr_text,
                has_caption=has_caption,
                reply_markup={"inline_keyboard": [[{"text": "⏳ Uploading to YouTube...", "callback_data": "tg:busy"}]]},
            )

        try:
            yt_rec = await service.publish_clip(
                job_id=job_id,
                clip_id=clip_id,
                platform="youtube",
                destination="dest-youtube-main",
                dry_run=False,
            )
        except Exception as exc:
            log.exception("Exception during YouTube publication for clip %s: %s", clip_id, exc)
            recs = store.list_publications_for_clip(clip_id)
            yt_candidates = [r for r in recs if r.platform.lower() == "youtube"]
            if yt_candidates:
                yt_rec = yt_candidates[0]
            else:
                yt_rec = models.PublicationRecord(
                    id=models.new_id(),
                    job_id=job_id,
                    clip_id=clip_id,
                    platform="youtube",
                    destination_id="dest-youtube-main",
                    status="FAILED_PERMANENT",
                    error_code="execution_error",
                    error_message=str(exc),
                )
        results["youtube"] = yt_rec

        if yt_rec and yt_rec.status == "PUBLISHED":
            platforms_status["YouTube Shorts"] = "✅ Published"
        else:
            err = (yt_rec.error_message if yt_rec else "Upload failed") or "Upload failed"
            platforms_status["YouTube Shorts"] = f"❌ Failed ({_sanitize_error(err)[:50]})"

        # -------------------------------------------------------------
        # 2. Publish to Instagram Reels
        # -------------------------------------------------------------
        platforms_status["Instagram Reels"] = "⏳ Uploading..."
        if bot_token and chat_id and message_id:
            curr_text = _format_card_content(
                original_text=original_text,
                status_header="⏳ *APPROVED — Publishing in progress...*",
                platforms_status=platforms_status,
                has_caption=has_caption,
            )
            await _safe_edit_telegram_message(
                bot_token=bot_token,
                chat_id=chat_id,
                message_id=message_id,
                text=curr_text,
                has_caption=has_caption,
                reply_markup={"inline_keyboard": [[{"text": "⏳ Uploading to Instagram...", "callback_data": "tg:busy"}]]},
            )

        try:
            ig_rec = await service.publish_clip(
                job_id=job_id,
                clip_id=clip_id,
                platform="instagram",
                destination="dest-instagram-main",
                dry_run=False,
            )
        except Exception as exc:
            log.exception("Exception during Instagram publication for clip %s: %s", clip_id, exc)
            recs = store.list_publications_for_clip(clip_id)
            ig_candidates = [r for r in recs if r.platform.lower() == "instagram"]
            if ig_candidates:
                ig_rec = ig_candidates[0]
            else:
                ig_rec = models.PublicationRecord(
                    id=models.new_id(),
                    job_id=job_id,
                    clip_id=clip_id,
                    platform="instagram",
                    destination_id="dest-instagram-main",
                    status="FAILED_PERMANENT",
                    error_code="execution_error",
                    error_message=str(exc),
                )
        results["instagram"] = ig_rec

        if ig_rec and ig_rec.status == "PUBLISHED":
            platforms_status["Instagram Reels"] = "✅ Published"
        else:
            err = (ig_rec.error_message if ig_rec else "Upload failed") or "Upload failed"
            platforms_status["Instagram Reels"] = f"❌ Failed ({_sanitize_error(err)[:50]})"

        # -------------------------------------------------------------
        # 3. Compute Final Outcomes & Status
        # -------------------------------------------------------------
        yt_pub = results.get("youtube")
        ig_pub = results.get("instagram")
        yt_ok = bool(yt_pub and yt_pub.status == "PUBLISHED")
        ig_ok = bool(ig_pub and ig_pub.status == "PUBLISHED")

        if yt_ok and ig_ok:
            final_badge = "✅ *CLIP PUBLISHED*"
            button_label = "✅ Published"
            overall_status = "PUBLISHED"
        elif yt_ok or ig_ok:
            final_badge = "⚠️ *PARTIALLY PUBLISHED*"
            button_label = "⚠️ Partially Published"
            overall_status = "PARTIALLY_PUBLISHED"
        else:
            final_badge = "❌ *PUBLISHING FAILED*"
            button_label = "❌ Publishing Failed"
            overall_status = "PUBLISH_FAILED"

        # Update card permanently
        if bot_token and chat_id and message_id:
            final_card_text = _format_card_content(
                original_text=original_text,
                status_header=final_badge,
                platforms_status=platforms_status,
                has_caption=has_caption,
            )
            await _safe_edit_telegram_message(
                bot_token=bot_token,
                chat_id=chat_id,
                message_id=message_id,
                text=final_card_text,
                has_caption=has_caption,
                reply_markup={"inline_keyboard": [[{"text": button_label, "callback_data": "tg:done"}]]},
            )

        # Send follow-up confirmation message
        if bot_token and chat_id:
            if yt_ok and ig_ok:
                yt_url = yt_pub.permalink or (yt_pub.response_metadata or {}).get("url") or ""
                ig_url = ig_pub.permalink or (ig_pub.response_metadata or {}).get("permalink") or ""
                followup_lines = [
                    "🎉 *Clip Successfully Published!*",
                    "",
                    f"🎬 *Clip ID:* `{clip_id}`",
                    f"📺 *YouTube Shorts:* [Watch on YouTube]({yt_url})" if yt_url else "📺 *YouTube Shorts:* ✅ Published",
                    f"📸 *Instagram Reels:* [Watch on Instagram]({ig_url})" if ig_url else "📸 *Instagram Reels:* ✅ Published",
                ]
            elif yt_ok or ig_ok:
                followup_lines = [
                    "⚠️ *Clip Partially Published*",
                    "",
                    f"🎬 *Clip ID:* `{clip_id}`",
                ]
                if yt_ok:
                    yt_url = yt_pub.permalink or (yt_pub.response_metadata or {}).get("url") or ""
                    followup_lines.append(f"• *YouTube Shorts:* ✅ [Watch on YouTube]({yt_url})" if yt_url else "• *YouTube Shorts:* ✅ Published")
                else:
                    yt_err = _sanitize_error(yt_pub.error_message if yt_pub else "Upload failed")
                    followup_lines.append(f"• *YouTube Shorts:* ❌ `{yt_err}`")

                if ig_ok:
                    ig_url = ig_pub.permalink or (ig_pub.response_metadata or {}).get("permalink") or ""
                    followup_lines.append(f"• *Instagram Reels:* ✅ [Watch on Instagram]({ig_url})" if ig_url else "• *Instagram Reels:* ✅ Published")
                else:
                    ig_err = _sanitize_error(ig_pub.error_message if ig_pub else "Upload failed")
                    followup_lines.append(f"• *Instagram Reels:* ❌ `{ig_err}`")
            else:
                yt_err = _sanitize_error(yt_pub.error_message if yt_pub else "Upload failed")
                ig_err = _sanitize_error(ig_pub.error_message if ig_pub else "Upload failed")
                followup_lines = [
                    "❌ *Clip Publishing Failed*",
                    "",
                    f"🎬 *Clip ID:* `{clip_id}`",
                    f"• *YouTube Shorts:* ❌ `{yt_err}`",
                    f"• *Instagram Reels:* ❌ `{ig_err}`",
                    "",
                    "Please verify platform credentials and media accessibility in Settings.",
                ]

            await _safe_send_telegram_message(
                bot_token=bot_token,
                chat_id=chat_id,
                text="\n".join(followup_lines),
                reply_to_message_id=message_id,
            )

        # Update approval audit & telemetry in DB
        try:
            approval = store.get_clip_approval(clip_id)
            if approval:
                approval.telemetry["publishing_result"] = overall_status
                approval.telemetry["publishing_completed_at"] = models.utcnow()
                approval.history.append({
                    "from_status": approval.current_status,
                    "to_status": approval.current_status,
                    "operator_action": "AUTO_PUBLISH_RESULT",
                    "operator_note": f"Publish outcome: {overall_status} (YT={yt_ok}, IG={ig_ok})",
                    "actor": "system:auto_publish",
                    "version": approval.version,
                    "timestamp": models.utcnow(),
                })
                approval.updated_at = models.utcnow()
                store.update_clip_approval(approval)
        except Exception as db_exc:
            log.warning("Could not update approval telemetry: %s", db_exc)

        return {"status": overall_status.lower(), "clip_id": clip_id, "results": results}

    except Exception as fatal_exc:
        log.exception("Fatal unhandled exception in auto-publish for clip %s: %s", clip_id, fatal_exc)
        sanitized_fatal = _sanitize_error(str(fatal_exc))
        if bot_token and chat_id:
            if message_id:
                await _safe_edit_telegram_message(
                    bot_token=bot_token,
                    chat_id=chat_id,
                    message_id=message_id,
                    text=f"{original_text}\n\n━━━━━━━━━━━━━━━━━━━━\n❌ *PUBLISHING FAILED*\n\nError: `{sanitized_fatal}`"[:1020 if has_caption else 4000],
                    has_caption=has_caption,
                    reply_markup={"inline_keyboard": [[{"text": "❌ Publishing Failed", "callback_data": "tg:done"}]]},
                )
            await _safe_send_telegram_message(
                bot_token=bot_token,
                chat_id=chat_id,
                text=f"❌ *Auto-Publish Error for Clip* `{clip_id}`:\n\n`{sanitized_fatal}`\n\nPlease check server logs.",
                reply_to_message_id=message_id,
            )
        return {"status": "error", "error": str(fatal_exc), "clip_id": clip_id}

    finally:
        async with _publishing_lock:
            _publishing_in_progress.discard(clip_id)


async def _update_telegram_message_status(
    bot_token: str,
    chat_id: int | str,
    message_id: int | str,
    has_caption: bool,
    original_text: str,
    status_text: str,
    button_text: str,
) -> None:
    """Helper to update a message status and replace buttons with a static disabled button."""
    updated_text = f"{original_text}\n\n━━━━━━━━━━━━━━━━━━━━\n{status_text}"
    markup = {"inline_keyboard": [[{"text": button_text, "callback_data": "tg:done"}]]}
    await _safe_edit_telegram_message(
        bot_token=bot_token,
        chat_id=chat_id,
        message_id=message_id,
        text=updated_text,
        has_caption=has_caption,
        reply_markup=markup,
    )
