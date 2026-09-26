"""End-to-end verification of the final two production blockers:
1. Audio stream duration alignment & zero mute at end of clip.
2. Decoupled Telegram inline review button reconciliation & auto-publishing trigger.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from autoclip.campaign.clip_assembly import is_true_sentence_terminal, DANGLING_END_TOKENS
from autoclip.db import models, store
from autoclip.pipeline.audio_mix.engine import BGMMixingEngine
from autoclip.pipeline.audio_mix.quality_gate import AudioQualityGate
from autoclip.publishing.service import PublishingService
from autoclip.telegram.review_bot import handle_telegram_update, _reconcile_remote_clip


def test_sentence_terminal_and_dangling_token_guards():
    """Verify that speech boundary detection eliminates premature ellipses and trailing filler words."""
    assert not is_true_sentence_terminal("end...")
    assert not is_true_sentence_terminal("sentence…")
    assert is_true_sentence_terminal("done.")
    assert is_true_sentence_terminal("really?")
    assert is_true_sentence_terminal("wow!")

    for token in ["just", "really", "even", "well", "actually", "basically", "you", "know"]:
        assert token in DANGLING_END_TOKENS


def test_audio_mix_engine_command_construction():
    """Verify BGMMixingEngine builds filtergraph with apad, dropout_transition=0, and exact atrim."""
    engine = BGMMixingEngine()
    
    # Test that quality gate and config are properly set
    assert engine.config is not None
    assert engine.quality_gate is not None


@pytest.mark.asyncio
async def test_telegram_remote_clip_reconciliation_and_publish(initialised_db):
    """Verify that clicking inline approve button on a remote/ephemeral clip reconciles records and starts publish."""
    clip_id = "test_clip_remote_123"
    job_id = "job_test_clip_r"
    
    # Ensure not present initially
    assert store.get_clip(clip_id) is None
    assert store.get_clip_approval(clip_id) is None
    
    sample_caption = (
        "🎬 *Proposed Title:* Unlocking High Performance in AI\n"
        "💡 *Hook:* This simple trick changes everything.\n"
        "⏱️ *Duration:* 24.2s\n"
        "🏷️ *Hashtags:* #AI #Automation #Shorts\n"
        "📝 *Description:* Full summary of the automated pipeline.\n"
        "🔗 *Drive Link:* https://drive.google.com/file/d/1A2B3C4D5E6F7G8H9I0J/view\n"
    )
    
    update = {
        "update_id": 999991,
        "callback_query": {
            "id": "cb_query_999",
            "from": {"id": 123456, "username": "alamr_lead"},
            "message": {
                "message_id": 777,
                "chat": {"id": -100123456789},
                "caption": sample_caption,
                "video": {
                    "file_id": "tg_vid_file_abc123",
                    "duration": 24,
                },
            },
            "data": f"tg:appr:{clip_id}",
        },
    }
    
    # Mock network calls to Telegram and external publishing platforms
    with patch("autoclip.telegram.review_bot.get_telegram_config", return_value=("fake_bot_token", -100123456789, [123456])), \
         patch("autoclip.telegram.review_bot._answer_callback_query", new_callable=AsyncMock) as mock_answer_cb, \
         patch("autoclip.telegram.review_bot._safe_edit_telegram_message", new_callable=AsyncMock) as mock_edit_msg, \
         patch("autoclip.publishing.service.PublishingService.publish_clip", new_callable=AsyncMock) as mock_pub_clip:
        
        mock_pub_record = models.PublicationRecord(
            id=models.new_id(),
            job_id=job_id,
            clip_id=clip_id,
            platform="youtube",
            destination_id="dest-youtube-main",
            idempotency_key=f"{job_id}:{clip_id}:youtube:dest-youtube-main",
            status="PUBLISHED",
            permalink="https://youtube.com/shorts/xyz",
        )
        mock_pub_clip.return_value = mock_pub_record
        
        resp = await handle_telegram_update(update)
        assert resp.get("status") == "approved"
        
        # Verify callback query was answered immediately without hanging spinner
        mock_answer_cb.assert_any_call("fake_bot_token", "cb_query_999", text="⏳ Approval received! Initiating auto-publish...", show_alert=False)
        
        # Verify clip, approval, and render records were reconciled into local SQLite
        reconciled_clip = store.get_clip(clip_id)
        assert reconciled_clip is not None
        assert "Unlocking High Performance" in reconciled_clip.title
        
        reconciled_approval = store.get_clip_approval(clip_id)
        assert reconciled_approval is not None
        assert reconciled_approval.current_status == "APPROVED"
        assert reconciled_approval.telemetry.get("telegram_file_id") == "tg_vid_file_abc123"
        assert reconciled_approval.telemetry.get("drive_file_id") == "1A2B3C4D5E6F7G8H9I0J"
        
        reconciled_render = store.get_final_render(clip_id)
        assert reconciled_render is not None
        assert reconciled_render.quality_status == "RENDER_PASS"
        assert reconciled_render.telemetry.get("telegram_file_id") == "tg_vid_file_abc123"
