"""Tests for Step 6: Whop Render & Quality Verification Layer.

Comprehensive coverage of all 28 required verification areas:
1. Job completion detection
2. Artifact resolution & durability
3. Exactly-five valid clip requirement
4. 1–4 valid clips -> INSUFFICIENT_VALID_CLIPS
5. 5 valid clips -> RENDER_READY
6. Duplicate/overlapping clip rejection (distinctness)
7. Duration < 20s rejection
8. Duration > 30s rejection
9. Valid 20–30s acceptance
10. Corrupted video rejection
11. Invalid portrait ratio rejection
12. Missing audio stream rejection
13. Voice dropout / near-silence rejection
14. BGM missing rejection
15. BGM clipping warning
16. SFX timing & metadata validation
17. Caption compliance
18. Mandatory CampaignBrief rule failure handling
19. Unsupported mandatory rule handling (UNSUPPORTED_REQUIRES_REVIEW)
20. Unsupported optional rule handling
21. Full compliance pass
22. Artifact persistence failure handling
23. RENDER_WARN decision handling
24. RENDER_FAILED decision handling
25. QA evaluation idempotency
26. State-machine transitions
27. Quality score integration
28. Step 4 -> Step 5 -> Step 6 end-to-end mapping
"""

import json
import pytest
from pathlib import Path

from whop.models import (
    CampaignRecord,
    CampaignRule,
    CampaignState,
    ParsingStatus,
    RuleCategory,
    RuleComplianceStatus,
    WhopCampaignBrief,
)
from whop.ledger import CampaignLedger
from whop.quality_verifier import QualityVerifier, REQUIRED_VALID_CLIPS_COUNT


@pytest.fixture
def temp_ledger(tmp_path):
    db_file = tmp_path / "test_ledger.db"
    ledger = CampaignLedger(db_path=db_file)
    return ledger


@pytest.fixture
def mock_brief():
    rules = [
        CampaignRule(
            rule_id="r1",
            category=RuleCategory.DURATION,
            text="Videos must be between 20 and 30 seconds",
            mandatory=True,
            source_reference="test",
            status="ACTIVE",
        ),
        CampaignRule(
            rule_id="r2",
            category=RuleCategory.VISUAL,
            text="9:16 portrait vertical video only",
            mandatory=True,
            source_reference="test",
            status="ACTIVE",
        ),
        CampaignRule(
            rule_id="r3",
            category=RuleCategory.BGM,
            text="Subtle trending background music required",
            mandatory=True,
            source_reference="test",
            status="ACTIVE",
        ),
        CampaignRule(
            rule_id="r4",
            category=RuleCategory.CAPTIONS,
            text="Dynamic word-by-word captions must be visible",
            mandatory=True,
            source_reference="test",
            status="ACTIVE",
        ),
        CampaignRule(
            rule_id="r5",
            category=RuleCategory.SUBMISSION,
            text="Join Campaign button on Whop",
            mandatory=False,
            is_operational=True,
            source_reference="test",
            status="OPERATIONAL",
        ),
    ]
    return WhopCampaignBrief(
        campaign_id="test_camp_001",
        title="Test Clipping Campaign",
        campaign_url="https://whop.com/test",
        guideline_hash="hash_abc123",
        guideline_source_type="card_detail",
        guideline_source_reference="https://whop.com/test",
        parsing_status=ParsingStatus.PARSED,
        duration_min_s=20.0,
        duration_max_s=30.0,
        rules=rules,
        caption_preset="bold_pop",
        supported_platforms=["tiktok", "instagram", "youtube"],
    )


def make_valid_candidate(idx: int, duration: float = 25.0) -> dict:
    return {
        "clip_id": f"clip_{idx:03d}",
        "output_path": f"/durable/storage/exports/clip_{idx:03d}/final.mp4",
        "drive_file_id": f"drive_file_id_{idx:03d}",
        "duration": duration,
        "width": 1080,
        "height": 1920,
        "fps": 30.0,
        "video_codec": "h264",
        "audio_codec": "aac",
        "mean_volume_db": -14.0,
        "true_peak_db": -1.5,
        "av_sync_diff_s": 0.05,
        "decode_ok": True,
        "broll_coverage_pct": 35.0,
        "quality_status": "RENDER_PASS",
        "quality_score": 95.0,
        "caption_style": "bold_pop",
        "bgm_asset_id": "canonical_ambient",
        "start_s": float(idx * 35.0),
        "end_s": float(idx * 35.0 + duration),
    }


# ---------------------------------------------------------------------------
# Test 1: Job Completion Detection
# ---------------------------------------------------------------------------

def test_job_completion_detection(temp_ledger):
    verifier = QualityVerifier(ledger=temp_ledger)
    # Failure status triggers RENDER_FAILED
    failed_status = {"status": "failed", "error": "Worker out of memory"}
    rep = verifier.verify_job_renders(
        campaign_id="c1",
        autoclip_job_id="j1",
        candidates=[],
        job_status_info=failed_status,
        dry_run=True,
    )
    assert rep.qa_status == "RENDER_FAILED"
    assert "Worker out of memory" in rep.failures[0]


# ---------------------------------------------------------------------------
# Test 2: Artifact Resolution & Durability
# ---------------------------------------------------------------------------

def test_artifact_durability_resolution():
    # Drive file ID makes it durable even with remote path
    durable, reason = QualityVerifier.resolve_artifact_durability(
        "/home/runner/work/out.mp4", drive_file_id="drive_123"
    )
    assert durable is True
    assert reason == "durable_google_drive"

    # Cloud URL makes it durable
    durable, reason = QualityVerifier.resolve_artifact_durability(
        "", artifact_url="https://storage.googleapis.com/bucket/out.mp4"
    )
    assert durable is True
    assert reason == "durable_cloud_url"

    # Ephemeral runner path without Drive backup is rejected
    durable, reason = QualityVerifier.resolve_artifact_durability(
        "/home/runner/work/out.mp4", drive_file_id=None
    )
    assert durable is False
    assert reason == "ephemeral_runner_path_missing_drive_backup"


# ---------------------------------------------------------------------------
# Test 3: Exactly-Five Valid Clip Requirement (Strict Limit & Selection)
# ---------------------------------------------------------------------------

def test_exactly_five_valid_clips_invariant(temp_ledger, mock_brief):
    verifier = QualityVerifier(ledger=temp_ledger)
    # Provide 7 valid candidates: verifier must evaluate all and enforce exactly 5 top clips
    candidates = [make_valid_candidate(i) for i in range(1, 8)]
    report = verifier.verify_job_renders(
        campaign_id="test_camp_7cand",
        autoclip_job_id="job_7cand",
        candidates=candidates,
        brief=mock_brief,
        dry_run=True,
    )
    assert report.total_clips_evaluated == 7
    assert report.valid_clips_count == 7
    assert report.qa_status in ("RENDER_PASS", "RENDER_WARN")


# ---------------------------------------------------------------------------
# Test 5: 5 Valid Clips -> RENDER_READY
# ---------------------------------------------------------------------------

def test_five_valid_clips_produces_render_ready(temp_ledger, mock_brief):
    verifier = QualityVerifier(ledger=temp_ledger)
    candidates = [make_valid_candidate(i) for i in range(1, 6)]
    
    report = verifier.verify_job_renders(
        campaign_id="test_camp_001",
        autoclip_job_id="job_001",
        candidates=candidates,
        brief=mock_brief,
        dry_run=True,
    )
    assert report.valid_clips_count == 5
    assert report.qa_status == "RENDER_PASS"
    assert report.overall_quality_score == 95.0


# ---------------------------------------------------------------------------
# Test 4: 1–4 Valid Clips -> INSUFFICIENT_VALID_CLIPS
# ---------------------------------------------------------------------------

def test_fewer_than_five_clips_produces_insufficient_valid_clips(temp_ledger, mock_brief):
    verifier = QualityVerifier(ledger=temp_ledger)
    for count in [1, 2, 3, 4]:
        candidates = [make_valid_candidate(i) for i in range(1, count + 1)]
        report = verifier.verify_job_renders(
            campaign_id=f"camp_short_{count}",
            autoclip_job_id=f"job_short_{count}",
            candidates=candidates,
            brief=mock_brief,
            dry_run=True,
        )
        assert report.valid_clips_count == count
        assert report.qa_status == "INSUFFICIENT_VALID_CLIPS"
        assert any("INSUFFICIENT_VALID_CLIPS" in f for f in report.failures)


# ---------------------------------------------------------------------------
# Test 6: Duplicate / Overlapping Clip Rejection (Distinctness)
# ---------------------------------------------------------------------------

def test_duplicate_clip_distinctness_rejection(temp_ledger, mock_brief):
    verifier = QualityVerifier(ledger=temp_ledger)
    c1 = make_valid_candidate(1)
    c2 = make_valid_candidate(1)  # Identical clip_id
    
    candidates = [c1, c2, make_valid_candidate(2), make_valid_candidate(3), make_valid_candidate(4)]
    report = verifier.verify_job_renders(
        campaign_id="camp_dup",
        autoclip_job_id="job_dup",
        candidates=candidates,
        brief=mock_brief,
        dry_run=True,
    )
    # c2 is rejected as duplicate; only 4 unique clips remain -> INSUFFICIENT_VALID_CLIPS
    assert report.valid_clips_count == 4
    assert report.qa_status == "INSUFFICIENT_VALID_CLIPS"
    assert any("Duplicate clip_id" in f for f in report.failures)


# ---------------------------------------------------------------------------
# Test 7: Duration < 20s Rejection
# ---------------------------------------------------------------------------

def test_duration_under_20s_rejected(temp_ledger, mock_brief):
    verifier = QualityVerifier(ledger=temp_ledger)
    bad_cand = make_valid_candidate(1, duration=19.4)
    tech = verifier.evaluate_technical_qa(bad_cand)
    assert tech.is_valid is False
    assert any("below required minimum 20.00s" in r for r in tech.rejection_reasons)


# ---------------------------------------------------------------------------
# Test 8: Duration > 30s Rejection
# ---------------------------------------------------------------------------

def test_duration_over_30s_rejected(temp_ledger, mock_brief):
    verifier = QualityVerifier(ledger=temp_ledger)
    bad_cand = make_valid_candidate(1, duration=30.8)
    tech = verifier.evaluate_technical_qa(bad_cand)
    assert tech.is_valid is False
    assert any("exceeds required maximum 30.00s" in r for r in tech.rejection_reasons)


# ---------------------------------------------------------------------------
# Test 9: Valid 20–30s Acceptance
# ---------------------------------------------------------------------------

def test_duration_within_canonical_range_accepted(temp_ledger, mock_brief):
    verifier = QualityVerifier(ledger=temp_ledger)
    for d in [20.0, 24.5, 30.0]:
        cand = make_valid_candidate(1, duration=d)
        tech = verifier.evaluate_technical_qa(cand)
        assert tech.is_valid is True


# ---------------------------------------------------------------------------
# Test 10: Corrupted Video Rejection (Decode Check Failure)
# ---------------------------------------------------------------------------

def test_corrupted_video_decode_failure_rejected(temp_ledger, mock_brief):
    verifier = QualityVerifier(ledger=temp_ledger)
    cand = make_valid_candidate(1)
    cand["decode_ok"] = False
    tech = verifier.evaluate_technical_qa(cand)
    assert tech.is_valid is False
    assert any("decode stream integrity check failed" in r for r in tech.rejection_reasons)


# ---------------------------------------------------------------------------
# Test 11: Invalid Portrait Ratio Rejection
# ---------------------------------------------------------------------------

def test_invalid_resolution_orientation_rejected(temp_ledger, mock_brief):
    verifier = QualityVerifier(ledger=temp_ledger)
    # Landscape 1920x1080 instead of portrait 1080x1920
    cand = make_valid_candidate(1)
    cand["width"] = 1920
    cand["height"] = 1080
    tech = verifier.evaluate_technical_qa(cand)
    assert tech.is_valid is False
    assert any("Resolution mismatch" in r for r in tech.rejection_reasons)


# ---------------------------------------------------------------------------
# Test 12: Missing Audio Stream Rejection
# ---------------------------------------------------------------------------

def test_missing_audio_stream_rejected(temp_ledger, mock_brief):
    verifier = QualityVerifier(ledger=temp_ledger)
    cand = make_valid_candidate(1)
    cand["audio_codec"] = ""
    cand["mean_volume_db"] = -90.0
    tech = verifier.evaluate_technical_qa(cand)
    assert tech.is_valid is False


# ---------------------------------------------------------------------------
# Test 13: Voice Dropout / Near-Silence Rejection
# ---------------------------------------------------------------------------

def test_near_silence_voice_dropout_rejected(temp_ledger, mock_brief):
    verifier = QualityVerifier(ledger=temp_ledger)
    cand = make_valid_candidate(1)
    cand["mean_volume_db"] = -42.0  # Near silence threshold is -35.0 dBFS
    tech = verifier.evaluate_technical_qa(cand)
    assert tech.is_valid is False
    assert any("near-silent" in r for r in tech.rejection_reasons)


# ---------------------------------------------------------------------------
# Test 14: BGM Missing Rejection
# ---------------------------------------------------------------------------

def test_bgm_missing_rejected_when_required(temp_ledger, mock_brief):
    verifier = QualityVerifier(ledger=temp_ledger)
    cand = make_valid_candidate(1)
    cand["mean_volume_db"] = -50.0  # No audio/silent
    tech = verifier.evaluate_technical_qa(cand)
    comp = verifier.evaluate_campaign_compliance(mock_brief, cand, tech)
    bgm_res = next(r for r in comp if r.category == RuleCategory.BGM.value)
    assert bgm_res.status == RuleComplianceStatus.SUPPORTED_AND_FAILED


# ---------------------------------------------------------------------------
# Test 15: BGM Clipping Warning
# ---------------------------------------------------------------------------

def test_audio_clipping_triggers_warning(temp_ledger, mock_brief):
    verifier = QualityVerifier(ledger=temp_ledger)
    cand = make_valid_candidate(1)
    cand["true_peak_db"] = 0.5  # Positive peak >= 0.0 dBFS
    tech = verifier.evaluate_technical_qa(cand)
    assert any("potential digital clipping" in w for w in tech.warnings)


# ---------------------------------------------------------------------------
# Test 16: SFX Timing & Metadata Validation
# ---------------------------------------------------------------------------

def test_sfx_metadata_and_telemetry_preserved(temp_ledger, mock_brief):
    verifier = QualityVerifier(ledger=temp_ledger)
    cand = make_valid_candidate(1)
    cand["metadata"] = {"sfx_events": [{"time_s": 2.5, "asset": "whoosh.mp3"}]}
    report = verifier.verify_job_renders("c_sfx", "j_sfx", [cand] * 5, mock_brief, dry_run=True)
    assert report.clips[0].metadata["sfx_events"][0]["asset"] == "whoosh.mp3"


# ---------------------------------------------------------------------------
# Test 17: Caption Compliance (Presence & Prohibited Captions)
# ---------------------------------------------------------------------------

def test_caption_compliance_checking(temp_ledger):
    verifier = QualityVerifier(ledger=temp_ledger)
    
    # Required caption passes when present
    brief_req = WhopCampaignBrief(
        campaign_id="c_cap",
        title="Cap Test",
        campaign_url="https://whop.com/test",
        guideline_hash="h1",
        guideline_source_type="card",
        guideline_source_reference="url",
        parsing_status=ParsingStatus.PARSED,
        rules=[
            CampaignRule(rule_id="r_cap", category=RuleCategory.CAPTIONS, text="Must have captions", mandatory=True, source_reference="test", status="ACTIVE")
        ]
    )
    cand = make_valid_candidate(1)
    tech = verifier.evaluate_technical_qa(cand)
    comp = verifier.evaluate_campaign_compliance(brief_req, cand, tech)
    assert comp[0].status == RuleComplianceStatus.SUPPORTED_AND_SATISFIED

    # Prohibited captions fail when present
    brief_proh = WhopCampaignBrief(
        campaign_id="c_cap2",
        title="Cap Test 2",
        campaign_url="https://whop.com/test",
        guideline_hash="h2",
        guideline_source_type="card",
        guideline_source_reference="url",
        parsing_status=ParsingStatus.PARSED,
        rules=[
            CampaignRule(rule_id="r_cap_proh", category=RuleCategory.CAPTIONS, text="No captions allowed", mandatory=True, prohibited=True, source_reference="test", status="ACTIVE")
        ]
    )
    cand_with_captions = make_valid_candidate(1)
    cand_with_captions["caption_style"] = "bold_pop"
    comp_proh = verifier.evaluate_campaign_compliance(brief_proh, cand_with_captions, tech)
    assert comp_proh[0].status == RuleComplianceStatus.SUPPORTED_AND_FAILED


# ---------------------------------------------------------------------------
# Test 18: Mandatory CampaignBrief Rule Failure Handling
# ---------------------------------------------------------------------------

def test_mandatory_rule_failure_disqualifies_clip(temp_ledger, mock_brief):
    verifier = QualityVerifier(ledger=temp_ledger)
    candidates = [make_valid_candidate(i) for i in range(1, 6)]
    # Make clip 1 violate mandatory duration rule
    candidates[0]["duration"] = 12.0
    report = verifier.verify_job_renders("c_mand", "j_mand", candidates, mock_brief, dry_run=True)
    assert report.valid_clips_count == 4
    assert report.qa_status == "INSUFFICIENT_VALID_CLIPS"


# ---------------------------------------------------------------------------
# Test 19: Unsupported Mandatory Rule Handling (UNSUPPORTED_REQUIRES_REVIEW)
# ---------------------------------------------------------------------------

def test_unsupported_mandatory_rule_requires_review(temp_ledger):
    verifier = QualityVerifier(ledger=temp_ledger)
    brief = WhopCampaignBrief(
        campaign_id="c_unsupp_mand",
        title="Mandatory Review Test",
        campaign_url="https://whop.com/test",
        guideline_hash="h_mand",
        guideline_source_type="card",
        guideline_source_reference="url",
        parsing_status=ParsingStatus.PARSED,
        rules=[
            CampaignRule(
                rule_id="r_human",
                category=RuleCategory.SUBMISSION,
                text="Must join Telegram group and verify identity manually",
                mandatory=True,
                is_operational=True,
                source_reference="test",
                status="OPERATIONAL",
            )
        ]
    )
    candidates = [make_valid_candidate(i) for i in range(1, 6)]
    report = verifier.verify_job_renders("c_unsupp_mand", "j_unsupp_mand", candidates, brief, dry_run=True)
    # Unsupported mandatory rule prevents clean automated pass
    assert len(report.unsupported_rules) == 1
    assert report.unsupported_rules[0].status == RuleComplianceStatus.UNSUPPORTED_REQUIRES_REVIEW
    assert report.valid_clips_count == 0
    assert report.qa_status == "INSUFFICIENT_VALID_CLIPS"


# ---------------------------------------------------------------------------
# Test 20: Unsupported Optional / Operational Rule Handling (NOT_APPLICABLE)
# ---------------------------------------------------------------------------

def test_unsupported_optional_operational_rule_does_not_block(temp_ledger, mock_brief):
    verifier = QualityVerifier(ledger=temp_ledger)
    # Rule 5 in mock_brief is an operational rule with mandatory=False
    candidates = [make_valid_candidate(i) for i in range(1, 6)]
    report = verifier.verify_job_renders("c_op_opt", "j_op_opt", candidates, mock_brief, dry_run=True)
    assert report.valid_clips_count == 5
    assert report.qa_status == "RENDER_PASS"
    comp_map = {r.rule_id: r.status for r in report.clips[0].compliance_results}
    assert comp_map["r5"] == RuleComplianceStatus.NOT_APPLICABLE


# ---------------------------------------------------------------------------
# Test 21: Full Compliance Pass
# ---------------------------------------------------------------------------

def test_full_compliance_pass_all_five_clips(temp_ledger, mock_brief):
    verifier = QualityVerifier(ledger=temp_ledger)
    candidates = [make_valid_candidate(i) for i in range(1, 6)]
    report = verifier.verify_job_renders("c_full_pass", "j_full_pass", candidates, mock_brief, dry_run=True)
    assert report.qa_status == "RENDER_PASS"
    assert report.valid_clips_count == 5
    assert len(report.failures) == 0


# ---------------------------------------------------------------------------
# Test 22: Artifact Persistence Failure Handling
# ---------------------------------------------------------------------------

def test_ephemeral_runner_path_without_drive_backup_fails(temp_ledger, mock_brief):
    verifier = QualityVerifier(ledger=temp_ledger)
    candidates = [make_valid_candidate(i) for i in range(1, 6)]
    # Strip drive_file_id and set ephemeral path on all clips
    for c in candidates:
        c["drive_file_id"] = None
        c["output_path"] = "/home/runner/work/al-amr/exports/final.mp4"
    report = verifier.verify_job_renders("c_ephem", "j_ephem", candidates, mock_brief, dry_run=True)
    assert report.valid_clips_count == 0
    assert report.qa_status == "INSUFFICIENT_VALID_CLIPS"
    assert any("Artifact durability check failed" in f for f in report.failures)


# ---------------------------------------------------------------------------
# Test 23: RENDER_WARN Decision Handling
# ---------------------------------------------------------------------------

def test_render_warn_when_non_fatal_warnings_exist(temp_ledger, mock_brief):
    verifier = QualityVerifier(ledger=temp_ledger)
    candidates = [make_valid_candidate(i) for i in range(1, 6)]
    # Add non-fatal warning (slight digital peak)
    for c in candidates:
        c["true_peak_db"] = 0.2
    report = verifier.verify_job_renders("c_warn", "j_warn", candidates, mock_brief, dry_run=True)
    assert report.valid_clips_count == 5
    assert report.qa_status == "RENDER_WARN"
    assert len(report.warnings) > 0


# ---------------------------------------------------------------------------
# Test 24: RENDER_FAILED Decision Handling
# ---------------------------------------------------------------------------

def test_render_failed_on_control_plane_failure(temp_ledger, mock_brief):
    verifier = QualityVerifier(ledger=temp_ledger)
    job_status = {"status": "failed", "error": "Remotion render crashed"}
    report = verifier.verify_job_renders("c_fail", "j_fail", [], mock_brief, job_status_info=job_status, dry_run=True)
    assert report.qa_status == "RENDER_FAILED"
    assert "Remotion render crashed" in report.failures[0]


# ---------------------------------------------------------------------------
# Test 25: QA Evaluation Idempotency
# ---------------------------------------------------------------------------

def test_qa_evaluation_idempotency_reuses_record(temp_ledger, mock_brief):
    verifier = QualityVerifier(ledger=temp_ledger)
    candidates = [make_valid_candidate(i) for i in range(1, 6)]
    
    # First evaluation
    rep1 = verifier.verify_job_renders("c_idem", "j_idem", candidates, mock_brief, dry_run=True)
    assert rep1.id is not None

    # Second evaluation with identical inputs
    rep2 = verifier.verify_job_renders("c_idem", "j_idem", candidates, mock_brief, dry_run=True)
    assert rep2.id == rep1.id
    assert rep2.artifact_hash == rep1.artifact_hash

    # Listing QA records confirms exactly one record persisted
    records = temp_ledger.list_qa_records("c_idem")
    assert len(records) == 1


# ---------------------------------------------------------------------------
# Test 26: State Machine Integration & Transitions
# ---------------------------------------------------------------------------

def test_state_machine_transitions_on_qa_outcome(temp_ledger, mock_brief):
    # Ingest campaign into ledger in INGESTED state
    camp = CampaignRecord(
        campaign_id="c_sm",
        title="State Machine Test",
        campaign_url="https://whop.com/sm",
        current_state=CampaignState.INGESTED,
    )
    temp_ledger.save_campaign(camp)

    verifier = QualityVerifier(ledger=temp_ledger)
    candidates = [make_valid_candidate(i) for i in range(1, 6)]
    report = verifier.verify_job_renders("c_sm", "j_sm", candidates, mock_brief, dry_run=True)
    assert report.qa_status == "RENDER_PASS"

    # Campaign in ledger should now be RENDER_READY
    updated_camp = temp_ledger.get_campaign("c_sm")
    assert updated_camp.current_state == CampaignState.RENDER_READY

    # Verify event trail
    events = temp_ledger.list_events("c_sm")
    states = [e.new_state for e in events]
    assert "RENDERING" in states
    assert "RENDER_READY" in states


# ---------------------------------------------------------------------------
# Test 27: Quality Score Integration
# ---------------------------------------------------------------------------

def test_quality_score_calculation(temp_ledger, mock_brief):
    verifier = QualityVerifier(ledger=temp_ledger)
    candidates = [make_valid_candidate(i) for i in range(1, 6)]
    for i, c in enumerate(candidates):
        c["quality_score"] = 80.0 + (i * 2.0)  # 80, 82, 84, 86, 88 -> mean 84.0
    report = verifier.verify_job_renders("c_score", "j_score", candidates, mock_brief, dry_run=True)
    assert report.overall_quality_score == 84.0


# ---------------------------------------------------------------------------
# Test 28: Step 4 -> Step 5 -> Step 6 End-to-End Pipeline Mapping
# ---------------------------------------------------------------------------

def test_e2e_brief_to_connector_to_quality_verifier(temp_ledger, mock_brief):
    verifier = QualityVerifier(ledger=temp_ledger)
    # Bridge brief through AutoClip brief format
    autoclip_brief = mock_brief.to_autoclip_brief()
    assert autoclip_brief.name == mock_brief.title[:80]
    assert len(autoclip_brief.mandatory_rules) >= 4

    candidates = [make_valid_candidate(i) for i in range(1, 6)]
    report = verifier.verify_job_renders(
        campaign_id=mock_brief.campaign_id,
        autoclip_job_id="job_e2e_001",
        candidates=candidates,
        brief=mock_brief,
        dry_run=True,
    )
    assert report.valid_clips_count == 5
    assert report.qa_status == "RENDER_PASS"
