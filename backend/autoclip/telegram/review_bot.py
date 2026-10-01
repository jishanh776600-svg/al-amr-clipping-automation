"""Telegram Human Review & Auto-Publish Bot.

Delivers interactive video review cards to the operator via Telegram Bot API,
and processes inline keyboard approval callbacks to seamlessly trigger Step 25 & 26 publishing.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import tempfile
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
        status_block += f"\n• <b>{plat}</b>: {st}"

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

    if not bot_token or not chat_id or not allowed_ids:
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
            if not allowed_ids:
                v_uids = vault.retrieve_secret("telegram_allowed_user_ids") or vault.retrieve_secret("TELEGRAM_ALLOWED_USER_IDS")
                if v_uids:
                    allowed_ids = [uid.strip() for uid in v_uids.split(",") if uid.strip()]
        except Exception:
            pass

    return bot_token, chat_id, allowed_ids


def is_telegram_configured(job_settings: dict[str, Any] | None = None) -> bool:
    token, chat_id, _ = get_telegram_config(job_settings)
    return bool(token and chat_id)


def is_user_authorized(from_id: str | int | None, allowed_ids: list[str], username: str | None = None) -> bool:
    """Validate whether the user is authorized to perform approval actions.

    Supports numeric user IDs as well as Telegram usernames (with or without '@').
    """
    if not allowed_ids:
        return True
    fid = str(from_id).strip() if from_id is not None else ""
    u_norm = (username or "").strip().lstrip("@").lower()
    for allowed in allowed_ids:
        a_clean = str(allowed).strip()
        if fid and a_clean == fid:
            return True
        if u_norm and a_clean.lstrip("@").lower() == u_norm:
            return True
    return False


async def _safe_edit_telegram_message(
    bot_token: str,
    chat_id: int | str,
    message_id: int | str | None,
    text: str,
    has_caption: bool = False,
    reply_markup: dict[str, Any] | None = None,
) -> bool:
    """Safely edit a Telegram message or caption with HTML protection and length capping."""
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
        "parse_mode": "HTML",
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                return True
            # Retry without parse_mode if HTML parsing failed (HTTP 400)
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
    """Send a Telegram message with HTML formatting and plain text fallback."""
    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage"
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text[:4096],
        "parse_mode": "HTML",
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
    telegram_file_id: str | None = None,
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
        approval.telemetry = dict(approval.telemetry or {})
        if message_id:
            approval.telemetry["telegram_message_id"] = message_id
            approval.telemetry["telegram_chat_id"] = str(chat_id)
            approval.telemetry["telegram_has_caption"] = has_caption
        if telegram_file_id:
            approval.telemetry["telegram_file_id"] = telegram_file_id

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

    drive_file_id = None
    drive_link = ""
    for exp in exports:
        if exp.drive_file_id:
            drive_file_id = exp.drive_file_id
        if exp.drive_web_view_link:
            drive_link = exp.drive_web_view_link
        if drive_file_id and drive_link:
            break

    if not drive_file_id and final_render and final_render.telemetry:
        drive_file_id = final_render.telemetry.get("drive_file_id")
    if not drive_link and drive_file_id:
        drive_link = f"https://drive.google.com/file/d/{drive_file_id}/view"

    media_path = Path(final_render.output_path) if final_render and final_render.output_path else None
    has_local_media = bool(media_path and media_path.exists())
    has_drive_media = bool(drive_link or drive_file_id)

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
    meta_telemetry = (clip_meta.telemetry or {}) if clip_meta else {}
    yt_data = meta_telemetry.get("youtube") or {}
    ig_data = meta_telemetry.get("instagram") or {}
    yt_title = yt_data.get("title") or (clip_meta.final_title if clip_meta else (clip.title or (clip.hook.title() if clip.hook else "Key Insight & Lesson")))
    yt_title = re.sub(r"\bAL\s*AMR\s*Highlight\b", "Key Insight", yt_title, flags=re.IGNORECASE)
    yt_title = re.sub(r"\b[0-9a-f]{8,}\b", "", yt_title).strip()
    yt_title = re.sub(r"\.{2,}", "", yt_title).strip().rstrip(".!?,;: ")
    yt_title = yt_title.title() if yt_title.islower() else (yt_title or "Key Insight & Lesson")
    safe_yt_title = html.escape(yt_title)

    yt_desc = yt_data.get("description") or (clip_meta.final_description if clip_meta else "")
    yt_desc_snippet = yt_desc[:70] + ("..." if len(yt_desc) > 70 else "")
    safe_yt_desc = html.escape(yt_desc_snippet)

    yt_tags_list = yt_data.get("hashtags") or (clip_meta.final_hashtags if clip_meta else ["#Shorts"])
    safe_yt_tags = html.escape(" ".join(yt_tags_list[:4]))

    yt_mentions_list = yt_data.get("mentions") or []
    if not yt_mentions_list and yt_desc:
        yt_mentions_list = [m for m in re.findall(r"@[A-Za-z0-9_]+", yt_desc) if "tv" in m.lower()] or re.findall(r"@[A-Za-z0-9_]+", yt_desc)
    safe_yt_mentions = html.escape(" ".join(yt_mentions_list[:2])) if yt_mentions_list else "@HardScopeTV"

    yt_comp = yt_data.get("compliance_score", 100.0)
    yt_opt = yt_data.get("optimization_score", 95.0)

    ig_caption = ig_data.get("caption") or (clip_meta.final_description if clip_meta else "")
    ig_caption_snippet = ig_caption[:70] + ("..." if len(ig_caption) > 70 else "")
    safe_ig_caption = html.escape(ig_caption_snippet)

    ig_tags_list = ig_data.get("hashtags") or (clip_meta.final_hashtags if clip_meta else ["#reels"])
    safe_ig_tags = html.escape(" ".join(ig_tags_list[:4]))

    ig_mentions_list = ig_data.get("mentions") or []
    if not ig_mentions_list and ig_caption:
        ig_mentions_list = [m for m in re.findall(r"@[A-Za-z0-9_]+", ig_caption) if "tv" not in m.lower()] or re.findall(r"@[A-Za-z0-9_]+", ig_caption)
    safe_ig_mentions = html.escape(" ".join(ig_mentions_list[:2])) if ig_mentions_list else "@hardscope"

    ig_cta = ig_data.get("cta") or "👉 Follow for daily show highlights."
    safe_ig_cta = html.escape(ig_cta[:45] + ("..." if len(ig_cta) > 45 else ""))

    ig_comp = ig_data.get("compliance_score", 100.0)
    ig_opt = ig_data.get("optimization_score", 95.0)

    safe_hook = html.escape(clip.hook or "N/A")
    if len(safe_hook) > 50:
        safe_hook = safe_hook[:47] + "..."

    yt_channel_display = html.escape(yt_data.get("channel") or yt_data.get("channel_title") or "YouTube Shorts")
    ig_account_display = html.escape(ig_mentions_list[0] if ig_mentions_list else "Instagram Reels")

    caption_lines = [
        "🎬 <b>Clip Review Required</b>",
        "",
        f"📌 <b>Clip ID:</b> <code>{html.escape(clip.id)}</code>",
        f"⏱ <b>Duration:</b> {duration}s  |  <b>Rank:</b> #{clip.rank}",
        f"🎯 <b>Hook:</b> {safe_hook}",
        f"✨ <b>Quality:</b> {quality_score} ({quality_status})",
        f"📋 <b>Campaign Compliance:</b> {compliance_badge}",
        "",
        f"▶️ <b>YOUTUBE ({yt_channel_display}):</b>",
        f"• Title: {safe_yt_title}",
        f"• Description: {safe_yt_desc}",
        f"• Mentions: {safe_yt_mentions}",
        f"• Hashtags: {safe_yt_tags}",
        f"• Compliance: {yt_comp:.0f}% | Opt: {yt_opt:.0f}/100",
        "",
        f"📸 <b>INSTAGRAM ({ig_account_display}):</b>",
        f"• Caption: {safe_ig_caption}",
        f"• Mentions: {safe_ig_mentions}",
        f"• Hashtags: {safe_ig_tags}",
        f"• CTA: {safe_ig_cta}",
        f"• Compliance: {ig_comp:.0f}% | Opt: {ig_opt:.0f}/100",
    ]

    tk_adapter = service.get_adapter("tiktok")
    if tk_adapter and tk_adapter.is_configured():
        tk_user = os.getenv("TIKTOK_ACCOUNT_ID") or "TikTok"
        caption_lines.extend([
            "",
            f"🎵 <b>TIKTOK (@{html.escape(tk_user.lstrip('@'))}):</b>",
            f"• Caption: {safe_ig_caption}",
            f"• Hashtags: {safe_ig_tags}",
        ])

    if drive_link:
        caption_lines.append(f'\n🔗 <a href="{html.escape(drive_link)}">Drive Preview</a>')

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

    # Materialize genuine, verified MP4 video for review delivery
    from ..media_guard import is_valid_mp4, materialize_valid_clip_media

    preview_file_to_clean: Path | None = None
    effective_media_path: Path | None = None

    if media_path and is_valid_mp4(media_path):
        effective_media_path = media_path
    else:
        effective_media_path = await asyncio.to_thread(materialize_valid_clip_media, clip_id)

    def _generate_tg_preview(source_p: Path) -> Path | None:
        """Create a fast, lightweight MP4 (<=25MB) using ffmpeg for Telegram delivery."""
        try:
            import subprocess
            with tempfile.NamedTemporaryFile(suffix="_tg_prev.mp4", delete=False) as prev_tf:
                prev_path = Path(prev_tf.name)
            cmd = [
                "ffmpeg", "-y", "-i", str(source_p),
                "-vf", "scale='min(720,iw)':-2",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
                "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
                "-movflags", "+faststart",
                str(prev_path),
            ]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=40)
            if res.returncode == 0 and prev_path.is_file() and prev_path.stat().st_size > 1000:
                log.info("Compressed Telegram preview video created: %s (%.1f MB)", prev_path, prev_path.stat().st_size / (1024 * 1024))
                return prev_path
        except Exception as e_prev:
            log.warning("Could not generate ffmpeg preview for Telegram: %s", e_prev)
        return None

    try:
        # Attempt 1: Send real video preview (compress if > 48MB so 100% of clips get inline video)
        if effective_media_path and effective_media_path.exists():
            size_mb = effective_media_path.stat().st_size / (1024 * 1024)
            video_to_upload = effective_media_path

            if size_mb > 48:
                log.info("Clip %s size (%.1f MB) exceeds Telegram 48MB direct limit. Generating fast preview...", clip_id, size_mb)
                compressed = await asyncio.to_thread(_generate_tg_preview, effective_media_path)
                if compressed:
                    preview_file_to_clean = compressed
                    video_to_upload = compressed
                    size_mb = compressed.stat().st_size / (1024 * 1024)

            if size_mb <= 48:
                try:
                    log.info("Sending clip review video to Telegram chat %s (%.1f MB)...", chat_id, size_mb)
                    async with httpx.AsyncClient(timeout=120.0) as client:
                        with open(video_to_upload, "rb") as vf:
                            files = {"video": (f"clip_{clip_id}.mp4", vf, "video/mp4")}
                            data = {
                                "chat_id": chat_id,
                                "caption": caption_text[:1024],
                                "parse_mode": "HTML",
                                "reply_markup": json.dumps(reply_markup),
                                "supports_streaming": "true",
                            }
                            resp = await client.post(send_video_url, data=data, files=files)
                            if resp.status_code == 200:
                                log.info("Telegram review video delivered successfully for clip %s", clip_id)
                                resp_json = resp.json()
                                msg_id = resp_json.get("result", {}).get("message_id")
                                tg_fid = (resp_json.get("result", {}).get("video") or {}).get("file_id")
                                _record_review_sent(job_id, clip_id, chat_id, message_id=msg_id, has_caption=True, telegram_file_id=tg_fid)
                                return resp_json
                            elif resp.status_code == 400:
                                # Retry video without html parse_mode and with sanitized plain text
                                data_plain = dict(data)
                                data_plain.pop("parse_mode", None)
                                data_plain["caption"] = re.sub(r"<[^>]+>", "", caption_text)[:1024]
                                vf.seek(0)
                                files_plain = {"video": (f"clip_{clip_id}.mp4", vf, "video/mp4")}
                                resp_plain = await client.post(send_video_url, data=data_plain, files=files_plain)
                                if resp_plain.status_code == 200:
                                    log.info("Telegram review video (plain) delivered for clip %s", clip_id)
                                    resp_json = resp_plain.json()
                                    msg_id = resp_json.get("result", {}).get("message_id")
                                    tg_fid = (resp_json.get("result", {}).get("video") or {}).get("file_id")
                                    _record_review_sent(job_id, clip_id, chat_id, message_id=msg_id, has_caption=True, telegram_file_id=tg_fid)
                                    return resp_json
                                # If failed due to payload/size and hasn't been compressed yet, try compression
                                if video_to_upload == effective_media_path and not preview_file_to_clean:
                                    log.info("HTTP 400 on original video. Attempting preview compression for clip %s...", clip_id)
                                    compressed = await asyncio.to_thread(_generate_tg_preview, effective_media_path)
                                    if compressed:
                                        preview_file_to_clean = compressed
                                        with open(compressed, "rb") as cf:
                                             files_c = {"video": (f"clip_{clip_id}.mp4", cf, "video/mp4")}
                                             resp_c = await client.post(send_video_url, data=data_plain, files=files_c)
                                             if resp_c.status_code == 200:
                                                 log.info("Telegram review video (compressed fallback) delivered for clip %s", clip_id)
                                                 resp_json = resp_c.json()
                                                 msg_id = resp_json.get("result", {}).get("message_id")
                                                 tg_fid = (resp_json.get("result", {}).get("video") or {}).get("file_id")
                                                 _record_review_sent(job_id, clip_id, chat_id, message_id=msg_id, has_caption=True, telegram_file_id=tg_fid)
                                                 return resp_json
                            log.warning("Telegram sendVideo returned HTTP %s: %s", resp.status_code, resp.text)
                except Exception as exc:
                    log.warning("Failed sending video directly via Telegram API: %s", exc)
    finally:
        if preview_file_to_clean and preview_file_to_clean.exists():
            try:
                preview_file_to_clean.unlink(missing_ok=True)
            except Exception:
                pass

    # Attempt 2: Fallback to sendMessage with drive preview link and interactive keyboard
    try:
        log.info("Sending clip review card message to Telegram chat %s...", chat_id)
        async with httpx.AsyncClient(timeout=20.0) as client:
            data = {
                "chat_id": chat_id,
                "text": caption_text[:4096],
                "parse_mode": "HTML",
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
                # Retry message as clean plain text without broken HTML tags
                data_plain = dict(data)
                data_plain.pop("parse_mode", None)
                data_plain["text"] = re.sub(r"<[^>]+>", "", caption_text)[:4096]
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


def _reconcile_remote_clip(
    clip_id: str, message: dict[str, Any], bot_token: str | None = None
) -> tuple[models.Clip | None, models.ClipApprovalRecord | None]:
    """Reconcile clip and related database records if missing in local SQLite.

    Handles decoupled workflows where clips are rendered in remote/ephemeral environments
    (e.g. GitHub Actions), uploaded to Google Drive, and delivered to Telegram, while
    approval is performed by the operator via Telegram.
    """
    clip = store.get_clip(clip_id)
    approval = store.get_clip_approval(clip_id)
    if clip and approval:
        return clip, approval

    text = message.get("caption") or message.get("text") or ""
    video = message.get("video") or {}

    # Extract Hook
    hook = ""
    hook_m = re.search(r"(?:🎯\s*\*?Hook:\*?|•\s*Hook:|Hook:)\*?\s*([^\n\r]+)", text, re.IGNORECASE)
    if hook_m:
        hook = hook_m.group(1).strip().strip("*`_")
        hook = re.sub(r"\.{2,}", "", hook).strip()

    # Extract Title (Support • Title:, • *Title:*, Proposed Title:, Title:)
    title = ""
    title_m = re.search(r"(?:•\s*\*?Title:\*?|Proposed Title:|Title:)\*?\s*([^\n\r]+)", text, re.IGNORECASE)
    if title_m:
        title = title_m.group(1).strip().strip("*`_")
    
    # Clean automation tokens, hex clip IDs, and multiple trailing dots from title
    from autoclip.seo.sanitizer import sanitize_public_text
    title = sanitize_public_text(title, is_title=True)
    if not title:
        title = (hook.title() if hook else "Key Insight & Lesson").strip()
    if title.islower():
        title = title.title()

    # Extract Google Drive Link and File ID
    drive_file_id = ""
    drive_link = ""
    drive_m = re.search(r"https://drive\.google\.com/file/d/([A-Za-z0-9_-]+)", text)
    if drive_m:
        drive_file_id = drive_m.group(1)
        drive_link = drive_m.group(0) + "/view"

    # Extract Duration
    duration_s = float(video.get("duration", 0.0))
    if duration_s <= 0.0:
        dur_m = re.search(r"Duration:\*?\s*([\d\.]+)s", text, re.IGNORECASE)
        if dur_m:
            try:
                duration_s = float(dur_m.group(1))
            except Exception:
                duration_s = 24.0
    if duration_s <= 0.0:
        duration_s = 24.0

    # Extract Hashtags / Tags (Support • Tags:, • *Tags:*, • Hashtags:, Hashtags:)
    tags = ["Shorts", "Founders", "Trending"]
    tags_m = re.search(r"(?:•\s*\*?Hashtags:\*?|•\s*\*?Tags:\*?|Hashtags:|Tags:)\*?\s*([^\n\r]+)", text, re.IGNORECASE)
    if tags_m:
        extracted = [t.strip().lstrip("#") for t in tags_m.group(1).split() if t.strip()]
        clean_extracted = [t for t in extracted if t.upper() not in ("ALAMR", "AUTOCLIP", "RECONCILE")]
        if clean_extracted:
            tags = clean_extracted

    # Extract Description / Caption (Support • Description:, • Caption:, Description:)
    desc = hook or title
    desc_m = re.search(r"(?:•\s*\*?Description:\*?|•\s*\*?Desc:\*?|•\s*\*?Caption:\*?|Description:|Caption:)\*?\s*([^\n\r]+)", text, re.IGNORECASE)
    if desc_m:
        desc = desc_m.group(1).strip().strip("*`_")
    desc = sanitize_public_text(desc, is_title=False)

    source_id = "src_remote"
    if not store.get_source(source_id):
        try:
            store.create_source(
                models.Source(
                    id=source_id,
                    type="upload",
                    path="remote",
                    title="Remote Ingestion",
                    created_at=models.utcnow(),
                )
            )
        except Exception:
            pass

    job_id = f"job_{clip_id[:12]}"
    if not store.get_job(job_id):
        job = models.Job(
            id=job_id,
            source_id=source_id,
            status="done",
            current_stage="export",
            progress=1.0,
            provider="github_actions",
            created_at=models.utcnow(),
            updated_at=models.utcnow(),
        )
        try:
            store.create_job(job)
        except Exception:
            pass

    if not clip:
        clip = models.Clip(
            id=clip_id,
            job_id=job_id,
            start_s=0.0,
            end_s=duration_s,
            rank=1,
            start_word=0,
            end_word=0,
            title=title,
            hook=hook,
            score=90.0,
            reason="Reconciled from Telegram review card",
            status="exported",
            created_at=models.utcnow(),
        )
        try:
            store.create_clip(clip)
            log.info("Reconciled missing Clip %s from Telegram card", clip_id)
        except Exception as e:
            log.warning("Could not persist reconciled clip %s: %s", clip_id, e)

    if not approval:
        approval = models.ClipApprovalRecord(
            id=models.new_id(),
            clip_id=clip_id,
            job_id=job_id,
            current_status="PENDING_REVIEW",
            version=1,
            publish_eligible=True,
            blocking_reasons=[],
            telemetry={
                "telegram_message_id": message.get("message_id"),
                "telegram_chat_id": str((message.get("chat") or {}).get("id") or ""),
                "telegram_has_caption": bool(message.get("caption")),
                "telegram_file_id": video.get("file_id"),
                "drive_file_id": drive_file_id,
                "drive_web_view_link": drive_link,
            },
            created_at=models.utcnow(),
            updated_at=models.utcnow(),
        )
        try:
            store.create_clip_approval(approval)
        except Exception as e:
            log.warning("Could not persist reconciled approval %s: %s", clip_id, e)

    # Reconcile ClipMetadataRecord for publishing
    existing_meta = store.get_clip_metadata(clip_id)
    if not existing_meta:
        meta = models.ClipMetadataRecord(
            id=models.new_id(),
            clip_id=clip_id,
            job_id=job_id,
            generated_title=title,
            final_title=title,
            generated_description=desc,
            final_description=desc,
            generated_hashtags=tags,
            final_hashtags=tags,
            compliance_status="SEO_PASS",
            compliance_score=95.0,
            created_at=models.utcnow(),
            updated_at=models.utcnow(),
        )
        try:
            store.create_clip_metadata(meta)
        except Exception as e:
            log.warning("Could not persist reconciled clip metadata %s: %s", clip_id, e)
    else:
        needs_meta_update = False
        if any(b in (existing_meta.final_title or "") for b in ("AL AMR", "Highlight")) or re.search(r"\b[0-9a-f]{8,}\b", existing_meta.final_title or ""):
            existing_meta.final_title = title
            needs_meta_update = True
        if any(b in (existing_meta.final_description or "") for b in ("Archive Backup", "AL AMR")):
            existing_meta.final_description = desc
            needs_meta_update = True
        if any("alamr" in t.lower() for t in (existing_meta.final_hashtags or [])):
            existing_meta.final_hashtags = [t for t in existing_meta.final_hashtags if "alamr" not in t.lower()]
            needs_meta_update = True
        if needs_meta_update:
            try:
                store.create_clip_metadata(existing_meta)
            except Exception as e:
                log.warning("Could not update sanitized clip metadata %s: %s", clip_id, e)

    # Reconcile FinalRenderRecord and Export for publishing media resolution
    if not store.get_final_render(clip_id):
        frender = models.FinalRenderRecord(
            id=models.new_id(),
            job_id=job_id,
            clip_id=clip_id,
            output_path="",
            package_dir="",
            duration=duration_s,
            quality_score=95.0,
            quality_status="RENDER_PASS",
            render_status="completed",
            telemetry={
                "drive_file_id": drive_file_id,
                "drive_web_view_link": drive_link,
                "telegram_file_id": video.get("file_id"),
            },
            created_at=models.utcnow(),
            updated_at=models.utcnow(),
        )
        try:
            store.create_final_render(frender)
        except Exception as e:
            log.warning("Could not persist reconciled final render %s: %s", clip_id, e)

    exports = store.list_exports(clip_id)
    if not exports:
        exp = models.Export(
            id=models.new_id(),
            clip_id=clip_id,
            path="",
            ratio="9:16",
            drive_file_id=drive_file_id,
            drive_web_view_link=drive_link,
            created_at=models.utcnow(),
        )
        try:
            store.create_export(exp)
        except Exception as e:
            log.warning("Could not persist reconciled export %s: %s", clip_id, e)

    return clip, approval


async def handle_telegram_update(update: dict[str, Any]) -> dict[str, Any]:
    """Process incoming Telegram updates, validating authorization and executing actions."""
    bot_token, _, allowed_ids = get_telegram_config()
    if not bot_token:
        return {"status": "error", "message": "Telegram bot token not configured"}

    callback_query = update.get("callback_query")
    if not callback_query:
        return {"status": "ignored", "message": "No callback_query in update"}

    cb_id = callback_query.get("id") or ""
    from_user = callback_query.get("from") or {}
    user_id = from_user.get("id")
    raw_username = from_user.get("username", "")
    username = raw_username or str(user_id)
    cb_data = callback_query.get("data", "")
    message = callback_query.get("message") or {}
    message_id = message.get("message_id")
    chat_id = (
        (message.get("chat") or {}).get("id")
        if isinstance(message.get("chat"), dict)
        else (os.getenv("TELEGRAM_CHAT_ID") or "")
    )
    has_caption = bool(message.get("caption"))
    original_text = message.get("caption") or message.get("text") or ""

    # 1. Operator Authorization Check
    if not is_user_authorized(user_id, allowed_ids, raw_username):
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

    # Reconcile clip & approval records from store or Telegram card
    clip = store.get_clip(clip_id)
    approval = store.get_clip_approval(clip_id)
    if not clip or not approval:
        clip, approval = _reconcile_remote_clip(clip_id, message, bot_token)

    if not clip and not approval:
        await _answer_callback_query(bot_token, cb_id, text=f"Clip {clip_id} not found.", show_alert=True)
        return {"status": "not_found", "clip_id": clip_id}

    # Ensure video file_id from telegram message is preserved in approval / final_render telemetry
    video_obj = message.get("video") or {}
    vid_file_id = video_obj.get("file_id")
    if vid_file_id:
        if approval:
            approval.telemetry = dict(approval.telemetry or {})
            if not approval.telemetry.get("telegram_file_id"):
                approval.telemetry["telegram_file_id"] = vid_file_id
                store.update_clip_approval(approval)
        fr = store.get_final_render(clip_id)
        if fr:
            fr.telemetry = dict(fr.telemetry or {})
            if not fr.telemetry.get("telegram_file_id"):
                fr.telemetry["telegram_file_id"] = vid_file_id
                store.create_final_render(fr)

    job_id = clip.job_id if clip else (approval.job_id if approval else "")
    actor = f"telegram:@{username}" if username else f"telegram:{user_id}"

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

        # Campaign Mandatory Compliance Gate: Block publishing if mandatory rules failed
        clip_meta = store.get_clip_metadata(clip_id)
        if clip_meta and clip_meta.telemetry:
            comp_data = clip_meta.telemetry.get("campaign_compliance")
            if comp_data and comp_data.get("passed") is False:
                violations = comp_data.get("violations", [])
                v_text = ", ".join(violations[:2]) if violations else "Mandatory requirements not met"
                await _answer_callback_query(
                    bot_token,
                    cb_id,
                    text=f"❌ Cannot publish: Campaign compliance failed ({v_text}).",
                    show_alert=True,
                )
                return {"status": "compliance_failed", "clip_id": clip_id, "violations": violations}

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
        tk_adapter = service.get_adapter("tiktok")
        tk_configured = bool(tk_adapter and tk_adapter.is_configured())
        init_platforms = {
            "YouTube Shorts": "⏳ Queued",
            "Instagram Reels": "⏳ Queued",
        }
        if tk_configured:
            init_platforms["TikTok"] = "⏳ Queued"

        initial_status_text = _format_card_content(
            original_text=original_text,
            status_header="⏳ *APPROVED — Publishing started...*",
            platforms_status=init_platforms,
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
            ch_name = (yt_rec.response_metadata or {}).get("channel_title") or (yt_rec.response_metadata or {}).get("channelTitle") or "YouTube"
            platforms_status["YouTube Shorts"] = f"✅ Published ({ch_name})"
        elif yt_rec and yt_rec.error_code == "channel_mismatch":
            platforms_status["YouTube Shorts"] = "❌ Wrong channel — publication blocked"
        elif yt_rec and yt_rec.error_code == "visibility_incorrect":
            platforms_status["YouTube Shorts"] = "❌ Visibility is unlisted — publication not accepted"
        elif yt_rec and (yt_rec.error_code == "processing_incomplete" or yt_rec.status in ("PROCESSING", "UPLOAD_ACCEPTED")):
            platforms_status["YouTube Shorts"] = "⏳ Upload accepted — still processing"
        elif yt_rec and (yt_rec.error_code == "processing_failed" or "processing" in (yt_rec.error_message or "").lower()):
            clean_err = yt_rec.error_message or "Processing failed"
            platforms_status["YouTube Shorts"] = f"❌ Processing failed ({clean_err[:40]})"
        elif yt_rec and yt_rec.error_code == "invalid_media":
            platforms_status["YouTube Shorts"] = "❌ Media file corrupted or inaccessible"
        else:
            err = (yt_rec.error_message if yt_rec else "Upload failed") or "Upload failed"
            clean_err = re.sub(r"^YouTube channel mismatch:\s*", "", err).strip()
            sanitized = _sanitize_error(clean_err)
            if len(sanitized) > 85:
                sanitized = sanitized[:82] + "..."
            platforms_status["YouTube Shorts"] = f"❌ Failed ({sanitized})"

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
        elif ig_rec and ig_rec.error_code == "invalid_media":
            platforms_status["Instagram Reels"] = "❌ Media file corrupted or inaccessible"
        else:
            err = (ig_rec.error_message if ig_rec else "Upload failed") or "Upload failed"
            clean_err = re.sub(r"^Failed to create Instagram Reel container \(\d+\):\s*", "", err).strip()
            clean_err = re.sub(r"^Failed to publish Instagram Reel \(\d+\):\s*", "", clean_err).strip()
            sanitized = _sanitize_error(clean_err)
            if len(sanitized) > 85:
                sanitized = sanitized[:82] + "..."
            platforms_status["Instagram Reels"] = f"❌ Failed ({sanitized})"

        # -------------------------------------------------------------
        # 3. Publish to TikTok (if configured)
        # -------------------------------------------------------------
        tk_adapter = service.get_adapter("tiktok")
        tk_configured = bool(tk_adapter and tk_adapter.is_configured())
        if tk_configured:
            platforms_status["TikTok"] = "⏳ Uploading..."
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
                    reply_markup={"inline_keyboard": [[{"text": "⏳ Uploading to TikTok...", "callback_data": "tg:busy"}]]},
                )

            try:
                tk_rec = await service.publish_clip(
                    job_id=job_id,
                    clip_id=clip_id,
                    platform="tiktok",
                    destination="dest-tiktok-main",
                    dry_run=False,
                )
            except Exception as exc:
                log.exception("Exception during TikTok publication for clip %s: %s", clip_id, exc)
                recs = store.list_publications_for_clip(clip_id)
                tk_candidates = [r for r in recs if r.platform.lower() == "tiktok"]
                if tk_candidates:
                    tk_rec = tk_candidates[0]
                else:
                    tk_rec = models.PublicationRecord(
                        id=models.new_id(),
                        job_id=job_id,
                        clip_id=clip_id,
                        platform="tiktok",
                        destination_id="dest-tiktok-main",
                        status="FAILED_PERMANENT",
                        error_code="execution_error",
                        error_message=str(exc),
                    )
            results["tiktok"] = tk_rec

            if tk_rec and tk_rec.status == "PUBLISHED":
                platforms_status["TikTok"] = "✅ Published"
            elif tk_rec and (tk_rec.error_code == "processing_incomplete" or tk_rec.status in ("PROCESSING", "UPLOAD_ACCEPTED")):
                platforms_status["TikTok"] = "⏳ Upload accepted — still processing"
            elif tk_rec and tk_rec.error_code == "invalid_media":
                platforms_status["TikTok"] = "❌ Media file corrupted or inaccessible"
            else:
                err = (tk_rec.error_message if tk_rec else "Upload failed") or "Upload failed"
                sanitized = _sanitize_error(err)
                if len(sanitized) > 85:
                    sanitized = sanitized[:82] + "..."
                platforms_status["TikTok"] = f"❌ Failed ({sanitized})"

        # -------------------------------------------------------------
        # 4. Compute Final Outcomes & Status
        # -------------------------------------------------------------
        yt_pub = results.get("youtube")
        ig_pub = results.get("instagram")
        tk_pub = results.get("tiktok")
        yt_ok = bool(yt_pub and yt_pub.status == "PUBLISHED")
        ig_ok = bool(ig_pub and ig_pub.status == "PUBLISHED")
        tk_ok = bool(tk_pub and tk_pub.status == "PUBLISHED")

        all_ok = yt_ok and ig_ok and (tk_ok if tk_configured else True)
        any_ok = yt_ok or ig_ok or (tk_ok if tk_configured else False)

        if all_ok:
            final_badge = "✅ *CLIP PUBLISHED*"
            button_label = "✅ Published"
            overall_status = "PUBLISHED"
        elif any_ok:
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
            if yt_ok and ig_ok:
                kb = [[{"text": button_label, "callback_data": "tg:done"}]]
            else:
                kb = [
                    [{"text": "🔄 Retry Publish", "callback_data": f"tg:appr:{clip_id}"}],
                    [{"text": button_label, "callback_data": "tg:done"}],
                ]
            await _safe_edit_telegram_message(
                bot_token=bot_token,
                chat_id=chat_id,
                message_id=message_id,
                text=final_card_text,
                has_caption=has_caption,
                reply_markup={"inline_keyboard": kb},
            )

        # Send follow-up confirmation message
        if bot_token and chat_id:
            if yt_ok and ig_ok:
                yt_url = yt_pub.permalink or (yt_pub.response_metadata or {}).get("url") or ""
                yt_channel = (yt_pub.response_metadata or {}).get("channel_title") or (yt_pub.response_metadata or {}).get("channelTitle") or ""
                ig_url = ig_pub.permalink or (ig_pub.response_metadata or {}).get("permalink") or ""
                yt_line = f'📺 <b>YouTube Shorts:</b> <a href="{html.escape(yt_url)}">Watch on YouTube</a>' if yt_url else "📺 <b>YouTube Shorts:</b> ✅ Published"
                if yt_channel:
                    yt_line += f"\n   <b>Channel:</b> {html.escape(yt_channel)}"
                followup_lines = [
                    "🎉 <b>Clip Successfully Published!</b>",
                    "",
                    f"🎬 <b>Clip ID:</b> <code>{html.escape(clip_id)}</code>",
                    yt_line,
                    f'📸 <b>Instagram Reels:</b> <a href="{html.escape(ig_url)}">Watch on Instagram</a>' if ig_url else "📸 <b>Instagram Reels:</b> ✅ Published",
                ]
            elif yt_ok or ig_ok:
                followup_lines = [
                    "⚠️ <b>Clip Partially Published</b>",
                    "",
                    f"🎬 <b>Clip ID:</b> <code>{html.escape(clip_id)}</code>",
                ]
                if yt_ok:
                    yt_url = yt_pub.permalink or (yt_pub.response_metadata or {}).get("url") or ""
                    yt_channel = (yt_pub.response_metadata or {}).get("channel_title") or (yt_pub.response_metadata or {}).get("channelTitle") or ""
                    yt_line = f'• <b>YouTube Shorts:</b> ✅ <a href="{html.escape(yt_url)}">Watch on YouTube</a>' if yt_url else "• <b>YouTube Shorts:</b> ✅ Published"
                    if yt_channel:
                        yt_line += f" (Channel: {html.escape(yt_channel)})"
                    followup_lines.append(yt_line)
                else:
                    followup_lines.append(f"• <b>YouTube Shorts:</b> {platforms_status['YouTube Shorts']}")

                if ig_ok:
                    ig_url = ig_pub.permalink or (ig_pub.response_metadata or {}).get("permalink") or ""
                    followup_lines.append(f'• <b>Instagram Reels:</b> ✅ <a href="{html.escape(ig_url)}">Watch on Instagram</a>' if ig_url else "• <b>Instagram Reels:</b> ✅ Published")
                else:
                    ig_err = _sanitize_error(ig_pub.error_message if ig_pub else "Upload failed")
                    followup_lines.append(f"• <b>Instagram Reels:</b> ❌ <code>{html.escape(ig_err)}</code>")
            else:
                followup_lines = [
                    "❌ <b>Clip Publishing Failed</b>",
                    "",
                    f"🎬 <b>Clip ID:</b> <code>{html.escape(clip_id)}</code>",
                    f"• <b>YouTube Shorts:</b> {platforms_status['YouTube Shorts']}",
                    f"• <b>Instagram Reels:</b> {platforms_status['Instagram Reels']}",
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
                    text=f"{original_text}\n\n━━━━━━━━━━━━━━━━━━━━\n❌ <b>PUBLISHING FAILED</b>\n\nError: <code>{html.escape(sanitized_fatal)}</code>"[:1020 if has_caption else 4000],
                    has_caption=has_caption,
                    reply_markup={"inline_keyboard": [[{"text": "❌ Publishing Failed", "callback_data": "tg:done"}]]},
                )
            await _safe_send_telegram_message(
                bot_token=bot_token,
                chat_id=chat_id,
                text=f"❌ <b>Auto-Publish Error for Clip</b> <code>{html.escape(clip_id)}</code>:\n\n<code>{html.escape(sanitized_fatal)}</code>\n\nPlease check server logs.",
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


async def poll_telegram_updates(
    bot_token: str | None = None,
    poll_timeout_s: int = 30,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Continuously poll Telegram getUpdates for interactive callbacks.

    Enables full Telegram approval workflows in environments where incoming webhooks
    cannot reach the server (e.g. local development or strict firewall egress).
    """
    if not bot_token:
        bot_token, _, _ = get_telegram_config()
    if not bot_token:
        log.warning("Cannot start Telegram polling: TELEGRAM_BOT_TOKEN not configured.")
        return

    offset = 0
    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/getUpdates"
    log.info("Starting Telegram Bot API interactive update polling loop...")

    async with httpx.AsyncClient(timeout=float(poll_timeout_s + 10)) as client:
        # If a webhook was previously set, delete it so getUpdates succeeds without 409 Conflict
        try:
            del_resp = await client.post(
                f"{TELEGRAM_API_BASE}/bot{bot_token}/deleteWebhook",
                json={"drop_pending_updates": False},
            )
            if del_resp.status_code == 200:
                log.info("Telegram webhook removed/verified for polling mode.")
        except Exception as del_err:
            log.warning("Could not verify deleteWebhook before polling: %s", del_err)

        while stop_event is None or not stop_event.is_set():
            try:
                params: dict[str, Any] = {
                    "offset": offset,
                    "timeout": poll_timeout_s,
                    "allowed_updates": ["callback_query", "message"],
                }
                resp = await client.get(url, params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("ok"):
                        updates = data.get("result", [])
                        for upd in updates:
                            offset = max(offset, upd.get("update_id", 0) + 1)
                            try:
                                await handle_telegram_update(upd)
                            except Exception as upd_err:
                                log.error(
                                    "Error processing Telegram update %s: %s",
                                    upd.get("update_id"),
                                    upd_err,
                                )
                elif resp.status_code in (401, 404):
                    log.error("Telegram polling stopped with HTTP %s: Invalid bot token.", resp.status_code)
                    break
                else:
                    await asyncio.sleep(5.0)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.warning("Exception in Telegram polling loop: %s", exc)
                await asyncio.sleep(5.0)


# Canonical alias for webhook handling
handle_telegram_webhook_payload = handle_telegram_update


async def setup_telegram_bot_lifecycle() -> dict[str, Any] | None:
    """Configure webhook or start update polling based on runtime environment."""
    bot_token, chat_id, _ = get_telegram_config()
    if not bot_token:
        log.info("Telegram review bot not configured (no bot token).")
        return None

    public_url = (
        os.getenv("TELEGRAM_WEBHOOK_URL")
        or os.getenv("RENDER_EXTERNAL_URL")
        or os.getenv("ALAMR_PUBLIC_URL")
        or ""
    ).strip().rstrip("/")

    # Guard against dummy / test domains or empty strings overwriting live Telegram webhook
    DUMMY_DOMAINS = ("al-amr-test", "localhost", "127.0.0.1", "example.com", "test.com")
    if any(dummy in public_url.lower() for dummy in DUMMY_DOMAINS):
        public_url = "https://al-amr-clipping-automation-6d0c.onrender.com"
    elif not public_url:
        public_url = "https://al-amr-clipping-automation-6d0c.onrender.com"

    force_polling = os.getenv("TELEGRAM_POLLING") == "1"

    if public_url and not force_polling:
        webhook_target = f"{public_url}/api/telegram/webhook"
        log.info("Registering Telegram webhook at %s...", webhook_target)
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                webhook_payload: dict[str, Any] = {
                    "url": webhook_target,
                    "allowed_updates": ["callback_query", "message"],
                    "drop_pending_updates": False,
                }
                secret = (os.getenv("TELEGRAM_WEBHOOK_SECRET") or "").strip()
                if not secret:
                    try:
                        from ..security.vault import get_vault
                        v_sec = get_vault().retrieve_secret("telegram_webhook_secret") or get_vault().retrieve_secret("TELEGRAM_WEBHOOK_SECRET")
                        if v_sec:
                            secret = v_sec.strip()
                    except Exception:
                        pass
                if secret:
                    webhook_payload["secret_token"] = secret
                resp = await client.post(
                    f"{TELEGRAM_API_BASE}/bot{bot_token}/setWebhook",
                    json=webhook_payload,
                )
                if resp.status_code == 200 and resp.json().get("ok"):
                    log.info("Successfully registered Telegram webhook with Bot API: %s", webhook_target)
                    return {"mode": "webhook", "url": webhook_target}
                else:
                    log.warning("Telegram setWebhook returned HTTP %s: %s. Falling back to polling.", resp.status_code, resp.text)
        except Exception as exc:
            log.warning("Failed to register Telegram webhook: %s. Falling back to polling.", exc)

    # Polling mode fallback
    log.info("Starting Telegram Bot API update polling loop...")
    polling_task = asyncio.create_task(
        poll_telegram_updates(bot_token=bot_token),
        name="alamr-telegram-polling",
    )
    return {"mode": "polling", "task": polling_task}

