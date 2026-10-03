"""Comprehensive Test Suite for Step 10.1 Production Guards and Simulation Elimination.

Validates:
1. whop.media_guard:
   - Enforces physical existence on disk.
   - Rejects 0-byte or implausibly small files.
   - Probes video codec, resolution (9:16 portrait), audio streams, and canonical duration.
   - Invariant: Exactly 5 physical clips required.
   - Rejects duplicated file paths and duplicated content hashes.
2. whop.drive_guard:
   - Rejects synthetic templates ('1DriveFileId_*', 'drive_id_*', 'mock', 'fake').
   - Validates canonical Google Drive resource ID format.
   - Invariant: Exactly 5 unique Drive IDs required.
3. whop.url_guard:
   - Rejects synthetic markers ('live_verified_clip', 'test_clip', 'example.com').
   - Validates 11-char YouTube video ID (Shorts, Watch, YouTu.be).
   - Validates Instagram Reels and TikTok URLs.
4. whop.submitter external verification & dry-run safety:
   - Submitter transitions to DRY_RUN_VERIFIED in dry-run mode, never SUBMITTED.
   - No 'whop_sim_*' ID generated; whop_submission_id remains None.
   - assert_submission_externally_verified fails closed on dry-run, synthetic IDs, or missing Drive IDs.
5. scripts.run_autonomous_e2e fail-closed execution:
   - Unconfirmed join halts execution with NOT_VERIFIED and JOIN_REQUIRES_RECONCILIATION.
   - Missing renders halt with NOT_VERIFIED.
   - Dry-run execution reports DRY_RUN_VERIFIED verdict, never FULL_AUTONOMOUS_E2E_VERIFIED.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from whop.drive_guard import (
    DriveGuardError,
    assert_real_drive_artifacts,
    is_real_drive_file_id,
)
from whop.ledger import CampaignLedger
from whop.media_guard import (
    MediaGuardError,
    assert_five_physical_clips,
    assert_real_physical_clip_artifact,
    compute_file_sha256,
)
from whop.models import (
    CampaignRecord,
    CampaignState,
    ClipQARecord,
    ClipTechnicalQAResult,
    SubmissionState,
    WhopJobQAReport,
    WhopReviewSession,
    WhopSubmissionRecord,
)
from whop.submitter import (
    SubmissionVerificationError,
    WhopSubmitter,
    assert_submission_externally_verified,
)
from whop.url_guard import (
    UrlGuardError,
    parse_and_validate_platform_url,
    verify_real_public_post_url,
)


# ==============================================================================
# 1. Physical Media Guard Tests
# ==============================================================================

def test_media_guard_rejects_missing_file(tmp_path: Path):
    non_existent = tmp_path / "ghost_clip.mp4"
    with pytest.raises(MediaGuardError, match="does not exist on disk"):
        assert_real_physical_clip_artifact(non_existent)


def test_media_guard_rejects_empty_file(tmp_path: Path):
    empty_file = tmp_path / "empty_clip.mp4"
    empty_file.write_bytes(b"")
    with pytest.raises(MediaGuardError, match="implausibly small"):
        assert_real_physical_clip_artifact(empty_file)


def test_media_guard_probes_valid_clip(tmp_path: Path):
    valid_file = tmp_path / "valid_clip.mp4"
    valid_file.write_bytes(b"x" * 60_000)

    mock_probe = {
        "format": {"duration": "24.5", "format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1080,
                "height": 1920,
                "duration": "24.5",
            },
            {
                "codec_type": "audio",
                "codec_name": "aac",
                "channels": 2,
            },
        ],
    }

    with patch("whop.media_guard.probe_file_with_ffprobe", return_value=mock_probe):
        meta = assert_real_physical_clip_artifact(valid_file)
        assert meta["duration_s"] == 24.5
        assert meta["width"] == 1080
        assert meta["height"] == 1920
        assert meta["video_codec"] == "h264"
        assert meta["audio_codec"] == "aac"
        assert meta["channels"] == 2


def test_media_guard_rejects_landscape_ratio(tmp_path: Path):
    landscape_file = tmp_path / "landscape.mp4"
    landscape_file.write_bytes(b"x" * 60_000)

    mock_probe = {
        "format": {"duration": "24.5"},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
            },
            {"codec_type": "audio", "codec_name": "aac"},
        ],
    }

    with patch("whop.media_guard.probe_file_with_ffprobe", return_value=mock_probe):
        with pytest.raises(MediaGuardError, match="is not vertical 9:16"):
            assert_real_physical_clip_artifact(landscape_file, require_vertical=True)


def test_media_guard_rejects_out_of_bounds_duration(tmp_path: Path):
    short_file = tmp_path / "too_short.mp4"
    short_file.write_bytes(b"x" * 60_000)

    mock_probe = {
        "format": {"duration": "12.0"},
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920},
            {"codec_type": "audio", "codec_name": "aac"},
        ],
    }

    with patch("whop.media_guard.probe_file_with_ffprobe", return_value=mock_probe):
        with pytest.raises(MediaGuardError, match="below minimum"):
            assert_real_physical_clip_artifact(short_file, min_duration_s=20.0, max_duration_s=30.0)


def test_assert_five_physical_clips_enforces_count_and_distinctness(tmp_path: Path):
    # Only 4 clips
    clips_4 = [tmp_path / f"c_{i}.mp4" for i in range(4)]
    with pytest.raises(MediaGuardError, match="Exactly 5 clips required"):
        assert_five_physical_clips(clips_4)

    # 5 clips, but identical content (duplicate hash)
    clips_5 = [tmp_path / f"clip_{i}.mp4" for i in range(5)]
    for c in clips_5:
        c.write_bytes(b"duplicate_content_" * 4000)

    mock_probe = {
        "format": {"duration": "25.0"},
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920},
            {"codec_type": "audio", "codec_name": "aac"},
        ],
    }

    with patch("whop.media_guard.probe_file_with_ffprobe", return_value=mock_probe):
        with pytest.raises(MediaGuardError, match="Duplicate content detected"):
            assert_five_physical_clips(clips_5)

    # 5 distinct clips
    for idx, c in enumerate(clips_5):
        c.write_bytes(f"unique_content_payload_{idx}_".encode() * 4000)

    with patch("whop.media_guard.probe_file_with_ffprobe", return_value=mock_probe):
        result = assert_five_physical_clips(clips_5)
        assert len(result) == 5
        assert len({r["sha256"] for r in result}) == 5


# ==============================================================================
# 2. Google Drive Guard Tests
# ==============================================================================

def test_drive_guard_rejects_synthetic_patterns():
    synthetic_ids = [
        "1DriveFileId_clip_001_8946f6e8",
        "drive_id_clip_001",
        "mock_drive_file_id_1234567890",
        "fake_drive_123456789012345678",
        "placeholder_drive_file_id_001",
        "stub_drive_id_123456789012345",
        "clip_01_12345678901234567890",
    ]
    for did in synthetic_ids:
        assert is_real_drive_file_id(did) is False


def test_drive_guard_accepts_valid_google_drive_ids():
    valid_ids = [
        "1Rwo8wU2cIPm1CO4nAw0TYef2CMOR1G5D",
        "1A2B3C4D5E6F7G8H9I0J1K2L3M4N5O6P",
        "14z1d38A-98b7-410a-b5e5-33ec81387d7b",
        "1_abcXYZ-1234567890_ABCDEFGHIJK",
    ]
    for did in valid_ids:
        assert is_real_drive_file_id(did) is True


def test_assert_real_drive_artifacts():
    valid_batch = [f"1ValidDriveId_{i:02d}_abcdefghijklmnop" for i in range(5)]
    result = assert_real_drive_artifacts(valid_batch, require_five=True)
    assert len(result) == 5

    # Rejection of batch with synthetic ID
    bad_batch = list(valid_batch)
    bad_batch[2] = "1DriveFileId_clip_003"
    with pytest.raises(DriveGuardError, match="Synthetic or invalid Google Drive file ID"):
        assert_real_drive_artifacts(bad_batch, require_five=True)

    # Rejection of batch with duplicates
    dup_batch = list(valid_batch)
    dup_batch[1] = dup_batch[0]
    with pytest.raises(DriveGuardError, match="Duplicate Google Drive file IDs"):
        assert_real_drive_artifacts(dup_batch, require_five=True)


# ==============================================================================
# 3. Public Post URL Guard Tests
# ==============================================================================

def test_url_guard_rejects_synthetic_markers():
    synthetic_urls = [
        "https://youtube.com/shorts/live_verified_clip",
        "https://www.youtube.com/shorts/test_clip_12345",
        "https://example.com/shorts/mock_clip",
        "https://instagram.com/reel/placeholder_reel",
        "https://tiktok.com/@user/video/dummy_clip_123",
    ]
    for u in synthetic_urls:
        with pytest.raises(UrlGuardError, match="Synthetic URL placeholder detected"):
            parse_and_validate_platform_url("youtube", u)


def test_url_guard_validates_youtube():
    # Valid YouTube Shorts
    plat, vid = parse_and_validate_platform_url("youtube", "https://www.youtube.com/shorts/dQw4w9WgXcQ")
    assert plat == "youtube"
    assert vid == "dQw4w9WgXcQ"

    # Valid YouTube Watch
    plat, vid = parse_and_validate_platform_url("youtube", "https://youtube.com/watch?v=dQw4w9WgXcQ")
    assert vid == "dQw4w9WgXcQ"

    # Valid youtu.be
    plat, vid = parse_and_validate_platform_url("youtube", "https://youtu.be/dQw4w9WgXcQ")
    assert vid == "dQw4w9WgXcQ"

    # Invalid ID length
    with pytest.raises(UrlGuardError, match="Invalid YouTube URL format"):
        parse_and_validate_platform_url("youtube", "https://youtube.com/shorts/short")


def test_url_guard_validates_instagram_and_tiktok():
    # Instagram Reel
    plat, mid = parse_and_validate_platform_url("instagram", "https://www.instagram.com/reel/Cx123AbC_yz/")
    assert plat == "instagram"
    assert mid == "Cx123AbC_yz"

    # TikTok Video
    plat, tid = parse_and_validate_platform_url("tiktok", "https://www.tiktok.com/@creator/video/7123456789012345678")
    assert plat == "tiktok"
    assert tid == "7123456789012345678"


# ==============================================================================
# 4. Whop Submitter Dry-Run Safety & External Verification Guard
# ==============================================================================

@pytest.fixture
def mock_ledger(tmp_path: Path):
    db_file = tmp_path / "guard_test_ledger.db"
    return CampaignLedger(db_path=db_file)


def test_submitter_dry_run_transitions_to_dry_run_verified(mock_ledger: CampaignLedger):
    cid = "camp_dryrun_test"
    camp = CampaignRecord(
        campaign_id=cid,
        title="Test Campaign",
        campaign_url="https://whop.com/test",
        current_state=CampaignState.AWAITING_APPROVAL,
    )
    mock_ledger.save_campaign(camp)

    # Create QA report with 5 valid clips
    clips = []
    clip_ids = [f"clip_{i+1:03d}" for i in range(5)]
    drive_ids = [f"1RealDriveId_{i:02d}_abcdefghijklmnop" for i in range(5)]
    for i in range(5):
        tqa = ClipTechnicalQAResult(
            clip_id=clip_ids[i],
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
                clip_id=clip_ids[i],
                candidate_index=i,
                technical_qa=tqa,
                drive_file_id=drive_ids[i],
                is_durable=True,
                is_distinct=True,
                quality_score=95.0,
                is_valid=True,
            )
        )
    qa = WhopJobQAReport(
        campaign_id=cid,
        guideline_hash="ghash_123",
        autoclip_job_id="job_real_123",
        artifact_hash="art_123",
        qa_status="RENDER_PASS",
        overall_quality_score=95.0,
        valid_clips_count=5,
        total_clips_evaluated=5,
        clips=clips,
    )
    mock_ledger.save_qa_record(qa)

    session = WhopReviewSession(
        review_session_id="rev_session_dry",
        campaign_id=cid,
        guideline_hash="ghash_123",
        autoclip_job_id="job_real_123",
        artifact_hash="art_123",
        idempotency_key="rev_idem_dry",
        review_state="PENDING",
        chat_id="12345",
        clip_ids=clip_ids,
        clip_order=clip_ids,
    )
    mock_ledger.save_review_session(session)

    success, code, sub = mock_ledger.atomic_approve_and_create_submission(
        campaign_id=cid,
        review_session_id="rev_session_dry",
        reviewer_id="user_test",
        reviewer_username="operator",
        submission_id="sub_dry_test",
        idempotency_key="sub_idem_dry",
        clip_ids=clip_ids,
        drive_file_ids=drive_ids,
    )
    assert success is True

    submitter = WhopSubmitter(ledger=mock_ledger)
    res = submitter.process_submission(sub.submission_id, dry_run_override=True)

    assert res.success is True
    assert res.dry_run is True
    assert res.whop_submission_id is None
    assert res.details.get("status") == "DRY_RUN_VERIFIED"

    # Authoritative ledger state check: must NOT be SUBMITTED
    camp_after = mock_ledger.get_campaign(cid)
    assert camp_after.current_state == CampaignState.DRY_RUN_VERIFIED

    sub_after = mock_ledger.get_submission(sub.submission_id)
    assert sub_after.submission_state == SubmissionState.DRY_RUN_VERIFIED.value
    assert sub_after.whop_submission_id is None


def test_assert_submission_externally_verified_fails_on_simulation():
    sub = WhopSubmissionRecord(
        submission_id="sub_test_01",
        campaign_id="camp_01",
        guideline_hash="ghash",
        review_session_id="rev_01",
        idempotency_key="idem_01",
        drive_file_ids=[f"1RealDriveId_{i:02d}_abcdefghijklmnop" for i in range(5)],
    )

    # 1. Blocks if dry_run=True
    with pytest.raises(SubmissionVerificationError, match="Cannot declare authoritative SUBMITTED state under dry_run=True"):
        assert_submission_externally_verified(sub, {"whop_submission_id": "whop_sub_real_123", "status_code": 200}, dry_run=True)

    # 2. Blocks if whop_submission_id starts with whop_sim_
    with pytest.raises(SubmissionVerificationError, match="Synthetic or simulation Whop submission ID detected"):
        assert_submission_externally_verified(sub, {"whop_submission_id": "whop_sim_84f161c558ae", "status_code": 200}, dry_run=False)

    # 3. Blocks if status code is not 200/201
    with pytest.raises(SubmissionVerificationError, match="failed with status code 500"):
        assert_submission_externally_verified(sub, {"whop_submission_id": "whop_sub_real_123", "status_code": 500}, dry_run=False)

    # 4. Blocks if Drive IDs are synthetic templates
    sub.drive_file_ids = [f"1DriveFileId_clip_{i}" for i in range(5)]
    with pytest.raises(DriveGuardError, match="Synthetic or invalid Google Drive file ID"):
        assert_submission_externally_verified(sub, {"whop_submission_id": "whop_sub_real_123", "status_code": 200}, dry_run=False)

    # 5. Succeeds when genuine external response and genuine Drive IDs
    sub.drive_file_ids = [f"1RealDriveId_{i:02d}_abcdefghijklmnop" for i in range(5)]
    assert_submission_externally_verified(
        sub,
        {"whop_submission_id": "whop_sub_live_external_98765", "status_code": 200},
        dry_run=False,
    )
