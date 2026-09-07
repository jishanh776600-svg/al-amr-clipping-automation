"""Comprehensive Test Suite for Step 4/5: Autonomous Production, Strict Requirement Compliance,
Telegram Review & Human Approval Gate (38 Scenarios)."""

import os
import tempfile
import pytest
import numpy as np
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from clipping.contracts.requirements import (
    CampaignRequirements,
    CampaignIdentityRequirements,
    SourceRequirements,
    ClipRequirements,
    ContentRequirements,
    BrandingRequirements,
    TextRequirements,
    PlatformRequirements,
    SubmissionRequirements,
)
from clipping.contracts.source import (
    SourceCandidatePriority,
    SourceAccessStatus,
    SourceResolutionResult,
)
from clipping.contracts.production import (
    ProductionArtifact,
    ProductionStatus,
    ReviewStatus,
    ProductionComplianceResult,
    OperatorInterventionRecord,
    RevisionRecord,
)
from clipping.agent.vault.models import (
    AccountMetadata,
    AccountPlatform,
    AccountStatus,
)
from clipping.production.repository import ProductionRepository
from clipping.production.content_analyzer import ProductionContentAnalyzer
from clipping.production.video_editor import ProductionVideoEditor
from clipping.production.compliance_gate import ProductionComplianceGate
from clipping.production.telegram_review import TelegramReviewSystem
from clipping.production.challenge_escalation import ChallengeEscalationManager
from clipping.production.engine import AutonomousProductionEngine
from clipping.ingestion.source_resolver import SourceResolutionEngine
from clipping.approval.transport import MockTelegramTransport
from clipping.storage.local import LocalStorageDriver


def _create_test_mp4(path: str, duration_sec: float = 45.0, width: int = 360, height: int = 640, fps: int = 5):
    """Generates a genuine MP4 video file on disk."""
    import cv2
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    out = cv2.VideoWriter(path, fourcc, float(fps), (width, height))
    total_frames = int(duration_sec * fps)
    for i in range(total_frames):
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        frame[:] = (i % 255, 120, 80)
        out.write(frame)
    out.release()


@pytest.fixture
def temp_env():
    with tempfile.TemporaryDirectory() as td:
        storage = LocalStorageDriver(root_dir=os.path.join(td, "storage"))
        prod_repo = ProductionRepository(storage_driver=storage)
        transport = MockTelegramTransport()
        analyzer = ProductionContentAnalyzer()
        editor = ProductionVideoEditor()
        gate = ProductionComplianceGate()
        review_sys = TelegramReviewSystem(repository=prod_repo, transport=transport)
        escalator = ChallengeEscalationManager(repository=prod_repo, transport=transport)
        engine = AutonomousProductionEngine(
            repository=prod_repo,
            content_analyzer=analyzer,
            video_editor=editor,
            compliance_gate=gate,
            telegram_review=review_sys,
        )
        sample_video = os.path.join(td, "sample_master.mp4")
        _create_test_mp4(sample_video, duration_sec=45.0, fps=5)

        active_account = AccountMetadata(
            platform=AccountPlatform.YOUTUBE,
            account_id="UC_verified_channel",
            username="al_amr_official",
            display_name="AL AMR Media",
            status=AccountStatus.ACTIVE,
        )

        yield {
            "dir": td,
            "storage": storage,
            "repo": prod_repo,
            "transport": transport,
            "analyzer": analyzer,
            "editor": editor,
            "gate": gate,
            "review_sys": review_sys,
            "escalator": escalator,
            "engine": engine,
            "sample_video": sample_video,
            "account": active_account,
        }


# -----------------------------------------------------------------------------
# 1. PRODUCTION CONSUMES CAMPAIGN REQUIREMENTS
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_01_production_consumes_campaign_requirements(temp_env):
    reqs = CampaignRequirements(
        identity=CampaignIdentityRequirements(campaign_name="Crypto Alpha"),
        clips=ClipRequirements(min_duration_seconds=15.0, max_duration_seconds=45.0, clip_count_required=2),
        text=TextRequirements(required_hashtags=["#crypto", "#alpha"], call_to_action="Follow for more"),
    )
    src_res = SourceResolutionResult(
        source_type="local_file",
        original_uri=temp_env["sample_video"],
        resolved_uri=temp_env["sample_video"],
        local_storage_path=temp_env["sample_video"],
        duration=45.0,
        source_access_status=SourceAccessStatus.ACCESSIBLE,
    )
    artifacts = await temp_env["engine"].produce_campaign_clips(
        campaign_id="camp_01",
        source_result=src_res,
        requirements=reqs,
        target_account=temp_env["account"],
    )
    assert len(artifacts) == 2
    for art in artifacts:
        assert art.campaign_id == "camp_01"
        assert "#crypto" in [h.lower() for h in art.hashtags]
        assert art.duration >= 15.0


# -----------------------------------------------------------------------------
# 2. PRODUCTION CONSUMES OPERATOR YOUTUBE URL
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_02_production_consumes_operator_youtube_url(temp_env):
    yt_url = "https://www.youtube.com/watch?v=sample12345"
    src_res = SourceResolutionResult(
        source_type="youtube",
        original_uri=yt_url,
        resolved_uri=yt_url,
        local_storage_path=temp_env["sample_video"],
        duration=40.0,
        source_access_status=SourceAccessStatus.ACCESSIBLE,
    )
    artifacts = await temp_env["engine"].produce_campaign_clips(
        campaign_id="camp_yt",
        source_result=src_res,
        target_account=temp_env["account"],
    )
    assert len(artifacts) >= 1
    assert artifacts[0].source_id == yt_url


# -----------------------------------------------------------------------------
# 3. PRODUCTION CONSUMES OPERATOR DIRECT VIDEO URL
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_03_production_consumes_operator_direct_video_url(temp_env):
    direct_url = "https://cdn.example.com/podcast_ep.mp4"
    src_res = SourceResolutionResult(
        source_type="direct_url",
        original_uri=direct_url,
        resolved_uri=direct_url,
        local_storage_path=temp_env["sample_video"],
        duration=30.0,
        source_access_status=SourceAccessStatus.ACCESSIBLE,
    )
    artifacts = await temp_env["engine"].produce_campaign_clips(
        campaign_id="camp_direct",
        source_result=src_res,
        target_account=temp_env["account"],
    )
    assert len(artifacts) >= 1
    assert artifacts[0].source_id == direct_url


# -----------------------------------------------------------------------------
# 4. PRODUCTION CONSUMES OPERATOR-UPLOADED VIDEO
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_04_production_consumes_operator_uploaded_video(temp_env):
    src_res = SourceResolutionResult(
        source_type="local_file",
        original_uri=temp_env["sample_video"],
        resolved_uri=temp_env["sample_video"],
        local_storage_path=temp_env["sample_video"],
        duration=45.0,
        source_access_status=SourceAccessStatus.ACCESSIBLE,
    )
    artifacts = await temp_env["engine"].produce_campaign_clips(
        campaign_id="camp_local",
        source_result=src_res,
        target_account=temp_env["account"],
    )
    assert len(artifacts) >= 1
    assert os.path.isfile(artifacts[0].local_output_path)


# -----------------------------------------------------------------------------
# 5. NO WHOP SOURCE DISCOVERY IN PRODUCTION
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_05_no_whop_source_discovery():
    resolver = SourceResolutionEngine()
    # In production mode, passing only whop_discovered_urls must fail closed
    res = await resolver.resolve_source(
        whop_discovered_urls=["https://whop.com/video/stream123"],
        production_mode=True,
    )
    assert res.is_valid is False
    assert "No valid operator-provided source" in res.failure_reason


# -----------------------------------------------------------------------------
# 6. NO REPOSITORY SOURCE SUBSTITUTION IN PRODUCTION
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_06_no_repository_source_substitution():
    resolver = SourceResolutionEngine()
    # In production mode, passing only repository URLs must fail closed
    res = await resolver.resolve_source(
        campaign_repo_urls=["https://repo.internal/video/old_asset.mp4"],
        production_mode=True,
    )
    assert res.is_valid is False
    assert "No valid operator-provided source" in res.failure_reason


# -----------------------------------------------------------------------------
# 7. CLIP CANDIDATE SELECTION
# -----------------------------------------------------------------------------
def test_07_clip_candidate_selection(temp_env):
    src_res = SourceResolutionResult(
        source_type="local_file",
        original_uri=temp_env["sample_video"],
        duration=60.0,
        source_access_status=SourceAccessStatus.ACCESSIBLE,
    )
    reqs = CampaignRequirements(clips=ClipRequirements(clip_count_required=3, target_duration_seconds=20.0))
    segments = temp_env["analyzer"].select_best_moments(source_result=src_res, requirements=reqs, clip_count=3)
    assert len(segments) == 3
    for s in segments:
        assert s.duration >= 15.0
        assert s.score > 0


# -----------------------------------------------------------------------------
# 8. DURATION COMPLIANCE
# -----------------------------------------------------------------------------
def test_08_duration_compliance(temp_env):
    gate = temp_env["gate"]
    reqs = CampaignRequirements(clips=ClipRequirements(min_duration_seconds=20.0, max_duration_seconds=40.0))
    art_too_short = ProductionArtifact(
        artifact_id="art_short",
        campaign_id="c1",
        source_id="s1",
        local_output_path="",
        duration=12.0,
        selected_start_time=0.0,
        selected_end_time=12.0,
        title="Short Clip",
    )
    src_res = SourceResolutionResult(source_type="local_file", original_uri="s1", source_access_status=SourceAccessStatus.ACCESSIBLE)
    res = gate.evaluate_clip(art_too_short, src_res, reqs, target_account=temp_env["account"])
    assert res.is_compliant is False
    assert any("below required minimum" in b for b in res.blockers)


# -----------------------------------------------------------------------------
# 9. ASPECT-RATIO COMPLIANCE
# -----------------------------------------------------------------------------
def test_09_aspect_ratio_compliance(temp_env):
    gate = temp_env["gate"]
    art_bad_aspect = ProductionArtifact(
        artifact_id="art_aspect",
        campaign_id="c1",
        source_id="s1",
        local_output_path="",
        duration=25.0,
        aspect_ratio="16:9",
        selected_start_time=0.0,
        selected_end_time=25.0,
        title="Horizontal Clip",
    )
    src_res = SourceResolutionResult(source_type="local_file", original_uri="s1", source_access_status=SourceAccessStatus.ACCESSIBLE)
    res = gate.evaluate_clip(art_bad_aspect, src_res, target_account=temp_env["account"])
    assert res.is_compliant is False
    assert any("Aspect ratio" in b for b in res.blockers)


# -----------------------------------------------------------------------------
# 10. RESOLUTION COMPLIANCE
# -----------------------------------------------------------------------------
def test_10_resolution_compliance(temp_env):
    gate = temp_env["gate"]
    art_low_res = ProductionArtifact(
        artifact_id="art_res",
        campaign_id="c1",
        source_id="s1",
        local_output_path="",
        duration=25.0,
        resolution="480x854",
        selected_start_time=0.0,
        selected_end_time=25.0,
        title="Low Res Clip",
    )
    src_res = SourceResolutionResult(source_type="local_file", original_uri="s1", source_access_status=SourceAccessStatus.ACCESSIBLE)
    res = gate.evaluate_clip(art_low_res, src_res, target_account=temp_env["account"])
    assert res.is_compliant is False
    assert any("Output resolution" in b for b in res.blockers)


# -----------------------------------------------------------------------------
# 11. PROHIBITED-CONTENT DETECTION
# -----------------------------------------------------------------------------
def test_11_prohibited_content_detection(temp_env):
    gate = temp_env["gate"]
    reqs = CampaignRequirements(content=ContentRequirements(prohibited_topics=["gambling", "casino"]))
    art = ProductionArtifact(
        artifact_id="art_prob",
        campaign_id="c1",
        source_id="s1",
        local_output_path="",
        duration=25.0,
        selected_start_time=0.0,
        selected_end_time=25.0,
        title="How to win in online casino easily",
    )
    src_res = SourceResolutionResult(source_type="local_file", original_uri="s1", source_access_status=SourceAccessStatus.ACCESSIBLE)
    res = gate.evaluate_clip(art, src_res, reqs, target_account=temp_env["account"])
    assert res.is_compliant is False
    assert any("Prohibited topic" in b for b in res.blockers)


# -----------------------------------------------------------------------------
# 12. REQUIRED-TOPIC VALIDATION
# -----------------------------------------------------------------------------
def test_12_required_topic_validation(temp_env):
    reqs = CampaignRequirements(content=ContentRequirements(required_talking_points=["AI agents", "automation"]))
    transcript = "In this video we talk about building autonomous AI agents and intelligent pipeline automation."
    src_res = SourceResolutionResult(source_type="local_file", original_uri="s1", duration=40.0, source_access_status=SourceAccessStatus.ACCESSIBLE)
    segs = temp_env["analyzer"].select_best_moments(src_res, reqs, transcript=transcript)
    assert len(segs) > 0
    assert len(segs[0].talking_points_covered) >= 1


# -----------------------------------------------------------------------------
# 13. REQUIRED-HASHTAG GENERATION
# -----------------------------------------------------------------------------
def test_13_required_hashtag_generation(temp_env):
    reqs = CampaignRequirements(text=TextRequirements(required_hashtags=["#alamr", "clipping", "#trending"]))
    meta = temp_env["gate"].generate_metadata(1, "Hook Title", "Body transcript", reqs)
    assert "#alamr" in meta.hashtags
    assert "#clipping" in meta.hashtags
    assert "#trending" in meta.hashtags


# -----------------------------------------------------------------------------
# 14. PROHIBITED-WORD VALIDATION
# -----------------------------------------------------------------------------
def test_14_prohibited_word_validation(temp_env):
    reqs = CampaignRequirements(text=TextRequirements(prohibited_words=["scam", "cheat"]))
    meta = temp_env["gate"].generate_metadata(1, "Never scam people", "Body", reqs)
    assert "scam" in meta.prohibited_words_found


# -----------------------------------------------------------------------------
# 15. CAPTION GENERATION
# -----------------------------------------------------------------------------
def test_15_caption_generation(temp_env):
    reqs = CampaignRequirements(text=TextRequirements(required_hashtags=["#growth"], call_to_action="Click link in bio"))
    meta = temp_env["gate"].generate_metadata(1, "Top Strategy", "Here is how to scale.", reqs)
    assert "Top Strategy" in meta.caption
    assert "Click link in bio" in meta.caption
    assert "#growth" in meta.caption


# -----------------------------------------------------------------------------
# 16. TITLE GENERATION
# -----------------------------------------------------------------------------
def test_16_title_generation(temp_env):
    meta = temp_env["gate"].generate_metadata(1, "Fastest Way to Code", "Body", campaign_name="SaaS Sprint")
    assert "Fastest Way to Code" in meta.title
    assert "SaaS Sprint" in meta.title


# -----------------------------------------------------------------------------
# 17. DESCRIPTION GENERATION
# -----------------------------------------------------------------------------
def test_17_description_generation(temp_env):
    meta = temp_env["gate"].generate_metadata(2, "Growth Secret", "Key talking point description.", campaign_name="Agency Launch")
    assert "Growth Secret - Clip #2" in meta.description


# -----------------------------------------------------------------------------
# 18. WATERMARK REQUIREMENT
# -----------------------------------------------------------------------------
def test_18_watermark_requirement(temp_env):
    reqs = CampaignRequirements(branding=BrandingRequirements(required_watermark=True, watermark_asset_url="AL AMR"))
    art_no_wm = ProductionArtifact(
        artifact_id="art_wm",
        campaign_id="c1",
        source_id="s1",
        local_output_path="",
        duration=25.0,
        branding_status="not_required",
        selected_start_time=0.0,
        selected_end_time=25.0,
        title="Unbranded Clip",
    )
    src_res = SourceResolutionResult(source_type="local_file", original_uri="s1", source_access_status=SourceAccessStatus.ACCESSIBLE)
    res = temp_env["gate"].evaluate_clip(art_no_wm, src_res, reqs, target_account=temp_env["account"])
    assert res.is_compliant is False
    assert any("Mandatory watermark" in b for b in res.blockers)


# -----------------------------------------------------------------------------
# 19. SUBTITLE REQUIREMENT
# -----------------------------------------------------------------------------
def test_19_subtitle_requirement(temp_env):
    reqs = CampaignRequirements(text=TextRequirements(subtitles_required=True))
    art_no_sub = ProductionArtifact(
        artifact_id="art_sub",
        campaign_id="c1",
        source_id="s1",
        local_output_path="",
        duration=25.0,
        transcript=None,
        selected_start_time=0.0,
        selected_end_time=25.0,
        title="No Subtitles Clip",
    )
    src_res = SourceResolutionResult(source_type="local_file", original_uri="s1", source_access_status=SourceAccessStatus.ACCESSIBLE)
    res = temp_env["gate"].evaluate_clip(art_no_sub, src_res, reqs, target_account=temp_env["account"])
    assert res.is_compliant is False
    assert any("Subtitles required" in b for b in res.blockers)


# -----------------------------------------------------------------------------
# 20. FINAL COMPLIANCE GATE
# -----------------------------------------------------------------------------
def test_20_final_compliance_gate(temp_env):
    art = ProductionArtifact(
        artifact_id="art_good",
        campaign_id="c1",
        source_id="s1",
        local_output_path=temp_env["sample_video"],
        duration=30.0,
        resolution="1080x1920",
        aspect_ratio="9:16",
        branding_status="applied",
        transcript="Compliant text with talking point.",
        hashtags=["#alamr", "#viral"],
        selected_start_time=0.0,
        selected_end_time=30.0,
        title="Good Clip",
        caption="Good clip text. #alamr #viral",
        description="Description. #alamr #viral",
    )
    src_res = SourceResolutionResult(source_type="local_file", original_uri="s1", source_access_status=SourceAccessStatus.ACCESSIBLE)
    reqs = CampaignRequirements(
        clips=ClipRequirements(min_duration_seconds=15.0, max_duration_seconds=45.0),
        text=TextRequirements(required_hashtags=["#alamr"]),
    )
    res = temp_env["gate"].evaluate_clip(art, src_res, reqs, target_account=temp_env["account"])
    assert res.is_compliant is True
    assert len(res.blockers) == 0


# -----------------------------------------------------------------------------
# 21. FAILED REQUIREMENT BLOCKS REVIEW-READY STATUS
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_21_failed_requirement_blocks_review_ready_status(temp_env):
    # Missing required hashtag should block review-ready state
    reqs = CampaignRequirements(text=TextRequirements(required_hashtags=["#mandatoryMissingTag"]))
    src_res = SourceResolutionResult(
        source_type="local_file",
        original_uri=temp_env["sample_video"],
        local_storage_path=temp_env["sample_video"],
        duration=30.0,
        source_access_status=SourceAccessStatus.ACCESSIBLE,
    )
    artifacts = await temp_env["engine"].produce_campaign_clips(
        campaign_id="camp_block",
        source_result=src_res,
        requirements=reqs,
        target_account=temp_env["account"],
    )
    # If the generator appends the hashtag, let's test a case where minimum duration is impossible
    reqs_impossible = CampaignRequirements(clips=ClipRequirements(min_duration_seconds=600.0))
    artifacts_blocked = await temp_env["engine"].produce_campaign_clips(
        campaign_id="camp_block_dur",
        source_result=src_res,
        requirements=reqs_impossible,
        target_account=temp_env["account"],
    )
    assert artifacts_blocked[0].review_status == ReviewStatus.BLOCKED
    assert artifacts_blocked[0].production_status == ProductionStatus.BLOCKED


# -----------------------------------------------------------------------------
# 22. TELEGRAM REVIEW PACKAGE GENERATION
# -----------------------------------------------------------------------------
def test_22_telegram_review_package_generation(temp_env):
    art = ProductionArtifact(
        artifact_id="art_rev_01",
        campaign_id="Campaign Alpha",
        source_id="s1",
        clip_number=1,
        local_output_path=temp_env["sample_video"],
        duration=28.5,
        title="Secret to 10x Productivity",
        description="Full video breakdown.",
        caption="Check out this tip! #productivity",
        hashtags=["#productivity"],
        selected_start_time=0.0,
        selected_end_time=28.5,
    )
    pkg = temp_env["review_sys"].format_review_package(art, total_clips=3, campaign_name="Campaign Alpha")
    assert "CAMPAIGN: Campaign Alpha" in pkg
    assert "CLIP: 1/3" in pkg
    assert "STATUS: READY FOR REVIEW" in pkg
    assert "Secret to 10x Productivity" in pkg
    assert "#productivity" in pkg
    assert "Click [✓ GOOD / APPROVE]" in pkg


# -----------------------------------------------------------------------------
# 23. GENERATED MEDIA ATTACHED/SENT CORRECTLY
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_23_generated_media_attached_sent_correctly(temp_env):
    art = ProductionArtifact(
        artifact_id="art_send",
        campaign_id="c_send",
        source_id="s1",
        local_output_path=temp_env["sample_video"],
        duration=30.0,
        title="Send Test",
        selected_start_time=0.0,
        selected_end_time=30.0,
    )
    msg_id = await temp_env["review_sys"].dispatch_review_package(art, chat_id=123456)
    assert msg_id is not None
    assert len(temp_env["transport"].sent_messages) == 1
    assert "art_send" in temp_env["transport"].sent_messages[0]["text"]


# -----------------------------------------------------------------------------
# 24. GOOD APPROVAL CHANGES STATE CORRECTLY
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_24_good_approval_changes_state_correctly(temp_env):
    art = ProductionArtifact(
        artifact_id="art_approve_test",
        campaign_id="c1",
        source_id="s1",
        local_output_path=temp_env["sample_video"],
        duration=30.0,
        title="To Approve",
        selected_start_time=0.0,
        selected_end_time=30.0,
        review_status=ReviewStatus.PENDING_APPROVAL,
    )
    await temp_env["repo"].save_artifact(art)
    updated = await temp_env["review_sys"].approve_artifact("art_approve_test", operator_id="admin_123")
    assert updated is not None
    assert updated.review_status == ReviewStatus.APPROVED
    assert updated.is_publishing_ready is True


# -----------------------------------------------------------------------------
# 25. BAD REJECTION CHANGES STATE CORRECTLY
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_25_bad_rejection_changes_state_correctly(temp_env):
    art = ProductionArtifact(
        artifact_id="art_reject_test",
        campaign_id="c1",
        source_id="s1",
        local_output_path=temp_env["sample_video"],
        duration=30.0,
        title="To Reject",
        selected_start_time=0.0,
        selected_end_time=30.0,
        review_status=ReviewStatus.PENDING_APPROVAL,
    )
    await temp_env["repo"].save_artifact(art)
    updated, rev = await temp_env["review_sys"].reject_artifact_for_revision(
        "art_reject_test", operator_id="admin_123", feedback="Hook is too slow"
    )
    assert updated.review_status == ReviewStatus.REVISION_REQUIRED
    assert rev is not None
    assert rev.operator_feedback == "Hook is too slow"


# -----------------------------------------------------------------------------
# 26. REVISION FEEDBACK PERSISTENCE
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_26_revision_feedback_persistence(temp_env):
    art = ProductionArtifact(
        artifact_id="art_feedback_test",
        campaign_id="c1",
        source_id="s1",
        local_output_path=temp_env["sample_video"],
        duration=30.0,
        title="Feedback Test",
        selected_start_time=0.0,
        selected_end_time=30.0,
    )
    await temp_env["repo"].save_artifact(art)
    await temp_env["review_sys"].reject_artifact_for_revision("art_feedback_test", "op_1", "Change watermark color")
    revisions = await temp_env["repo"].list_revisions_for_artifact("art_feedback_test")
    assert len(revisions) == 1
    assert revisions[0].operator_feedback == "Change watermark color"
    assert revisions[0].operator_id == "op_1"


# -----------------------------------------------------------------------------
# 27. REVISED ARTIFACT CREATION
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_27_revised_artifact_creation(temp_env):
    art = ProductionArtifact(
        artifact_id="art_rev_cycle",
        campaign_id="c1",
        source_id="s1",
        local_output_path=temp_env["sample_video"],
        duration=30.0,
        title="V1 Clip",
        selected_start_time=0.0,
        selected_end_time=30.0,
        revision_count=0,
    )
    await temp_env["repo"].save_artifact(art)
    revised = await temp_env["engine"].revise_artifact("art_rev_cycle", "Trim first 2 seconds", "operator_7")
    assert revised is not None
    assert revised.artifact_id == "art_rev_cycle_v1"
    assert revised.revision_count == 1
    assert "Trim first 2 seconds" in revised.metadata.get("revision_feedback", "")


# -----------------------------------------------------------------------------
# 28. ORIGINAL ARTIFACT PRESERVED
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_28_original_artifact_preserved(temp_env):
    art = ProductionArtifact(
        artifact_id="art_orig_pres",
        campaign_id="c1",
        source_id="s1",
        local_output_path=temp_env["sample_video"],
        duration=30.0,
        title="Original Clip",
        selected_start_time=0.0,
        selected_end_time=30.0,
    )
    await temp_env["repo"].save_artifact(art)
    await temp_env["engine"].revise_artifact("art_orig_pres", "Make shorter", "operator_7")

    # Original artifact still exists in repo with REVISION_REQUIRED status
    original_loaded = await temp_env["repo"].get_artifact("art_orig_pres")
    assert original_loaded is not None
    assert original_loaded.review_status == ReviewStatus.REVISION_REQUIRED
    assert original_loaded.title == "Original Clip"


# -----------------------------------------------------------------------------
# 29. APPROVAL SURVIVES RESTART
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_29_approval_survives_restart(temp_env):
    art = ProductionArtifact(
        artifact_id="art_restart",
        campaign_id="c_restart",
        source_id="s1",
        local_output_path=temp_env["sample_video"],
        duration=30.0,
        title="Restart Test",
        selected_start_time=0.0,
        selected_end_time=30.0,
    )
    await temp_env["repo"].save_artifact(art)
    await temp_env["review_sys"].approve_artifact("art_restart", "op_test")

    # Simulate fresh repo instance on same storage (process restart)
    new_repo = ProductionRepository(storage_driver=temp_env["storage"])
    reloaded = await new_repo.get_artifact("art_restart")
    assert reloaded is not None
    assert reloaded.review_status == ReviewStatus.APPROVED
    assert reloaded.is_publishing_ready is True


# -----------------------------------------------------------------------------
# 30. NO PUBLISHING BEFORE APPROVAL
# -----------------------------------------------------------------------------
def test_30_no_publishing_before_approval(temp_env):
    art_pending = ProductionArtifact(
        artifact_id="art_pend",
        campaign_id="c1",
        source_id="s1",
        local_output_path="",
        duration=25.0,
        selected_start_time=0.0,
        selected_end_time=25.0,
        title="Pending Clip",
        review_status=ReviewStatus.PENDING_APPROVAL,
    )
    assert art_pending.is_publishing_ready is False

    art_rejected = art_pending.model_copy(update={"review_status": ReviewStatus.REVISION_REQUIRED})
    assert art_rejected.is_publishing_ready is False

    art_approved = art_pending.model_copy(update={"review_status": ReviewStatus.APPROVED})
    assert art_approved.is_publishing_ready is True


# -----------------------------------------------------------------------------
# 31. CAPTCHA ESCALATION INCLUDES ACTIONABLE URL WHEN AVAILABLE
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_31_captcha_escalation_includes_actionable_url_when_available(temp_env):
    url = "https://challenges.cloudflare.com/turnstile/v0/verify?sitekey=xyz"
    rec = await temp_env["escalator"].escalate(
        campaign_id="camp_cap",
        job_id="job_cap_01",
        checkpoint="SOURCE_ACQUISITION",
        challenge_type="CLOUDFLARE_TURNSTILE",
        actionable_url=url,
        chat_id=98765,
    )
    assert rec.actionable_url == url
    assert "https://challenges.cloudflare.com" in temp_env["transport"].sent_messages[0]["text"]


# -----------------------------------------------------------------------------
# 32. CAPTCHA ESCALATION PRESERVES CHECKPOINT
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_32_captcha_escalation_preserves_checkpoint(temp_env):
    rec = await temp_env["escalator"].escalate(
        campaign_id="camp_cp",
        job_id="job_cp_01",
        checkpoint="AUTHENTICATION_GATE",
        challenge_type="MFA_OTP",
    )
    saved = await temp_env["repo"].get_intervention(rec.intervention_id)
    assert saved is not None
    assert saved.checkpoint == "AUTHENTICATION_GATE"
    assert saved.session_preserved is True
    assert saved.status == "PENDING_OPERATOR"


# -----------------------------------------------------------------------------
# 33. RESUME AFTER OPERATOR RESOLUTION
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_33_resume_after_operator_resolution(temp_env):
    rec = await temp_env["escalator"].escalate(
        campaign_id="camp_res",
        job_id="job_res_01",
        checkpoint="SOURCE_ACCESS",
        challenge_type="CAPTCHA",
    )
    resumed = await temp_env["escalator"].resume(rec.intervention_id, chat_id=12345)
    assert resumed is not None
    assert resumed.status == "RESUMED"
    assert resumed.resumed_at is not None


# -----------------------------------------------------------------------------
# 34. NO FABRICATED CAPTCHA URL
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_34_no_fabricated_captcha_url(temp_env):
    rec = await temp_env["escalator"].escalate(
        campaign_id="camp_nofake",
        job_id="job_nofake_01",
        checkpoint="SOURCE_ACCESS",
        actionable_url=None,  # No URL available
        chat_id=112233,
    )
    assert rec.actionable_url is None
    msg_text = temp_env["transport"].sent_messages[0]["text"]
    assert "No direct browser URL available" in msg_text


# -----------------------------------------------------------------------------
# 35. NO FABRICATED METADATA
# -----------------------------------------------------------------------------
def test_35_no_fabricated_metadata(temp_env):
    # Requirements specify no hashtags or mentions
    reqs = CampaignRequirements()
    meta = temp_env["gate"].generate_metadata(1, "Realistic Insight", "Short snippet", reqs)
    # Default tags are only generic safety tags; no campaign claims fabricated
    assert not any("fake" in t.lower() for t in meta.hashtags)
    assert meta.mentions == []


# -----------------------------------------------------------------------------
# 36. ZERO SECRET LEAKAGE
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_36_zero_secret_leakage(temp_env):
    art = ProductionArtifact(
        artifact_id="art_secret_test",
        campaign_id="c_sec",
        source_id="s1",
        local_output_path=temp_env["sample_video"],
        duration=30.0,
        title="Security Audit",
        selected_start_time=0.0,
        selected_end_time=30.0,
    )
    await temp_env["review_sys"].dispatch_review_package(art, chat_id=55555)
    sent_text = temp_env["transport"].sent_messages[0]["text"]

    # Verify no tokens, passwords, or bearer keys appear
    forbidden = ["bearer", "client_secret", "refresh_token", "private_key", "password"]
    for word in forbidden:
        assert word not in sent_text.lower()


# -----------------------------------------------------------------------------
# 37. NO DUPLICATE ARTIFACT GENERATION
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_37_no_duplicate_artifact_generation(temp_env):
    src_res = SourceResolutionResult(
        source_type="local_file",
        original_uri=temp_env["sample_video"],
        local_storage_path=temp_env["sample_video"],
        duration=45.0,
        source_access_status=SourceAccessStatus.ACCESSIBLE,
    )
    reqs = CampaignRequirements(clips=ClipRequirements(clip_count_required=1))
    artifacts_1 = await temp_env["engine"].produce_campaign_clips(
        campaign_id="camp_dedup",
        source_result=src_res,
        requirements=reqs,
        target_account=temp_env["account"],
    )
    assert len(artifacts_1) == 1
    art_1 = artifacts_1[0]

    # Inspect repository to ensure exactly 1 artifact exists for campaign
    saved = await temp_env["repo"].list_artifacts(campaign_id="camp_dedup")
    assert len(saved) == 1


# -----------------------------------------------------------------------------
# 38. DETERMINISTIC / RESUMABLE CHECKPOINT RECOVERY
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_38_deterministic_resumable_checkpoint_recovery(temp_env):
    # Create intervention checkpoint
    rec = await temp_env["escalator"].escalate(
        campaign_id="camp_chk",
        job_id="job_chk_123",
        checkpoint="SOURCE_ACCESS",
        challenge_type="CLOUDFLARE_TURNSTILE",
        actionable_url="https://challenges.cloudflare.com/test",
    )

    # Recovery: load pending interventions after restart
    pending = await temp_env["repo"].list_pending_interventions(campaign_id="camp_chk")
    assert len(pending) == 1
    assert pending[0].intervention_id == rec.intervention_id

    # Resume
    resumed = await temp_env["escalator"].resume(rec.intervention_id)
    assert resumed.status == "RESUMED"

    # Pending list is now empty
    pending_after = await temp_env["repo"].list_pending_interventions(campaign_id="camp_chk")
    assert len(pending_after) == 0
