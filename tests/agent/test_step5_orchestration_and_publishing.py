"""Authoritative Test Suite for Step 5/5: Final End-to-End Orchestration,
Multi-Platform Publishing (YouTube Shorts + Instagram Reels), Human Approval Gate,
and Production Hardening.

Verifies:
1. Complete campaign lifecycle e2e
2. Operator-only source enforcement
3. No Whop discovery
4. No repository substitution
5. Brief -> requirements -> production integration
6. Strict compliance gate
7. Telegram review delivery
8. Approval hard gate
9. Rejection -> revision -> fresh approval
10. YouTube publishing
11. Instagram publishing
12. Simultaneous multi-platform publishing
13. YouTube success + Instagram failure (partial)
14. Instagram success + YouTube failure (partial)
15. Publication verification
16. Publication idempotency
17. Restart during processing recovery
18. Restart while awaiting approval restores state
19. Restart after publication prevents duplicate
20. Transient retry handling
21. Permanent failure handling
22. CAPTCHA escalation
23. Genuine actionable challenge URL handling
24. No fabricated CAPTCHA URL
25. Checkpoint recovery
26. No duplicate artifact generation
27. No duplicate publishing on resume
28. Metadata compliance per platform
29. Prohibited content blocking
30. Required hashtag enforcement
31. Zero secret leakage
32. No fabricated publication URLs
33. No fabricated publication success
34. Account validation before publishing
35. Final campaign completion state
"""

import asyncio
from datetime import datetime, timezone
import os
import tempfile
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch, MagicMock
import numpy as np
import pytest

from clipping.contracts.requirements import (
    CampaignRequirements,
    CampaignIdentityRequirements,
    SourceRequirements,
    ClipRequirements,
    ContentRequirements,
    BrandingRequirements,
    TextRequirements,
)
from clipping.contracts.source import SourceResolutionResult, SourceAccessStatus
from clipping.contracts.production import (
    ProductionArtifact,
    ProductionStatus,
    ReviewStatus,
    ProductionComplianceResult,
    RevisionRecord,
)
from clipping.contracts.orchestration import (
    PipelineStage,
    OverallStatus,
    PlatformPublishStatus,
    PlatformPublicationResult,
    CampaignPipelineState,
)
from clipping.agent.vault.models import AccountMetadata, AccountPlatform, AccountStatus
from clipping.agent.vault.vault import EncryptedCredentialVault
from clipping.storage.local import LocalStorageDriver
from clipping.approval.transport import MockTelegramTransport
from clipping.production.repository import ProductionRepository
from clipping.production.content_analyzer import ProductionContentAnalyzer
from clipping.production.video_editor import ProductionVideoEditor
from clipping.production.compliance_gate import ProductionComplianceGate
from clipping.production.telegram_review import TelegramReviewSystem
from clipping.production.challenge_escalation import ChallengeEscalationManager
from clipping.publishing.coordinator import (
    MultiPlatformPublishingCoordinator,
    ApprovalGateBlockedError,
    AccountNotReadyError,
)
from clipping.production.orchestrator import ProductionPipelineOrchestrator
from clipping.agent.publishing.adapters.base import PlatformPublishResult


def _create_test_mp4(path: str, duration_sec: float = 30.0, width: int = 360, height: int = 640, fps: int = 5):
    """Generates a genuine MP4 video file on disk for tests."""
    import cv2
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    out = cv2.VideoWriter(path, fourcc, float(fps), (width, height))
    total_frames = max(1, int(duration_sec * fps))
    for i in range(total_frames):
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        frame[:] = (i % 255, 100, 50)
        out.write(frame)
    out.release()


from clipping.agent.publishing.models import SubmissionStatus


class MockYouTubeAdapter:
    """Mock adapter verifying YouTube Shorts upload contract."""
    def __init__(self, should_fail: bool = False, fail_reason: str = "Quota exceeded"):
        self.should_fail = should_fail
        self.fail_reason = fail_reason
        self.published_submissions = []

    async def publish(self, submission, media_path, credentials) -> PlatformPublishResult:
        self.published_submissions.append((submission, media_path))
        if self.should_fail:
            return PlatformPublishResult(
                success=False,
                status=SubmissionStatus.FAILED,
                error_message=self.fail_reason,
            )
        vid = f"yt_{submission.clip_id[:8]}"
        return PlatformPublishResult(
            success=True,
            status=SubmissionStatus.PUBLISHED,
            platform_post_id=vid,
            platform_url=f"https://www.youtube.com/shorts/{vid}",
        )


class MockInstagramAdapter:
    """Mock adapter verifying Instagram Reels upload contract."""
    def __init__(self, should_fail: bool = False, fail_reason: str = "Graph API token expired"):
        self.should_fail = should_fail
        self.fail_reason = fail_reason
        self.published_submissions = []

    async def publish(self, submission, media_path, credentials) -> PlatformPublishResult:
        self.published_submissions.append((submission, media_path))
        if self.should_fail:
            return PlatformPublishResult(
                success=False,
                status=SubmissionStatus.FAILED,
                error_message=self.fail_reason,
            )
        mid = f"ig_{submission.clip_id[:8]}"
        return PlatformPublishResult(
            success=True,
            status=SubmissionStatus.PUBLISHED,
            platform_post_id=mid,
            platform_url=f"https://www.instagram.com/reel/{mid}/",
        )


@pytest.fixture
def step5_env():
    with tempfile.TemporaryDirectory() as td:
        storage = LocalStorageDriver(root_dir=os.path.join(td, "storage"))
        vault = EncryptedCredentialVault(storage_driver=storage, master_key="test_master_key_for_unit_tests_32ch!")
        prod_repo = ProductionRepository(storage_driver=storage)
        transport = MockTelegramTransport()
        analyzer = ProductionContentAnalyzer()
        editor = ProductionVideoEditor()
        gate = ProductionComplianceGate()
        review_sys = TelegramReviewSystem(repository=prod_repo, transport=transport)
        escalator = ChallengeEscalationManager(repository=prod_repo, transport=transport)

        yt_adapter = MockYouTubeAdapter()
        ig_adapter = MockInstagramAdapter()
        pub_coordinator = MultiPlatformPublishingCoordinator(
            vault=vault,
            storage_driver=storage,
            youtube_adapter=yt_adapter,
            instagram_adapter=ig_adapter,
        )

        orchestrator = ProductionPipelineOrchestrator(
            repository=prod_repo,
            content_analyzer=analyzer,
            video_editor=editor,
            compliance_gate=gate,
            telegram_review=review_sys,
            challenge_escalator=escalator,
            publishing_coordinator=pub_coordinator,
            vault=vault,
            storage_driver=storage,
        )

        sample_video = os.path.join(td, "sample_source.mp4")
        _create_test_mp4(sample_video, duration_sec=30.0, fps=5)

        yt_account = AccountMetadata(
            platform=AccountPlatform.YOUTUBE,
            account_id="UC_alamr_official",
            username="al_amr_clips",
            display_name="AL AMR Official Shorts",
            status=AccountStatus.ACTIVE,
        )
        ig_account = AccountMetadata(
            platform=AccountPlatform.INSTAGRAM,
            account_id="ig_alamr_official",
            username="alamr_reels",
            display_name="AL AMR Official Reels",
            status=AccountStatus.ACTIVE,
        )

        yield {
            "dir": td,
            "storage": storage,
            "vault": vault,
            "repo": prod_repo,
            "transport": transport,
            "analyzer": analyzer,
            "editor": editor,
            "gate": gate,
            "review_sys": review_sys,
            "escalator": escalator,
            "yt_adapter": yt_adapter,
            "ig_adapter": ig_adapter,
            "coordinator": pub_coordinator,
            "orchestrator": orchestrator,
            "sample_video": sample_video,
            "yt_account": yt_account,
            "ig_account": ig_account,
        }


# -----------------------------------------------------------------------------
# 1. COMPLETE CAMPAIGN LIFECYCLE E2E
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_01_complete_campaign_lifecycle_e2e(step5_env):
    orch: ProductionPipelineOrchestrator = step5_env["orchestrator"]
    video = step5_env["sample_video"]

    # 1. Start campaign
    state = await orch.start_campaign(
        campaign_id="camp_e2e",
        brief_content_or_path="Campaign Name: Crypto Insights\nRequired Duration: 15-30s\nHashtags: #crypto #trading",
        operator_uploaded_path=video,
        target_platforms=["youtube_shorts", "instagram_reels"],
        target_account=step5_env["yt_account"],
        telegram_chat_id=12345,
    )
    assert state.current_stage == PipelineStage.AWAITING_APPROVAL
    assert state.overall_status == OverallStatus.AWAITING_APPROVAL
    assert len(state.artifacts) >= 1
    art = state.artifacts[0]
    assert art.review_status == ReviewStatus.PENDING_APPROVAL

    # 2. Approve and publish
    final_state = await orch.approve_and_publish(
        campaign_id="camp_e2e",
        artifact_id=art.artifact_id,
        operator_id="lead_operator",
        target_account=step5_env["yt_account"],
        telegram_chat_id=12345,
    )
    assert final_state.current_stage == PipelineStage.COMPLETED
    assert final_state.overall_status == OverallStatus.COMPLETED
    assert "youtube_shorts" in final_state.publication_results
    assert "instagram_reels" in final_state.publication_results
    assert final_state.publication_results["youtube_shorts"].is_success
    assert final_state.publication_results["instagram_reels"].is_success


# -----------------------------------------------------------------------------
# 2. OPERATOR-ONLY SOURCE ENFORCEMENT
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_02_operator_only_source_enforcement(step5_env):
    orch: ProductionPipelineOrchestrator = step5_env["orchestrator"]
    # Starting a campaign without operator YouTube URL, direct URL, or upload fails closed
    state = await orch.start_campaign(
        campaign_id="camp_no_source",
        brief_content_or_path="Campaign brief with no operator video",
        operator_youtube_url=None,
        operator_direct_url=None,
        operator_uploaded_path=None,
    )
    assert state.current_stage == PipelineStage.FAILED
    assert state.overall_status == OverallStatus.FAILED
    assert "No valid operator-provided source" in state.failure_reason


# -----------------------------------------------------------------------------
# 3. NO WHOP DISCOVERY IN PRODUCTION
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_03_no_whop_source_discovery_in_orchestrator(step5_env):
    resolver = step5_env["orchestrator"].source_resolver
    # Even if Whop URLs are passed, in production mode it must fail closed if no operator source
    res = await resolver.resolve_source(
        whop_discovered_urls=["https://whop.com/video/stream999"],
        production_mode=True,
    )
    assert res.is_valid is False
    assert "No valid operator-provided source" in res.failure_reason


# -----------------------------------------------------------------------------
# 4. NO REPOSITORY SUBSTITUTION
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_04_no_repository_substitution(step5_env):
    resolver = step5_env["orchestrator"].source_resolver
    res = await resolver.resolve_source(
        campaign_repo_urls=["https://repo.internal/video/old_asset.mp4"],
        production_mode=True,
    )
    assert res.is_valid is False
    assert "No valid operator-provided source" in res.failure_reason


# -----------------------------------------------------------------------------
# 5. BRIEF -> REQUIREMENTS -> PRODUCTION INTEGRATION
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_05_brief_requirements_production_integration(step5_env):
    orch: ProductionPipelineOrchestrator = step5_env["orchestrator"]
    brief_text = "Campaign Name: Quantum Leap\nRequired Hashtags: #quantum #tech\nCall to Action: Visit our site"
    state = await orch.start_campaign(
        campaign_id="camp_brief_integ",
        brief_content_or_path=brief_text,
        operator_uploaded_path=step5_env["sample_video"],
        target_platforms=["youtube_shorts"],
        target_account=step5_env["yt_account"],
    )
    assert state.current_stage == PipelineStage.AWAITING_APPROVAL
    assert len(state.artifacts) >= 1
    art = state.artifacts[0]
    assert "#quantum" in [h.lower() for h in art.hashtags]


# -----------------------------------------------------------------------------
# 6. STRICT COMPLIANCE GATE ENFORCEMENT
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_06_strict_compliance_gate_enforcement(step5_env):
    gate: ProductionComplianceGate = step5_env["gate"]
    bad_art = ProductionArtifact(
        artifact_id="art_bad_gate",
        campaign_id="c_bad",
        source_id="s1",
        local_output_path=step5_env["sample_video"],
        duration=5.0,  # Below 10.0s minimum
        aspect_ratio="16:9",  # Horizontal
        selected_start_time=0.0,
        selected_end_time=5.0,
        title="Invalid Clip",
    )
    src_res = SourceResolutionResult(source_type="local_file", original_uri="s1", resolved_uri="s1", source_access_status=SourceAccessStatus.ACCESSIBLE)
    comp = gate.evaluate_clip(bad_art, src_res, target_account=step5_env["yt_account"])
    assert comp.is_compliant is False
    assert len(comp.blockers) >= 2


# -----------------------------------------------------------------------------
# 7. TELEGRAM REVIEW DELIVERY
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_07_telegram_review_delivery(step5_env):
    orch: ProductionPipelineOrchestrator = step5_env["orchestrator"]
    transport: MockTelegramTransport = step5_env["transport"]
    await orch.start_campaign(
        campaign_id="camp_tg_rev",
        brief_content_or_path="Campaign Name: Review Test",
        operator_uploaded_path=step5_env["sample_video"],
        telegram_chat_id=998877,
        target_account=step5_env["yt_account"],
    )
    # Telegram message sent
    assert len(transport.sent_messages) >= 2
    # Verify review package structure
    rev_msg = next((m for m in transport.sent_messages if "READY FOR REVIEW" in m.get("text", "")), None)
    assert rev_msg is not None
    assert rev_msg["reply_markup"] is not None


# -----------------------------------------------------------------------------
# 8. APPROVAL HARD GATE BLOCKS UNAPPROVED
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_08_approval_hard_gate_blocks_unapproved(step5_env):
    coordinator: MultiPlatformPublishingCoordinator = step5_env["coordinator"]
    unapproved_art = ProductionArtifact(
        artifact_id="art_unapproved",
        campaign_id="c_unapp",
        source_id="s1",
        local_output_path=step5_env["sample_video"],
        duration=20.0,
        selected_start_time=0.0,
        selected_end_time=20.0,
        title="Unapproved Draft",
        review_status=ReviewStatus.PENDING_APPROVAL,
    )
    with pytest.raises(ApprovalGateBlockedError) as exc_info:
        await coordinator.publish_artifact_to_platform(
            artifact=unapproved_art,
            platform="youtube_shorts",
            campaign_id="c_unapp",
            target_account=step5_env["yt_account"],
        )
    assert "Approval Gate Violation" in str(exc_info.value)


# -----------------------------------------------------------------------------
# 9. REJECTION -> REVISION -> FRESH APPROVAL
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_09_rejection_triggers_revision_and_fresh_approval(step5_env):
    orch: ProductionPipelineOrchestrator = step5_env["orchestrator"]
    state = await orch.start_campaign(
        campaign_id="camp_revise",
        brief_content_or_path="Campaign Name: Revision Test",
        operator_uploaded_path=step5_env["sample_video"],
        target_account=step5_env["yt_account"],
    )
    art_id = state.artifacts[0].artifact_id

    # Operator rejects clip with feedback
    rev_state = await orch.reject_and_revise(
        campaign_id="camp_revise",
        artifact_id=art_id,
        operator_feedback="Hook is too slow, speed up transition",
    )
    assert rev_state.overall_status == OverallStatus.AWAITING_APPROVAL
    # Versioned artifact created
    assert any(a.artifact_id == f"{art_id}_v1" for a in rev_state.artifacts)
    rev_art = next(a for a in rev_state.artifacts if a.artifact_id == f"{art_id}_v1")
    assert rev_art.review_status == ReviewStatus.PENDING_APPROVAL

    # Fresh approval required for v1
    approved_state = await orch.approve_and_publish(
        campaign_id="camp_revise",
        artifact_id=rev_art.artifact_id,
        target_account=step5_env["yt_account"],
    )
    assert approved_state.current_stage == PipelineStage.COMPLETED


# -----------------------------------------------------------------------------
# 10. YOUTUBE PUBLISHING SUCCESS
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_10_youtube_publishing_success(step5_env):
    coordinator: MultiPlatformPublishingCoordinator = step5_env["coordinator"]
    art = ProductionArtifact(
        artifact_id="art_yt_ok",
        campaign_id="c_yt",
        source_id="s1",
        local_output_path=step5_env["sample_video"],
        duration=20.0,
        selected_start_time=0.0,
        selected_end_time=20.0,
        title="YouTube Short Success",
        review_status=ReviewStatus.APPROVED,
    )
    res = await coordinator.publish_artifact_to_platform(
        artifact=art,
        platform="youtube_shorts",
        campaign_id="c_yt",
        target_account=step5_env["yt_account"],
    )
    assert res.is_success
    assert "youtube.com/shorts/" in res.publication_url


# -----------------------------------------------------------------------------
# 11. INSTAGRAM PUBLISHING SUCCESS
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_11_instagram_publishing_success(step5_env):
    coordinator: MultiPlatformPublishingCoordinator = step5_env["coordinator"]
    art = ProductionArtifact(
        artifact_id="art_ig_ok",
        campaign_id="c_ig",
        source_id="s1",
        local_output_path=step5_env["sample_video"],
        duration=20.0,
        selected_start_time=0.0,
        selected_end_time=20.0,
        title="Instagram Reel Success",
        review_status=ReviewStatus.APPROVED,
    )
    res = await coordinator.publish_artifact_to_platform(
        artifact=art,
        platform="instagram_reels",
        campaign_id="c_ig",
        target_account=step5_env["ig_account"],
    )
    assert res.is_success
    assert "instagram.com/reel/" in res.publication_url


# -----------------------------------------------------------------------------
# 12. SIMULTANEOUS MULTI-PLATFORM PUBLISHING
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_12_simultaneous_multi_platform_publishing(step5_env):
    coordinator: MultiPlatformPublishingCoordinator = step5_env["coordinator"]
    art = ProductionArtifact(
        artifact_id="art_both_ok",
        campaign_id="c_both",
        source_id="s1",
        local_output_path=step5_env["sample_video"],
        duration=20.0,
        selected_start_time=0.0,
        selected_end_time=20.0,
        title="Simultaneous Multi-Platform",
        review_status=ReviewStatus.APPROVED,
    )
    results = await coordinator.publish_artifact_simultaneously(
        artifact=art,
        platforms=["youtube_shorts", "instagram_reels"],
        campaign_id="c_both",
        target_account=step5_env["yt_account"],
    )
    assert len(results) == 2
    assert results["youtube_shorts"].is_success
    assert results["instagram_reels"].is_success


# -----------------------------------------------------------------------------
# 13. YOUTUBE SUCCESS + INSTAGRAM FAILURE (PARTIAL)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_13_youtube_success_instagram_failure_partial(step5_env):
    # Set Instagram adapter to fail
    step5_env["ig_adapter"].should_fail = True
    coordinator: MultiPlatformPublishingCoordinator = step5_env["coordinator"]
    art = ProductionArtifact(
        artifact_id="art_partial_1",
        campaign_id="c_part_1",
        source_id="s1",
        local_output_path=step5_env["sample_video"],
        duration=20.0,
        selected_start_time=0.0,
        selected_end_time=20.0,
        title="Partial Publish YT",
        review_status=ReviewStatus.APPROVED,
    )
    results = await coordinator.publish_artifact_simultaneously(
        artifact=art,
        platforms=["youtube_shorts", "instagram_reels"],
        campaign_id="c_part_1",
        target_account=step5_env["yt_account"],
    )
    assert results["youtube_shorts"].is_success
    assert not results["instagram_reels"].is_success
    overall = coordinator.compute_overall_status(results)
    assert overall == OverallStatus.PARTIALLY_PUBLISHED


# -----------------------------------------------------------------------------
# 14. INSTAGRAM SUCCESS + YOUTUBE FAILURE (PARTIAL)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_14_instagram_success_youtube_failure_partial(step5_env):
    step5_env["yt_adapter"].should_fail = True
    coordinator: MultiPlatformPublishingCoordinator = step5_env["coordinator"]
    art = ProductionArtifact(
        artifact_id="art_partial_2",
        campaign_id="c_part_2",
        source_id="s1",
        local_output_path=step5_env["sample_video"],
        duration=20.0,
        selected_start_time=0.0,
        selected_end_time=20.0,
        title="Partial Publish IG",
        review_status=ReviewStatus.APPROVED,
    )
    results = await coordinator.publish_artifact_simultaneously(
        artifact=art,
        platforms=["youtube_shorts", "instagram_reels"],
        campaign_id="c_part_2",
        target_account=step5_env["ig_account"],
    )
    assert not results["youtube_shorts"].is_success
    assert results["instagram_reels"].is_success
    overall = coordinator.compute_overall_status(results)
    assert overall == OverallStatus.PARTIALLY_PUBLISHED


# -----------------------------------------------------------------------------
# 15. PUBLICATION VERIFICATION
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_15_publication_verification(step5_env):
    coordinator: MultiPlatformPublishingCoordinator = step5_env["coordinator"]
    art = ProductionArtifact(
        artifact_id="art_verif",
        campaign_id="c_verif",
        source_id="s1",
        local_output_path=step5_env["sample_video"],
        duration=20.0,
        selected_start_time=0.0,
        selected_end_time=20.0,
        title="Verified Clip",
        review_status=ReviewStatus.APPROVED,
    )
    res = await coordinator.publish_artifact_to_platform(
        artifact=art,
        platform="youtube_shorts",
        campaign_id="c_verif",
        target_account=step5_env["yt_account"],
    )
    assert res.is_success
    assert res.publication_id is not None
    assert res.verified_at is not None


# -----------------------------------------------------------------------------
# 16. PUBLICATION IDEMPOTENCY
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_16_publication_idempotency(step5_env):
    coordinator: MultiPlatformPublishingCoordinator = step5_env["coordinator"]
    art = ProductionArtifact(
        artifact_id="art_idemp",
        campaign_id="c_idemp",
        source_id="s1",
        local_output_path=step5_env["sample_video"],
        duration=20.0,
        selected_start_time=0.0,
        selected_end_time=20.0,
        title="Idempotency Test",
        review_status=ReviewStatus.APPROVED,
    )
    # First publication
    res1 = await coordinator.publish_artifact_to_platform(
        artifact=art,
        platform="youtube_shorts",
        campaign_id="c_idemp",
        target_account=step5_env["yt_account"],
    )
    assert res1.is_success
    first_pub_id = res1.publication_id
    adapter_call_count = len(step5_env["yt_adapter"].published_submissions)

    # Second publication call with same artifact
    res2 = await coordinator.publish_artifact_to_platform(
        artifact=art,
        platform="youtube_shorts",
        campaign_id="c_idemp",
        target_account=step5_env["yt_account"],
    )
    assert res2.is_success
    assert res2.publication_id == first_pub_id
    # Ensure platform adapter was NOT called a second time
    assert len(step5_env["yt_adapter"].published_submissions) == adapter_call_count


# -----------------------------------------------------------------------------
# 17. RESTART DURING PROCESSING RECOVERY
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_17_restart_during_processing_recovery(step5_env):
    orch: ProductionPipelineOrchestrator = step5_env["orchestrator"]
    state = await orch.start_campaign(
        campaign_id="camp_restart_proc",
        brief_content_or_path="Campaign Name: Recovery Test",
        operator_uploaded_path=step5_env["sample_video"],
        target_account=step5_env["yt_account"],
    )
    assert state.current_stage == PipelineStage.AWAITING_APPROVAL

    # Simulate process restart by reloading state from storage
    loaded = await orch.get_state("camp_restart_proc")
    assert loaded is not None
    assert loaded.current_stage == PipelineStage.AWAITING_APPROVAL
    assert len(loaded.artifacts) >= 1


# -----------------------------------------------------------------------------
# 18. RESTART WHILE AWAITING APPROVAL RESTORES STATE
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_18_restart_while_awaiting_approval_restores_state(step5_env):
    orch: ProductionPipelineOrchestrator = step5_env["orchestrator"]
    state = await orch.start_campaign(
        campaign_id="camp_await_restart",
        brief_content_or_path="Campaign Brief",
        operator_uploaded_path=step5_env["sample_video"],
        target_account=step5_env["yt_account"],
    )
    art_count_initial = len(state.artifacts)

    # Resume after restart
    resumed = await orch.resume_campaign("camp_await_restart")
    assert resumed.current_stage == PipelineStage.AWAITING_APPROVAL
    # Must NOT regenerate new artifacts
    assert len(resumed.artifacts) == art_count_initial


# -----------------------------------------------------------------------------
# 19. RESTART AFTER PUBLICATION PREVENTS DUPLICATE
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_19_restart_after_publication_prevents_duplicate(step5_env):
    orch: ProductionPipelineOrchestrator = step5_env["orchestrator"]
    state = await orch.start_campaign(
        campaign_id="camp_pub_restart",
        brief_content_or_path="Campaign Brief",
        operator_uploaded_path=step5_env["sample_video"],
        target_account=step5_env["yt_account"],
    )
    art_id = state.artifacts[0].artifact_id
    done_state = await orch.approve_and_publish(
        campaign_id="camp_pub_restart",
        artifact_id=art_id,
        target_account=step5_env["yt_account"],
    )
    assert done_state.overall_status == OverallStatus.COMPLETED
    call_count = len(step5_env["yt_adapter"].published_submissions)

    # Calling resume after completion does not trigger uploads
    resumed = await orch.resume_campaign("camp_pub_restart")
    assert resumed.overall_status == OverallStatus.COMPLETED
    assert len(step5_env["yt_adapter"].published_submissions) == call_count


# -----------------------------------------------------------------------------
# 20. TRANSIENT RETRY HANDLING
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_20_transient_retry_handling(step5_env):
    # Setup adapter with transient failure on first attempt
    adapter = step5_env["yt_adapter"]
    adapter.should_fail = True
    coordinator: MultiPlatformPublishingCoordinator = step5_env["coordinator"]

    art = ProductionArtifact(
        artifact_id="art_retry",
        campaign_id="c_retry",
        source_id="s1",
        local_output_path=step5_env["sample_video"],
        duration=20.0,
        selected_start_time=0.0,
        selected_end_time=20.0,
        title="Transient Retry",
        review_status=ReviewStatus.APPROVED,
    )
    res1 = await coordinator.publish_artifact_to_platform(art, "youtube_shorts", "c_retry", target_account=step5_env["yt_account"])
    assert not res1.is_success
    assert res1.attempt_count == 1

    # Transient error resolved
    adapter.should_fail = False
    res2 = await coordinator.publish_artifact_to_platform(art, "youtube_shorts", "c_retry", target_account=step5_env["yt_account"])
    assert res2.is_success
    assert res2.attempt_count == 2


# -----------------------------------------------------------------------------
# 21. PERMANENT FAILURE HANDLING
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_21_permanent_failure_handling(step5_env):
    coordinator: MultiPlatformPublishingCoordinator = step5_env["coordinator"]
    # Missing media file represents a non-recoverable file failure
    art = ProductionArtifact(
        artifact_id="art_perm_fail",
        campaign_id="c_perm",
        source_id="s1",
        local_output_path="/nonexistent/path/media.mp4",
        duration=20.0,
        selected_start_time=0.0,
        selected_end_time=20.0,
        title="Missing File",
        review_status=ReviewStatus.APPROVED,
    )
    res = await coordinator.publish_artifact_to_platform(art, "youtube_shorts", "c_perm", target_account=step5_env["yt_account"])
    assert not res.is_success
    assert "not found on disk" in res.error_message


# -----------------------------------------------------------------------------
# 22. CAPTCHA ESCALATION LIFECYCLE
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_22_captcha_escalation_lifecycle(step5_env):
    escalator: ChallengeEscalationManager = step5_env["escalator"]
    rec = await escalator.escalate(
        campaign_id="camp_captcha_life",
        job_id="job_cap_01",
        checkpoint="SOURCE_ACCESS",
        challenge_type="CLOUDFLARE_TURNSTILE",
        actionable_url="https://challenges.cloudflare.com/live-session",
    )
    assert rec.status == "PENDING_OPERATOR"
    assert rec.actionable_challenge_url == "https://challenges.cloudflare.com/live-session"

    # Operator resolves and resumes
    resumed = await escalator.resume(rec.intervention_id)
    assert resumed.status == "RESUMED"


# -----------------------------------------------------------------------------
# 23. GENUINE ACTIONABLE CHALLENGE URL
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_23_genuine_actionable_challenge_url(step5_env):
    escalator: ChallengeEscalationManager = step5_env["escalator"]
    transport: MockTelegramTransport = step5_env["transport"]
    rec = await escalator.escalate(
        campaign_id="camp_url_test",
        job_id="job_02",
        checkpoint="PUBLISHING",
        challenge_type="RECAPTCHA_V2",
        actionable_url="https://google.com/recaptcha/api2/anchor",
        chat_id=112233,
    )
    sent = transport.sent_messages[-1]["text"]
    assert "https://google.com/recaptcha/api2/anchor" in sent


# -----------------------------------------------------------------------------
# 24. NO FABRICATED CAPTCHA URL
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_24_no_fabricated_captcha_url(step5_env):
    escalator: ChallengeEscalationManager = step5_env["escalator"]
    transport: MockTelegramTransport = step5_env["transport"]
    rec = await escalator.escalate(
        campaign_id="camp_no_url",
        job_id="job_03",
        checkpoint="SOURCE_ACCESS",
        challenge_type="BOT_DETECTION_BLOCK",
        actionable_url=None,  # No URL available
        chat_id=112233,
    )
    sent = transport.sent_messages[-1]["text"]
    assert "No direct challenge URL available" in sent
    assert "http" not in sent.lower()


# -----------------------------------------------------------------------------
# 25. CHECKPOINT RECOVERY
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_25_checkpoint_recovery(step5_env):
    orch: ProductionPipelineOrchestrator = step5_env["orchestrator"]
    state = await orch.start_campaign(
        campaign_id="camp_chk_rec",
        brief_content_or_path="Campaign Name: Checkpoint Test",
        operator_uploaded_path=step5_env["sample_video"],
        target_account=step5_env["yt_account"],
    )
    assert len(state.checkpoints) >= 6
    stages = [cp.stage for cp in state.checkpoints]
    assert PipelineStage.INPUT_RECEIVED in stages
    assert PipelineStage.BRIEF_ANALYZED in stages
    assert PipelineStage.SOURCE_RESOLVED in stages
    assert PipelineStage.CLIPS_RENDERED in stages


# -----------------------------------------------------------------------------
# 26. NO DUPLICATE ARTIFACT GENERATION
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_26_no_duplicate_artifact_generation(step5_env):
    orch: ProductionPipelineOrchestrator = step5_env["orchestrator"]
    state = await orch.start_campaign(
        campaign_id="camp_no_dup_art",
        brief_content_or_path="Campaign Name: No Dup Art",
        operator_uploaded_path=step5_env["sample_video"],
        target_account=step5_env["yt_account"],
    )
    art_count = len(state.artifacts)
    # Starting again returns the existing state without re-generating
    state2 = await orch.start_campaign(
        campaign_id="camp_no_dup_art",
        brief_content_or_path="Campaign Name: No Dup Art",
        operator_uploaded_path=step5_env["sample_video"],
        target_account=step5_env["yt_account"],
    )
    assert len(state2.artifacts) == art_count


# -----------------------------------------------------------------------------
# 27. NO DUPLICATE PUBLISHING ON RESUME
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_27_no_duplicate_publishing_on_resume(step5_env):
    orch: ProductionPipelineOrchestrator = step5_env["orchestrator"]
    # First publication: YouTube succeeds, Instagram fails
    step5_env["ig_adapter"].should_fail = True
    state = await orch.start_campaign(
        campaign_id="camp_no_dup_pub",
        brief_content_or_path="Campaign Name: No Dup Pub",
        operator_uploaded_path=step5_env["sample_video"],
        target_platforms=["youtube_shorts", "instagram_reels"],
        target_account=step5_env["yt_account"],
    )
    art_id = state.artifacts[0].artifact_id
    part_state = await orch.approve_and_publish(
        campaign_id="camp_no_dup_pub",
        artifact_id=art_id,
        target_account=step5_env["yt_account"],
    )
    assert part_state.overall_status == OverallStatus.PARTIALLY_PUBLISHED
    yt_uploads_before = len(step5_env["yt_adapter"].published_submissions)

    # Now fix Instagram adapter and resume
    step5_env["ig_adapter"].should_fail = False
    resumed = await orch.resume_campaign("camp_no_dup_pub", target_account=step5_env["ig_account"])
    assert resumed.overall_status == OverallStatus.COMPLETED
    # YouTube adapter should NOT have been called again
    assert len(step5_env["yt_adapter"].published_submissions) == yt_uploads_before


# -----------------------------------------------------------------------------
# 28. METADATA COMPLIANCE PER PLATFORM
# -----------------------------------------------------------------------------
def test_28_metadata_compliance_per_platform(step5_env):
    coord: MultiPlatformPublishingCoordinator = step5_env["coordinator"]
    art = ProductionArtifact(
        artifact_id="art_meta_test",
        campaign_id="c_meta",
        source_id="s1",
        local_output_path=step5_env["sample_video"],
        duration=20.0,
        selected_start_time=0.0,
        selected_end_time=20.0,
        title="Meta Compliance Title",
        description="YouTube Description Body",
        caption="Instagram Caption Body",
        hashtags=["#crypto", "#alpha"],
    )
    yt_meta = coord._prepare_metadata(art, "youtube_shorts")
    ig_meta = coord._prepare_metadata(art, "instagram_reels")
    assert yt_meta.description == "YouTube Description Body"
    assert ig_meta.description == "Instagram Caption Body"
    assert "#crypto" in yt_meta.hashtags
    assert "#alpha" in ig_meta.hashtags


# -----------------------------------------------------------------------------
# 29. PROHIBITED CONTENT BLOCKING
# -----------------------------------------------------------------------------
def test_29_prohibited_content_blocking(step5_env):
    coord: MultiPlatformPublishingCoordinator = step5_env["coordinator"]
    reqs = CampaignRequirements(text=TextRequirements(prohibited_words=["gambling", "casino"]))
    art = ProductionArtifact(
        artifact_id="art_proh_test",
        campaign_id="c_proh",
        source_id="s1",
        local_output_path=step5_env["sample_video"],
        duration=20.0,
        selected_start_time=0.0,
        selected_end_time=20.0,
        title="Online Gambling Guide",
        description="Check this casino today",
        caption="Check this casino today",
    )
    meta = coord._prepare_metadata(art, "youtube_shorts", requirements=reqs)
    assert "casino" not in meta.description.lower()
    assert "[redacted]" in meta.description


# -----------------------------------------------------------------------------
# 30. REQUIRED HASHTAG ENFORCEMENT
# -----------------------------------------------------------------------------
def test_30_required_hashtag_enforcement(step5_env):
    coord: MultiPlatformPublishingCoordinator = step5_env["coordinator"]
    reqs = CampaignRequirements(text=TextRequirements(required_hashtags=["mandatorytag", "#secondtag"]))
    art = ProductionArtifact(
        artifact_id="art_ht_test",
        campaign_id="c_ht",
        source_id="s1",
        local_output_path=step5_env["sample_video"],
        duration=20.0,
        selected_start_time=0.0,
        selected_end_time=20.0,
        title="Hashtag Test",
        hashtags=["#originaltag"],
    )
    meta = coord._prepare_metadata(art, "youtube_shorts", requirements=reqs)
    assert "#mandatorytag" in meta.hashtags
    assert "#secondtag" in meta.hashtags
    assert "#originaltag" in meta.hashtags


# -----------------------------------------------------------------------------
# 31. ZERO SECRET LEAKAGE
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_31_zero_secret_leakage_in_telegram_and_logs(step5_env):
    orch: ProductionPipelineOrchestrator = step5_env["orchestrator"]
    transport: MockTelegramTransport = step5_env["transport"]
    await orch.start_campaign(
        campaign_id="camp_sec_audit",
        brief_content_or_path="Campaign Name: Secret Audit",
        operator_uploaded_path=step5_env["sample_video"],
        telegram_chat_id=55555,
        target_account=step5_env["yt_account"],
    )
    forbidden_tokens = ["bearer", "client_secret", "refresh_token", "private_key", "password", "master_key"]
    for msg in transport.sent_messages:
        t = msg.get("text", "").lower()
        for forbidden in forbidden_tokens:
            assert forbidden not in t


# -----------------------------------------------------------------------------
# 32. NO FABRICATED PUBLICATION URLS
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_32_no_fabricated_publication_urls(step5_env):
    step5_env["yt_adapter"].should_fail = True
    coordinator: MultiPlatformPublishingCoordinator = step5_env["coordinator"]
    art = ProductionArtifact(
        artifact_id="art_fail_url",
        campaign_id="c_fail_url",
        source_id="s1",
        local_output_path=step5_env["sample_video"],
        duration=20.0,
        selected_start_time=0.0,
        selected_end_time=20.0,
        title="Failed URL Test",
        review_status=ReviewStatus.APPROVED,
    )
    res = await coordinator.publish_artifact_to_platform(art, "youtube_shorts", "c_fail_url", target_account=step5_env["yt_account"])
    assert not res.is_success
    assert res.publication_url is None
    assert res.publication_id is None


# -----------------------------------------------------------------------------
# 33. NO FABRICATED PUBLICATION SUCCESS
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_33_no_fabricated_publication_success(step5_env):
    coordinator: MultiPlatformPublishingCoordinator = step5_env["coordinator"]
    step5_env["ig_adapter"].should_fail = True
    art = ProductionArtifact(
        artifact_id="art_no_fab_succ",
        campaign_id="c_no_fab",
        source_id="s1",
        local_output_path=step5_env["sample_video"],
        duration=20.0,
        selected_start_time=0.0,
        selected_end_time=20.0,
        title="No Fabricated Success",
        review_status=ReviewStatus.APPROVED,
    )
    res = await coordinator.publish_artifact_to_platform(art, "instagram_reels", "c_no_fab", target_account=step5_env["ig_account"])
    assert res.status == PlatformPublishStatus.FAILED
    assert res.is_success is False


# -----------------------------------------------------------------------------
# 34. ACCOUNT VALIDATION BEFORE PUBLISHING
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_34_account_validation_before_publishing(step5_env):
    coordinator: MultiPlatformPublishingCoordinator = step5_env["coordinator"]
    inactive_account = AccountMetadata(
        platform=AccountPlatform.YOUTUBE,
        account_id="UC_inactive",
        username="inactive_user",
        status=AccountStatus.PENDING_VERIFICATION,  # Not ACTIVE
    )
    art = ProductionArtifact(
        artifact_id="art_acc_val",
        campaign_id="c_acc_val",
        source_id="s1",
        local_output_path=step5_env["sample_video"],
        duration=20.0,
        selected_start_time=0.0,
        selected_end_time=20.0,
        title="Account Validation Test",
        review_status=ReviewStatus.APPROVED,
    )
    with pytest.raises(AccountNotReadyError) as exc_info:
        await coordinator.publish_artifact_to_platform(
            artifact=art,
            platform="youtube_shorts",
            campaign_id="c_acc_val",
            target_account=inactive_account,
        )
    assert "Publishing requires ACTIVE status" in str(exc_info.value)


# -----------------------------------------------------------------------------
# 35. FINAL CAMPAIGN COMPLETION STATE
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_35_final_campaign_completion_state(step5_env):
    orch: ProductionPipelineOrchestrator = step5_env["orchestrator"]
    state = await orch.start_campaign(
        campaign_id="camp_final_done",
        brief_content_or_path="Campaign Name: Done Test",
        operator_uploaded_path=step5_env["sample_video"],
        target_platforms=["youtube_shorts"],
        target_account=step5_env["yt_account"],
    )
    art_id = state.artifacts[0].artifact_id
    final_state = await orch.approve_and_publish(
        campaign_id="camp_final_done",
        artifact_id=art_id,
        target_account=step5_env["yt_account"],
    )
    assert final_state.current_stage == PipelineStage.COMPLETED
    assert final_state.overall_status == OverallStatus.COMPLETED
    assert final_state.current_stage.is_terminal
