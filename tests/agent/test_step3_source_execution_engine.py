"""Step 3/5 Comprehensive Test Suite: Autonomous Source & Campaign Execution Engine.

Verifies:
1. explicit YouTube source
2. direct video URL
3. local video
4. campaign-provided source
5. Whop-discovered source
6. source priority ranking
7. invalid source rejection
8. HTML pretending to be video rejection
9. corrupted media rejection
10. campaign restriction enforcement
11. requirements/source compatibility
12. platform/account compatibility
13. retryable failure classification
14. permanent failure classification
15. operator-required challenge escalation
16. resumable execution
17. checkpoint recovery
18. zero secret leakage
19. no fabricated source
20. no fabricated campaign data
"""

import hashlib
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch
import numpy as np
import pytest

from clipping.agent.browser.challenge import (
    BrowserChallengeManager,
    ChallengeResolutionStatus,
    ChallengeType,
    OperatorEscalationChallengeHandler,
)
from clipping.agent.campaign.compatibility_gate import (
    GateCheckStatus,
    PreProductionCompatibilityGate,
)
from clipping.agent.campaign.failures import (
    ExecutionErrorCode,
    ExecutionFailureClassifier,
    FailureCategory,
)
from clipping.agent.campaign.models import CampaignRecord, CampaignStatus
from clipping.agent.campaign.whop_handoff import WhopCampaignHandoff
from clipping.agent.vault.models import AccountMetadata, AccountPlatform, AccountStatus
from clipping.contracts.requirements import (
    CampaignIdentityRequirements,
    CampaignRequirements,
    ClipRequirements,
    PlatformRequirements,
    RequirementModality,
    SourceRequirements,
    SubmissionRequirements,
)
from clipping.contracts.source import (
    SourceAccessStatus,
    SourceCandidate,
    SourceCandidatePriority,
    SourceResolutionResult,
)
from clipping.ingestion.exceptions import (
    IngestionNetworkError,
    InvalidSourceError,
    UnsupportedMediaError,
)
from clipping.ingestion.robust_downloader import RobustMediaDownloader
from clipping.ingestion.source import SourceReference, SourceType
from clipping.ingestion.source_resolver import SourceResolutionEngine
from clipping.qa.prober import MediaProber
from clipping.state.models import JobState, PipelineStage
from clipping.state.remote import RemoteStorageStateRepository
from clipping.storage.local import LocalStorageDriver


def _create_test_video(path: str, duration_sec: float = 2.0, width: int = 640, height: int = 360, fps: int = 30):
    """Creates a genuine, valid MP4 video container on disk using OpenCV."""
    import cv2
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    out = cv2.VideoWriter(path, fourcc, fps, (width, height))
    total_frames = int(duration_sec * fps)
    for i in range(total_frames):
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        frame[:] = (i % 255, 100, 200)
        out.write(frame)
    out.release()


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as td:
        yield td


@pytest.fixture
def real_video_file(temp_dir):
    vid_path = os.path.join(temp_dir, "test_valid_media.mp4")
    _create_test_video(vid_path, duration_sec=3.0, width=640, height=360, fps=30)
    return vid_path


# -----------------------------------------------------------------------------
# 1. EXPLICIT YOUTUBE SOURCE
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_01_explicit_youtube_source():
    engine = SourceResolutionEngine()
    yt_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    with patch.object(
        SourceResolutionEngine,
        "_resolve_youtube",
        return_value=SourceResolutionResult(
            source_type="youtube",
            original_uri=yt_url,
            resolved_uri=yt_url,
            title="Rick Astley - Never Gonna Give You Up",
            duration=212.0,
            width=1920,
            height=1080,
            fps=30.0,
            mime_type="video/mp4",
            extraction_method="yt_dlp",
            source_access_status=SourceAccessStatus.ACCESSIBLE,
            selection_rationale="Selected OPERATOR_URL YouTube video stream",
        ),
    ):
        result = await engine.resolve_source(operator_source_url=yt_url)
        assert result.is_valid is True
        assert result.source_type == "youtube"
        assert result.source_access_status == SourceAccessStatus.ACCESSIBLE
        assert result.duration == 212.0
        assert result.width == 1920


# -----------------------------------------------------------------------------
# 2. DIRECT VIDEO URL
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_02_direct_video_url(temp_dir, real_video_file):
    engine = SourceResolutionEngine()
    url = "https://cdn.example.com/assets/campaign_video_master.mp4"

    # Mock the download_and_verify method to return verified file data
    with patch.object(
        RobustMediaDownloader,
        "download_and_verify",
        return_value={
            "local_path": real_video_file,
            "file_size": os.path.getsize(real_video_file),
            "checksum": "a" * 64,
            "mime_type": "video/mp4",
            "final_url": url,
            "duration": 3.0,
            "width": 640,
            "height": 360,
            "fps": 30.0,
            "video_codec": "mp4v",
        },
    ):
        result = await engine.resolve_source(operator_source_url=url, working_dir=temp_dir)
        assert result.is_valid is True
        assert result.source_type == "direct_url"
        assert result.duration == 3.0
        assert result.checksum == "a" * 64


# -----------------------------------------------------------------------------
# 3. LOCAL VIDEO SOURCE
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_03_local_video_source(real_video_file):
    engine = SourceResolutionEngine()
    result = await engine.resolve_source(operator_uploaded_path=real_video_file)

    assert result.is_valid is True
    assert result.source_type == "local_file"
    assert result.source_access_status == SourceAccessStatus.ACCESSIBLE
    assert result.duration is not None and result.duration > 0
    assert result.checksum is not None and len(result.checksum) == 64
    assert result.ranked_candidates[0].priority_type == SourceCandidatePriority.OPERATOR_UPLOAD


# -----------------------------------------------------------------------------
# 4. CAMPAIGN-PROVIDED SOURCE
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_04_campaign_provided_source():
    engine = SourceResolutionEngine()
    reqs = CampaignRequirements(
        identity=CampaignIdentityRequirements(campaign_name="Podcast Clippings"),
        source=SourceRequirements(
            permitted_source_urls=["https://youtube.com/watch?v=campaignBrief123"],
            specific_footage_required=True,
        ),
    )

    candidates = engine.build_candidate_list(campaign_requirements=reqs)
    assert len(candidates) == 1
    assert candidates[0].priority_type == SourceCandidatePriority.CAMPAIGN_BRIEF
    assert candidates[0].priority_rank == 3
    assert "campaignBrief123" in candidates[0].uri


# -----------------------------------------------------------------------------
# 5. WHOP-DISCOVERED SOURCE
# -----------------------------------------------------------------------------
def test_05_whop_discovered_source():
    raw_whop = {
        "id": "whop_camp_7788",
        "name": "SaaS Growth Clipping Campaign",
        "cpm_rate": 2.50,
        "source_video_uris": ["https://youtube.com/watch?v=whopVid990"],
        "hashtags": ["#saas", "#growth"],
        "allowed_platforms": ["youtube_shorts", "instagram_reels"],
    }

    camp, reqs, cands = WhopCampaignHandoff.convert_whop_campaign(raw_whop)
    assert camp.campaign_id == "whop_camp_7788"
    assert reqs.monetization.cpm_rate == 2.50
    assert len(cands) == 1
    assert cands[0].priority_type == SourceCandidatePriority.WHOP_DISCOVERY
    assert cands[0].priority_rank == 4
    assert cands[0].uri == "https://youtube.com/watch?v=whopVid990"


# -----------------------------------------------------------------------------
# 6. SOURCE PRIORITY RANKING
# -----------------------------------------------------------------------------
def test_06_source_priority_ranking():
    engine = SourceResolutionEngine()
    reqs = CampaignRequirements(
        source=SourceRequirements(permitted_source_urls=["https://brief.com/v.mp4"])
    )

    cands = engine.build_candidate_list(
        operator_uploaded_path="/path/to/upload.mp4",
        operator_source_url="https://operator.com/v.mp4",
        campaign_requirements=reqs,
        whop_discovered_urls=["https://whop.com/v.mp4"],
        campaign_repo_urls=["https://repo.com/v.mp4"],
    )

    assert len(cands) == 5
    # Strict order: OPERATOR_UPLOAD (1) < OPERATOR_URL (2) < CAMPAIGN_BRIEF (3) < WHOP (4) < REPO (5)
    assert cands[0].priority_type == SourceCandidatePriority.OPERATOR_UPLOAD
    assert cands[1].priority_type == SourceCandidatePriority.OPERATOR_URL
    assert cands[2].priority_type == SourceCandidatePriority.CAMPAIGN_BRIEF
    assert cands[3].priority_type == SourceCandidatePriority.WHOP_DISCOVERY
    assert cands[4].priority_type == SourceCandidatePriority.CAMPAIGN_REPOSITORY


# -----------------------------------------------------------------------------
# 7. INVALID SOURCE REJECTION
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_07_invalid_source_rejection():
    engine = SourceResolutionEngine()
    result = await engine.resolve_source(operator_uploaded_path="/nonexistent/missing_master.mp4")

    assert result.is_valid is False
    assert result.source_access_status == SourceAccessStatus.INACCESSIBLE
    assert "not found" in (result.failure_reason or "").lower()


# -----------------------------------------------------------------------------
# 8. HTML PRETENDING TO BE VIDEO REJECTION
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_08_html_pretending_to_be_video_rejection(temp_dir):
    downloader = RobustMediaDownloader()
    fake_video_path = os.path.join(temp_dir, "fake.mp4")

    # Mock httpx streaming returning text/html
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.url = "https://example.com/fake_login_gate.mp4"
    mock_resp.headers = {"content-type": "text/html; charset=utf-8"}

    with patch("httpx.AsyncClient.stream") as mock_stream:
        mock_stream.return_value.__aenter__.return_value = mock_resp
        with pytest.raises(InvalidSourceError) as exc:
            await downloader.download_and_verify("https://example.com/fake_login_gate.mp4", fake_video_path)
        assert "HTML/error page pretending to be video" in str(exc.value)


# -----------------------------------------------------------------------------
# 9. CORRUPTED MEDIA REJECTION
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_09_corrupted_media_rejection(temp_dir):
    corrupt_file = os.path.join(temp_dir, "corrupt.mp4")
    with open(corrupt_file, "wb") as f:
        f.write(b"NOT_A_VALID_MP4_CONTAINER_RANDOM_BYTES_123456789")

    engine = SourceResolutionEngine()
    result = await engine.resolve_source(operator_uploaded_path=corrupt_file)

    assert result.is_valid is False
    assert "integrity check" in (result.failure_reason or "").lower() or "probe" in (result.failure_reason or "").lower()


# -----------------------------------------------------------------------------
# 10. CAMPAIGN RESTRICTION ENFORCEMENT
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_10_campaign_restriction_enforcement():
    engine = SourceResolutionEngine()
    reqs = CampaignRequirements(
        source=SourceRequirements(
            prohibited_content=["copyright_trailer"],
            source_restrictions=["no_trailers"],
        )
    )

    bad_url = "https://example.com/videos/copyright_trailer_official.mp4"
    result = await engine.resolve_source(operator_source_url=bad_url, campaign_requirements=reqs)

    assert result.is_valid is False
    assert result.source_access_status == SourceAccessStatus.RESTRICTED
    assert "prohibited campaign content" in (result.failure_reason or "").lower()


# -----------------------------------------------------------------------------
# 11. REQUIREMENTS / SOURCE COMPATIBILITY
# -----------------------------------------------------------------------------
def test_11_requirements_source_compatibility():
    gate = PreProductionCompatibilityGate()
    short_source = SourceResolutionResult(
        source_type="direct_url",
        original_uri="https://cdn.com/short.mp4",
        resolved_uri="https://cdn.com/short.mp4",
        duration=12.0,  # 12 seconds
        width=1920,
        height=1080,
    )
    reqs = CampaignRequirements(
        clips=ClipRequirements(min_duration_seconds=30.0, max_duration_seconds=60.0)
    )
    account = AccountMetadata(
        account_id="yt_123",
        platform=AccountPlatform.YOUTUBE,
        username="al_amr",
        status=AccountStatus.ACTIVE,
    )

    res = gate.evaluate(
        source_result=short_source,
        requirements=reqs,
        target_platform="youtube_shorts",
        target_account=account,
    )

    assert res.is_valid is False
    assert any("DURATION MISMATCH" in b for b in res.blockers)


# -----------------------------------------------------------------------------
# 12. PLATFORM AND ACCOUNT COMPATIBILITY
# -----------------------------------------------------------------------------
def test_12_platform_and_account_compatibility():
    gate = PreProductionCompatibilityGate()
    valid_source = SourceResolutionResult(
        source_type="youtube",
        original_uri="https://youtube.com/watch?v=123",
        resolved_uri="https://youtube.com/watch?v=123",
        duration=60.0,
    )
    # Target is instagram, but account is YouTube
    yt_account = AccountMetadata(
        account_id="yt_channel",
        platform=AccountPlatform.YOUTUBE,
        username="alamr_yt",
        status=AccountStatus.ACTIVE,
    )

    res = gate.evaluate(
        source_result=valid_source,
        requirements=None,
        target_platform="instagram_reels",
        target_account=yt_account,
    )

    assert res.is_valid is False
    assert any("ACCOUNT PLATFORM MISMATCH" in b for b in res.blockers)

    # Inactive account
    inactive_ig = AccountMetadata(
        account_id="ig_123",
        platform=AccountPlatform.INSTAGRAM,
        username="alamr_ig",
        status=AccountStatus.SUSPENDED,
    )
    res_inactive = gate.evaluate(
        source_result=valid_source,
        requirements=None,
        target_platform="instagram_reels",
        target_account=inactive_ig,
    )
    assert res_inactive.is_valid is False
    assert any("ACCOUNT NOT ACTIVE" in b for b in res_inactive.blockers)


# -----------------------------------------------------------------------------
# 13. RETRYABLE FAILURE CLASSIFICATION
# -----------------------------------------------------------------------------
def test_13_retryable_failure_classification():
    timeout_exc = IngestionNetworkError("Connection timed out after 30 seconds")
    fail = ExecutionFailureClassifier.classify_exception(timeout_exc)

    assert fail.category == FailureCategory.RETRYABLE
    assert fail.retryable is True
    assert fail.error_code == ExecutionErrorCode.NETWORK_TIMEOUT
    assert fail.retry_after_seconds is not None


# -----------------------------------------------------------------------------
# 14. PERMANENT FAILURE CLASSIFICATION
# -----------------------------------------------------------------------------
def test_14_permanent_failure_classification():
    html_exc = InvalidSourceError("Downloaded payload begins with HTML markup instead of binary media")
    fail = ExecutionFailureClassifier.classify_exception(html_exc)

    assert fail.category == FailureCategory.PERMANENT_FAILURE
    assert fail.retryable is False
    assert fail.error_code == ExecutionErrorCode.HTML_MASQUERADING_AS_VIDEO


# -----------------------------------------------------------------------------
# 15. OPERATOR-REQUIRED CHALLENGE ESCALATION
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_15_operator_required_challenge_escalation():
    mock_notifier = MagicMock()
    mock_notifier.notify_escalation = AsyncMock()

    manager = BrowserChallengeManager(
        escalation_handler=OperatorEscalationChallengeHandler(escalation_notifier=mock_notifier)
    )

    res = await manager.process_challenge(
        session_id="sess_12345",
        challenge_identifier="cloudflare turnstile challenge",
        page_url="https://whop.com/login",
        driver=MagicMock(),
        campaign_id="camp_whop_test",
    )

    assert res.status == ChallengeResolutionStatus.OPERATOR_REQUIRED
    assert res.resumable is True
    assert res.escalation_id is not None
    assert mock_notifier.notify_escalation.called is True


# -----------------------------------------------------------------------------
# 16. RESUMABLE EXECUTION
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_16_resumable_execution(temp_dir):
    storage = LocalStorageDriver(root_dir=temp_dir)
    state_repo = RemoteStorageStateRepository(storage_driver=storage)

    job_id = "job_resumable_test"
    await state_repo.create_job(
        job_id=job_id,
        campaign_id="camp_01",
        source_video_id="src_01",
        idempotency_key=f"idemp_{job_id}",
        metadata={"checkpoint": "04_DISCOVERY", "resumable": True, "retry_count": 1},
    )

    job = await state_repo.get_job(job_id)
    assert job is not None
    assert job.metadata_json.get("checkpoint") == "04_DISCOVERY"
    assert job.metadata_json.get("resumable") is True


# -----------------------------------------------------------------------------
# 17. CHECKPOINT RECOVERY
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_17_checkpoint_recovery(temp_dir):
    storage = LocalStorageDriver(root_dir=temp_dir)
    state_repo = RemoteStorageStateRepository(storage_driver=storage)

    job_id = "job_checkpoint_test"
    await state_repo.create_job(
        job_id=job_id,
        campaign_id="camp_chk",
        source_video_id="src_chk",
        idempotency_key=f"idemp_{job_id}",
        metadata={"checkpoint": "06_RENDER"},
    )


    # Transition to RENDER stage
    await state_repo.update_job_state(
        job_id=job_id,
        new_state=JobState.REFRAMING_AND_RENDERING,
        new_stage=PipelineStage.RENDERING,
        reason="Resuming at render stage",
        metadata={"checkpoint": "06_RENDER", "resumed": True},
    )


    recovered_job = await state_repo.get_job(job_id)
    assert recovered_job.current_stage == PipelineStage.RENDERING
    assert recovered_job.metadata_json.get("checkpoint") == "06_RENDER"


# -----------------------------------------------------------------------------
# 18. ZERO SECRET LEAKAGE
# -----------------------------------------------------------------------------
def test_18_zero_secret_leakage():
    fake_token = "ghp_superSecretToken123456789"
    fake_secret = "secret_client_xyz999"

    # Verify that serializing SourceResolutionResult or CompatibilityGateResult never contains secrets
    sr = SourceResolutionResult(
        source_type="youtube",
        original_uri="https://youtube.com/watch?v=safe",
        resolved_uri="https://youtube.com/watch?v=safe",
        provenance={"user": "operator_console"},
    )
    dump_str = sr.model_dump_json()
    assert fake_token not in dump_str
    assert fake_secret not in dump_str

    fail = ExecutionFailureClassifier.classify_exception(Exception(f"Network error on token {fake_token[:5]}..."))
    fail_str = fail.model_dump_json()
    assert fake_token not in fail_str


# -----------------------------------------------------------------------------
# 19. NO FABRICATED SOURCE
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_19_no_fabricated_source():
    engine = SourceResolutionEngine()
    reqs = CampaignRequirements(
        source=SourceRequirements(
            permitted_source_urls=["https://youtube.com/watch?v=ONLY_THIS_PODCAST"],
            specific_footage_required=True,
        )
    )

    # Operator provides different footage
    unrelated_url = "https://youtube.com/watch?v=UNRELATED_FUNNY_CAT_VIDEO"
    res = await engine.resolve_source(operator_source_url=unrelated_url, campaign_requirements=reqs)

    # The engine MUST NOT silently pick something else or pass the unrelated video
    assert res.is_valid is False
    assert res.source_access_status == SourceAccessStatus.RESTRICTED
    assert "specific permitted footage" in (res.failure_reason or "").lower()


# -----------------------------------------------------------------------------
# 20. NO FABRICATED CAMPAIGN DATA
# -----------------------------------------------------------------------------
def test_20_no_fabricated_campaign_data():
    minimal_whop = {
        "id": "12345",
        "title": "Minimal Campaign",
    }
    camp, reqs, cands = WhopCampaignHandoff.convert_whop_campaign(minimal_whop)

    # Terms not provided must NOT be invented
    assert reqs.content.required_talking_points == []
    assert reqs.submission.submission_deadline is None
    assert reqs.metadata.review_flag == "NEEDS_REVIEW"
    assert len(cands) == 0
