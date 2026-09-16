"""Targeted regression test suite for integration fixes:
1. max_clips resolution and propagation.
2. CandidateDiscoveryEngine target_clip_count resolution for ad-hoc jobs.
3. norm_platform NameError fix in PublishingService.publish_clip.
4. _check_publish_readiness allows Google Drive backup when local file is missing.
5. ClipApprovalRecord instantiation default_factory for id.
6. Telegram review card message construction, Markdown escaping, and Drive link handling.
7. Dispatcher workflow payload includes max_clips.
8. Ad-hoc job compatibility (campaign_spec=None, campaign_brief=None).
"""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

from autoclip.campaign.duration import resolve_max_clips
from autoclip.campaign.candidate_discovery import CandidateDiscoveryEngine
from autoclip.db import models, store
from autoclip.publishing.service import PublishingService
from autoclip.api.jobs import _check_publish_readiness
from autoclip.telegram.review_bot import _escape_md, is_telegram_configured, get_telegram_config
from autoclip.pipeline.transcript import Transcript, Word


def test_1_max_clips_resolution():
    """Verify resolve_max_clips resolves from top-level, nested, and defaults."""
    # Top-level
    assert resolve_max_clips({"max_clips": 5}) == 5
    assert resolve_max_clips({"max_clips": "7"}) == 7

    # Nested clips
    assert resolve_max_clips({"clips": {"max_clips": 5}}) == 5

    # Campaign spec overrides nested
    spec = MagicMock()
    spec.output_count.value = 4
    assert resolve_max_clips({"clips": {"max_clips": 10}}, campaign_spec=spec) == 4

    # Default fallback
    assert resolve_max_clips({}) == 5
    assert resolve_max_clips(None) == 5


def test_2_candidate_discovery_ad_hoc_max_clips():
    """Verify CandidateDiscoveryEngine sets target_clip_count to 5 for ad-hoc jobs."""
    engine = CandidateDiscoveryEngine(
        campaign_spec=None,
        campaign_brief=None,
        job_settings={"clips": {"max_clips": 5}},
    )
    assert engine.target_clip_count == 5

    engine2 = CandidateDiscoveryEngine(
        campaign_spec=None,
        campaign_brief=None,
        job_settings={"max_clips": 8},
    )
    assert engine2.target_clip_count == 8


def test_3_publish_clip_no_norm_platform_name_error():
    """Verify publish_clip defines norm_platform and does not raise NameError."""
    service = PublishingService()

    fake_clip = models.Clip(id="c1", job_id="j1", start_s=0, end_s=25)
    with patch.object(store, "get_clip", return_value=fake_clip), \
         patch.object(service, "get_adapter", return_value=None):
        try:
            # Should raise ValueError for unsupported platform, NOT NameError!
            asyncio.run(service.publish_clip("j1", "c1", "unknown_platform"))
        except ValueError as exc:
            assert "Unsupported publishing platform" in str(exc)
        except NameError as ne:
            raise AssertionError(f"norm_platform NameError was raised: {ne}")


def test_4_check_publish_readiness_allows_drive_backup():
    """Verify _check_publish_readiness does not block when local file is missing but Drive backup exists."""
    clip_id = "test-clip-drive"

    # Final render pointing to a non-existent runner path
    final_render = models.FinalRenderRecord(
        id="fr1",
        job_id="j1",
        clip_id=clip_id,
        output_path="/home/runner/.autoclip/exports/test/final.mp4",
        quality_status="RENDER_PASS",
    )
    # Clip metadata ready
    clip_meta = models.ClipMetadataRecord(
        id="cm1",
        job_id="j1",
        clip_id=clip_id,
        generated_title="Test Title",
        final_title="Test Title",
        generated_description="Test Desc",
        final_description="Test Desc",
        compliance_status="SEO_PASS",
    )
    # Export with drive_file_id
    export_with_drive = models.Export(
        id="exp1",
        clip_id=clip_id,
        path="/home/runner/.autoclip/exports/test/final.mp4",
        drive_file_id="google-drive-file-id-12345",
        drive_web_view_link="https://drive.google.com/file/d/12345/view",
    )

    with patch.object(store, "get_final_render", return_value=final_render),          patch.object(store, "get_clip_metadata", return_value=clip_meta),          patch.object(store, "list_exports", return_value=[export_with_drive]):

        eligible, blocking = _check_publish_readiness(clip_id)
        assert eligible is True, f"Expected eligible=True but got blocking: {blocking}"
        assert len(blocking) == 0


def test_5_check_publish_readiness_blocks_when_no_drive_backup():
    """Verify _check_publish_readiness blocks when local file is missing AND no Drive backup exists."""
    clip_id = "test-clip-no-drive"

    final_render = models.FinalRenderRecord(
        id="fr1",
        job_id="j1",
        clip_id=clip_id,
        output_path="/home/runner/.autoclip/exports/test/final.mp4",
        quality_status="RENDER_PASS",
    )
    clip_meta = models.ClipMetadataRecord(
        id="cm1",
        job_id="j1",
        clip_id=clip_id,
        generated_title="Test Title",
        final_title="Test Title",
        generated_description="Test Desc",
        final_description="Test Desc",
        compliance_status="SEO_PASS",
    )
    # Export WITHOUT drive_file_id
    export_no_drive = models.Export(
        id="exp1",
        clip_id=clip_id,
        path="/home/runner/.autoclip/exports/test/final.mp4",
        drive_file_id=None,
    )

    with patch.object(store, "get_final_render", return_value=final_render), \
         patch.object(store, "get_clip_metadata", return_value=clip_meta), \
         patch.object(store, "list_exports", return_value=[export_no_drive]):

        eligible, blocking = _check_publish_readiness(clip_id)
        assert eligible is False
        assert any("no Google Drive backup found" in b for b in blocking)


def test_6_clip_approval_record_default_id():
    """Verify ClipApprovalRecord instantiates without explicit id without raising TypeError."""
    rec = models.ClipApprovalRecord(
        clip_id="c1",
        job_id="j1",
        current_status="PENDING_REVIEW",
    )
    assert rec.id is not None
    assert len(rec.id) > 0


def test_7_telegram_markdown_escaping():
    """Verify _escape_md escapes markdown characters so Telegram does not fail."""
    text_with_special_chars = "title_with_underscores *bold* [link] `code`"
    escaped = _escape_md(text_with_special_chars)
    assert "\\_" in escaped
    assert "\\*" in escaped
    assert "\\[" in escaped
    assert "\\`" in escaped


def test_8_dispatcher_passes_max_clips():
    """Verify dispatcher includes max_clips in the dispatch payload."""
    import asyncio
    from unittest.mock import AsyncMock
    from autoclip.jobs.dispatcher import dispatch_job_to_github

    job = models.Job(
        id="test-job-disp",
        source_id="s1",
        settings={"clips": {"max_clips": 5, "min_duration_s": 20, "max_duration_s": 30}},
    )
    source = models.Source(id="s1", type="url", path="", url="https://example.com/video.mp4")

    mock_post = AsyncMock(return_value=MagicMock(status_code=204))
    with patch("autoclip.jobs.dispatcher.get_github_token", return_value="fake-pat"), \
         patch("httpx.AsyncClient.post", mock_post), \
         patch("autoclip.db.store.get_job", return_value=None), \
         patch("autoclip.db.store.update_job"):

        asyncio.run(dispatch_job_to_github(job, source))

        assert mock_post.called
        call_kwargs = mock_post.call_args[1]
        payload = call_kwargs["json"]
        inputs = payload["inputs"]
        assert "max_clips" in inputs
        assert inputs["max_clips"] == "5"


if __name__ == "__main__":
    print("Running test_1_max_clips_resolution...")
    test_1_max_clips_resolution()
    print("Running test_2_candidate_discovery_ad_hoc_max_clips...")
    test_2_candidate_discovery_ad_hoc_max_clips()
    print("Running test_3_publish_clip_no_norm_platform_name_error...")
    test_3_publish_clip_no_norm_platform_name_error()
    print("Running test_4_check_publish_readiness_allows_drive_backup...")
    test_4_check_publish_readiness_allows_drive_backup()
    print("Running test_5_check_publish_readiness_blocks_when_no_drive_backup...")
    test_5_check_publish_readiness_blocks_when_no_drive_backup()
    print("Running test_6_clip_approval_record_default_id...")
    test_6_clip_approval_record_default_id()
    print("Running test_7_telegram_markdown_escaping...")
    test_7_telegram_markdown_escaping()
    print("Running test_8_dispatcher_passes_max_clips...")
    test_8_dispatcher_passes_max_clips()
    print("ALL INTEGRATION FIX TESTS PASSED SUCCESSFULLY!")
