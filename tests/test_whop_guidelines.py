"""Unit tests for Whop Campaign Guidelines Parser and CampaignBrief (Step 4)."""

import hashlib
import json
from pathlib import Path
import pytest

from whop.config import WhopConfig
from whop.browser import FORBIDDEN_MUTATION_ACTIONS
from whop.models import (
    CampaignRule,
    ParsingStatus,
    RuleCategory,
    WhopCampaignBrief,
    validate_campaign_brief,
)
from whop.ledger import CampaignLedger
from whop.guidelines import (
    compute_guideline_hash,
    normalize_guideline_content,
    parse_campaign_guidelines,
)


@pytest.fixture
def temp_ledger(tmp_path: Path) -> CampaignLedger:
    """Fixture providing an isolated CampaignLedger instance."""
    db_file = tmp_path / "test_brief_ledger.db"
    return CampaignLedger(db_path=db_file)


# ---------------------------------------------------------------------------
# Test 1: CampaignBrief Schema Validation
# ---------------------------------------------------------------------------

def test_campaign_brief_schema_validation():
    valid_brief = WhopCampaignBrief(
        campaign_id="camp-valid-1",
        title="Valid Campaign",
        campaign_url="https://whop.com/discover/camp-valid-1",
        guideline_hash="abc123def456",
        parsing_status=ParsingStatus.PARSED,
        rules=[
            CampaignRule(
                rule_id="rule_001",
                category=RuleCategory.DURATION,
                text="Keep clips between 30 and 60 seconds",
                source_reference="https://whop.com/discover/camp-valid-1",
                platform="all",
            )
        ],
    )
    assert validate_campaign_brief(valid_brief) == []

    invalid_brief = WhopCampaignBrief(
        campaign_id="",
        title="",
        campaign_url="https://whop.com",
        guideline_hash="hash",
    )
    errors = validate_campaign_brief(invalid_brief)
    assert any("Missing campaign_id" in e for e in errors)
    assert any("Missing title" in e for e in errors)

    contradictory_brief = WhopCampaignBrief(
        campaign_id="camp-bad-dur",
        title="Bad Duration",
        campaign_url="https://whop.com",
        guideline_hash="hash",
        duration_min_s=90.0,
        duration_max_s=30.0,
    )
    assert any("Contradictory duration" in e for e in validate_campaign_brief(contradictory_brief))

    dup_rule_brief = WhopCampaignBrief(
        campaign_id="camp-dup",
        title="Dup Rules",
        campaign_url="https://whop.com",
        guideline_hash="hash",
        rules=[
            CampaignRule(rule_id="r1", category=RuleCategory.CONTENT, text="Rule 1", source_reference="src"),
            CampaignRule(rule_id="r1", category=RuleCategory.CONTENT, text="Rule 1 Dup", source_reference="src"),
        ],
    )
    assert any("Duplicate rule_id" in e for e in validate_campaign_brief(dup_rule_brief))


# ---------------------------------------------------------------------------
# Test 2: Basic Guideline Parsing
# ---------------------------------------------------------------------------

def test_basic_guideline_parsing():
    raw = """
    Spacetime Chronicles Video Clipping Campaign
    Unique hand-made 2.5D cinematic adventure game.
    Paying $1.25 CPM for viral short-form clips on TikTok, Shorts & Reels.
    
    Content requirements:
    - Include brand logo
    - Add link in bio https://store.steampowered.com/app/12345
    - Keep clips between 30 and 60 seconds
    - Get approval before publishing
    """
    brief = parse_campaign_guidelines(
        campaign_id="camp-spacetime",
        title="Spacetime Chronicles",
        campaign_url="https://contentrewards.com/discover/camp-spacetime",
        raw_text=raw,
        download_external=False,
    )

    assert brief.campaign_id == "camp-spacetime"
    assert brief.title == "Spacetime Chronicles"
    assert brief.parsing_status == ParsingStatus.PARSED
    assert brief.logo_watermark_required is True
    assert brief.link_in_bio == "https://store.steampowered.com/app/12345"
    assert brief.approval_gate_required is True
    assert len(brief.rules) > 0


# ---------------------------------------------------------------------------
# Test 3: Headings and Bullets Extraction
# ---------------------------------------------------------------------------

def test_headings_and_bullets():
    raw = """
    ## SECTION 1: VISUAL RULES
    * Use bold pop captions
    * Keep subject in center frame
    
    ## SECTION 2: AUDIO RULES
    1. Use trending audio
    2. Normalize speech volume
    """
    brief = parse_campaign_guidelines(
        campaign_id="camp-headings",
        title="Headings & Bullets Campaign",
        campaign_url="https://whop.com/headings",
        raw_text=raw,
        download_external=False,
    )
    assert len(brief.rules) >= 2
    assert any("trending audio" in r.text.lower() for r in brief.rules)
    assert any("bold pop" in r.text.lower() for r in brief.rules)


# ---------------------------------------------------------------------------
# Test 4: Multi-Column and Whitespace Normalization
# ---------------------------------------------------------------------------

def test_multi_column_and_whitespace_normalization():
    dirty_text = "\r\n  RULE 1:   Keep   clips under   60 seconds.  \r\n\r\n\r\n\r\n  RULE 2:   no combat.  \r\n"
    normalized = normalize_guideline_content(dirty_text)

    assert "rule 1: keep clips under 60 seconds." in normalized
    assert "rule 2: no combat." in normalized
    assert "\r" not in normalized


# ---------------------------------------------------------------------------
# Test 5: Mandatory Rule Extraction
# ---------------------------------------------------------------------------

def test_mandatory_rule_extraction():
    raw = """
    - Mandatory: Include brand logo on all video clips.
    - Strong hooks required in first 3 seconds.
    """
    brief = parse_campaign_guidelines(
        campaign_id="c-mand", title="Mandatory Campaign", campaign_url="url",
        raw_text=raw, download_external=False,
    )
    mandatory_rules = [r for r in brief.rules if r.mandatory]
    assert len(mandatory_rules) >= 1
    assert any(r.category == RuleCategory.BRANDING for r in mandatory_rules)


# ---------------------------------------------------------------------------
# Test 6: Prohibited Rule Extraction
# ---------------------------------------------------------------------------

def test_prohibited_rule_extraction():
    raw = """
    - Prohibited: Avoid controversial political topics.
    - Forbidden: Do not include copyrighted music.
    """
    brief = parse_campaign_guidelines(
        campaign_id="c-prohib", title="Prohibited Campaign", campaign_url="url",
        raw_text=raw, download_external=False,
    )
    prohibited_rules = [r for r in brief.rules if r.prohibited]
    assert len(prohibited_rules) >= 1
    assert any("Prohibited" in r.text or r.prohibited for r in prohibited_rules)


# ---------------------------------------------------------------------------
# Test 7: Duration Extraction (Min, Max, Preferred, Exact)
# ---------------------------------------------------------------------------

def test_duration_extraction():
    b1 = parse_campaign_guidelines(
        campaign_id="c1", title="T1", campaign_url="url",
        raw_text="Clips must be between 25 and 50 seconds in length.",
        download_external=False,
    )
    assert b1.duration_min_s == 25.0
    assert b1.duration_max_s == 50.0
    assert b1.duration_preferred_s == 37.5

    b2 = parse_campaign_guidelines(
        campaign_id="c2", title="T2", campaign_url="url",
        raw_text="All submissions must be under 45s.",
        download_external=False,
    )
    assert b2.duration_max_s == 45.0
    assert b2.duration_min_s == 15.0

    b3 = parse_campaign_guidelines(
        campaign_id="c3", title="T3", campaign_url="url",
        raw_text="Video length: exact 30 seconds.",
        download_external=False,
    )
    assert b3.duration_exact_s == 30.0
    assert b3.duration_min_s == 30.0
    assert b3.duration_max_s == 30.0


# ---------------------------------------------------------------------------
# Test 8: CTA & Link In Bio Extraction
# ---------------------------------------------------------------------------

def test_cta_and_link_in_bio_extraction():
    raw = "Add link in bio https://store.steampowered.com/app/5020490/Spacetime_Chronicles"
    brief = parse_campaign_guidelines(
        campaign_id="c-cta", title="CTA Title", campaign_url="url",
        raw_text=raw, download_external=False,
    )
    assert brief.link_in_bio == "https://store.steampowered.com/app/5020490/Spacetime_Chronicles"
    bio_rules = [r for r in brief.rules if "link in bio" in r.text.lower()]
    assert len(bio_rules) == 1
    assert bio_rules[0].category == RuleCategory.PUBLISHING


# ---------------------------------------------------------------------------
# Test 9: Caption & Subtitle Extraction
# ---------------------------------------------------------------------------

def test_caption_and_subtitle_extraction():
    raw = "Use bold pop captions with high contrast. Include brand logo."
    brief = parse_campaign_guidelines(
        campaign_id="c-cap", title="Cap Title", campaign_url="url",
        raw_text=raw, download_external=False,
    )
    assert brief.caption_preset == "bold_pop"
    assert brief.logo_watermark_required is True


# ---------------------------------------------------------------------------
# Test 10: Platform-Specific Rules Scope
# ---------------------------------------------------------------------------

def test_platform_specific_rules_scope():
    raw = """
    Post clips across networks:
    - On TikTok: use vertical format and trending audio.
    - On YouTube Shorts: add pinned comment with full episode link.
    - On Instagram: tag creator account in caption.
    """
    brief = parse_campaign_guidelines(
        campaign_id="c-platforms", title="Multi Platform", campaign_url="url",
        raw_text=raw, download_external=False,
    )
    tiktok_rules = [r for r in brief.rules if r.platform == "tiktok"]
    youtube_rules = [r for r in brief.rules if r.platform == "youtube"]
    instagram_rules = [r for r in brief.rules if r.platform == "instagram"]

    assert len(tiktok_rules) >= 1
    assert len(youtube_rules) >= 1
    assert len(instagram_rules) >= 1


# ---------------------------------------------------------------------------
# Test 11: Unknown & Ambiguous Rules (INTERPRETATION_REQUIRED)
# ---------------------------------------------------------------------------

def test_unknown_and_ambiguous_rules_flagged_interpretation_required():
    raw = "Make it engaging. Inspirational moments over trending sounds hit hardest."
    brief = parse_campaign_guidelines(
        campaign_id="c-ambiguous", title="Ambiguous", campaign_url="url",
        raw_text=raw, download_external=False,
    )
    unresolved = [r for r in brief.rules if r.status == "INTERPRETATION_REQUIRED"]
    assert len(unresolved) >= 1
    for r in unresolved:
        assert r.normalized_value is None


# ---------------------------------------------------------------------------
# Test 12: Separation of Operational Instructions from Content Rules
# ---------------------------------------------------------------------------

def test_operational_instruction_separation():
    raw = """
    Join Campaign
    Per 1K views: $1.50
    Min payout: $1.50
    Max payout: $100.00
    Budget: $1,000 remaining
    Views across every approved clip
    Get approval before publishing
    Help & Support
    
    Content requirements:
    Include brand logo
    Clips must be 30-60s
    """
    brief = parse_campaign_guidelines(
        campaign_id="c-ops", title="Ops Separation", campaign_url="url",
        raw_text=raw, download_external=False,
    )

    assert len(brief.operational_instructions) >= 4
    assert any("Join Campaign" in op for op in brief.operational_instructions)
    assert any("Per 1K views" in op for op in brief.operational_instructions)

    content_rules = [r for r in brief.rules if not r.is_operational]
    for r in content_rules:
        assert "per 1k views" not in r.text.lower()
        assert "min payout" not in r.text.lower()


# ---------------------------------------------------------------------------
# Test 13: Deterministic Guideline Hashing
# ---------------------------------------------------------------------------

def test_deterministic_guideline_hashing():
    text1 = "Spacetime Chronicles. Include brand logo. Keep clips 30-60s."
    text2 = "  SPACETIME CHRONICLES.   Include brand logo. \r\n Keep clips 30-60s.  "

    h1 = compute_guideline_hash(text1)
    h2 = compute_guideline_hash(text2)
    assert h1 == h2

    h_diff = compute_guideline_hash("Different campaign rules.")
    assert h1 != h_diff


# ---------------------------------------------------------------------------
# Test 14: Idempotent Reprocessing
# ---------------------------------------------------------------------------

def test_idempotent_reprocessing(temp_ledger: CampaignLedger):
    from whop.catalog import build_discovered_campaign
    camp = build_discovered_campaign(
        campaign_id="c-idemp-test",
        title="Idempotent Campaign",
        campaign_url="https://whop.com/idemp",
        payout_raw="$1.50 CPM",
        platforms_raw="YouTube",
        source_urls=["https://youtube.com/watch?v=123"],
    )
    temp_ledger.ingest_discovered_campaign(camp)

    brief = parse_campaign_guidelines(
        campaign_id="c-idemp-test",
        title="Idempotent Campaign",
        campaign_url="https://whop.com/idemp",
        raw_text="Include brand logo. Clips 30-60s.",
        download_external=False,
    )
    saved1, is_new1 = temp_ledger.save_campaign_brief(brief)
    assert is_new1 is True

    # Same brief saved again
    saved2, is_new2 = temp_ledger.save_campaign_brief(brief)
    assert is_new2 is False

    briefs = temp_ledger.list_campaign_briefs("c-idemp-test")
    assert len(briefs) == 1


# ---------------------------------------------------------------------------
# Test 15: Changed Guideline Hash Preserves History
# ---------------------------------------------------------------------------

def test_changed_guideline_hash_preserves_history(temp_ledger: CampaignLedger):
    from whop.catalog import build_discovered_campaign
    camp = build_discovered_campaign(
        campaign_id="c-history-test",
        title="History Campaign",
        campaign_url="https://whop.com/hist",
        payout_raw="$1.50 CPM",
        platforms_raw="YouTube",
        source_urls=["https://youtube.com/watch?v=123"],
    )
    temp_ledger.ingest_discovered_campaign(camp)

    brief_v1 = parse_campaign_guidelines(
        campaign_id="c-history-test",
        title="History Campaign",
        campaign_url="https://whop.com/hist",
        raw_text="Include brand logo. Clips 30-60s.",
        download_external=False,
    )
    temp_ledger.save_campaign_brief(brief_v1)

    brief_v2 = parse_campaign_guidelines(
        campaign_id="c-history-test",
        title="History Campaign",
        campaign_url="https://whop.com/hist",
        raw_text="Include brand logo. Clips 15-30s. Version 2 updated guidelines.",
        download_external=False,
    )
    saved2, is_new2 = temp_ledger.save_campaign_brief(brief_v2)
    assert is_new2 is True

    history = temp_ledger.list_campaign_briefs("c-history-test")
    assert len(history) == 2
    assert history[0].duration_max_s == 60.0
    assert history[1].duration_max_s == 30.0


# ---------------------------------------------------------------------------
# Test 16: Malformed Guideline Handling
# ---------------------------------------------------------------------------

def test_malformed_guideline_handling():
    brief = parse_campaign_guidelines(
        campaign_id="c-malformed",
        title="Contradictory Duration",
        campaign_url="https://whop.com",
        raw_text="Duration must be 90 to 20 seconds.",
        download_external=False,
    )
    assert brief.parsing_status in (ParsingStatus.PARSED, ParsingStatus.PARTIAL)


# ---------------------------------------------------------------------------
# Test 17: Unavailable Guidelines Handling
# ---------------------------------------------------------------------------

def test_unavailable_guidelines_handling():
    brief = parse_campaign_guidelines(
        campaign_id="c-empty",
        title="Empty Campaign",
        campaign_url="https://whop.com/empty",
        raw_text="",
        download_external=False,
    )
    assert brief.parsing_status == ParsingStatus.GUIDELINES_UNAVAILABLE
    assert brief.guideline_hash == ""
    assert len(brief.rules) == 0


# ---------------------------------------------------------------------------
# Test 18: Rule Provenance and Source Excerpts
# ---------------------------------------------------------------------------

def test_rule_provenance_and_source_excerpts():
    raw = "Include brand logo in the top right corner."
    brief = parse_campaign_guidelines(
        campaign_id="c-prov",
        title="Provenance Test",
        campaign_url="https://whop.com/prov",
        raw_text=raw,
        download_external=False,
    )
    assert len(brief.rules) >= 1
    for r in brief.rules:
        assert r.source_reference == "https://whop.com/prov"
        assert len(r.source_excerpt) > 0
        assert r.confidence in ("explicit", "inferred")


# ---------------------------------------------------------------------------
# Test 19: Ledger Brief Persistence and Audit Events
# ---------------------------------------------------------------------------

def test_ledger_brief_persistence_and_events(temp_ledger: CampaignLedger):
    from whop.catalog import build_discovered_campaign
    camp = build_discovered_campaign(
        campaign_id="c-audit-1",
        title="Audit Campaign",
        campaign_url="https://whop.com/audit",
        payout_raw="$1.00 CPM",
        platforms_raw="TikTok",
        source_urls=["https://tiktok.com/@video/1"],
    )
    temp_ledger.ingest_discovered_campaign(camp)

    brief = parse_campaign_guidelines(
        campaign_id="c-audit-1",
        title="Audit Campaign",
        campaign_url="https://whop.com/audit",
        raw_text="Clips under 60 seconds. Strong hooks required.",
        download_external=False,
    )
    temp_ledger.save_campaign_brief(brief)

    latest = temp_ledger.get_latest_campaign_brief("c-audit-1")
    assert latest is not None
    assert latest.campaign_id == "c-audit-1"

    events = temp_ledger.list_events("c-audit-1")
    brief_events = [e for e in events if e.reason in ("BRIEF_PARSED", "BRIEF_REUSED")]
    assert len(brief_events) == 1
    assert brief_events[0].reason == "BRIEF_PARSED"


# ---------------------------------------------------------------------------
# Test 20: Zero-Mutation Whop Dry Run Safety
# ---------------------------------------------------------------------------

def test_no_whop_mutation_behavior():
    cfg = WhopConfig(dry_run=True)
    assert cfg.dry_run is True

    # FORBIDDEN_MUTATION_ACTIONS must contain all mutation verbs
    for action in ("join", "claim", "apply", "submit", "upload"):
        assert any(action in forbidden for forbidden in FORBIDDEN_MUTATION_ACTIONS)


# ---------------------------------------------------------------------------
# Test 21: AutoClip Brief Interoperability
# ---------------------------------------------------------------------------

def test_autoclip_brief_interoperability():
    brief = WhopCampaignBrief(
        campaign_id="c-interop-1",
        title="Interop Campaign",
        campaign_url="https://whop.com",
        guideline_hash="hash123",
        duration_min_s=30.0,
        duration_max_s=60.0,
        duration_preferred_s=45.0,
        caption_preset="bold_pop",
        cta_wording="Check link in bio",
        hashtags=["#gaming", "#viral"],
        banned_words=["leaked", "spoiler"],
    )
    autoclip_brief = brief.to_autoclip_brief()
    assert autoclip_brief.campaign_id == "c-interop-1"
    assert autoclip_brief.minimum_duration == 30.0
    assert autoclip_brief.maximum_duration == 60.0
    assert autoclip_brief.caption_preset == "bold_pop"
    assert "#gaming" in autoclip_brief.hashtags
    assert "leaked" in autoclip_brief.banned_words


# ---------------------------------------------------------------------------
# Test 22: Deep DOCX and PDF Extraction
# ---------------------------------------------------------------------------

def test_deep_docx_and_pdf_document_extraction(tmp_path: Path):
    import io
    import docx
    from whop.guidelines import fetch_guideline_document

    # Create an in-memory DOCX
    doc = docx.Document()
    doc.add_heading("Candid Club Video Guidelines", 0)
    doc.add_paragraph("Must include #CandidClub and tag @candidclub official account.")
    doc.add_paragraph("No profanity or NSFW content allowed.")
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Min Duration"
    table.cell(0, 1).text = "20s"
    table.cell(1, 0).text = "Max Duration"
    table.cell(1, 1).text = "30s"

    docx_path = tmp_path / "test_guidelines.docx"
    doc.save(str(docx_path))

    # Test direct file extraction
    text, dtype, err = fetch_guideline_document(str(docx_path))
    assert err is None
    assert "docx" in dtype
    assert "Candid Club Video Guidelines" in text
    assert "#CandidClub" in text
    assert "@candidclub" in text
    assert "20s" in text


# ---------------------------------------------------------------------------
# Test 23: Mandatory Hashtag, Mention, and CTA Extraction
# ---------------------------------------------------------------------------

def test_mandatory_hashtag_mention_and_cta_extraction():
    guideline_text = """
    Welcome to The Candid Club Creator Campaign!
    Please make sure every clip has #CandidClub and #ClipHouse tags.
    Tag our official creator handle @thecandidclub and @whop.
    Prohibited: vulgarity, competitor links.
    Add link in bio: https://whop.com/candid
    Call to action: Check out the app and join today!
    Duration between 20 and 30 seconds.
    """
    brief = parse_campaign_guidelines(
        campaign_id="c-deep-1",
        title="The Candid Club",
        campaign_url="https://whop.com/candid",
        raw_text=guideline_text,
    )
    assert "#CandidClub" in brief.hashtags
    assert "#ClipHouse" in brief.hashtags
    assert "@thecandidclub" in brief.required_mentions
    assert "@whop" in brief.required_mentions
    assert brief.link_in_bio == "https://whop.com/candid"
    assert brief.duration_min_s == 20.0
    assert brief.duration_max_s == 30.0
    assert len(brief.rules) > 5

    # Verify rule categories
    rule_texts = [r.text for r in brief.rules]
    assert any("Mandatory Hashtag: #CandidClub" in t for t in rule_texts)
    assert any("Required Account Mention: @thecandidclub" in t for t in rule_texts)

